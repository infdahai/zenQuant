"""Framework-level historical optimizer with shared anti-overfit scoring."""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from typing import Any

from zenquant.data.cot_store import macro_cot_available
from zenquant.data.deribit_status import collect_deribit_status
from zenquant.data.liquidation_pipeline import liquidation_readiness
from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktestConfig
from zenquant.historical.storage import read_parquet_rows
from zenquant.research.attribution import (
    build_bear_window_direction_exposure_attribution,
    build_bull_window_direction_exposure_attribution,
)
from zenquant.research.auto_research import (
    CampaignConfig,
    SearchSpace,
    SearchStageTimeoutError,
    run_search_stage,
)
from zenquant.research.comparison import (
    VariantSpec,
    WindowSpec,
    _run_variant_backtest,
    default_comparison_windows,
    default_variant_specs,
    default_walk_forward_windows,
    filtered_worst_period_windows,
    fixed_2023_08_window,
    paulwei_risk_baseline,
    split_window_spec,
)
from zenquant.research.data_source_review import load_source_admission_review
from zenquant.research.framework_params import (
    DEFAULT_DEV_FEE_STABILITY_LIMITS,
    RELAXED_DEV_FEE_STABILITY_LIMITS,
    STRICT_TRAIN_FEE_STOP_PROFILE_FLAGS,
    STRICT_TRAIN_TRUTH_PROFILE_FLAGS,
    baseline_anchor_seed_trials,
    binance_factor_weight_repair_seed_trials,
    build_execution_config,
    build_indicator_config,
    build_plugin_config_overrides,
    build_replay_toggles,
    build_risk_config,
    build_signal_weight_overrides,
    build_strategy_config,
    execution_inventory_rethink_seed_trials,
    hostile_constraint_violations,
    intent_churn_ablation_seed_trials,
    intent_churn_repair_seed_trials,
    param_grid_for_mode,
    paulwei_balanced_tranche_seed_trials,
    paulwei_dual_phase_fee_repair_seed_trials,
    paulwei_dual_phase_seed_trials,
    paulwei_dual_phase_spot_context_relaxed_fee_seed_trials,
    paulwei_dual_phase_spot_context_seed_trials,
    ralph_execution_fee_cooldown_seed_trials,
    ralph_intent_reduce_cadence_seed_trials,
    ralph_protective_reduce_fee_repair_seed_trials,
    ralph_protective_reduce_seed_trials,
    search_space_profile,
)
from zenquant.research.historical_extension import load_historical_extension_candidate_contract
from zenquant.research.regime_objective import score_regime_objective_candidate_generation
from zenquant.research.source_independence import load_source_independence_audit
from zenquant.research.splits import DatasetSplitSpec, default_btc_split_spec, split_summary_json
from zenquant.strategy import ARCStrategyConfig


def _etf_flow_source_metadata(macro_dir: Path) -> dict[str, Any]:
    metadata_path = macro_dir / "btc_etf_netflow" / "metadata.json"
    if not metadata_path.exists():
        return {
            "etf_flow_source": None,
            "etf_flow_source_confidence": "missing",
            "etf_flow_last_success_at": None,
        }
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "etf_flow_source": None,
            "etf_flow_source_confidence": "unknown",
            "etf_flow_last_success_at": None,
        }
    return {
        "etf_flow_source": payload.get("etf_flow_source"),
        "etf_flow_source_confidence": payload.get("etf_flow_source_confidence", "unknown"),
        "etf_flow_last_success_at": payload.get("etf_flow_last_success_at"),
    }


def _mvrv_source_metadata(macro_dir: Path) -> dict[str, Any]:
    metadata_path = macro_dir / "btc_mvrv" / "metadata.json"
    if not metadata_path.exists():
        return {
            "mvrv_source": None,
            "mvrv_source_confidence": "missing",
            "mvrv_last_success_at": None,
        }
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "mvrv_source": None,
            "mvrv_source_confidence": "unknown",
            "mvrv_last_success_at": None,
        }
    return {
        "mvrv_source": payload.get("btc_mvrv_source"),
        "mvrv_source_confidence": payload.get("btc_mvrv_source_confidence", "unknown"),
        "mvrv_last_success_at": payload.get("btc_mvrv_last_success_at"),
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _public_baseline_reference() -> dict[str, Any] | None:
    path = _repo_root() / "results" / "paulwei_research" / "variant_comparison_review.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload.get("variant_window_summary", {}).get("paulwei_public_archive_baseline")


def _safe_metric(metrics: dict[str, Any] | None, key: str) -> float:
    if not isinstance(metrics, dict):
        return 0.0
    return float(metrics.get(key, 0.0) or 0.0)


def _admission_contract(source_name: str) -> dict[str, Any]:
    review = load_source_admission_review(source_name)
    if not review:
        return {
            "review_present": False,
            "tier_decision": None,
            "time_granularity": None,
            "research_ready": False,
            "use_for_research_only": False,
            "exclude_from_promote_truth": True,
            "allowed_in_training_observation": False,
            "allowed_in_candidate_explanation": False,
            "direction_vote_allowed": False,
            "intent_aggregation_allowed": False,
            "position_sizing_allowed": False,
            "strategy_wiring_allowed": False,
            "risk_overlay_wiring_allowed": False,
            "feature_freeze_candidate": False,
            "blocked_by": "admission_review_missing",
        }
    return {
        "review_present": True,
        "tier_decision": review.get("tier_decision"),
        "time_granularity": review.get("time_granularity"),
        "research_ready": bool(review.get("research_ready")),
        "use_for_research_only": bool(review.get("use_for_research_only")),
        "exclude_from_promote_truth": bool(review.get("exclude_from_promote_truth")),
        "allowed_in_training_observation": bool(review.get("allowed_in_training_observation")),
        "allowed_in_candidate_explanation": bool(review.get("allowed_in_candidate_explanation")),
        "direction_vote_allowed": bool(review.get("direction_vote_allowed")),
        "intent_aggregation_allowed": bool(review.get("intent_aggregation_allowed")),
        "position_sizing_allowed": bool(review.get("position_sizing_allowed")),
        "strategy_wiring_allowed": bool(review.get("strategy_wiring_allowed")),
        "risk_overlay_wiring_allowed": bool(review.get("risk_overlay_wiring_allowed")),
        "feature_freeze_candidate": bool(review.get("feature_freeze_candidate")),
        "blocked_by": review.get("blocked_by"),
    }


def _independence_contract(source_name: str) -> dict[str, Any]:
    review = load_source_independence_audit(source_name)
    if not review:
        return {
            "review_present": False,
            "allowed_in_state_features": False,
            "allowed_in_pressure_context": False,
            "allowed_in_candidate_explanation": False,
            "direction_vote_allowed": False,
            "intent_aggregation_allowed": False,
            "risk_overlay_wiring_allowed": False,
            "use_for_research_only": False,
            "blocked_by": "independence_review_missing",
            "max_abs_correlation": None,
        }
    return {
        "review_present": True,
        "allowed_in_state_features": bool(review.get("allowed_in_state_features")),
        "allowed_in_pressure_context": bool(review.get("allowed_in_pressure_context")),
        "allowed_in_candidate_explanation": bool(review.get("allowed_in_candidate_explanation")),
        "direction_vote_allowed": bool(review.get("direction_vote_allowed")),
        "intent_aggregation_allowed": bool(review.get("intent_aggregation_allowed")),
        "risk_overlay_wiring_allowed": bool(review.get("risk_overlay_wiring_allowed")),
        "use_for_research_only": bool(review.get("use_for_research_only")),
        "blocked_by": review.get("blocked_by"),
        "max_abs_correlation": review.get("max_abs_correlation"),
    }


def _active_signal_independence_contract(
    review_dir: Path = Path("results/signal_combination"),
) -> dict[str, Any]:
    path = review_dir / "active_signal_independence.json"
    if not path.exists():
        return {
            "audit_present": False,
            "redundant_pairs": [],
            "role_overlap_pairs": [],
            "direction_layer_clean": False,
            "state_layer_clean": False,
            "blocked_by": "active_signal_independence_missing",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "audit_present": False,
            "redundant_pairs": [],
            "role_overlap_pairs": [],
            "direction_layer_clean": False,
            "state_layer_clean": False,
            "blocked_by": "active_signal_independence_unreadable",
        }
    summary = payload.get("summary", {})
    blocked_by = list(summary.get("blocked_by", []))
    return {
        "audit_present": True,
        "redundant_pairs": summary.get("same_role_redundant_pairs", []),
        "role_overlap_pairs": summary.get("cross_role_overlap_pairs", []),
        "direction_layer_clean": bool(summary.get("direction_layer_clean")),
        "state_layer_clean": bool(summary.get("state_layer_clean")),
        "blocked_by": blocked_by or None,
    }


def _historical_extension_candidate_contract() -> dict[str, Any]:
    review = load_historical_extension_candidate_contract()
    if not review:
        return {
            "present": False,
            "source": None,
            "candidate_name": None,
            "ready": False,
            "layered_train_enabled": False,
            "historical_extension_mode": None,
            "formal_train_truth": False,
            "train_truth_legs": [],
            "dev_truth_source": "futures_only",
            "test_truth_source": "futures_only",
            "mixed_era_declared": False,
            "full_unified_truth": False,
            "blocked_by": ["historical_extension_candidate_missing"],
            "spot_train_leg_blocked_by": ["historical_extension_candidate_missing"],
            "allowed_uses": [],
            "candidate_window_start": None,
            "candidate_window_end": None,
            "source_boundary": None,
        }
    blocked_by = review.get("blocked_by") or []
    return {
        "present": True,
        "source": review.get("candidate_source_type"),
        "candidate_name": review.get("candidate_name"),
        "ready": bool(review.get("ready")),
        "layered_train_enabled": bool(review.get("layered_train_enabled")),
        "historical_extension_mode": review.get("historical_extension_mode"),
        "formal_train_truth": bool(review.get("formal_train_truth")),
        "train_truth_legs": list(review.get("train_truth_legs") or []),
        "dev_truth_source": review.get("dev_truth_source", "futures_only"),
        "test_truth_source": review.get("test_truth_source", "futures_only"),
        "mixed_era_declared": bool(review.get("mixed_era_declared")),
        "full_unified_truth": bool(review.get("full_unified_truth")),
        "blocked_by": blocked_by,
        "spot_train_leg_blocked_by": list(review.get("spot_train_leg_blocked_by") or blocked_by),
        "allowed_uses": list(review.get("allowed_uses") or []),
        "candidate_window_start": review.get("candidate_window_start"),
        "candidate_window_end": review.get("candidate_window_end"),
        "source_boundary": review.get("source_boundary"),
    }


def _dedupe_seed_trials(seed_trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for trial in seed_trials:
        key = json.dumps(trial, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(trial))
    return deduped


def _project_seed_trials_to_search_grid(
    seed_trials: list[dict[str, Any]],
    *,
    search_grid: dict[str, list[float]],
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for trial in seed_trials:
        normalized: dict[str, Any] = {}
        for key, value in dict(trial).items():
            choices = search_grid.get(key)
            if not choices:
                continue
            target = float(value)
            normalized[key] = min((float(item) for item in choices), key=lambda item: abs(item - target))
        if normalized:
            projected.append(normalized)
    return _dedupe_seed_trials(projected)


def _window_has_timeframe_rows(
    root_dir: Path,
    symbol: str,
    timeframe: Timeframe,
    *,
    start_ns: int | None,
    end_ns: int | None,
) -> bool:
    rows = read_parquet_rows(
        str(root_dir / "klines" / symbol / timeframe.value / "*.parquet"),
        where_sql=("close_time_ns >= ? AND close_time_ns < ?" if start_ns is not None and end_ns is not None else ""),
        params=((start_ns, end_ns) if start_ns is not None and end_ns is not None else ()),
        order_by="close_time_ns ASC",
    )
    return bool(rows)


def _iso_to_ns(value: str | None) -> int | None:
    if not value:
        return None
    return int(datetime.fromisoformat(value).timestamp() * 1_000_000_000)


def _ns_from_utc(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1_000_000_000)


PAULWEI_SPOT_AUXILIARY_TRAIN_OBJECTIVES = (
    "regime_context",
    "direction_selection",
    "profit_run",
    "low_switch_trend_hold",
)


PAULWEI_TEMPORAL_WINDOW_TARGETS: dict[str, dict[str, Any]] = {
    "train_temporal_2020": {
        "source": "paulwei_public_btc_2020_behavior",
        "role": "bull_expansion_profit_run",
        "min_equity_multiple": 1.02,
        "min_sharpe": 0.0,
        "max_drawdown": 0.20,
        "primary_blocker": "paulwei_2020_profit_run_fail_fast",
        "reference": {
            "btc_episode_long_share": 0.676,
            "btc_position_switches_per_30d": 8.449,
            "median_hold_hours": 10.14,
            "median_add_fills_per_episode": 14.0,
            "median_reduce_fills_per_episode": 10.0,
        },
    },
    "train_temporal_2022": {
        "source": "paulwei_public_btc_2022_behavior",
        "role": "bear_market_cash_flow_adjusted_survival",
        "min_equity_multiple": 1.00,
        "min_sharpe": -0.25,
        "max_drawdown": 0.20,
        "primary_blocker": "paulwei_2022_bear_survival_fail_fast",
        "reference": {
            "btc_episode_long_share": 0.61,
            "btc_position_switches_per_30d": 5.671,
            "median_hold_hours": 15.20,
            "loss_median_hold_hours": 28.62,
            "cash_flow_adjusted_btc_multiple": 1.0353,
            "cash_flow_adjusted_btc_max_drawdown": 0.1911,
        },
    },
}


def _spot_context_windows(contract: dict[str, Any]) -> list[WindowSpec]:
    start_ns = _iso_to_ns(contract.get("candidate_window_start"))
    end_ns = min(
        _iso_to_ns(contract.get("candidate_window_end")) or _ns_from_utc(2020, 1, 1),
        _ns_from_utc(2020, 1, 1),
    )
    if start_ns is None or end_ns <= start_ns:
        return []

    def _bounded(label: str, start: int, end: int) -> WindowSpec | None:
        bounded_start = max(start_ns, start)
        bounded_end = min(end_ns, end)
        if bounded_end <= bounded_start:
            return None
        return WindowSpec(label=label, start_ns=bounded_start, end_ns=bounded_end)

    windows = [
        WindowSpec(label="spot_context_full_2017_2019", start_ns=start_ns, end_ns=end_ns),
        _bounded("spot_context_bull_expansion_2017", start_ns, _ns_from_utc(2018, 1, 1)),
        _bounded("spot_context_bear_crash_2018", _ns_from_utc(2018, 1, 1), _ns_from_utc(2019, 1, 1)),
        _bounded("spot_context_recovery_2019", _ns_from_utc(2019, 1, 1), _ns_from_utc(2020, 1, 1)),
    ]
    return [window for window in windows if window is not None]


def _spot_context_allowed(contract: dict[str, Any]) -> bool:
    allowed_uses = set(contract.get("allowed_uses") or [])
    return (
        bool(contract.get("ready"))
        and contract.get("source") == "spot"
        and contract.get("historical_extension_mode") == "spot_context_only"
        and ("train_side_regime_context_objective" in allowed_uses or "pre_2020_regime_context" in allowed_uses)
    )


def _spot_context_snapshot_payload(snapshot: BacktestSnapshot, *, starting_equity: float) -> dict[str, Any]:
    return {
        "sharpe": round(float(snapshot.sharpe), 6),
        "net_pnl": round(float(snapshot.net_pnl), 6),
        "equity_multiple": round((starting_equity + float(snapshot.net_pnl)) / starting_equity, 6)
        if starting_equity > 0
        else 0.0,
        "max_drawdown": round(float(snapshot.max_drawdown), 6),
        "win_rate": round(float(snapshot.win_rate), 6),
        "profit_factor": round(float(snapshot.profit_factor), 6),
        "fee_drag_pct": round(float(snapshot.fee_drag_pct), 6),
    }


def _spot_context_score(windows: dict[str, dict[str, Any]]) -> float:
    full = windows.get("spot_context_full_2017_2019") or {}
    bull = windows.get("spot_context_bull_expansion_2017") or {}
    bear = windows.get("spot_context_bear_crash_2018") or {}
    recovery = windows.get("spot_context_recovery_2019") or {}

    full_multiple = float(full.get("equity_multiple", 1.0) or 1.0)
    bull_multiple = float(bull.get("equity_multiple", 1.0) or 1.0)
    recovery_multiple = float(recovery.get("equity_multiple", 1.0) or 1.0)
    bear_drawdown = float(bear.get("max_drawdown", 0.0) or 0.0)
    full_drawdown = float(full.get("max_drawdown", 0.0) or 0.0)
    full_sharpe = float(full.get("sharpe", 0.0) or 0.0)
    bear_sharpe = float(bear.get("sharpe", 0.0) or 0.0)
    bull_fee_drag_pct = float(bull.get("fee_drag_pct", 0.0) or 0.0)
    bear_fee_drag_pct = float(bear.get("fee_drag_pct", 0.0) or 0.0)

    expansion_bonus = min(0.85, math.log(max(1.0, full_multiple), 2.0) * 0.30)
    bull_bonus = min(0.55, math.log(max(1.0, bull_multiple), 2.0) * 0.32)
    recovery_bonus = min(0.35, math.log(max(1.0, recovery_multiple), 2.0) * 0.20)
    bear_survival_bonus = min(0.25, max(0.0, 0.35 - bear_drawdown) * 0.80)
    trend_quality_bonus = min(0.25, max(0.0, full_sharpe) * 0.05)

    full_loss_penalty = min(1.25, max(0.0, 1.0 - full_multiple) * 1.25)
    bull_undercapture_penalty = min(1.00, max(0.0, 1.20 - bull_multiple) * 4.0)
    full_profit_run_penalty = min(0.55, max(0.0, 1.25 - full_multiple) * 1.10)
    bull_fee_drag_penalty = min(0.35, max(0.0, bull_fee_drag_pct - 0.018) * 6.0)
    drawdown_penalty = min(0.85, max(0.0, full_drawdown - 0.35) * 1.80)
    bear_collapse_penalty = min(0.60, max(0.0, bear_drawdown - 0.45) * 1.20)
    bear_fee_drag_penalty = min(0.35, max(0.0, bear_fee_drag_pct - 0.05) * 4.0)
    negative_bear_penalty = min(0.35, max(0.0, -bear_sharpe) * 0.05)

    return round(
        expansion_bonus
        + bull_bonus
        + recovery_bonus
        + bear_survival_bonus
        + trend_quality_bonus
        - full_loss_penalty
        - bull_undercapture_penalty
        - full_profit_run_penalty
        - bull_fee_drag_penalty
        - drawdown_penalty
        - bear_collapse_penalty
        - bear_fee_drag_penalty
        - negative_bear_penalty,
        6,
    )


def _spot_context_replay_audit(
    evaluator: FrameworkEvaluator,
    params: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any] | None:
    if not (
        evaluator.search_space_profile.get("paulwei_dual_phase_search")
        or evaluator.search_space_profile.get("paulwei_balanced_tranche_search")
    ):
        return None
    if not _spot_context_allowed(contract):
        return None
    windows = _spot_context_windows(contract)
    if not windows:
        return None

    spot_root_dir = evaluator._base_config.root_dir.parent / "binance_spot"
    full_window = windows[0]
    if not _window_has_timeframe_rows(
        spot_root_dir,
        evaluator._base_config.symbol,
        Timeframe.TF_1H,
        start_ns=full_window.start_ns,
        end_ns=full_window.end_ns,
    ):
        return {
            "scope": "formal_auxiliary_train_regime_direction_profit_run",
            "formal_train_truth": False,
            "formal_auxiliary_train_leg": True,
            "execution_truth": False,
            "promotion_allowed": False,
            "test_evaluated": False,
            "blocked_by": "spot_context_1h_missing",
            "windows": {},
            "context_score_adjustment": 0.0,
        }

    variant = evaluator.candidate_variant(params, name="spot_context_candidate", label="Spot Context Candidate")
    strategy_config = replace(
        variant.strategy_config or ARCStrategyConfig(),
        execution_timeframe=Timeframe.TF_1H,
    )
    variant = replace(
        variant,
        name="spot_context_candidate",
        label="Spot-Era Context Candidate",
        strategy_config=strategy_config,
        liquidation_data_enabled=False,
        state_liquidation_overlay_enabled=False,
    )
    spot_config = HistoricalBacktestConfig(
        root_dir=spot_root_dir,
        symbol=evaluator._base_config.symbol,
        portfolio_symbol=evaluator._base_config.portfolio_symbol,
        starting_equity=evaluator._base_config.starting_equity,
        execution_timeframe=Timeframe.TF_1H,
        bias_timeframe=Timeframe.TF_1D,
        direction_timeframe=Timeframe.TF_4H,
        context_timeframe=Timeframe.TF_1H,
        macro_dir=evaluator._base_config.macro_dir,
        options_dir=evaluator._base_config.options_dir,
        cot_path=evaluator._base_config.cot_path,
        liquidation_dir=evaluator._base_config.liquidation_dir,
        profile_path=evaluator._base_config.profile_path,
        research_compute_mode=evaluator._base_config.research_compute_mode,
        strategy_config=variant.strategy_config,
        risk_config=variant.risk_config,
        execution_config=variant.execution_config,
        signal_weight_overrides=variant.signal_weight_overrides,
        plugin_config_overrides=variant.plugin_config_overrides,
        indicator_config=variant.indicator_config,
        liquidation_data_enabled=False,
        state_liquidation_overlay_enabled=False,
    )
    window_payloads: dict[str, dict[str, Any]] = {}
    for window in windows:
        snapshot = _run_variant_backtest_snapshot(
            spot_config,
            variant,
            start_ns=window.start_ns,
            end_ns=window.end_ns,
        )
        window_payloads[window.label] = _spot_context_snapshot_payload(
            snapshot,
            starting_equity=evaluator._base_config.starting_equity,
        )
    return {
        "scope": "formal_auxiliary_train_regime_direction_profit_run",
        "formal_train_truth": False,
        "formal_auxiliary_train_leg": True,
        "execution_truth": False,
        "promotion_allowed": False,
        "test_evaluated": False,
        "source": "binance_spot",
        "context_only": True,
        "formal_execution_truth_source": "post_2020_futures_train",
        "execution_timeframe": Timeframe.TF_1H.value,
        "allowed_training_objectives": list(PAULWEI_SPOT_AUXILIARY_TRAIN_OBJECTIVES),
        "objective": "spot_era_regime_direction_profit_run_auxiliary_train_only",
        "windows": window_payloads,
        "context_score_adjustment": _spot_context_score(window_payloads),
    }


def _spot_train_leg(contract: dict[str, Any]) -> dict[str, Any] | None:
    for leg in list(contract.get("train_truth_legs") or []):
        if leg.get("name") == "pre_2020_spot_train":
            return dict(leg)
    return None


def _spot_train_leg_snapshot(
    evaluator: FrameworkEvaluator,
    params: dict[str, Any],
    contract: dict[str, Any],
) -> BacktestSnapshot | None:
    leg = _spot_train_leg(contract)
    if not leg or not contract.get("layered_train_enabled"):
        return None
    if contract.get("spot_train_leg_blocked_by"):
        blocking = {
            "spot_timeframe_gap",
            "futures_1h_missing",
            "futures_1h_incomplete_current_truth",
        }
        if any(reason in blocking for reason in list(contract.get("spot_train_leg_blocked_by") or [])):
            return None
    variant = evaluator.candidate_variant(params, name="spot_train_candidate", label="Spot Train Candidate")
    spot_root_dir = evaluator._base_config.root_dir.parent / "binance_spot"
    spot_config = HistoricalBacktestConfig(
        root_dir=spot_root_dir,
        symbol=evaluator._base_config.symbol,
        portfolio_symbol=evaluator._base_config.portfolio_symbol,
        starting_equity=evaluator._base_config.starting_equity,
        execution_timeframe=evaluator._base_config.execution_timeframe,
        bias_timeframe=evaluator._base_config.bias_timeframe,
        direction_timeframe=evaluator._base_config.direction_timeframe,
        context_timeframe=evaluator._base_config.context_timeframe,
        macro_dir=evaluator._base_config.macro_dir,
        options_dir=evaluator._base_config.options_dir,
        cot_path=evaluator._base_config.cot_path,
        liquidation_dir=evaluator._base_config.liquidation_dir,
        profile_path=evaluator._base_config.profile_path,
        research_compute_mode=evaluator._base_config.research_compute_mode,
        strategy_config=variant.strategy_config,
        risk_config=variant.risk_config,
        execution_config=variant.execution_config,
        signal_weight_overrides=variant.signal_weight_overrides,
        plugin_config_overrides=variant.plugin_config_overrides,
        indicator_config=variant.indicator_config,
        liquidation_data_enabled=False,
        state_liquidation_overlay_enabled=False,
    )
    return _run_variant_backtest_snapshot(
        spot_config,
        variant,
        start_ns=_iso_to_ns(leg.get("start_at")),
        end_ns=_iso_to_ns(leg.get("end_at")),
    )


def _combine_train_leg_metrics(
    futures_metrics: dict[str, Any],
    spot_context_audit: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, float]:
    futures_sharpe = float(futures_metrics.get("sharpe", 0.0) or 0.0)
    futures_score = float(futures_metrics.get("score", futures_sharpe) or futures_sharpe)
    if spot_context_audit is None:
        return dict(futures_metrics), None, futures_score
    windows = dict(spot_context_audit.get("windows") or {})
    full_context = dict(windows.get("spot_context_full_2017_2019") or {})
    context_adjustment = float(spot_context_audit.get("context_score_adjustment", 0.0) or 0.0)
    combined_score = round(futures_score + context_adjustment, 6)
    combined_metrics = dict(futures_metrics)
    combined_metrics["score"] = combined_score
    combined_metrics["combined_train_score"] = combined_score
    combined_metrics["spot_context_score_adjustment"] = context_adjustment
    combined_metrics["spot_context_formal_auxiliary_train_leg"] = bool(
        spot_context_audit.get("formal_auxiliary_train_leg")
    )
    combined_metrics["spot_context_equity_multiple"] = float(full_context.get("equity_multiple", 1.0) or 1.0)
    combined_metrics["spot_context_sharpe"] = float(full_context.get("sharpe", 0.0) or 0.0)
    combined_metrics["spot_context_drawdown"] = float(full_context.get("max_drawdown", 0.0) or 0.0)
    return combined_metrics, spot_context_audit, combined_score


def _temporal_robustness_enabled(evaluator: FrameworkEvaluator) -> bool:
    profile = evaluator.search_space_profile
    return any(
        bool(profile.get(key, False))
        for key in (
            "paulwei_dual_phase_spot_context_relaxed_fee_search",
            "paulwei_dual_phase_spot_context_search",
            "paulwei_dual_phase_fee_repair_search",
            "paulwei_dual_phase_search",
            "paulwei_balanced_tranche_search",
        )
    )


def _train_temporal_robustness_windows(split_spec: DatasetSplitSpec) -> list[WindowSpec]:
    train_end = split_spec.train.end_ns
    if train_end is None:
        return []
    windows: list[WindowSpec] = []
    for year in range(2020, 2024):
        start_ns = max(split_spec.train.start_ns, _ns_from_utc(year, 1, 1))
        end_ns = min(train_end, _ns_from_utc(year + 1, 1, 1))
        if end_ns > start_ns:
            windows.append(WindowSpec(label=f"train_temporal_{year}", start_ns=start_ns, end_ns=end_ns))
    return windows


def _late_dev_regime_window(split_spec: DatasetSplitSpec) -> WindowSpec | None:
    if split_spec.dev.end_ns is None:
        return None
    start_ns = max(split_spec.dev.start_ns, _shift_months(split_spec.dev.end_ns, -6))
    if split_spec.dev.end_ns <= start_ns:
        return None
    start_dt = datetime.fromtimestamp(start_ns / 1e9, tz=UTC)
    return WindowSpec(
        label=f"late_dev_regime_{start_dt.year}_{start_dt.month:02d}",
        start_ns=start_ns,
        end_ns=split_spec.dev.end_ns,
    )


def _paulwei_temporal_target_audit(train_payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    windows: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    penalty = 0.0
    for label, target in PAULWEI_TEMPORAL_WINDOW_TARGETS.items():
        payload = dict(train_payloads.get(label) or {})
        if not payload:
            continue
        equity_multiple = float(payload.get("equity_multiple", 1.0) or 1.0)
        sharpe = float(payload.get("sharpe", 0.0) or 0.0)
        max_drawdown = float(payload.get("max_drawdown", 0.0) or 0.0)
        min_equity_multiple = float(target["min_equity_multiple"])
        min_sharpe = float(target["min_sharpe"])
        max_allowed_drawdown = float(target["max_drawdown"])
        window_blockers: list[str] = []
        if equity_multiple < min_equity_multiple or sharpe < min_sharpe:
            window_blockers.append(str(target["primary_blocker"]))
            penalty += 0.65 * max(0.0, min_equity_multiple - equity_multiple) / max(0.01, min_equity_multiple - 1.0)
            penalty += 0.35 * max(0.0, min_sharpe - sharpe)
        if max_drawdown > max_allowed_drawdown:
            window_blockers.append("paulwei_temporal_drawdown_budget_fail_fast")
            penalty += 0.60 * (max_drawdown - max_allowed_drawdown) / max_allowed_drawdown
        blockers.extend(item for item in window_blockers if item not in blockers)
        windows[label] = {
            "source": target["source"],
            "role": target["role"],
            "reference": dict(target["reference"]),
            "target": {
                "min_equity_multiple": min_equity_multiple,
                "min_sharpe": min_sharpe,
                "max_drawdown": max_allowed_drawdown,
            },
            "metrics": payload,
            "blocked_by": window_blockers,
        }
    return {
        "scope": "paulwei_2020_2022_temporal_behavior_target",
        "source": "paulwei_public_archive_btc_behavior",
        "formal_train_truth": False,
        "promotion_allowed": False,
        "test_evaluated": False,
        "windows": windows,
        "blocked_by": blockers,
        "score_penalty": round(min(1.5, penalty), 6),
        "primary_blocker": blockers[0] if blockers else None,
    }


def _temporal_robustness_audit(
    evaluator: FrameworkEvaluator,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    if not _temporal_robustness_enabled(evaluator):
        return None
    train_windows = _train_temporal_robustness_windows(evaluator._split_spec)
    late_dev_window = _late_dev_regime_window(evaluator._split_spec)
    if not train_windows and late_dev_window is None:
        return None

    variant = evaluator.candidate_variant(
        params,
        name="temporal_robustness_candidate",
        label="Temporal Robustness Candidate",
    )
    audit_config = replace(
        evaluator._base_config,
        research_compute_mode="lightweight_train_search",
        profiling_enabled=False,
        early_stop_enabled=False,
        trial_runtime_budget_s=None,
    )

    train_payloads: dict[str, dict[str, Any]] = {}
    train_sharpes: list[float] = []
    for window in train_windows:
        snapshot = _run_variant_backtest_snapshot(
            audit_config,
            variant,
            start_ns=window.start_ns,
            end_ns=window.end_ns,
        )
        train_payloads[window.label] = _spot_context_snapshot_payload(
            snapshot,
            starting_equity=evaluator._base_config.starting_equity,
        )
        train_sharpes.append(float(snapshot.sharpe))

    late_dev_payload = None
    late_dev_sharpe = 0.0
    if late_dev_window is not None:
        late_dev_snapshot = _run_variant_backtest_snapshot(
            audit_config,
            variant,
            start_ns=late_dev_window.start_ns,
            end_ns=late_dev_window.end_ns,
        )
        late_dev_payload = {
            "label": late_dev_window.label,
            **_spot_context_snapshot_payload(
                late_dev_snapshot,
                starting_equity=evaluator._base_config.starting_equity,
            ),
        }
        late_dev_sharpe = float(late_dev_snapshot.sharpe)

    avg_train_sharpe = sum(train_sharpes) / len(train_sharpes) if train_sharpes else 0.0
    min_train_sharpe = min(train_sharpes) if train_sharpes else 0.0
    variance = (
        sum((value - avg_train_sharpe) ** 2 for value in train_sharpes) / len(train_sharpes) if train_sharpes else 0.0
    )
    train_sharpe_std = math.sqrt(max(0.0, variance))
    negative_train_windows = sum(1 for value in train_sharpes if value < 0.0)
    paulwei_target_audit = _paulwei_temporal_target_audit(train_payloads)

    blockers: list[str] = []
    blockers.extend(paulwei_target_audit["blocked_by"])
    if negative_train_windows > 1:
        blockers.append("temporal_train_negative_window_count")
    if min_train_sharpe < -0.50:
        blockers.append("temporal_train_min_sharpe_fail_fast")
    if late_dev_payload is not None and late_dev_sharpe < -0.25:
        blockers.append("late_dev_regime_fail_fast")
    blockers = list(dict.fromkeys(blockers))

    consistency_penalty = (
        float(paulwei_target_audit["score_penalty"])
        + 0.20 * train_sharpe_std
        + 0.45 * negative_train_windows
        + 0.25 * max(0.0, -min_train_sharpe)
        + 0.35 * max(0.0, -0.25 - late_dev_sharpe if late_dev_payload is not None else 0.0)
    )
    score_adjustment = -round(min(2.5, consistency_penalty), 6)
    return {
        "scope": "train_dev_only_temporal_robustness",
        "formal_train_truth": False,
        "promotion_allowed": False,
        "test_evaluated": False,
        "train_windows": train_payloads,
        "late_dev_window": late_dev_payload,
        "train_avg_sharpe": round(avg_train_sharpe, 6),
        "train_min_sharpe": round(min_train_sharpe, 6),
        "train_sharpe_std": round(train_sharpe_std, 6),
        "train_negative_window_count": negative_train_windows,
        "late_dev_sharpe": round(late_dev_sharpe, 6),
        "paulwei_temporal_target_audit": paulwei_target_audit,
        "score_adjustment": score_adjustment,
        "blocked_by": blockers,
        "primary_blocker": blockers[0] if blockers else None,
    }


def _bull_window_direction_exposure_attribution(
    evaluator: FrameworkEvaluator,
    params: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any] | None:
    if not _temporal_robustness_enabled(evaluator):
        return None

    variant = evaluator.candidate_variant(
        params,
        name="bull_window_direction_exposure_candidate",
        label="Bull Window Direction Exposure Candidate",
    )
    audit_config = replace(
        evaluator._base_config,
        research_compute_mode="lightweight_train_search",
        diagnostic_collect_ledgers=True,
        profiling_enabled=False,
        early_stop_enabled=False,
        trial_runtime_budget_s=None,
    )
    windows: dict[str, Any] = {}
    train_2020 = next(
        (
            window
            for window in _train_temporal_robustness_windows(evaluator._split_spec)
            if window.label == "train_temporal_2020"
        ),
        None,
    )
    if train_2020 is not None:
        runner, result = _run_variant_backtest(
            audit_config,
            variant,
            start_ns=train_2020.start_ns,
            end_ns=train_2020.end_ns,
        )
        windows[train_2020.label] = build_bull_window_direction_exposure_attribution(
            runner,
            result,
            variant=variant.name,
            window_label=train_2020.label,
            source="post_2020_futures_train",
            role="bull_expansion_profit_run",
            formal_train_truth=True,
            execution_truth=True,
            starting_equity=evaluator._base_config.starting_equity,
        )

    train_2022 = next(
        (
            window
            for window in _train_temporal_robustness_windows(evaluator._split_spec)
            if window.label == "train_temporal_2022"
        ),
        None,
    )
    if train_2022 is not None:
        runner, result = _run_variant_backtest(
            audit_config,
            variant,
            start_ns=train_2022.start_ns,
            end_ns=train_2022.end_ns,
        )
        windows[train_2022.label] = build_bear_window_direction_exposure_attribution(
            runner,
            result,
            variant=variant.name,
            window_label=train_2022.label,
            source="post_2020_futures_train",
            role="bear_survival_direction_exposure",
            formal_train_truth=True,
            execution_truth=True,
            starting_equity=evaluator._base_config.starting_equity,
        )

    if _spot_context_allowed(contract):
        spot_window = next(
            (
                window
                for window in _spot_context_windows(contract)
                if window.label == "spot_context_bull_expansion_2017"
            ),
            None,
        )
        spot_root_dir = evaluator._base_config.root_dir.parent / "binance_spot"
        if spot_window is not None and _window_has_timeframe_rows(
            spot_root_dir,
            evaluator._base_config.symbol,
            Timeframe.TF_1H,
            start_ns=spot_window.start_ns,
            end_ns=spot_window.end_ns,
        ):
            spot_variant = evaluator.candidate_variant(
                params,
                name="spot_bull_direction_exposure_candidate",
                label="Spot Bull Direction Exposure Candidate",
            )
            spot_strategy_config = replace(
                spot_variant.strategy_config or ARCStrategyConfig(),
                execution_timeframe=Timeframe.TF_1H,
            )
            spot_variant = replace(
                spot_variant,
                name="spot_bull_direction_exposure_candidate",
                label="Spot Bull Direction Exposure Candidate",
                strategy_config=spot_strategy_config,
                liquidation_data_enabled=False,
                state_liquidation_overlay_enabled=False,
            )
            spot_config = HistoricalBacktestConfig(
                root_dir=spot_root_dir,
                symbol=evaluator._base_config.symbol,
                portfolio_symbol=evaluator._base_config.portfolio_symbol,
                starting_equity=evaluator._base_config.starting_equity,
                execution_timeframe=Timeframe.TF_1H,
                bias_timeframe=Timeframe.TF_1D,
                direction_timeframe=Timeframe.TF_4H,
                context_timeframe=Timeframe.TF_1H,
                macro_dir=evaluator._base_config.macro_dir,
                options_dir=evaluator._base_config.options_dir,
                cot_path=evaluator._base_config.cot_path,
                liquidation_dir=evaluator._base_config.liquidation_dir,
                profile_path=evaluator._base_config.profile_path,
                research_compute_mode="lightweight_train_search",
                diagnostic_collect_ledgers=True,
                profiling_enabled=False,
                early_stop_enabled=False,
                trial_runtime_budget_s=None,
                strategy_config=spot_variant.strategy_config,
                risk_config=spot_variant.risk_config,
                execution_config=spot_variant.execution_config,
                signal_weight_overrides=spot_variant.signal_weight_overrides,
                plugin_config_overrides=spot_variant.plugin_config_overrides,
                indicator_config=spot_variant.indicator_config,
                liquidation_data_enabled=False,
                state_liquidation_overlay_enabled=False,
            )
            spot_runner, spot_result = _run_variant_backtest(
                spot_config,
                spot_variant,
                start_ns=spot_window.start_ns,
                end_ns=spot_window.end_ns,
            )
            windows[spot_window.label] = build_bull_window_direction_exposure_attribution(
                spot_runner,
                spot_result,
                variant=spot_variant.name,
                window_label=spot_window.label,
                source="binance_spot",
                role="spot_era_bull_expansion_direction_profit_run_auxiliary",
                formal_train_truth=False,
                formal_auxiliary_train_leg=True,
                execution_truth=False,
                starting_equity=evaluator._base_config.starting_equity,
            )

    if not windows:
        return None
    primary_modes = [
        (payload.get("diagnosis") or {}).get("primary_failure_mode")
        for payload in windows.values()
        if (payload.get("diagnosis") or {}).get("primary_failure_mode")
    ]
    bear_modes = [
        (payload.get("diagnosis") or {}).get("primary_failure_mode")
        for key, payload in windows.items()
        if "2022" in key and (payload.get("diagnosis") or {}).get("primary_failure_mode")
    ]
    return {
        "scope": "train_dev_only_direction_exposure_attribution",
        "legacy_scope": "train_dev_only_bull_window_direction_exposure_attribution",
        "formal_train_truth": False,
        "promotion_allowed": False,
        "test_evaluated": False,
        "windows": windows,
        "primary_failure_modes": list(dict.fromkeys(str(item) for item in primary_modes if item)),
        "bear_primary_failure_modes": list(dict.fromkeys(str(item) for item in bear_modes if item)),
    }


def _run_variant_backtest_snapshot(
    base_config: HistoricalBacktestConfig,
    variant: VariantSpec,
    *,
    start_ns: int | None,
    end_ns: int | None,
) -> BacktestSnapshot:
    _, result = _run_variant_backtest(base_config, variant, start_ns=start_ns, end_ns=end_ns)
    return BacktestSnapshot(
        sharpe=result.metrics.sharpe,
        net_pnl=result.metrics.net_pnl,
        max_drawdown=result.metrics.max_drawdown,
        win_rate=result.metrics.win_rate,
        sortino=getattr(result.metrics, "sortino", 0.0),
        profit_factor=getattr(result.metrics, "profit_factor", 0.0),
        turnover_per_day=getattr(result.metrics, "turnover_per_day", 0.0),
        fee_drag_pct=getattr(result.metrics, "fee_drag_pct", 0.0),
    )


def _shutdown_process_pool_quickly(pool: ProcessPoolExecutor | None, futures: list[Any] | None = None) -> None:
    if futures:
        for future in futures:
            try:
                future.cancel()
            except Exception:
                pass
    if pool is None:
        return
    try:
        processes = list((getattr(pool, "_processes", None) or {}).values())
    except Exception:
        processes = []
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    for process in processes:
        try:
            if process.is_alive():
                process.terminate()
        except Exception:
            pass


def _run_variant_backtest_with_timeout(
    base_config: HistoricalBacktestConfig,
    variant: VariantSpec,
    *,
    start_ns: int | None,
    end_ns: int | None,
    timeout_s: float,
    pool: ProcessPoolExecutor | None = None,
    mp_start_method: str | None = None,
) -> BacktestSnapshot:
    if pool is not None:
        future = pool.submit(
            _run_variant_backtest_snapshot,
            base_config,
            variant,
            start_ns=start_ns,
            end_ns=end_ns,
        )
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"variant_backtest_timeout:{timeout_s:.0f}s") from exc
    executor_kwargs: dict[str, Any] = {"max_workers": 1}
    if mp_start_method:
        executor_kwargs["mp_context"] = mp.get_context(mp_start_method)
    local_pool = ProcessPoolExecutor(**executor_kwargs)
    future = local_pool.submit(
        _run_variant_backtest_snapshot,
        base_config,
        variant,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    shutdown_wait = True
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeoutError as exc:
        shutdown_wait = False
        _shutdown_process_pool_quickly(local_pool, [future])
        raise TimeoutError(f"variant_backtest_timeout:{timeout_s:.0f}s") from exc
    finally:
        if shutdown_wait:
            local_pool.shutdown(wait=True)


@dataclass(slots=True, frozen=True)
class CandidateSummary:
    variant: str
    robust_score: float
    selection_split: str
    dev_sharpe: float
    dev_pnl: float
    dev_drawdown: float
    avg_dev_sharpe: float
    avg_dev_ratio: float
    worst_dev_sharpe: float
    weak_window_sharpe: float
    negative_dev_worst_windows: int
    promotion_candidate: bool
    test_sharpe: float | None = None
    test_drawdown: float | None = None
    test_adversarial_failures: int | None = None
    test_evaluated: bool = False
    blocked_by: str | None = None
    failed_gate_metrics: dict[str, bool] | None = None
    promotion_failure_reason: str | None = None
    sortino: float = 0.0
    profit_factor: float = 0.0
    turnover_per_day: float = 0.0
    fee_drag_pct: float = 0.0
    baseline_gate_pass_current: bool = False
    baseline_gate_pass_public: bool = False
    gap_to_current: dict[str, float] | None = None
    gap_to_paulwei_public: dict[str, float] | None = None
    candidate_score: float = 0.0
    weakest_window_penalty: float = 0.0
    hostile_fragility_penalty: float = 0.0


@dataclass(slots=True, frozen=True)
class BacktestSnapshot:
    sharpe: float
    net_pnl: float
    max_drawdown: float
    win_rate: float
    sortino: float = 0.0
    profit_factor: float = 0.0
    turnover_per_day: float = 0.0
    fee_drag_pct: float = 0.0


@dataclass(slots=True)
class SearchReport:
    created_at: str
    symbol: str
    readiness: dict[str, Any]
    deribit_status: dict[str, Any]
    split_contract: dict[str, Any]
    training_window_coverage: dict[str, Any]
    training_input_contract: dict[str, Any]
    search_status: dict[str, Any]
    current: CandidateSummary
    paulwei_baseline: CandidateSummary
    optuna_best: CandidateSummary | None
    rl_best: CandidateSummary | None
    best_variant: str
    best_score: float
    best_params: dict[str, Any] | None
    comparison_payload: dict[str, Any]
    test_comparison_payload: dict[str, Any] | None = None
    test_evaluated: bool = False
    report_mode: str = "fast"
    report_degraded_due_to_memory: bool = False
    smoke_completed: bool = False
    signal_audit_summary: dict[str, Any] | None = None
    evaluation_role: str = "development"
    test_access_allowed: bool = False
    candidate_ready_for_handoff: bool = False
    candidate_frozen_id: str | None = None
    frozen_params: dict[str, Any] | None = None
    handoff_blockers: list[str] | None = None
    dev_gate_summary: dict[str, Any] | None = None
    dev_gate_used_this_round: bool = True
    dev_gate_last_round: int | None = None
    dev_gate_interval: int | None = None
    full_stage2_executed_this_round: bool = True


class ValidationBundleTimeoutError(RuntimeError):
    """Raised when a development validation leg exceeds its replay budget."""

    def __init__(self, *, variant: str, window_label: str, leg: str, timeout_s: float) -> None:
        super().__init__(
            f"validation_timeout: variant={variant} window={window_label} leg={leg} timeout_s={timeout_s:.0f}"
        )
        self.variant = variant
        self.window_label = window_label
        self.leg = leg
        self.timeout_s = timeout_s


class PrepareVariantTimeoutError(RuntimeError):
    """Raised when a lightweight stage2 prepare window exceeds its budget."""

    def __init__(self, *, variant: str, window_label: str, timeout_s: float) -> None:
        super().__init__(
            f"stage2_prepare_variant_timeout: variant={variant} window={window_label} timeout_s={timeout_s:.0f}"
        )
        self.variant = variant
        self.window_label = window_label
        self.timeout_s = timeout_s


class PrepareVariantRuntimeError(RuntimeError):
    """Raised when a lightweight stage2 prepare window fails for a non-timeout runtime reason."""

    def __init__(self, *, variant: str, window_label: str, driver: str, detail: str) -> None:
        super().__init__(
            f"stage2_prepare_variant_runtime_failure: variant={variant} window={window_label} driver={driver} detail={detail}"
        )
        self.variant = variant
        self.window_label = window_label
        self.driver = driver
        self.detail = detail


class ValidationBundleRuntimeError(RuntimeError):
    """Raised when a development validation leg fails for a non-timeout runtime reason."""

    def __init__(self, *, variant: str, window_label: str, leg: str, driver: str, detail: str) -> None:
        super().__init__(
            f"validation_runtime_failure: variant={variant} window={window_label} leg={leg} driver={driver} detail={detail}"
        )
        self.variant = variant
        self.window_label = window_label
        self.leg = leg
        self.driver = driver
        self.detail = detail


def _runtime_failure_driver(exc: Exception) -> str:
    if isinstance(exc, BrokenProcessPool):
        return "validation_pool_broken"
    normalized = str(exc or "").lower()
    if "terminated abruptly" in normalized or "child process" in normalized or "process pool" in normalized:
        return "child_process_exited"
    return "runtime_exception"


def _best_tuned_candidate(report: SearchReport) -> CandidateSummary | None:
    tuned = [candidate for candidate in (report.optuna_best, report.rl_best) if candidate is not None]
    if not tuned:
        return None
    return max(tuned, key=lambda candidate: candidate.robust_score)


def _hostile_regression_summary(report: SearchReport) -> dict[str, Any]:
    tuned = _best_tuned_candidate(report)
    baseline = report.current
    if tuned is None:
        return {
            "dev_primary_improved": False,
            "hostile_window_regressed": False,
            "hostile_regression_driver": None,
        }

    dev_primary_improved = tuned.dev_sharpe > baseline.dev_sharpe
    hostile_window_regressed = (
        tuned.worst_dev_sharpe < baseline.worst_dev_sharpe
        or tuned.negative_dev_worst_windows > baseline.negative_dev_worst_windows
    )
    hostile_regression_driver = None
    if hostile_window_regressed:
        if tuned.weak_window_sharpe < baseline.weak_window_sharpe:
            hostile_regression_driver = "weak_window_2023_08"
        elif tuned.negative_dev_worst_windows > baseline.negative_dev_worst_windows:
            hostile_regression_driver = "worst_dd_window"
        elif tuned.avg_dev_sharpe < baseline.avg_dev_sharpe:
            hostile_regression_driver = "walk_forward_regression"
        else:
            hostile_regression_driver = "hostile_window_regression"
    return {
        "dev_primary_improved": dev_primary_improved,
        "hostile_window_regressed": hostile_window_regressed,
        "hostile_regression_driver": hostile_regression_driver,
    }


def _params_equivalent(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if set(left) != set(right):
        return False
    for key, left_value in left.items():
        right_value = right.get(key)
        try:
            if round(float(left_value), 10) != round(float(right_value), 10):
                return False
        except (TypeError, ValueError):
            if left_value != right_value:
                return False
    return True


def _best_train_candidate_metrics(report: SearchReport) -> dict[str, Any] | None:
    diagnostics = report.search_status.get("train_candidate_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    train_trials = diagnostics.get("top_train_trial_diagnostics")
    if not isinstance(train_trials, list) or not train_trials:
        return None
    best_params = report.best_params if isinstance(report.best_params, dict) else None
    if best_params:
        for trial in train_trials:
            if not isinstance(trial, dict):
                continue
            if _params_equivalent(trial.get("params"), best_params):
                metrics = trial.get("metrics")
                return dict(metrics) if isinstance(metrics, dict) else None
    first_metrics = train_trials[0].get("metrics") if isinstance(train_trials[0], dict) else None
    return dict(first_metrics) if isinstance(first_metrics, dict) else None


def _variant_window_metrics(report: SearchReport, variant: str) -> dict[str, dict[str, Any]]:
    comparison_payload = report.comparison_payload if isinstance(report.comparison_payload, dict) else {}
    window_reports = comparison_payload.get("window_reports")
    if not isinstance(window_reports, list):
        return {}
    metrics_by_window: dict[str, dict[str, Any]] = {}
    for window_report in window_reports:
        if not isinstance(window_report, dict) or window_report.get("variant") != variant:
            continue
        label = str(window_report.get("window_label") or "")
        metrics = window_report.get("metrics")
        if label and isinstance(metrics, dict):
            metrics_by_window[label] = dict(metrics)
    return metrics_by_window


def _dev_fee_stability_summary(report: SearchReport) -> dict[str, Any]:
    search_profile = report.search_status.get("search_space_profile") or {}
    limits = (
        RELAXED_DEV_FEE_STABILITY_LIMITS
        if isinstance(search_profile, dict) and search_profile.get("research_relaxed_fee_probe")
        else DEFAULT_DEV_FEE_STABILITY_LIMITS
    )
    tuned = _best_tuned_candidate(report)
    if tuned is None:
        return {
            "passed": True,
            "failures": [],
            "dev_fee_drag_pct": None,
            "train_fee_drag_pct": None,
            "dev_to_train_fee_drag_ratio": None,
            "dev_train_fee_drag_gap": None,
            "window_fee_drag_pct": {},
            "limits": {
                "dev_fee_drag_hard_cap": limits.dev_fee_drag_hard_cap,
                "dev_hostile_window_fee_drag_hard_cap": limits.dev_hostile_window_fee_drag_hard_cap,
                "dev_to_train_fee_drag_ratio_cap": limits.dev_to_train_fee_drag_ratio_cap,
                "dev_to_train_fee_drag_gap_cap": limits.dev_to_train_fee_drag_gap_cap,
            },
        }

    failures: list[str] = []
    train_metrics = _best_train_candidate_metrics(report)
    train_fee_drag_pct = _safe_metric(train_metrics, "fee_drag_pct") if train_metrics is not None else None
    dev_fee_drag_pct = float(tuned.fee_drag_pct or 0.0)
    dev_to_train_ratio = None
    dev_train_gap = None

    if dev_fee_drag_pct > limits.dev_fee_drag_hard_cap:
        failures.append("dev_fee_drag_cap_exceeded")
    if train_fee_drag_pct is not None and train_fee_drag_pct > 0.0:
        dev_to_train_ratio = dev_fee_drag_pct / train_fee_drag_pct
        dev_train_gap = dev_fee_drag_pct - train_fee_drag_pct
        if (
            dev_to_train_ratio > limits.dev_to_train_fee_drag_ratio_cap
            or dev_train_gap > limits.dev_to_train_fee_drag_gap_cap
        ):
            failures.append("dev_train_fee_drag_mismatch")

    window_fee_drag_pct = {
        label: round(_safe_metric(metrics, "fee_drag_pct"), 6)
        for label, metrics in _variant_window_metrics(report, tuned.variant).items()
    }
    hostile_fee_failures = [
        label
        for label, fee_drag_pct in window_fee_drag_pct.items()
        if label != "dev" and fee_drag_pct > limits.dev_hostile_window_fee_drag_hard_cap
    ]
    if hostile_fee_failures:
        failures.append("hostile_window_fee_drag_cap_exceeded")

    return {
        "passed": not failures,
        "failures": failures,
        "dev_fee_drag_pct": round(dev_fee_drag_pct, 6),
        "train_fee_drag_pct": round(train_fee_drag_pct, 6) if train_fee_drag_pct is not None else None,
        "dev_to_train_fee_drag_ratio": round(dev_to_train_ratio, 4) if dev_to_train_ratio is not None else None,
        "dev_train_fee_drag_gap": round(dev_train_gap, 6) if dev_train_gap is not None else None,
        "window_fee_drag_pct": window_fee_drag_pct,
        "hostile_fee_failure_windows": hostile_fee_failures,
        "limits": {
            "dev_fee_drag_hard_cap": limits.dev_fee_drag_hard_cap,
            "dev_hostile_window_fee_drag_hard_cap": limits.dev_hostile_window_fee_drag_hard_cap,
            "dev_to_train_fee_drag_ratio_cap": limits.dev_to_train_fee_drag_ratio_cap,
            "dev_to_train_fee_drag_gap_cap": limits.dev_to_train_fee_drag_gap_cap,
        },
    }


def _no_tuned_candidate_reason(report: SearchReport) -> str | None:
    explicit_reason = report.search_status.get("no_tuned_candidate_reason")
    if explicit_reason:
        return str(explicit_reason)
    if report.optuna_best is None and report.rl_best is None:
        empty_shortlist_reason = report.search_status.get("empty_shortlist_reason")
        if empty_shortlist_reason:
            return str(empty_shortlist_reason)
        return "empty_train_shortlist"
    for candidate in [report.optuna_best, report.rl_best]:
        if candidate is None:
            continue
        blocked_by = candidate.blocked_by
        if blocked_by in {"pre_validation_rejection", "prepare_timeout"}:
            return "prevalidation_rejected"
    return None


def _candidate_frozen_id(best_variant: str, best_params: dict[str, Any] | None) -> str | None:
    if not best_params:
        return None
    payload = json.dumps(
        {
            "variant": best_variant,
            "params": best_params,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha1(payload.encode("utf-8")).hexdigest()[:12]


def _assess_candidate_handoff_readiness(report: SearchReport) -> dict[str, Any]:
    dev_gate_summary = _assess_full_training_readiness(report, smoke_completed=report.smoke_completed)
    blockers = list(dev_gate_summary.get("blockers", []))
    return {
        "ready": not blockers,
        "decision": "handoff_to_acceptance" if not blockers else "defer_handoff",
        "blockers": blockers,
        "warnings": list(dev_gate_summary.get("warnings", [])),
        "best_tuned_variant": dev_gate_summary.get("best_tuned_variant"),
        "gates": dict(dev_gate_summary.get("gates", {})),
    }


def _assess_full_training_readiness(
    report: SearchReport,
    *,
    smoke_completed: bool,
) -> dict[str, Any]:
    execution_contract = report.training_input_contract.get("execution_framework", {})
    state_sources = report.training_input_contract.get("state_feature_sources", {})
    signal_independence_contract = report.training_input_contract.get("signal_independence_contract", {})
    search_profile = report.search_status.get("search_space_profile") or {}
    direction_policy = search_profile.get("direction_weight_policy", {})
    baseline = report.current
    tuned = _best_tuned_candidate(report)

    system_failures: list[str] = []
    if not smoke_completed:
        system_failures.append("smoke_campaign_incomplete")
    if not execution_contract.get("five_min_direct_execution_enabled"):
        system_failures.append("five_min_direct_execution_disabled")

    data_failures: list[str] = []
    data_warnings: list[str] = []
    if not execution_contract.get("five_min_feature_aggregation_enabled"):
        data_failures.append("missing_five_min_bars")
    if not execution_contract.get("one_hour_bars_available"):
        data_failures.append("missing_one_hour_bars")
    if not execution_contract.get("four_hour_bars_available"):
        data_failures.append("missing_four_hour_bars")
    if not state_sources.get("hyperliquid_in_state_features"):
        data_warnings.append("hyperliquid_state_features_unavailable")
    if not report.deribit_status.get("observation_ready"):
        data_warnings.append("deribit_observation_not_ready")
    if not state_sources.get("etf_flow_in_state_features"):
        data_warnings.append("etf_flow_state_features_unavailable")
    if not state_sources.get("mvrv_in_state_features"):
        data_warnings.append("mvrv_state_features_unavailable")
    if not state_sources.get("deribit_block_flow_in_state_features"):
        data_warnings.append("deribit_block_flow_state_features_unavailable")
    if not signal_independence_contract.get("audit_present"):
        data_warnings.append("active_signal_independence_missing")
    if report.search_status.get("liquidation_in_search") and not report.readiness.get("promotion_ready"):
        data_failures.append("liquidation_search_enabled_before_promotion_ready")

    model_failures: list[str] = []
    fee_stability = _dev_fee_stability_summary(report)
    if tuned is None:
        model_failures.append("no_tuned_candidate_from_smoke")
    else:
        if tuned.worst_dev_sharpe < baseline.worst_dev_sharpe:
            model_failures.append("worst_dev_sharpe_worse_than_paulwei")
        if tuned.negative_dev_worst_windows > baseline.negative_dev_worst_windows:
            model_failures.append("negative_worst_windows_increased")
        if tuned.dev_drawdown > baseline.dev_drawdown + 0.03:
            model_failures.append("drawdown_worse_than_baseline_relative_gate")
        model_failures.extend(fee_stability["failures"])
        if report.evaluation_role == "acceptance":
            if not tuned.test_evaluated:
                model_failures.append("test_not_evaluated")
            elif not tuned.promotion_candidate:
                model_failures.append(tuned.promotion_failure_reason or "test_acceptance_failed")

    search_failures: list[str] = []
    if not direction_policy.get("correlation_constrained"):
        search_failures.append("direction_weights_not_correlation_constrained")
    if direction_policy.get("capped_direction_signal") != "crowding_signal":
        search_failures.append("redundant_signal_not_frozen_or_capped")
    if direction_policy.get("demoted_signal") != "funding_alpha_signal":
        search_failures.append("redundant_pair_constraint_missing")
    if direction_policy.get("demoted_signal_role") != "state":
        search_failures.append("redundant_pair_constraint_missing")
    if signal_independence_contract.get("audit_present") and not signal_independence_contract.get(
        "direction_layer_clean", False
    ):
        search_failures.append("direction_signal_independence_unclean")

    blockers = [*system_failures, *data_failures, *model_failures, *search_failures]
    return {
        "ready": not blockers,
        "decision": "allow_full_training" if not blockers else "defer_full_training",
        "recommended_budget": {"optuna_trials": 80, "rl_episodes": 40} if not blockers else None,
        "gates": {
            "system_gate": {"passed": not system_failures, "failures": system_failures},
            "data_gate": {"passed": not data_failures, "failures": data_failures},
            "model_gate": {"passed": not model_failures, "failures": model_failures},
            "fee_stability_gate": fee_stability,
            "search_gate": {"passed": not search_failures, "failures": search_failures},
        },
        "blockers": blockers,
        "warnings": data_warnings,
        "best_tuned_variant": tuned.variant if tuned is not None else None,
    }


def _classify_post_smoke_scenario(
    report: SearchReport,
    *,
    full_training_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assessment = full_training_assessment or _assess_full_training_readiness(
        report, smoke_completed=report.smoke_completed
    )
    blockers = list(assessment.get("blockers", []))
    model_failures = set(assessment.get("gates", {}).get("model_gate", {}).get("failures", []))
    data_failures = set(assessment.get("gates", {}).get("data_gate", {}).get("failures", []))
    search_failures = set(assessment.get("gates", {}).get("search_gate", {}).get("failures", []))

    if not report.smoke_completed:
        return {
            "scenario": "smoke_incomplete",
            "category": "pending",
            "should_open_full_training": False,
            "next_action": "continue_smoke_until_authoritative_verdict",
            "blockers": blockers,
        }

    if assessment.get("ready"):
        return {
            "scenario": "scenario_a_full_training_allowed",
            "category": "full_training_ready",
            "should_open_full_training": True,
            "next_action": "start_full_training_budget",
            "recommended_budget": assessment.get("recommended_budget"),
            "blockers": [],
        }

    hostile_window_failures = {
        "worst_dev_sharpe_worse_than_paulwei",
        "negative_worst_windows_increased",
        "drawdown_worse_than_baseline_relative_gate",
    }
    if model_failures & hostile_window_failures:
        return {
            "scenario": "scenario_b_hostile_window_failure",
            "category": "execution_hostile_window",
            "should_open_full_training": False,
            "next_action": "continue_execution_first_hostile_window_optimization",
            "blockers": blockers,
        }

    if "no_tuned_candidate_from_smoke" in model_failures:
        return {
            "scenario": "scenario_b_no_tuned_candidate",
            "category": "search_candidate_quality",
            "should_open_full_training": False,
            "next_action": "tighten_train_search_and_rerun_smoke",
            "blockers": blockers,
        }

    fee_efficiency_failures = {
        "dev_fee_drag_cap_exceeded",
        "dev_train_fee_drag_mismatch",
        "hostile_window_fee_drag_cap_exceeded",
    }
    if model_failures & fee_efficiency_failures:
        return {
            "scenario": "scenario_b_fee_efficiency_failure",
            "category": "train_dev_fee_stability",
            "should_open_full_training": False,
            "next_action": "tighten_train_dev_fee_efficiency_and_rerun_smoke",
            "blockers": blockers,
        }

    if data_failures:
        return {
            "scenario": "scenario_c_state_data_gap",
            "category": "state_data_readiness",
            "should_open_full_training": False,
            "next_action": "close_state_readiness_gaps_before_budget_increase",
            "blockers": blockers,
        }

    if search_failures:
        return {
            "scenario": "scenario_d_direction_gate_failure",
            "category": "direction_search_constraints",
            "should_open_full_training": False,
            "next_action": "tighten_direction_constraints_and_rerun_smoke",
            "blockers": blockers,
        }

    return {
        "scenario": "scenario_unclassified",
        "category": "manual_review",
        "should_open_full_training": False,
        "next_action": "inspect_final_verdict_and_refresh_blocker_classification",
        "blockers": blockers,
    }


def _build_framework_diagnosis(report: SearchReport) -> dict[str, Any]:
    tuned = _best_tuned_candidate(report)
    baseline = report.current
    hostile_regression = _hostile_regression_summary(report)
    signal_audit = report.signal_audit_summary or {}
    summary = signal_audit.get("summary", {})
    redundant_pairs = summary.get("high_correlation_pairs", [])

    issues = [
        {
            "category": "direction_layer_quality",
            "severity": "high",
            "evidence": {
                "redundant_pair_count": summary.get("redundant_pair_count"),
                "top_redundant_pairs": redundant_pairs[:2],
            },
            "problem": "Direction layer remains a constrained proxy stack rather than a stable independent alpha stack.",
        },
        {
            "category": "execution_chain_validation",
            "severity": "high",
            "evidence": {
                "five_min_direct_execution_enabled": report.training_input_contract.get("execution_framework", {}).get(
                    "five_min_direct_execution_enabled"
                ),
                "report_mode": report.report_mode,
                "test_evaluated": report.test_evaluated,
            },
            "problem": "The 5m timing layer is integrated but must still prove it improves hostile windows without adding churn.",
        },
        {
            "category": "state_data_readiness",
            "severity": "high",
            "evidence": {
                "deribit_observation_ready": report.deribit_status.get("observation_ready"),
                "liquidation_promotion_ready": report.readiness.get("promotion_ready"),
                "etf_flow_in_state_features": report.training_input_contract.get("state_feature_sources", {}).get(
                    "etf_flow_in_state_features"
                ),
            },
            "problem": "State architecture is ahead of actual data readiness, especially for Deribit, ETF flow, and liquidation promotion eligibility.",
        },
        {
            "category": "hostile_window_stability",
            "severity": "critical",
            "evidence": {
                "current_worst_dev_sharpe": baseline.worst_dev_sharpe,
                "tuned_worst_dev_sharpe": tuned.worst_dev_sharpe if tuned is not None else None,
                "tuned_negative_worst_windows": tuned.negative_dev_worst_windows if tuned is not None else None,
                "dev_primary_improved": hostile_regression["dev_primary_improved"],
                "hostile_window_regressed": hostile_regression["hostile_window_regressed"],
                "hostile_regression_driver": hostile_regression["hostile_regression_driver"],
            },
            "problem": "The current weakness remains hostile-window quality, not average opportunity capture.",
        },
        {
            "category": "feature_role_boundaries",
            "severity": "medium",
            "evidence": {
                "liquidation_in_search": report.search_status.get("liquidation_in_search"),
                "hyperliquid_in_state_features": report.training_input_contract.get("state_feature_sources", {}).get(
                    "hyperliquid_in_state_features"
                ),
                "deribit_in_state_features": report.training_input_contract.get("state_feature_sources", {}).get(
                    "deribit_in_state_features"
                ),
            },
            "problem": "Multi-source inputs are valuable, but only if they remain state/risk features and do not leak into direction authority prematurely.",
        },
    ]

    return {
        "current_state": "post_infrastructure_validation" if not report.test_evaluated else "post_smoke_verification",
        "issues": issues,
        "future_architecture_direction": [
            "Keep BTC as the single flagship asset until hostile-window stability is proven.",
            "Retain 4h regime + 1h context + 5m execution, with 5m limited to timing and urgency.",
            "Keep Hyperliquid, Deribit, ETF flow, and liquidation in state/risk/execution layers before any direction promotion.",
            "Continue signal-combination work in research first, then absorb only validated bounds and freezes into production.",
            "Prioritize inventory engineering, unwind logic, and asymmetric long/short behavior over adding more direction signals.",
        ],
        "recommended_next_steps": [
            "Finish the active smoke campaign and inspect worst-dev-window behavior before any budget increase.",
            "Make ETF flow data live in macro state features and verify point-in-time alignment.",
            "Wait for Deribit observation readiness or explicitly validate a partial-confidence downgrade path.",
            "Keep liquidation out of promote search until promotion_ready becomes true.",
            "Open full training only if the best tuned candidate stops regressing on worst_dev_sharpe and negative worst windows.",
        ],
        "dev_primary_improved": hostile_regression["dev_primary_improved"],
        "hostile_window_regressed": hostile_regression["hostile_window_regressed"],
        "hostile_regression_driver": hostile_regression["hostile_regression_driver"],
    }


class FrameworkEvaluator:
    """Evaluate framework candidates against current and PaulWei baselines."""

    def __init__(
        self,
        base_config: HistoricalBacktestConfig,
        *,
        split_spec: DatasetSplitSpec | None = None,
        search_space_mode: str = "execution_first_v2_correlation_constrained",
    ) -> None:
        self._base_config = base_config
        self._split_spec = split_spec or default_btc_split_spec()
        self._search_space_mode = search_space_mode
        readiness = liquidation_readiness(
            base_config.root_dir,
            base_config.symbol,
            liquidation_dir=base_config.liquidation_dir,
        )
        self._readiness = {
            "total_bars": readiness.total_bars,
            "nonzero_bars": readiness.nonzero_bars,
            "nonstale_bars": readiness.nonstale_bars,
            "extreme_bars": readiness.extreme_bars,
            "max_total_liq_usd": readiness.max_total_liq_usd,
            "data_ready": readiness.data_ready,
            "promotion_ready": readiness.promotion_ready,
            "blocked_by": readiness.blocked_by,
            "detail": readiness.detail,
        }
        self._backtest_cache: dict[tuple[str, int | None, int | None], BacktestSnapshot] = {}
        self._validation_cache: dict[tuple[str, tuple[str, ...]], dict[str, float]] = {}
        self._summary_cache: dict[tuple[str, bool, bool], CandidateSummary] = {}
        self._validation_pool: ProcessPoolExecutor | None = None
        self._validation_pool_workers = 1
        self._validation_pool_start_method: str | None = None
        self._full_dev_compare_windows = default_comparison_windows(self._split_spec, "dev")
        worst_dev_windows = filtered_worst_period_windows(self._split_spec, "dev")
        self._fast_dev_compare_windows = [
            split_window_spec(self._split_spec, "dev"),
            fixed_2023_08_window(),
            *([worst_dev_windows[-1]] if worst_dev_windows else []),
        ]
        self._fast_dev_prepare_windows = list(self._fast_dev_compare_windows)
        self._full_dev_prepare_windows = list(self._full_dev_compare_windows)
        self._dev_windows = list(self._full_dev_compare_windows)
        self._test_windows = [
            *default_comparison_windows(self._split_spec, "test"),
            *filtered_worst_period_windows(self._split_spec, "test"),
        ]
        self._fast_dev_wf_windows = default_walk_forward_windows(split_spec=self._split_spec, comparison_split="dev")[
            :2
        ]
        self._full_dev_wf_windows = default_walk_forward_windows(split_spec=self._split_spec, comparison_split="dev")
        cpu_budget = max(1, (os.cpu_count() or 2) - 2)
        self._compute_profiles = {
            "smoke": {
                "workers": 1,
                "wf_windows": 0,
                "compare_windows": len(self._fast_dev_compare_windows),
                "compare_workers": 2,
                "compare_validation_workers": 1,
                "compare_validation_leg_timeout_s": 90.0,
                "compare_validation_variant_budget_s": 180.0,
                "prepare_window_timeout_s": 90.0,
                "prepare_variant_budget_s": 240.0,
                "replay_timeout_s": 120.0,
            },
            "fast": {
                "workers": min(2, cpu_budget),
                "wf_windows": len(self._fast_dev_wf_windows),
                "compare_windows": len(self._fast_dev_compare_windows),
                "compare_workers": min(2, cpu_budget),
                "compare_validation_workers": min(2, cpu_budget),
                "compare_validation_leg_timeout_s": 180.0,
                "compare_validation_variant_budget_s": 480.0,
                "prepare_window_timeout_s": 180.0,
                "prepare_variant_budget_s": 420.0,
                "replay_timeout_s": 180.0,
            },
            "high": {
                "workers": min(4, cpu_budget),
                "wf_windows": len(self._full_dev_wf_windows),
                "compare_windows": len(self._full_dev_compare_windows),
                "compare_workers": min(4, cpu_budget),
                "compare_validation_workers": min(4, cpu_budget),
                "compare_validation_leg_timeout_s": 360.0,
                "compare_validation_variant_budget_s": 1200.0,
                "prepare_window_timeout_s": 360.0,
                "prepare_variant_budget_s": 1200.0,
                "replay_timeout_s": 360.0,
            },
        }
        self._current_variant = default_variant_specs()[0]
        self._paulwei_variant = default_variant_specs()[1]
        self._deribit_status = asdict(collect_deribit_status(base_config.options_dir, "BTC"))
        self._training_window_coverage = {
            "train_window": split_summary_json(self._split_spec)["windows"]["train"],
            "dev_windows": [window.label for window in self._full_dev_compare_windows],
            "fast_dev_compare_windows": [window.label for window in self._fast_dev_compare_windows],
            "dev_walk_forward_windows": [window.label for window in self._full_dev_wf_windows],
            "test_windows": [window.label for window in self._test_windows],
        }
        train_start_ns = self._split_spec.train.start_ns
        train_end_ns = self._split_spec.train.end_ns
        five_min_train_available = _window_has_timeframe_rows(
            base_config.root_dir,
            base_config.symbol,
            Timeframe.TF_5M,
            start_ns=train_start_ns,
            end_ns=train_end_ns,
        )
        one_hour_train_direct = _window_has_timeframe_rows(
            base_config.root_dir,
            base_config.symbol,
            Timeframe.TF_1H,
            start_ns=train_start_ns,
            end_ns=train_end_ns,
        )
        one_hour_train_available = one_hour_train_direct or (
            base_config.execution_timeframe == Timeframe.TF_5M and five_min_train_available
        )
        four_hour_train_available = _window_has_timeframe_rows(
            base_config.root_dir,
            base_config.symbol,
            Timeframe.TF_4H,
            start_ns=train_start_ns,
            end_ns=train_end_ns,
        )
        self._training_window_coverage.update(
            {
                "five_min_train_available": five_min_train_available,
                "one_hour_train_direct_available": one_hour_train_direct,
                "one_hour_train_available": one_hour_train_available,
                "one_hour_train_source": "native_1h"
                if one_hour_train_direct
                else ("derived_from_5m" if one_hour_train_available else "missing"),
                "four_hour_train_available": four_hour_train_available,
            }
        )
        etf_flow_metadata = _etf_flow_source_metadata(base_config.macro_dir)
        mvrv_metadata = _mvrv_source_metadata(base_config.macro_dir)
        hyperliquid_admission = _admission_contract("hyperliquid")
        mvrv_admission = _admission_contract("mvrv")
        mvrv_independence = _independence_contract("btc_mvrv")
        deribit_block_admission = _admission_contract("deribit_block_flow")
        deribit_block_independence = _independence_contract("deribit_block_flow")
        deribit_large_option_admission = _admission_contract("deribit_large_option_flow")
        deribit_large_option_independence = _independence_contract("deribit_large_option_flow")
        active_signal_independence = _active_signal_independence_contract()
        historical_extension_candidate = _historical_extension_candidate_contract()
        self._training_input_contract = {
            "primary_truth_source": "binance",
            "historical_extension_train_enabled": bool(historical_extension_candidate.get("layered_train_enabled")),
            "historical_extension_mode": historical_extension_candidate.get("historical_extension_mode"),
            "train_truth_legs": list(historical_extension_candidate.get("train_truth_legs") or []),
            "dev_truth_source": historical_extension_candidate.get("dev_truth_source", "futures_only"),
            "test_truth_source": historical_extension_candidate.get("test_truth_source", "futures_only"),
            "mixed_era_declared": bool(historical_extension_candidate.get("mixed_era_declared")),
            "historical_extension_candidate_contract": historical_extension_candidate,
            "execution_framework": {
                "primary_timeframe": "4h",
                "entry_optimization_timeframe": "1h",
                "final_execution_timeframe": "5m" if base_config.execution_timeframe == Timeframe.TF_5M else None,
                "five_min_feature_aggregation_enabled": five_min_train_available,
                "one_hour_bars_available": one_hour_train_available,
                "one_hour_bars_source": (
                    "native_1h"
                    if one_hour_train_direct
                    else ("derived_from_5m" if one_hour_train_available else "missing")
                ),
                "four_hour_bars_available": four_hour_train_available,
                "five_min_direct_execution_enabled": base_config.execution_timeframe == Timeframe.TF_5M,
            },
            "state_feature_sources": {
                "deribit_in_state_features": bool(
                    self._deribit_status["enabled"] and self._deribit_status["snapshot_count_7d"] > 0
                ),
                "hyperliquid_in_state_features": bool(self._readiness["data_ready"]),
                "hyperliquid_admission_review_present": hyperliquid_admission["review_present"],
                "hyperliquid_tier_decision": hyperliquid_admission["tier_decision"],
                "etf_flow_in_state_features": (base_config.macro_dir / "btc_etf_netflow" / "data.parquet").exists(),
                "etf_flow_source": etf_flow_metadata["etf_flow_source"],
                "etf_flow_source_confidence": etf_flow_metadata["etf_flow_source_confidence"],
                "etf_flow_last_success_at": etf_flow_metadata["etf_flow_last_success_at"],
                "mvrv_in_state_features": bool(
                    (base_config.macro_dir / "btc_mvrv" / "data.parquet").exists()
                    and mvrv_admission["review_present"]
                    and mvrv_admission["research_ready"]
                    and mvrv_independence["review_present"]
                    and mvrv_independence["allowed_in_state_features"]
                    and mvrv_admission["tier_decision"]
                    in {"tier2_research_only_feature", "tier3_promoted_state_signal"}
                ),
                "mvrv_source": mvrv_metadata["mvrv_source"],
                "mvrv_source_confidence": mvrv_metadata["mvrv_source_confidence"],
                "mvrv_last_success_at": mvrv_metadata["mvrv_last_success_at"],
                "mvrv_admission_review_present": mvrv_admission["review_present"],
                "mvrv_tier_decision": mvrv_admission["tier_decision"],
                "mvrv_time_granularity": mvrv_admission["time_granularity"],
                "mvrv_use_for_research_only": mvrv_admission["use_for_research_only"],
                "mvrv_exclude_from_promote_truth": mvrv_admission["exclude_from_promote_truth"],
                "mvrv_independence_review_present": mvrv_independence["review_present"],
                "mvrv_independence_allowed": mvrv_independence["allowed_in_state_features"],
                "mvrv_max_abs_correlation": mvrv_independence["max_abs_correlation"],
                "mvrv_blocked_by": mvrv_independence["blocked_by"] or mvrv_admission["blocked_by"],
                "deribit_block_flow_in_state_features": bool(
                    deribit_block_admission["review_present"]
                    and deribit_block_admission["research_ready"]
                    and deribit_block_independence["review_present"]
                    and deribit_block_independence["allowed_in_state_features"]
                    and deribit_block_admission["tier_decision"]
                    in {"tier2_research_only_feature", "tier3_promoted_state_signal"}
                ),
                "deribit_block_flow_admission_review_present": deribit_block_admission["review_present"],
                "deribit_block_flow_tier_decision": deribit_block_admission["tier_decision"],
                "deribit_block_flow_independence_review_present": deribit_block_independence["review_present"],
                "deribit_block_flow_independence_allowed": deribit_block_independence["allowed_in_state_features"],
                "deribit_block_flow_max_abs_correlation": deribit_block_independence["max_abs_correlation"],
                "deribit_block_flow_blocked_by": (
                    deribit_block_independence["blocked_by"] or deribit_block_admission["blocked_by"]
                ),
                "deribit_large_option_flow_in_state_features": False,
                "deribit_large_option_flow_research_candidate": bool(
                    deribit_large_option_admission["review_present"]
                    and deribit_large_option_admission["research_ready"]
                    and deribit_large_option_admission["feature_freeze_candidate"]
                    and deribit_large_option_admission["tier_decision"] == "tier2_research_candidate"
                ),
                "deribit_large_option_flow_admission_review_present": deribit_large_option_admission["review_present"],
                "deribit_large_option_flow_tier_decision": deribit_large_option_admission["tier_decision"],
                "deribit_large_option_flow_time_granularity": deribit_large_option_admission["time_granularity"],
                "deribit_large_option_flow_use_for_research_only": deribit_large_option_admission[
                    "use_for_research_only"
                ],
                "deribit_large_option_flow_exclude_from_promote_truth": deribit_large_option_admission[
                    "exclude_from_promote_truth"
                ],
                "deribit_large_option_flow_strategy_wiring_allowed": deribit_large_option_admission[
                    "strategy_wiring_allowed"
                ],
                "deribit_large_option_flow_risk_overlay_wiring_allowed": deribit_large_option_admission[
                    "risk_overlay_wiring_allowed"
                ],
                "deribit_large_option_flow_direction_vote_allowed": deribit_large_option_admission[
                    "direction_vote_allowed"
                ],
                "deribit_large_option_flow_intent_aggregation_allowed": deribit_large_option_admission[
                    "intent_aggregation_allowed"
                ],
                "deribit_large_option_flow_position_sizing_allowed": deribit_large_option_admission[
                    "position_sizing_allowed"
                ],
                "deribit_large_option_flow_training_observation_allowed": deribit_large_option_admission[
                    "allowed_in_training_observation"
                ],
                "deribit_large_option_flow_candidate_explanation_allowed": deribit_large_option_admission[
                    "allowed_in_candidate_explanation"
                ],
                "deribit_large_option_flow_independence_review_present": deribit_large_option_independence[
                    "review_present"
                ],
                "deribit_large_option_flow_independence_allowed": deribit_large_option_independence[
                    "allowed_in_state_features"
                ],
                "deribit_large_option_flow_pressure_context_allowed": deribit_large_option_independence[
                    "allowed_in_pressure_context"
                ],
                "deribit_large_option_flow_max_abs_correlation": deribit_large_option_independence[
                    "max_abs_correlation"
                ],
                "deribit_large_option_flow_blocked_by": deribit_large_option_admission["blocked_by"],
                "cme_positioning_in_state_features": macro_cot_available(Path("data/raw/cftc/cot_btc.parquet")),
                "liquidation_in_search": bool(self._readiness["promotion_ready"]),
            },
            "signal_stack_contract": {
                "direction_state_execution_split_enabled": True,
                "state_signals_do_not_vote_direction": True,
                "execution_modifiers_do_not_flip_direction": True,
            },
            "signal_independence_contract": active_signal_independence,
        }
        self._search_param_grid = param_grid_for_mode(
            mode=search_space_mode,
            include_liquidation_toggles=self._readiness["promotion_ready"],
        )
        self._search_space_profile = search_space_profile(
            include_liquidation_toggles=self._readiness["promotion_ready"],
            mode=search_space_mode,
        )
        self._public_baseline_reference = _public_baseline_reference()
        self._paulwei_fast_lightweight: CandidateSummary | None = None
        self._paulwei_fast_full: CandidateSummary | None = None
        self._current_fast: CandidateSummary | None = None
        self._stage2_mp_start_method = "spawn" if sys.platform == "darwin" else None
        self._validation_pool_mode = "process_pool_spawn" if self._stage2_mp_start_method else "process_pool_default"

    def _reset_validation_pool(self) -> None:
        _shutdown_process_pool_quickly(self._validation_pool)
        self._validation_pool = None

    def _compute_profile(self, *, fast: bool, lightweight: bool) -> dict[str, Any]:
        if lightweight:
            return self._compute_profiles["smoke" if not fast else "fast"]
        return self._compute_profiles["fast" if fast else "high"]

    def _prepare_windows(self, *, fast: bool, lightweight: bool) -> list[WindowSpec]:
        if lightweight:
            return list(self._fast_dev_prepare_windows if fast else self._full_dev_prepare_windows)
        return list(self._dev_windows)

    def _run_prepare_backtest_cached(
        self,
        variant: VariantSpec,
        window: WindowSpec,
        *,
        timeout_s: float,
        replay_pool: ProcessPoolExecutor | None,
    ) -> BacktestSnapshot:
        key = (self._variant_key(variant), window.start_ns, window.end_ns)
        cached = self._backtest_cache.get(key)
        if cached is not None:
            return cached
        try:
            snapshot = _run_variant_backtest_with_timeout(
                self._base_config,
                variant,
                start_ns=window.start_ns,
                end_ns=window.end_ns,
                timeout_s=timeout_s,
                pool=replay_pool,
                mp_start_method=self._stage2_mp_start_method,
            )
        except TimeoutError as exc:
            self._reset_validation_pool()
            raise PrepareVariantTimeoutError(
                variant=variant.name,
                window_label=window.label,
                timeout_s=timeout_s,
            ) from exc
        except Exception as exc:
            raise PrepareVariantRuntimeError(
                variant=variant.name,
                window_label=window.label,
                driver=_runtime_failure_driver(exc),
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        self._backtest_cache[key] = snapshot
        return snapshot

    def _get_validation_pool(self, *, workers: int) -> ProcessPoolExecutor:
        if (
            self._validation_pool is None
            or self._validation_pool_workers != workers
            or self._validation_pool_start_method != self._stage2_mp_start_method
        ):
            if self._validation_pool is not None:
                self._validation_pool.shutdown(wait=False, cancel_futures=True)
            executor_kwargs: dict[str, Any] = {"max_workers": max(1, workers)}
            if self._stage2_mp_start_method:
                executor_kwargs["mp_context"] = mp.get_context(self._stage2_mp_start_method)
            self._validation_pool = ProcessPoolExecutor(**executor_kwargs)
            self._validation_pool_workers = max(1, workers)
            self._validation_pool_start_method = self._stage2_mp_start_method
        return self._validation_pool

    @property
    def stage2_runtime_contract(self) -> dict[str, Any]:
        return {
            "stage2_mp_start_method": self._stage2_mp_start_method or "default",
            "validation_pool_mode": self._validation_pool_mode,
        }

    @property
    def readiness(self) -> dict[str, Any]:
        return dict(self._readiness)

    @property
    def search_param_grid(self) -> dict[str, list[float]]:
        return dict(self._search_param_grid)

    @property
    def search_space_profile(self) -> dict[str, Any]:
        return dict(self._search_space_profile)

    @property
    def training_input_contract(self) -> dict[str, Any]:
        return dict(self._training_input_contract)

    def candidate_variant(self, params: dict[str, Any], *, name: str, label: str) -> VariantSpec:
        risk_config = build_risk_config(params)
        execution_config = build_execution_config(params)
        strategy_config = build_strategy_config(params)
        indicator_config = build_indicator_config(params)
        signal_weight_overrides = build_signal_weight_overrides(params)
        plugin_config_overrides = build_plugin_config_overrides(params)
        liquidation_data_enabled, state_liquidation_overlay_enabled = build_replay_toggles(params)
        if not self._readiness["data_ready"]:
            liquidation_data_enabled = False
            state_liquidation_overlay_enabled = False
            risk_config = paulwei_risk_baseline() if not risk_config.enable_liq_risk_guard else risk_config
        return VariantSpec(
            name=name,
            label=label,
            risk_config=risk_config,
            execution_config=execution_config,
            strategy_config=strategy_config,
            indicator_config=indicator_config,
            signal_weight_overrides=signal_weight_overrides,
            plugin_config_overrides=plugin_config_overrides,
            liquidation_data_enabled=liquidation_data_enabled,
            state_liquidation_overlay_enabled=state_liquidation_overlay_enabled,
        )

    def evaluate_params(
        self,
        params: dict[str, Any],
        *,
        name: str = "candidate",
        label: str = "Candidate",
        fast: bool = True,
    ) -> CandidateSummary:
        requested_liquidation, requested_state_overlay = build_replay_toggles(params)
        requested_liq_guard = bool(round(float(params.get("liq_risk_guard_enabled", 0.0))))
        variant = self.candidate_variant(params, name=name, label=label)
        summary = self._evaluate_variant(variant, fast=fast)
        if (requested_liquidation or requested_state_overlay or requested_liq_guard) and not self._readiness[
            "data_ready"
        ]:
            summary = CandidateSummary(
                **{**asdict(summary), "promotion_candidate": False, "blocked_by": self._readiness["blocked_by"]}
            )
        return summary

    def final_report(
        self,
        *,
        optuna_params: dict[str, Any] | None,
        rl_params: dict[str, Any] | None,
        search_status: dict[str, Any] | None = None,
        full: bool = False,
        evaluate_test: bool = False,
        include_comparison_payload: bool = True,
        status_callback: Any | None = None,
        comparison_compute_mode: str = "full",
        evaluation_role: str = "development",
        test_access_allowed: bool = False,
        dev_gate_used_this_round: bool = True,
        dev_gate_last_round: int | None = None,
        dev_gate_interval: int | None = None,
        full_stage2_executed_this_round: bool = True,
        smoke_completed: bool = True,
    ) -> SearchReport:
        if evaluation_role != "acceptance" and evaluate_test:
            raise ValueError("development path must not evaluate test")
        if evaluation_role == "acceptance" and not test_access_allowed:
            raise ValueError("acceptance role requires test access")
        optuna_variant = (
            self.candidate_variant(optuna_params, name="optuna_tuned", label="Optuna Tuned") if optuna_params else None
        )
        rl_variant = self.candidate_variant(rl_params, name="rl_tuned", label="RL Tuned") if rl_params else None
        variant_specs = [
            ("current", self._current_variant),
            ("paulwei_baseline", self._paulwei_variant),
            *([("optuna_tuned", optuna_variant)] if optuna_variant is not None else []),
            *([("rl_tuned", rl_variant)] if rl_variant is not None else []),
        ]
        prepare_total_units = max(1, len(variant_specs) * 4)
        lightweight_prepare = evaluation_role == "development" and not full

        def _variant_progress(variant_name: str, variant_index: int):
            def _emit(step_completed: int | str, unit: str) -> None:
                if status_callback is None:
                    return
                if isinstance(step_completed, str):
                    status_callback(
                        event="stage2_prepare_variant_validation_progress",
                        variant=variant_name,
                        unit=f"{step_completed}:{unit}",
                        completed_units=(variant_index * 4) + 3,
                        total_units=prepare_total_units,
                    )
                    return
                status_callback(
                    event="stage2_prepare_variant_unit_completed",
                    variant=variant_name,
                    unit=unit,
                    completed_units=(variant_index * 4) + step_completed,
                    total_units=prepare_total_units,
                )

            return _emit

        if status_callback is not None:
            status_callback(event="stage2_prepare_variants_started", completed_units=0, total_units=prepare_total_units)
        current_summary = self._evaluate_variant(
            self._current_variant,
            fast=not full,
            lightweight=lightweight_prepare,
            progress_callback=_variant_progress("current", 0),
        )
        if status_callback is not None:
            status_callback(
                event="stage2_prepare_variants_progress",
                variant="current",
                completed_units=4,
                total_units=prepare_total_units,
            )
        paulwei_summary = self._evaluate_variant(
            self._paulwei_variant,
            fast=not full,
            lightweight=lightweight_prepare,
            progress_callback=_variant_progress("paulwei_baseline", 1),
        )
        summaries = {
            "current": current_summary,
            "paulwei_baseline": paulwei_summary,
        }
        if status_callback is not None:
            status_callback(
                event="stage2_prepare_variants_progress",
                variant="paulwei_baseline",
                completed_units=8,
                total_units=prepare_total_units,
            )
        if optuna_variant is not None:
            summaries["optuna_tuned"] = self._evaluate_variant(
                optuna_variant,
                fast=not full,
                lightweight=lightweight_prepare,
                progress_callback=_variant_progress("optuna_tuned", 2),
            )
            if status_callback is not None:
                status_callback(
                    event="stage2_prepare_variants_progress",
                    variant="optuna_tuned",
                    completed_units=12,
                    total_units=prepare_total_units,
                )
        if rl_variant is not None:
            rl_index = 3 if optuna_variant is not None else 2
            summaries["rl_tuned"] = self._evaluate_variant(
                rl_variant,
                fast=not full,
                lightweight=lightweight_prepare,
                progress_callback=_variant_progress("rl_tuned", rl_index),
            )
            if status_callback is not None:
                status_callback(
                    event="stage2_prepare_variants_progress",
                    variant="rl_tuned",
                    completed_units=prepare_total_units,
                    total_units=prepare_total_units,
                )

        best_name, best_summary = max(summaries.items(), key=lambda item: item[1].robust_score)
        evaluated_best_summary = best_summary
        if evaluate_test:
            best_variant_spec = next(
                variant
                for variant in [self._current_variant, self._paulwei_variant, optuna_variant, rl_variant]
                if variant is not None and variant.name == best_name
            )
            evaluated_best_summary = self._evaluate_test_acceptance(best_variant_spec, best_summary)
            summaries[best_name] = evaluated_best_summary
        variants = [
            self._current_variant,
            self._paulwei_variant,
            *([optuna_variant] if optuna_variant is not None else []),
            *([rl_variant] if rl_variant is not None else []),
        ]
        report_mode, report_degraded_due_to_memory = self._comparison_report_mode(
            variant_count=len([variant for variant in variants if variant is not None]),
            window_count=len(self._full_dev_compare_windows if full else self._fast_dev_compare_windows),
            walk_forward_window_count=len(self._full_dev_wf_windows if full else self._fast_dev_wf_windows),
        )
        comparison_payload: dict[str, Any] = {}
        if include_comparison_payload:
            comparison_profile = self._compute_profiles[
                "high" if full else ("smoke" if comparison_compute_mode == "smoke_minimal" else "fast")
            ]
            if status_callback is not None:
                status_callback(
                    event="stage2_compare_windows_started",
                    compare_workers=int(comparison_profile["compare_workers"]),
                    validation_workers=int(comparison_profile["compare_validation_workers"]),
                    window_units_total=len([variant for variant in variants if variant is not None])
                    * len(self._full_dev_compare_windows if full else self._fast_dev_compare_windows),
                    validation_units_total=len([variant for variant in variants if variant is not None])
                    * len(self._full_dev_wf_windows if full else self._fast_dev_wf_windows)
                    * 2,
                )
            comparison_payload = _build_comparison_payload(
                self._base_config,
                variants=[variant for variant in variants if variant is not None],
                windows=self._full_dev_compare_windows if full else self._fast_dev_compare_windows,
                walk_forward_windows=self._full_dev_wf_windows if full else self._fast_dev_wf_windows,
                split_spec=self._split_spec,
                comparison_split="dev",
                report_mode=report_mode,
                comparison_compute_mode=comparison_compute_mode,
                window_workers=int(comparison_profile["compare_workers"]),
                validation_workers=int(comparison_profile["compare_validation_workers"]),
                validation_leg_timeout_s=float(comparison_profile["compare_validation_leg_timeout_s"]),
                validation_variant_budget_s=float(comparison_profile["compare_validation_variant_budget_s"]),
                status_callback=status_callback,
            )
            comparison_payload["liquidation_readiness"] = dict(self._readiness)
        test_payload = None
        if evaluate_test and include_comparison_payload:
            test_payload = _build_comparison_payload(
                self._base_config,
                variants=[variant for variant in variants if variant is not None and variant.name == best_name],
                windows=self._test_windows,
                walk_forward_windows=[],
                split_spec=self._split_spec,
                comparison_split="test",
                report_mode="summary_only",
                comparison_compute_mode="smoke_minimal",
                status_callback=status_callback,
            )
        if status_callback is not None:
            status_callback(event="stage2_rank_variants_completed", variant=best_name)
        provisional_report = SearchReport(
            created_at=datetime.now(UTC).isoformat(),
            symbol=self._base_config.symbol,
            readiness=dict(self._readiness),
            deribit_status=dict(self._deribit_status),
            split_contract=split_summary_json(self._split_spec),
            training_window_coverage=dict(self._training_window_coverage),
            training_input_contract=dict(self._training_input_contract),
            search_status=dict(search_status or {}),
            current=summaries["current"],
            paulwei_baseline=summaries["paulwei_baseline"],
            optuna_best=summaries.get("optuna_tuned"),
            rl_best=summaries.get("rl_tuned"),
            best_variant=best_name,
            best_score=evaluated_best_summary.robust_score,
            best_params=optuna_params
            if best_name == "optuna_tuned"
            else rl_params
            if best_name == "rl_tuned"
            else None,
            comparison_payload=comparison_payload,
            test_comparison_payload=test_payload,
            test_evaluated=evaluate_test,
            report_mode=report_mode if full else "fast",
            report_degraded_due_to_memory=report_degraded_due_to_memory if full else False,
            smoke_completed=smoke_completed,
            evaluation_role=evaluation_role,
            test_access_allowed=test_access_allowed,
            dev_gate_used_this_round=dev_gate_used_this_round,
            dev_gate_last_round=dev_gate_last_round,
            dev_gate_interval=dev_gate_interval,
            full_stage2_executed_this_round=full_stage2_executed_this_round,
        )
        handoff_summary = _assess_candidate_handoff_readiness(provisional_report)
        frozen_id = _candidate_frozen_id(provisional_report.best_variant, provisional_report.best_params)
        return SearchReport(
            created_at=provisional_report.created_at,
            symbol=provisional_report.symbol,
            readiness=provisional_report.readiness,
            deribit_status=provisional_report.deribit_status,
            split_contract=provisional_report.split_contract,
            training_window_coverage=provisional_report.training_window_coverage,
            training_input_contract=provisional_report.training_input_contract,
            search_status=provisional_report.search_status,
            current=provisional_report.current,
            paulwei_baseline=provisional_report.paulwei_baseline,
            optuna_best=provisional_report.optuna_best,
            rl_best=provisional_report.rl_best,
            best_variant=provisional_report.best_variant,
            best_score=provisional_report.best_score,
            best_params=provisional_report.best_params,
            comparison_payload=provisional_report.comparison_payload,
            test_comparison_payload=provisional_report.test_comparison_payload,
            test_evaluated=provisional_report.test_evaluated,
            report_mode=provisional_report.report_mode,
            report_degraded_due_to_memory=provisional_report.report_degraded_due_to_memory,
            smoke_completed=provisional_report.smoke_completed,
            signal_audit_summary=provisional_report.signal_audit_summary,
            evaluation_role=provisional_report.evaluation_role,
            test_access_allowed=provisional_report.test_access_allowed,
            candidate_ready_for_handoff=handoff_summary["ready"] if dev_gate_used_this_round else False,
            candidate_frozen_id=frozen_id if dev_gate_used_this_round and handoff_summary["ready"] else None,
            frozen_params=provisional_report.best_params
            if dev_gate_used_this_round and handoff_summary["ready"]
            else None,
            handoff_blockers=([] if handoff_summary["ready"] else handoff_summary["blockers"])
            if dev_gate_used_this_round
            else ["dev_gate_not_run_this_round"],
            dev_gate_summary=handoff_summary if dev_gate_used_this_round else None,
            dev_gate_used_this_round=dev_gate_used_this_round,
            dev_gate_last_round=dev_gate_last_round,
            dev_gate_interval=dev_gate_interval,
            full_stage2_executed_this_round=full_stage2_executed_this_round,
        )

    def _evaluate_variant(
        self,
        variant: VariantSpec,
        *,
        fast: bool,
        lightweight: bool = False,
        progress_callback: Any | None = None,
    ) -> CandidateSummary:
        cache_key = (self._variant_key(variant), fast, lightweight)
        cached = self._summary_cache.get(cache_key)
        if cached is not None:
            if progress_callback is not None:
                progress_callback("cache_hit", "summary_cache")
                progress_callback("replay_reused", "summary_cache")
                progress_callback(4, "cached")
            return cached
        if progress_callback is not None:
            progress_callback("cache_miss", "summary_cache")
        profile = self._compute_profile(fast=fast, lightweight=lightweight)
        windows = self._prepare_windows(fast=fast, lightweight=lightweight)
        wf_windows = self._fast_dev_wf_windows if fast else self._full_dev_wf_windows
        prepare_started_at = datetime.now(UTC).timestamp()
        prepare_window_timeout_s = float(profile["prepare_window_timeout_s"])
        prepare_variant_budget_s = float(profile["prepare_variant_budget_s"])

        def _prepare_budget_guard(window_label: str) -> None:
            if not lightweight:
                return
            elapsed_s = datetime.now(UTC).timestamp() - prepare_started_at
            if elapsed_s > prepare_variant_budget_s:
                raise PrepareVariantTimeoutError(
                    variant=variant.name,
                    window_label=window_label,
                    timeout_s=prepare_variant_budget_s,
                )

        try:
            dev_window = next(window for window in windows if window.label == "dev")
            if lightweight:
                if progress_callback is not None:
                    dev_key = (self._variant_key(variant), dev_window.start_ns, dev_window.end_ns)
                    progress_callback(
                        "cache_hit" if dev_key in self._backtest_cache else "cache_miss",
                        "window_snapshot:dev",
                    )
                dev_result = self._run_prepare_backtest_cached(
                    variant,
                    dev_window,
                    timeout_s=prepare_window_timeout_s,
                    replay_pool=self._get_validation_pool(workers=1),
                )
            else:
                dev_result = self._run_variant_backtest_cached(
                    variant,
                    start_ns=dev_window.start_ns,
                    end_ns=dev_window.end_ns,
                )
            if progress_callback is not None:
                progress_callback(1, "dev")
            weak_window = next(window for window in windows if window.label == "2023-08")
            _prepare_budget_guard(weak_window.label)
            if lightweight:
                if progress_callback is not None:
                    weak_key = (self._variant_key(variant), weak_window.start_ns, weak_window.end_ns)
                    progress_callback(
                        "cache_hit" if weak_key in self._backtest_cache else "cache_miss",
                        "window_snapshot:2023-08",
                    )
                weak_result = self._run_prepare_backtest_cached(
                    variant,
                    weak_window,
                    timeout_s=prepare_window_timeout_s,
                    replay_pool=self._get_validation_pool(workers=1),
                )
            else:
                weak_result = self._run_variant_backtest_cached(
                    variant,
                    start_ns=weak_window.start_ns,
                    end_ns=weak_window.end_ns,
                )
            if progress_callback is not None:
                progress_callback(2, "2023-08")
            worst_periods = [window for window in windows if window.label not in {"dev", "2023-08"}]
            worst_results: list[BacktestSnapshot] = []
            for window in worst_periods:
                _prepare_budget_guard(window.label)
                if lightweight:
                    if progress_callback is not None:
                        worst_key = (self._variant_key(variant), window.start_ns, window.end_ns)
                        progress_callback(
                            "cache_hit" if worst_key in self._backtest_cache else "cache_miss",
                            f"window_snapshot:{window.label}",
                        )
                    result = self._run_prepare_backtest_cached(
                        variant,
                        window,
                        timeout_s=prepare_window_timeout_s,
                        replay_pool=self._get_validation_pool(workers=1),
                    )
                    if progress_callback is not None:
                        progress_callback("worst_window", window.label)
                else:
                    result = self._run_variant_backtest_cached(
                        variant,
                        start_ns=window.start_ns,
                        end_ns=window.end_ns,
                    )
                worst_results.append(result)
            negative_worst_windows = sum(1 for result in worst_results if result.sharpe < 0)
            baseline = None
            if variant.name != "current":
                if progress_callback is not None and lightweight:
                    current_summary_key = (self._variant_key(self._current_variant), True, lightweight)
                    progress_callback(
                        "cache_hit" if current_summary_key in self._summary_cache else "cache_miss",
                        "baseline_summary",
                    )
                    progress_callback("baseline_summary_started", "lightweight")
                baseline = self._get_current_fast_summary(
                    lightweight=lightweight,
                    progress_callback=(
                        (
                            lambda step_completed, unit: progress_callback(
                                (
                                    f"baseline_{step_completed}"
                                    if isinstance(step_completed, str)
                                    else f"baseline_step_{step_completed}"
                                ),
                                unit,
                            )
                        )
                        if progress_callback is not None and lightweight
                        else None
                    ),
                )
                if progress_callback is not None and lightweight:
                    progress_callback("baseline_summary_completed", "lightweight")
        except PrepareVariantTimeoutError as exc:
            if variant.name in {"current", "paulwei_baseline"}:
                raise SearchStageTimeoutError(
                    f"stage2_prepare_variant_timeout: variant={variant.name} window={exc.window_label}"
                ) from exc
            summary = CandidateSummary(
                variant=variant.name,
                robust_score=-9.0,
                selection_split="dev",
                dev_sharpe=0.0,
                dev_pnl=0.0,
                dev_drawdown=1.0,
                avg_dev_sharpe=-5.0,
                avg_dev_ratio=0.0,
                worst_dev_sharpe=-5.0,
                weak_window_sharpe=-5.0,
                negative_dev_worst_windows=1,
                promotion_candidate=False,
                blocked_by="prepare_timeout",
                failed_gate_metrics=None,
                promotion_failure_reason="prepare_timeout",
            )
            if progress_callback is not None:
                progress_callback(4, "prepare_timeout")
            self._summary_cache[cache_key] = summary
            return summary
        except PrepareVariantRuntimeError as exc:
            if variant.name in {"current", "paulwei_baseline"}:
                raise RuntimeError(
                    f"stage2_prepare_variant_runtime_failure: variant={variant.name} window={exc.window_label} "
                    f"driver={exc.driver} detail={exc.detail}"
                ) from exc
            summary = CandidateSummary(
                variant=variant.name,
                robust_score=-9.0,
                selection_split="dev",
                dev_sharpe=0.0,
                dev_pnl=0.0,
                dev_drawdown=1.0,
                avg_dev_sharpe=-5.0,
                avg_dev_ratio=0.0,
                worst_dev_sharpe=-5.0,
                weak_window_sharpe=-5.0,
                negative_dev_worst_windows=1,
                promotion_candidate=False,
                blocked_by="prepare_runtime_failure",
                failed_gate_metrics=None,
                promotion_failure_reason="prepare_runtime_failure",
            )
            if progress_callback is not None:
                progress_callback(4, "prepare_runtime_failure")
            self._summary_cache[cache_key] = summary
            return summary
        if progress_callback is not None:
            progress_callback(3, "worst_windows_completed")
            if lightweight:
                progress_callback("summary_scoring_started", "lightweight")
        if lightweight:
            window_sharpes = [dev_result.sharpe, weak_result.sharpe, *(result.sharpe for result in worst_results)]
            wf_metrics = {
                "avg_test_sharpe": sum(window_sharpes) / len(window_sharpes) if window_sharpes else 0.0,
                "avg_oos_ratio": 1.0,
                "worst_test_sharpe": min(window_sharpes) if window_sharpes else 0.0,
            }
        else:
            if variant.name not in {"current", "paulwei_baseline"} and baseline is not None:
                if (
                    dev_result.sharpe < baseline.dev_sharpe - 0.25
                    or negative_worst_windows > baseline.negative_dev_worst_windows
                    or dev_result.max_drawdown > baseline.dev_drawdown * 1.05
                ):
                    summary = CandidateSummary(
                        variant=variant.name,
                        robust_score=-8.0,
                        selection_split="dev",
                        dev_sharpe=dev_result.sharpe,
                        dev_pnl=dev_result.net_pnl,
                        dev_drawdown=dev_result.max_drawdown,
                        avg_dev_sharpe=dev_result.sharpe,
                        avg_dev_ratio=0.0,
                        worst_dev_sharpe=min(
                            [dev_result.sharpe, weak_result.sharpe, *(result.sharpe for result in worst_results)]
                        ),
                        weak_window_sharpe=weak_result.sharpe,
                        negative_dev_worst_windows=negative_worst_windows,
                        promotion_candidate=False,
                        blocked_by="pre_validation_rejection",
                        failed_gate_metrics=None,
                        promotion_failure_reason="pre_validation_rejection",
                        sortino=getattr(dev_result, "sortino", 0.0),
                        profit_factor=getattr(dev_result, "profit_factor", 0.0),
                        turnover_per_day=getattr(dev_result, "turnover_per_day", 0.0),
                        fee_drag_pct=getattr(dev_result, "fee_drag_pct", 0.0),
                    )
                    if progress_callback is not None:
                        progress_callback(4, "pre_validation_rejection")
                    self._summary_cache[cache_key] = summary
                    return summary
            try:
                wf_metrics = self._run_validation_bundle_cached(
                    variant,
                    wf_windows,
                    progress_callback=progress_callback,
                    replay_timeout_s=float(profile["replay_timeout_s"]),
                    replay_pool=self._get_validation_pool(workers=int(profile["workers"])),
                )
            except ValidationBundleTimeoutError:
                if variant.name in {"current", "paulwei_baseline"}:
                    raise
                summary = CandidateSummary(
                    variant=variant.name,
                    robust_score=-10.0,
                    selection_split="dev",
                    dev_sharpe=dev_result.sharpe,
                    dev_pnl=dev_result.net_pnl,
                    dev_drawdown=dev_result.max_drawdown,
                    avg_dev_sharpe=-5.0,
                    avg_dev_ratio=0.0,
                    worst_dev_sharpe=-5.0,
                    weak_window_sharpe=weak_result.sharpe,
                    negative_dev_worst_windows=len(worst_results),
                    promotion_candidate=False,
                    blocked_by="validation_timeout",
                    failed_gate_metrics=None,
                    promotion_failure_reason="validation_timeout",
                )
                if progress_callback is not None:
                    progress_callback(4, "validation_timeout")
                self._summary_cache[cache_key] = summary
                return summary
            except ValidationBundleRuntimeError as exc:
                if variant.name in {"current", "paulwei_baseline"}:
                    raise RuntimeError(
                        f"validation_runtime_failure: variant={variant.name} window={exc.window_label} "
                        f"leg={exc.leg} driver={exc.driver} detail={exc.detail}"
                    ) from exc
                summary = CandidateSummary(
                    variant=variant.name,
                    robust_score=-10.0,
                    selection_split="dev",
                    dev_sharpe=dev_result.sharpe,
                    dev_pnl=dev_result.net_pnl,
                    dev_drawdown=dev_result.max_drawdown,
                    avg_dev_sharpe=-5.0,
                    avg_dev_ratio=0.0,
                    worst_dev_sharpe=-5.0,
                    weak_window_sharpe=weak_result.sharpe,
                    negative_dev_worst_windows=len(worst_results),
                    promotion_candidate=False,
                    blocked_by="validation_runtime_failure",
                    failed_gate_metrics=None,
                    promotion_failure_reason="validation_runtime_failure",
                )
                if progress_callback is not None:
                    progress_callback(4, "validation_runtime_failure")
                self._summary_cache[cache_key] = summary
                return summary
        try:
            _prepare_budget_guard("summary_scoring")
        except PrepareVariantTimeoutError as exc:
            if variant.name in {"current", "paulwei_baseline"}:
                raise SearchStageTimeoutError(
                    f"stage2_prepare_variant_timeout: variant={variant.name} window=summary_scoring"
                ) from exc
            summary = CandidateSummary(
                variant=variant.name,
                robust_score=-9.0,
                selection_split="dev",
                dev_sharpe=dev_result.sharpe,
                dev_pnl=dev_result.net_pnl,
                dev_drawdown=dev_result.max_drawdown,
                avg_dev_sharpe=dev_result.sharpe,
                avg_dev_ratio=0.0,
                worst_dev_sharpe=min(
                    [dev_result.sharpe, weak_result.sharpe, *(result.sharpe for result in worst_results)]
                ),
                weak_window_sharpe=weak_result.sharpe,
                negative_dev_worst_windows=negative_worst_windows,
                promotion_candidate=False,
                blocked_by="prepare_timeout",
                failed_gate_metrics=None,
                promotion_failure_reason="prepare_timeout",
            )
            if progress_callback is not None:
                progress_callback(4, "prepare_timeout")
            self._summary_cache[cache_key] = summary
            return summary
        score = _candidate_score(
            avg_test_sharpe=wf_metrics["avg_test_sharpe"],
            worst_test_sharpe=wf_metrics["worst_test_sharpe"],
            avg_oos_ratio=wf_metrics["avg_oos_ratio"],
            overall_sharpe=dev_result.sharpe,
            overall_pnl=dev_result.net_pnl,
            weak_window_sharpe=weak_result.sharpe,
            overall_drawdown=dev_result.max_drawdown,
            negative_worst_windows=negative_worst_windows,
            baseline=baseline,
            overall_sortino=getattr(dev_result, "sortino", 0.0),
            overall_profit_factor=getattr(dev_result, "profit_factor", 0.0),
            turnover_per_day=getattr(dev_result, "turnover_per_day", 0.0),
            fee_drag_pct=getattr(dev_result, "fee_drag_pct", 0.0),
        )
        gate_metrics, failure_reason = _promotion_diagnostics(
            avg_test_sharpe=wf_metrics["avg_test_sharpe"],
            worst_test_sharpe=wf_metrics["worst_test_sharpe"],
            overall_drawdown=dev_result.max_drawdown,
            negative_worst_windows=negative_worst_windows,
            baseline=baseline,
        )
        promote = all(gate_metrics.values())
        provisional = CandidateSummary(
            variant=variant.name,
            robust_score=round(score, 4),
            selection_split="dev",
            dev_sharpe=dev_result.sharpe,
            dev_pnl=dev_result.net_pnl,
            dev_drawdown=dev_result.max_drawdown,
            avg_dev_sharpe=wf_metrics["avg_test_sharpe"],
            avg_dev_ratio=wf_metrics["avg_oos_ratio"],
            worst_dev_sharpe=wf_metrics["worst_test_sharpe"],
            weak_window_sharpe=weak_result.sharpe,
            negative_dev_worst_windows=negative_worst_windows,
            promotion_candidate=promote,
            blocked_by=None,
            failed_gate_metrics=gate_metrics,
            promotion_failure_reason=failure_reason,
            sortino=getattr(dev_result, "sortino", 0.0),
            profit_factor=getattr(dev_result, "profit_factor", 0.0),
            turnover_per_day=getattr(dev_result, "turnover_per_day", 0.0),
            fee_drag_pct=getattr(dev_result, "fee_drag_pct", 0.0),
        )
        gap_to_current = _gap_to_candidate(provisional, baseline) if baseline is not None else None
        gap_to_public = _gap_to_public_reference(provisional, self._public_baseline_reference)
        weakest_window_penalty = round(
            max(0.0, (baseline.worst_dev_sharpe - provisional.worst_dev_sharpe) if baseline is not None else 0.0), 4
        )
        hostile_fragility_penalty = round(
            1.75 * weakest_window_penalty
            + 1.10
            * max(0, negative_worst_windows - (baseline.negative_dev_worst_windows if baseline is not None else 0)),
            4,
        )
        summary = CandidateSummary(
            **{
                **asdict(provisional),
                "baseline_gate_pass_current": _baseline_gate_current(provisional, baseline)
                if baseline is not None
                else True,
                "baseline_gate_pass_public": _baseline_gate_public(
                    provisional,
                    baseline if baseline is not None else provisional,
                    self._public_baseline_reference,
                ),
                "gap_to_current": gap_to_current,
                "gap_to_paulwei_public": gap_to_public,
                "candidate_score": round(score, 4),
                "weakest_window_penalty": weakest_window_penalty,
                "hostile_fragility_penalty": hostile_fragility_penalty,
            }
        )
        if progress_callback is not None:
            if lightweight:
                progress_callback("summary_scoring_completed", "lightweight")
            progress_callback(4, "lightweight_summary" if lightweight else "validation_bundle")
        if lightweight and variant.name == "current":
            self._current_fast = summary
            if self._paulwei_fast_lightweight is None:
                self._paulwei_fast_lightweight = summary
        self._summary_cache[cache_key] = summary
        return summary

    def _evaluate_test_acceptance(self, variant: VariantSpec, summary: CandidateSummary) -> CandidateSummary:
        test_window = next(window for window in self._test_windows if window.label == "test")
        test_result = self._run_variant_backtest_cached(
            variant, start_ns=test_window.start_ns, end_ns=test_window.end_ns
        )
        worst_test_windows = [window for window in self._test_windows if window.label != "test"]
        worst_failures = 0
        for window in worst_test_windows:
            result = self._run_variant_backtest_cached(variant, start_ns=window.start_ns, end_ns=window.end_ns)
            if result.sharpe < 0:
                worst_failures += 1
        return CandidateSummary(
            **{
                **asdict(summary),
                "test_sharpe": test_result.sharpe,
                "test_drawdown": test_result.max_drawdown,
                "test_adversarial_failures": worst_failures,
                "test_evaluated": True,
                "promotion_candidate": summary.promotion_candidate and test_result.sharpe >= 0 and worst_failures == 0,
                "promotion_failure_reason": (
                    summary.promotion_failure_reason
                    or ("test_sharpe" if test_result.sharpe < 0 else "test_adversarial" if worst_failures else None)
                ),
            }
        )

    def _run_variant_backtest_cached(
        self,
        variant: VariantSpec,
        *,
        start_ns: int | None,
        end_ns: int | None,
    ) -> BacktestSnapshot:
        key = (self._variant_key(variant), start_ns, end_ns)
        cached = self._backtest_cache.get(key)
        if cached is not None:
            return cached
        _, result = _run_variant_backtest(self._base_config, variant, start_ns=start_ns, end_ns=end_ns)
        snapshot = BacktestSnapshot(
            sharpe=result.metrics.sharpe,
            net_pnl=result.metrics.net_pnl,
            max_drawdown=result.metrics.max_drawdown,
            win_rate=result.metrics.win_rate,
            sortino=getattr(result.metrics, "sortino", 0.0),
            profit_factor=getattr(result.metrics, "profit_factor", 0.0),
            turnover_per_day=getattr(result.metrics, "turnover_per_day", 0.0),
            fee_drag_pct=getattr(result.metrics, "fee_drag_pct", 0.0),
        )
        self._backtest_cache[key] = snapshot
        return snapshot

    def _run_validation_bundle_cached(
        self,
        variant: VariantSpec,
        windows: list[WindowSpec],
        *,
        progress_callback: Any | None = None,
        replay_timeout_s: float | None = None,
        replay_pool: ProcessPoolExecutor | None = None,
    ) -> dict[str, float]:
        key = (self._variant_key(variant), tuple(window.label for window in windows), bool(replay_timeout_s))
        cached = self._validation_cache.get(key)
        if cached is not None:
            if progress_callback is not None:
                progress_callback("cache_hit", "validation_bundle")
                progress_callback("replay_reused", "validation_bundle")
            return cached
        if progress_callback is not None:
            progress_callback("cache_miss", "validation_bundle")
        try:
            result = _run_validation_bundle(
                self._base_config,
                variant,
                windows,
                run_backtest=self._run_variant_backtest_cached,
                progress_callback=progress_callback,
                replay_timeout_s=replay_timeout_s,
                replay_pool=replay_pool,
            )
        except ValidationBundleTimeoutError:
            self._reset_validation_pool()
            raise
        self._validation_cache[key] = result
        return result

    @staticmethod
    def _variant_key(variant: VariantSpec) -> str:
        payload = json.dumps(asdict(variant), sort_keys=True, default=str)
        return sha1(payload.encode("utf-8")).hexdigest()

    def _get_paulwei_fast_summary(
        self,
        *,
        lightweight: bool = False,
        progress_callback: Any | None = None,
    ) -> CandidateSummary:
        if lightweight:
            if self._paulwei_fast_lightweight is None:
                self._paulwei_fast_lightweight = self._evaluate_variant(
                    self._paulwei_variant,
                    fast=True,
                    lightweight=True,
                    progress_callback=progress_callback,
                )
            return self._paulwei_fast_lightweight
        if self._paulwei_fast_full is None:
            self._paulwei_fast_full = self._evaluate_variant(
                self._paulwei_variant,
                fast=True,
                lightweight=False,
                progress_callback=progress_callback,
            )
        return self._paulwei_fast_full

    def _get_current_fast_summary(
        self,
        *,
        lightweight: bool = False,
        progress_callback: Any | None = None,
    ) -> CandidateSummary:
        if lightweight:
            if self._current_fast is None:
                self._current_fast = self._evaluate_variant(
                    self._current_variant,
                    fast=True,
                    lightweight=True,
                    progress_callback=progress_callback,
                )
            elif progress_callback is not None:
                progress_callback("cache_hit", "current_fast_summary")
                progress_callback("replay_reused", "current_fast_summary")
            if self._paulwei_fast_lightweight is None:
                self._paulwei_fast_lightweight = self._current_fast
            return self._current_fast
        if self._current_fast is not None and not lightweight:
            if progress_callback is not None:
                progress_callback("cache_hit", "current_fast_summary")
                progress_callback("replay_reused", "current_fast_summary")
            return self._current_fast
        if self._current_fast is None:
            self._current_fast = self._evaluate_variant(
                self._current_variant,
                fast=True,
                lightweight=False,
                progress_callback=progress_callback,
            )
        return self._current_fast

    @staticmethod
    def _comparison_report_mode(
        *,
        variant_count: int,
        window_count: int,
        walk_forward_window_count: int,
    ) -> tuple[str, bool]:
        estimated_backtest_count = variant_count * (window_count + (2 * walk_forward_window_count))
        if estimated_backtest_count >= 20:
            return "summary_only", True
        return "full_diagnostics", False


def _run_validation_bundle(
    base_config: HistoricalBacktestConfig,
    variant: VariantSpec,
    windows: list[WindowSpec],
    *,
    run_backtest: Any = None,
    progress_callback: Any | None = None,
    replay_timeout_s: float | None = None,
    replay_pool: ProcessPoolExecutor | None = None,
) -> dict[str, float]:
    if not windows:
        return {"avg_test_sharpe": 0.0, "avg_oos_ratio": 0.0, "worst_test_sharpe": 0.0}
    if run_backtest is None:

        def run(v: VariantSpec, start_ns: int | None, end_ns: int | None) -> BacktestSnapshot:
            _, result = _run_variant_backtest(base_config, v, start_ns=start_ns, end_ns=end_ns)
            return BacktestSnapshot(
                sharpe=result.metrics.sharpe,
                net_pnl=result.metrics.net_pnl,
                max_drawdown=result.metrics.max_drawdown,
                win_rate=result.metrics.win_rate,
                sortino=getattr(result.metrics, "sortino", 0.0),
                profit_factor=getattr(result.metrics, "profit_factor", 0.0),
                turnover_per_day=getattr(result.metrics, "turnover_per_day", 0.0),
                fee_drag_pct=getattr(result.metrics, "fee_drag_pct", 0.0),
            )
    else:
        run = run_backtest
    test_sharpes: list[float] = []
    ratios: list[float] = []
    for window in windows:
        train_end = window.start_ns
        assert train_end is not None
        train_start = _shift_months(train_end, -24)
        if progress_callback is not None:
            progress_callback("validation_train_started", window.label)
        try:
            if replay_timeout_s is not None:
                train_result = _run_variant_backtest_with_timeout(
                    base_config,
                    variant,
                    start_ns=train_start,
                    end_ns=train_end,
                    timeout_s=replay_timeout_s,
                    pool=replay_pool,
                )
            else:
                train_result = run(variant, start_ns=train_start, end_ns=train_end)
        except TimeoutError as exc:
            raise ValidationBundleTimeoutError(
                variant=variant.name,
                window_label=window.label,
                leg="train",
                timeout_s=replay_timeout_s or 0.0,
            ) from exc
        except Exception as exc:
            raise ValidationBundleRuntimeError(
                variant=variant.name,
                window_label=window.label,
                leg="train",
                driver=_runtime_failure_driver(exc),
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        if progress_callback is not None:
            progress_callback("validation_train_completed", window.label)
            progress_callback("validation_test_started", window.label)
        try:
            if replay_timeout_s is not None:
                test_result = _run_variant_backtest_with_timeout(
                    base_config,
                    variant,
                    start_ns=window.start_ns,
                    end_ns=window.end_ns,
                    timeout_s=replay_timeout_s,
                    pool=replay_pool,
                )
            else:
                test_result = run(variant, start_ns=window.start_ns, end_ns=window.end_ns)
        except TimeoutError as exc:
            raise ValidationBundleTimeoutError(
                variant=variant.name,
                window_label=window.label,
                leg="test",
                timeout_s=replay_timeout_s or 0.0,
            ) from exc
        except Exception as exc:
            raise ValidationBundleRuntimeError(
                variant=variant.name,
                window_label=window.label,
                leg="test",
                driver=_runtime_failure_driver(exc),
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        if progress_callback is not None:
            progress_callback("validation_test_completed", window.label)
        ratio = test_result.sharpe / train_result.sharpe if abs(train_result.sharpe) > 0.01 else 0.0
        test_sharpes.append(test_result.sharpe)
        ratios.append(ratio)
    return {
        "avg_test_sharpe": round(sum(test_sharpes) / len(test_sharpes), 4),
        "avg_oos_ratio": round(sum(ratios) / len(ratios), 4),
        "worst_test_sharpe": round(min(test_sharpes), 4),
    }


def _gap_to_candidate(candidate: CandidateSummary, baseline: CandidateSummary) -> dict[str, float]:
    return {
        "dev_sharpe_gap": round(candidate.dev_sharpe - baseline.dev_sharpe, 4),
        "worst_dev_sharpe_gap": round(candidate.worst_dev_sharpe - baseline.worst_dev_sharpe, 4),
        "dev_drawdown_gap": round(candidate.dev_drawdown - baseline.dev_drawdown, 6),
        "hostile_2023_08_gap": round(candidate.weak_window_sharpe - baseline.weak_window_sharpe, 4),
        "hostile_2024_11_gap": round(candidate.worst_dev_sharpe - baseline.worst_dev_sharpe, 4),
        "turnover_gap": round(candidate.turnover_per_day - baseline.turnover_per_day, 2),
        "fee_drag_gap": round(candidate.fee_drag_pct - baseline.fee_drag_pct, 6),
    }


def _gap_to_public_reference(
    candidate: CandidateSummary, public_reference: dict[str, Any] | None
) -> dict[str, float] | None:
    if not isinstance(public_reference, dict):
        return None
    dev_metrics = public_reference.get("dev_metrics", {})
    hostile_windows = public_reference.get("hostile_windows", {})
    hostile_sharpes = [
        _safe_metric(hostile_windows.get("2023-08"), "sharpe"),
        _safe_metric(hostile_windows.get("2024-11 DD"), "sharpe"),
    ]
    hostile_bundle = (
        sum(hostile_sharpes) / len([item for item in hostile_sharpes if item != 0.0]) if any(hostile_sharpes) else 0.0
    )
    candidate_hostile_bundle = (candidate.weak_window_sharpe + candidate.worst_dev_sharpe) / 2.0
    return {
        "dev_sharpe_gap": round(candidate.dev_sharpe - _safe_metric(dev_metrics, "sharpe"), 4),
        "hostile_bundle_gap": round(candidate_hostile_bundle - hostile_bundle, 4),
        "dev_drawdown_gap": round(candidate.dev_drawdown - _safe_metric(dev_metrics, "max_drawdown"), 6),
        "turnover_gap": round(candidate.turnover_per_day - _safe_metric(dev_metrics, "turnover_per_day"), 2),
        "fee_drag_gap": round(candidate.fee_drag_pct - _safe_metric(dev_metrics, "fee_drag_pct"), 6),
    }


def _baseline_gate_current(
    candidate: CandidateSummary, baseline: CandidateSummary, *, drawdown_hard_multiplier: float = 1.05
) -> bool:
    return (
        candidate.worst_dev_sharpe >= baseline.worst_dev_sharpe
        and candidate.negative_dev_worst_windows <= baseline.negative_dev_worst_windows
        and candidate.dev_drawdown <= baseline.dev_drawdown * drawdown_hard_multiplier
    )


def _baseline_gate_public(
    candidate: CandidateSummary,
    current: CandidateSummary,
    public_reference: dict[str, Any] | None,
    *,
    fee_drag_tolerance: float = 1.15,
    turnover_tolerance: float = 1.20,
) -> bool:
    if not isinstance(public_reference, dict):
        return False
    dev_metrics = public_reference.get("dev_metrics", {})
    hostile_regressed = bool(public_reference.get("hostile_window_regressed"))
    return (
        not hostile_regressed
        and candidate.fee_drag_pct
        <= max(current.fee_drag_pct * fee_drag_tolerance, _safe_metric(dev_metrics, "fee_drag_pct"))
        and candidate.turnover_per_day
        <= max(
            current.turnover_per_day * turnover_tolerance,
            _safe_metric(dev_metrics, "turnover_per_day"),
        )
        and candidate.dev_drawdown <= _safe_metric(dev_metrics, "max_drawdown")
    )


def _normalized_delta(delta: float, *, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return max(-1.0, min(1.0, delta / scale))


def _candidate_score(
    *,
    avg_test_sharpe: float,
    worst_test_sharpe: float,
    avg_oos_ratio: float,
    overall_sharpe: float,
    overall_pnl: float,
    weak_window_sharpe: float,
    overall_drawdown: float,
    negative_worst_windows: int,
    baseline: CandidateSummary | None,
    overall_sortino: float = 0.0,
    overall_profit_factor: float = 0.0,
    turnover_per_day: float = 0.0,
    fee_drag_pct: float = 0.0,
) -> float:
    capped_oos_ratio = max(-1.0, min(2.0, avg_oos_ratio))
    if baseline is None:
        return round(
            0.35 * overall_sharpe
            + 0.20 * worst_test_sharpe
            + 0.10 * weak_window_sharpe
            + 0.10 * overall_sortino
            + 0.10 * overall_profit_factor
            + 0.05 * capped_oos_ratio
            + 0.01 * (overall_pnl / 1_000_000.0)
            - 2.5 * overall_drawdown
            - 1.25 * negative_worst_windows,
            4,
        )

    hostile_window_sharpe_bundle = (weak_window_sharpe + worst_test_sharpe) / 2.0
    baseline_hostile_bundle = (baseline.weak_window_sharpe + baseline.worst_dev_sharpe) / 2.0
    drawdown_penalty = max(0.0, overall_drawdown - baseline.dev_drawdown)
    turnover_penalty = max(0.0, turnover_per_day - baseline.turnover_per_day)
    fee_drag_penalty = max(0.0, fee_drag_pct - baseline.fee_drag_pct)
    weakest_window_penalty = max(0.0, baseline.worst_dev_sharpe - worst_test_sharpe)
    hostile_fragility_penalty = 1.75 * weakest_window_penalty + 1.10 * max(
        0, negative_worst_windows - baseline.negative_dev_worst_windows
    )

    score = (
        0.35 * _normalized_delta(overall_sharpe - baseline.dev_sharpe, scale=max(1.0, abs(baseline.dev_sharpe)))
        + 0.22
        * _normalized_delta(
            worst_test_sharpe - baseline.worst_dev_sharpe, scale=max(1.0, abs(baseline.worst_dev_sharpe))
        )
        + 0.20
        * _normalized_delta(
            hostile_window_sharpe_bundle - baseline_hostile_bundle,
            scale=max(1.0, abs(baseline_hostile_bundle)),
        )
        + 0.08
        * _normalized_delta(
            weak_window_sharpe - baseline.weak_window_sharpe,
            scale=max(1.0, abs(baseline.weak_window_sharpe)),
        )
        + 0.10 * _normalized_delta(overall_sortino - baseline.sortino, scale=max(1.0, abs(baseline.sortino)))
        + 0.10
        * _normalized_delta(
            overall_profit_factor - baseline.profit_factor,
            scale=max(1.0, abs(baseline.profit_factor)),
        )
        + 0.05 * capped_oos_ratio
        + 0.01 * (overall_pnl / 1_000_000.0)
        - 0.20 * _normalized_delta(drawdown_penalty, scale=max(0.01, baseline.dev_drawdown))
        - 0.10 * _normalized_delta(turnover_penalty, scale=max(1.0, baseline.turnover_per_day))
        - 0.10 * _normalized_delta(fee_drag_penalty, scale=max(0.001, baseline.fee_drag_pct))
        - hostile_fragility_penalty
    )
    return round(score, 4)


def _promotion_diagnostics(
    *,
    avg_test_sharpe: float,
    worst_test_sharpe: float,
    overall_drawdown: float,
    negative_worst_windows: int,
    baseline: CandidateSummary | None,
) -> tuple[dict[str, bool], str | None]:
    if baseline is None:
        return {
            "avg_test_sharpe": True,
            "worst_test_sharpe": True,
            "overall_drawdown": True,
            "drawdown_preferred_band": True,
            "negative_worst_windows": True,
        }, None
    gate_metrics = {
        "avg_test_sharpe": avg_test_sharpe >= baseline.avg_dev_sharpe,
        "worst_test_sharpe": worst_test_sharpe >= baseline.worst_dev_sharpe,
        "overall_drawdown": overall_drawdown <= baseline.dev_drawdown * 1.05,
        "drawdown_preferred_band": overall_drawdown <= baseline.dev_drawdown * 1.05,
        "negative_worst_windows": negative_worst_windows <= baseline.negative_dev_worst_windows,
    }
    for key in ("worst_test_sharpe", "overall_drawdown", "negative_worst_windows", "avg_test_sharpe"):
        if not gate_metrics[key]:
            return gate_metrics, key
    return gate_metrics, None


def _stage1_fail_fast_reason(
    candidate: CandidateSummary,
    *,
    baseline: CandidateSummary,
    current: CandidateSummary,
    relaxed_fee_probe: bool = False,
) -> str | None:
    fee_limits = RELAXED_DEV_FEE_STABILITY_LIMITS if relaxed_fee_probe else DEFAULT_DEV_FEE_STABILITY_LIMITS
    hostile_anchor = baseline if baseline.worst_dev_sharpe >= current.worst_dev_sharpe else current
    turnover_anchor = max(baseline.turnover_per_day, current.turnover_per_day)
    fee_drag_anchor = max(baseline.fee_drag_pct, current.fee_drag_pct)
    if candidate.weak_window_sharpe < hostile_anchor.weak_window_sharpe - 0.25:
        return "weak_window_2023_08_fail_fast"
    if candidate.worst_dev_sharpe < hostile_anchor.worst_dev_sharpe - 0.25:
        return "worst_dev_sharpe_fail_fast"
    if candidate.fee_drag_pct > fee_limits.dev_fee_drag_fail_fast_cap:
        return "dev_fee_drag_fail_fast"
    if candidate.fee_drag_pct > max(fee_limits.dev_fee_drag_fail_fast_cap, fee_drag_anchor * 1.35):
        return "fee_drag_fail_fast"
    if candidate.turnover_per_day > max(turnover_anchor * 1.35, turnover_anchor + 250.0):
        return "turnover_fail_fast"
    if (
        candidate.profit_factor < 1.0
        and candidate.turnover_per_day > max(turnover_anchor * 1.15, turnover_anchor + 100.0)
        and candidate.fee_drag_pct > max(0.015, fee_drag_anchor * 1.15)
    ):
        return "profit_factor_churn_fail_fast"
    return None


def _shift_months(ts_ns: int, months: int) -> int:
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC)
    month_index = (dt.month - 1) + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1e9)


def _build_comparison_payload(
    base_config: HistoricalBacktestConfig,
    *,
    variants: list[VariantSpec],
    windows: list[WindowSpec],
    walk_forward_windows: list[WindowSpec],
    split_spec: DatasetSplitSpec,
    comparison_split: str,
    report_mode: str = "full_diagnostics",
    comparison_compute_mode: str = "full",
    window_workers: int = 1,
    validation_workers: int = 1,
    validation_leg_timeout_s: float | None = None,
    validation_variant_budget_s: float | None = None,
    status_callback: Any | None = None,
) -> dict[str, Any]:
    from zenquant.research.comparison import compare_variants

    return compare_variants(
        base_config,
        variants=variants,
        windows=windows,
        walk_forward_windows=walk_forward_windows,
        split_spec=split_spec,
        comparison_split=comparison_split,
        report_mode=report_mode,
        comparison_compute_mode=comparison_compute_mode,
        window_workers=window_workers,
        validation_workers=validation_workers,
        validation_leg_timeout_s=validation_leg_timeout_s,
        validation_variant_budget_s=validation_variant_budget_s,
        status_callback=status_callback,
    )


def _train_candidate_summary_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(candidate.get("metrics", {}))
    score = float(metrics.get("regime_objective_score", metrics.get("score", metrics.get("sharpe", 0.0))) or 0.0)
    sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
    pnl = float(metrics.get("net_pnl", metrics.get("pnl", 0.0)) or 0.0)
    drawdown = float(metrics.get("max_drawdown", 0.0) or 0.0)
    weak_sharpe = float(metrics.get("weak_window_sharpe", sharpe) or sharpe)
    return asdict(
        CandidateSummary(
            variant="optuna_candidate",
            robust_score=round(score, 4),
            selection_split="train",
            dev_sharpe=sharpe,
            dev_pnl=pnl,
            dev_drawdown=drawdown,
            avg_dev_sharpe=sharpe,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=sharpe,
            weak_window_sharpe=weak_sharpe,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
            blocked_by=None,
            failed_gate_metrics=None,
            promotion_failure_reason=None,
        )
    )


def run_optuna_search(
    evaluator: FrameworkEvaluator,
    *,
    n_trials: int = 12,
    status_callback: Any | None = None,
    search_execution_timeframe: Timeframe | None = None,
    search_context_timeframe: Timeframe | None = None,
    trial_timeout_s: float | None = None,
    stage_total_timeout_s: float | None = None,
    ranking_timeout_s: float | None = None,
    ranking_top_n: int | None = None,
    ranking_candidate_timeout_s: float | None = None,
    seed_trials: list[dict[str, Any]] | None = None,
    stage1_parallel_mode: str = "serial",
    stage1_max_workers: int | None = None,
    stage1_target_inflight: int | None = None,
    stage1_batch_size: int = 1,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    try:
        import optuna  # noqa: F401
    except ImportError:
        return (
            None,
            [],
            {
                "train_candidate_diagnostics": {
                    "raw_trial_count": 0,
                    "raw_nonzero_trade_trials": 0,
                    "eligible_trade_count_trials": 0,
                    "max_train_trade_count": 0,
                    "max_train_sharpe": 0.0,
                    "max_hostile_proxy_score": 0.0,
                    "hostile_proxy_penalty_applied": 0.0,
                    "min_execution_fragility_score": 0.0,
                    "min_inventory_path_dependence_score": 0.0,
                },
                "empty_shortlist_reason": "all_negative_primary_metric",
                "no_tuned_candidate_reason": "all_negative_primary_metric",
            },
        )
    effective_execution_timeframe = search_execution_timeframe or evaluator._base_config.execution_timeframe
    if evaluator.search_space_profile.get("binance_factor_weight_repair_search", False):
        resolved_seed_trials = binance_factor_weight_repair_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("paulwei_dual_phase_spot_context_relaxed_fee_search", False):
        resolved_seed_trials = paulwei_dual_phase_spot_context_relaxed_fee_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("paulwei_dual_phase_spot_context_search", False):
        resolved_seed_trials = paulwei_dual_phase_spot_context_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("paulwei_dual_phase_fee_repair_search", False):
        resolved_seed_trials = paulwei_dual_phase_fee_repair_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("paulwei_dual_phase_search", False):
        resolved_seed_trials = paulwei_dual_phase_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("paulwei_balanced_tranche_search", False):
        resolved_seed_trials = paulwei_balanced_tranche_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("ralph_intent_reduce_cadence_search", False):
        resolved_seed_trials = ralph_intent_reduce_cadence_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("ralph_execution_fee_cooldown_search", False):
        resolved_seed_trials = ralph_execution_fee_cooldown_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("ralph_protective_reduce_fee_repair_search", False):
        resolved_seed_trials = ralph_protective_reduce_fee_repair_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("ralph_protective_reduce_search", False):
        resolved_seed_trials = ralph_protective_reduce_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("intent_churn_ablation_search", False):
        resolved_seed_trials = intent_churn_ablation_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("intent_churn_repair_search", False):
        resolved_seed_trials = intent_churn_repair_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("execution_inventory_rethink_search", False):
        resolved_seed_trials = execution_inventory_rethink_seed_trials()[:n_trials]
    elif evaluator.search_space_profile.get("baseline_anchored_search", False):
        resolved_seed_trials = [
            seed for seed in baseline_anchor_seed_trials() if not hostile_constraint_violations(seed)
        ][:n_trials]
    else:
        resolved_seed_trials = []
    if seed_trials:
        resolved_seed_trials.extend(dict(trial) for trial in seed_trials)
    resolved_seed_trials = _project_seed_trials_to_search_grid(
        resolved_seed_trials,
        search_grid=evaluator.search_param_grid,
    )[:n_trials]
    relaxed_fee_probe = bool(evaluator.search_space_profile.get("research_relaxed_fee_probe", False))
    strict_train_fee_stop = (
        any(bool(evaluator.search_space_profile.get(key, False)) for key in STRICT_TRAIN_FEE_STOP_PROFILE_FLAGS)
        and not relaxed_fee_probe
    )
    strict_train_truth_search = any(
        bool(evaluator.search_space_profile.get(key, False)) for key in STRICT_TRAIN_TRUTH_PROFILE_FLAGS
    )
    worker_config = CampaignConfig(
        data_root=evaluator._base_config.root_dir,
        symbol=evaluator._base_config.symbol,
        split_spec=evaluator._split_spec,
        execution_timeframe=effective_execution_timeframe,
        bias_timeframe=evaluator._base_config.bias_timeframe,
        direction_timeframe=evaluator._base_config.direction_timeframe,
        context_timeframe=search_context_timeframe,
        search_space=SearchSpace(strategy_params=evaluator.search_param_grid),
        sweep_mode="bayesian",
        n_trials=n_trials,
        seed_trials=resolved_seed_trials,
        wf_splits=[],
        top_n_for_validation=ranking_top_n if ranking_top_n is not None else min(5, max(1, n_trials)),
        robustness_seeds=[],
        campaign_label="framework_stage1",
        output_dir=Path("results/archive/dev_runs/auto_research_stage1"),
        verbose=False,
        status_callback=status_callback,
        trial_timeout_s=trial_timeout_s,
        stage_total_timeout_s=stage_total_timeout_s,
        stage_wall_clock_budget_s=720.0 if effective_execution_timeframe == Timeframe.TF_5M else None,
        min_completed_trials=min(n_trials, 24) if effective_execution_timeframe == Timeframe.TF_5M else 0,
        stage1_parallel_mode=stage1_parallel_mode,
        stage1_max_workers=stage1_max_workers,
        stage1_target_inflight=stage1_target_inflight,
        stage1_batch_size=stage1_batch_size,
        research_compute_mode="lightweight_train_search",
        enable_early_stop=not strict_train_truth_search,
        trial_runtime_budget_s=None
        if strict_train_truth_search
        else 90.0
        if effective_execution_timeframe == Timeframe.TF_5M
        else 60.0,
        early_stop_fee_drag_buffer=1.25 if strict_train_fee_stop else 2.0,
    )
    sweep = run_search_stage(worker_config)
    ranking_started_at = datetime.now(UTC).timestamp()
    ranked: list[dict[str, Any]] = []
    total_candidates = len(sweep.top_n)
    historical_extension_contract = evaluator.training_input_contract.get("historical_extension_candidate_contract", {})
    stage1_fail_fast_enabled = any(
        "hostile_proxy_score" in dict(candidate.get("metrics", {}))
        or "weak_window_sharpe" in dict(candidate.get("metrics", {}))
        for candidate in sweep.top_n
    )
    current_lightweight: CandidateSummary | None = None
    paulwei_lightweight: CandidateSummary | None = None
    hostile_proxy_penalized_count = 0
    fail_fast_rejection_count = 0
    fail_fast_reason_counts: dict[str, int] = {}
    rejected_candidate_diagnostics: list[dict[str, Any]] = []
    for index, candidate in enumerate(sweep.top_n, start=1):
        if ranking_timeout_s is not None and (datetime.now(UTC).timestamp() - ranking_started_at) > ranking_timeout_s:
            raise SearchStageTimeoutError(
                f"stage1_dev_validation_timeout: ranked {index - 1}/{total_candidates} candidates"
            )
        if status_callback is not None:
            status_callback(
                event="ranking_candidate_started",
                current_candidate=index,
                total_candidates=total_candidates,
                completed_candidates=index - 1,
                runtime_s=datetime.now(UTC).timestamp() - ranking_started_at,
            )
        params = dict(candidate["params"])
        if ranking_candidate_timeout_s is not None and ranking_candidate_timeout_s <= 0:
            raise SearchStageTimeoutError(
                f"stage1_dev_validation_no_progress_timeout: candidate {index}/{total_candidates}"
            )
        combined_metrics, spot_context_metrics, combined_train_score = _combine_train_leg_metrics(
            dict(candidate.get("metrics", {})),
            _spot_context_replay_audit(evaluator, params, historical_extension_contract),
        )
        temporal_robustness_audit = _temporal_robustness_audit(evaluator, params)
        temporal_score_adjustment = (
            float(temporal_robustness_audit.get("score_adjustment", 0.0) or 0.0)
            if temporal_robustness_audit is not None
            else 0.0
        )
        regime_objective = score_regime_objective_candidate_generation(
            combined_metrics,
            baseline_metrics=dict(candidate.get("baseline_train_metrics", {}) or {}),
            relaxed_fee_probe=relaxed_fee_probe,
        )
        regime_score = float(regime_objective["regime_objective_score"])
        stage1_score = min(regime_score, combined_train_score) + temporal_score_adjustment
        combined_metrics["regime_objective_raw_score"] = regime_score
        combined_metrics["temporal_robustness_score_adjustment"] = temporal_score_adjustment
        combined_metrics["regime_objective_score"] = stage1_score
        combined_metrics["stage1_score"] = stage1_score
        train_hostile_proxy_score = float(combined_metrics.get("hostile_proxy_score", 0.0) or 0.0)
        if train_hostile_proxy_score < -3.0:
            hostile_proxy_penalized_count += 1
        candidate_payload = dict(candidate)
        candidate_payload["metrics"] = combined_metrics
        train_summary = CandidateSummary(**_train_candidate_summary_payload(candidate_payload))
        if not regime_objective["allow_dev_diagnostic_review"]:
            fail_fast_reason = "regime_objective_train_bucket_fail_fast"
            lightweight_dev_summary = None
        elif temporal_robustness_audit is not None and temporal_robustness_audit.get("primary_blocker"):
            fail_fast_reason = str(temporal_robustness_audit["primary_blocker"])
            lightweight_dev_summary = None
        elif stage1_fail_fast_enabled:
            if current_lightweight is None:
                current_lightweight = evaluator._get_current_fast_summary(lightweight=True)
            if paulwei_lightweight is None:
                paulwei_lightweight = evaluator._get_paulwei_fast_summary(lightweight=True)
            if paulwei_lightweight is not None and current_lightweight is not None:
                dev_screen = evaluator._evaluate_variant(
                    evaluator.candidate_variant(params, name="optuna_candidate", label="Optuna Candidate"),
                    fast=True,
                    lightweight=True,
                )
                fail_fast_reason = _stage1_fail_fast_reason(
                    dev_screen,
                    baseline=paulwei_lightweight,
                    current=current_lightweight,
                    relaxed_fee_probe=relaxed_fee_probe,
                )
                lightweight_dev_summary = asdict(dev_screen)
            else:
                fail_fast_reason = None
                lightweight_dev_summary = None
        else:
            fail_fast_reason = None
            lightweight_dev_summary = None
        if fail_fast_reason is not None:
            bull_window_attribution = None
            temporal_primary_blocker = (
                str(temporal_robustness_audit.get("primary_blocker") or "")
                if temporal_robustness_audit is not None
                else ""
            )
            temporal_blockers = (
                [str(item) for item in temporal_robustness_audit.get("blocked_by", [])]
                if temporal_robustness_audit is not None
                else []
            )
            attribution_trigger_reasons = [
                reason
                for reason in [str(fail_fast_reason), temporal_primary_blocker, *temporal_blockers]
                if reason.startswith(("paulwei_2020_", "paulwei_2022_", "temporal_", "late_dev_"))
            ]
            if attribution_trigger_reasons:
                try:
                    bull_window_attribution = _bull_window_direction_exposure_attribution(
                        evaluator,
                        params,
                        historical_extension_contract,
                    )
                except Exception as exc:
                    bull_window_attribution = {
                        "scope": "train_dev_only_bull_window_direction_exposure_attribution",
                        "promotion_allowed": False,
                        "test_evaluated": False,
                        "blocked_by": "bull_window_direction_exposure_attribution_failed",
                        "error": str(exc)[:240],
                    }
            masked_temporal_primary_blocker = (
                temporal_primary_blocker
                if temporal_primary_blocker and temporal_primary_blocker != str(fail_fast_reason)
                else None
            )
            fail_fast_rejection_count += 1
            fail_fast_reason_counts[fail_fast_reason] = int(fail_fast_reason_counts.get(fail_fast_reason, 0) or 0) + 1
            rejected_candidate_diagnostics.append(
                {
                    "params": params,
                    "train_metrics": combined_metrics,
                    "spot_context_metrics": spot_context_metrics,
                    "temporal_robustness_audit": temporal_robustness_audit,
                    "bull_window_direction_exposure_attribution": bull_window_attribution,
                    "direction_window_exposure_attribution": bull_window_attribution,
                    "masked_temporal_primary_blocker": masked_temporal_primary_blocker,
                    "masked_temporal_blockers": temporal_blockers if masked_temporal_primary_blocker else [],
                    "direction_attribution_trigger_reasons": list(dict.fromkeys(attribution_trigger_reasons)),
                    "summary": asdict(train_summary),
                    "stage1_fail_fast_reason": fail_fast_reason,
                    "regime_objective": regime_objective,
                    "combined_train_diagnostics": {
                        "combined_train_score": combined_train_score,
                        "mixed_era_declared": bool(historical_extension_contract.get("mixed_era_declared")),
                        "historical_extension_mode": historical_extension_contract.get("historical_extension_mode"),
                        "spot_context_objective_enabled": spot_context_metrics is not None,
                        "temporal_robustness_enabled": temporal_robustness_audit is not None,
                    },
                    "lightweight_dev_summary": lightweight_dev_summary,
                }
            )
            if status_callback is not None:
                status_callback(
                    event="ranking_candidate_completed",
                    current_candidate=index,
                    total_candidates=total_candidates,
                    completed_candidates=index,
                    best_value_so_far=max((item["summary"]["robust_score"] for item in ranked), default=None),
                    best_params_so_far=max(ranked, key=lambda item: item["summary"]["robust_score"])["params"]
                    if ranked
                    else None,
                    runtime_s=datetime.now(UTC).timestamp() - ranking_started_at,
                )
            continue
        ranked.append(
            {
                "params": params,
                "train_metrics": combined_metrics,
                "spot_train_leg_metrics": None,
                "spot_context_metrics": spot_context_metrics,
                "temporal_robustness_audit": temporal_robustness_audit,
                "futures_train_leg_metrics": dict(candidate.get("metrics", {})),
                "combined_train_diagnostics": {
                    "combined_train_score": combined_train_score,
                    "temporal_robustness_score_adjustment": temporal_score_adjustment,
                    "regime_objective": regime_objective,
                    "mixed_era_declared": bool(historical_extension_contract.get("mixed_era_declared")),
                    "historical_extension_mode": historical_extension_contract.get("historical_extension_mode"),
                    "spot_context_objective_enabled": spot_context_metrics is not None,
                    "temporal_robustness_enabled": temporal_robustness_audit is not None,
                    "stage1_fail_fast_reason": None,
                },
                "summary": asdict(train_summary),
                "lightweight_dev_summary": lightweight_dev_summary,
                "candidate_source": "auto_research_train_only",
                "split_contract_version": evaluator._split_spec.version,
            }
        )
        if status_callback is not None:
            status_callback(
                event="ranking_candidate_completed",
                current_candidate=index,
                total_candidates=total_candidates,
                completed_candidates=index,
                best_value_so_far=max((item["summary"]["robust_score"] for item in ranked), default=None),
                best_params_so_far=max(ranked, key=lambda item: item["summary"]["robust_score"])["params"]
                if ranked
                else None,
                runtime_s=datetime.now(UTC).timestamp() - ranking_started_at,
            )
    ranked.sort(key=lambda item: item["summary"]["robust_score"], reverse=True)
    best = ranked[0] if ranked else None
    diagnostics = {
        "train_candidate_diagnostics": dict(getattr(sweep, "train_candidate_diagnostics", {}) or {}),
        "empty_shortlist_reason": getattr(sweep, "empty_shortlist_reason", None),
        "no_tuned_candidate_reason": (getattr(sweep, "train_candidate_diagnostics", {}) or {}).get(
            "no_tuned_candidate_reason"
        ),
        "stage1_budget_mode": getattr(sweep, "budget_mode", "trial_count_primary"),
        "stage1_stop_reason": getattr(sweep, "stop_reason", "trial_budget_reached"),
        "historical_extension_train_enabled": bool(historical_extension_contract.get("layered_train_enabled")),
        "historical_extension_mode": historical_extension_contract.get("historical_extension_mode"),
        "mixed_era_declared": bool(historical_extension_contract.get("mixed_era_declared")),
        "train_truth_legs": list(historical_extension_contract.get("train_truth_legs") or []),
        "spot_train_leg_blocked_by": list(historical_extension_contract.get("spot_train_leg_blocked_by") or []),
        "full_unified_truth": bool(historical_extension_contract.get("full_unified_truth")),
    }
    weak_window_fail_fast_count = int(fail_fast_reason_counts.get("weak_window_2023_08_fail_fast", 0) or 0)
    diagnostics["train_candidate_diagnostics"]["stage1_fail_fast_rejection_count"] = fail_fast_rejection_count
    diagnostics["train_candidate_diagnostics"]["stage1_fail_fast_reasons"] = fail_fast_reason_counts
    diagnostics["train_candidate_diagnostics"]["rejected_candidate_diagnostics"] = rejected_candidate_diagnostics[:5]
    diagnostics["train_candidate_diagnostics"]["hostile_proxy_penalized_count"] = hostile_proxy_penalized_count
    diagnostics["train_candidate_diagnostics"]["regime_objective_enabled"] = True
    diagnostics["train_candidate_diagnostics"]["regime_objective_rejection_count"] = int(
        fail_fast_reason_counts.get("regime_objective_train_bucket_fail_fast", 0) or 0
    )
    diagnostics["train_candidate_diagnostics"]["weak_window_2023_08_fail_fast_count"] = weak_window_fail_fast_count
    diagnostics["train_candidate_diagnostics"]["weak_window_2023_08_fail_fast_triggered"] = (
        weak_window_fail_fast_count > 0
    )
    diagnostics["train_candidate_diagnostics"]["candidate_existence_recovered"] = bool(best is not None)
    temporal_ranked = [item for item in ranked if item.get("temporal_robustness_audit")]
    temporal_rejection_count = sum(
        int(count)
        for reason, count in fail_fast_reason_counts.items()
        if str(reason).startswith(("temporal_", "late_dev_", "paulwei_2020_", "paulwei_2022_", "paulwei_temporal_"))
    )
    diagnostics["train_candidate_diagnostics"]["temporal_robustness_enabled"] = bool(temporal_ranked) or any(
        "temporal_robustness_audit" in item for item in rejected_candidate_diagnostics
    )
    diagnostics["train_candidate_diagnostics"]["temporal_robustness_rejection_count"] = temporal_rejection_count
    diagnostics["train_candidate_diagnostics"]["best_temporal_robustness_audit"] = (
        temporal_ranked[0].get("temporal_robustness_audit") if temporal_ranked else None
    )
    bull_window_attributions = [
        item.get("bull_window_direction_exposure_attribution")
        for item in rejected_candidate_diagnostics
        if item.get("bull_window_direction_exposure_attribution")
    ]
    bear_window_attributions = [
        item for item in bull_window_attributions if "train_temporal_2022" in (item.get("windows") or {})
    ]
    masked_temporal_blocker_count = sum(
        1 for item in rejected_candidate_diagnostics if item.get("masked_temporal_primary_blocker")
    )
    diagnostics["train_candidate_diagnostics"]["bull_window_direction_exposure_attribution_enabled"] = bool(
        bull_window_attributions
    )
    diagnostics["train_candidate_diagnostics"]["best_bull_window_direction_exposure_attribution"] = (
        bull_window_attributions[0] if bull_window_attributions else None
    )
    diagnostics["train_candidate_diagnostics"]["direction_window_exposure_attribution_enabled"] = bool(
        bull_window_attributions
    )
    diagnostics["train_candidate_diagnostics"]["best_direction_window_exposure_attribution"] = (
        bull_window_attributions[0] if bull_window_attributions else None
    )
    diagnostics["train_candidate_diagnostics"]["bear_window_direction_exposure_attribution_enabled"] = bool(
        bear_window_attributions
    )
    diagnostics["train_candidate_diagnostics"]["masked_temporal_blocker_attribution_count"] = (
        masked_temporal_blocker_count
    )
    spot_context_ranked = [item for item in ranked if item.get("spot_context_metrics")]
    diagnostics["spot_era_context_replay_audit"] = {
        "enabled": bool(spot_context_ranked),
        "scope": "formal_auxiliary_train_regime_direction_profit_run",
        "formal_train_truth": False,
        "formal_auxiliary_train_leg": True,
        "execution_truth": False,
        "promotion_allowed": False,
        "test_evaluated": False,
        "allowed_training_objectives": list(PAULWEI_SPOT_AUXILIARY_TRAIN_OBJECTIVES),
        "candidate_count": len(spot_context_ranked),
        "best_candidate": {
            "params": spot_context_ranked[0]["params"],
            "context_score_adjustment": spot_context_ranked[0]["spot_context_metrics"].get("context_score_adjustment"),
            "windows": spot_context_ranked[0]["spot_context_metrics"].get("windows"),
        }
        if spot_context_ranked
        else None,
    }
    if best is None and diagnostics["empty_shortlist_reason"] is None and fail_fast_rejection_count > 0:
        diagnostics["empty_shortlist_reason"] = "stage1_fail_fast_rejected"
        diagnostics["no_tuned_candidate_reason"] = "stage1_fail_fast_rejected"
        diagnostics["train_candidate_diagnostics"]["candidate_existence_recovered"] = False
    return (best["params"] if best else None), ranked, diagnostics


def build_markdown_report(report: SearchReport) -> str:
    readiness = dict(report.readiness or {})
    readiness.setdefault("data_ready", False)
    readiness.setdefault("promotion_ready", False)
    readiness.setdefault("blocked_by", "not_promotion_ready")
    readiness.setdefault("detail", "")
    full_training_assessment = (
        _assess_full_training_readiness(
            report,
            smoke_completed=report.smoke_completed,
        )
        if report.evaluation_role == "acceptance"
        else {
            "decision": "acceptance_required" if report.candidate_ready_for_handoff else "defer_handoff",
            "ready": False,
            "blockers": list(report.handoff_blockers or []),
            "recommended_budget": None,
        }
    )
    post_smoke_scenario = (
        {
            "scenario": "scenario_development_handoff_ready"
            if report.candidate_ready_for_handoff
            else "scenario_development_train_iteration",
            "category": "development_handoff" if report.candidate_ready_for_handoff else "train_iteration",
            "should_open_full_training": False,
            "next_action": "handoff_frozen_candidate_to_acceptance"
            if report.candidate_ready_for_handoff
            else "continue_train_search_until_dev_gate",
            "blockers": list(report.handoff_blockers or []),
        }
        if report.evaluation_role == "development"
        else _classify_post_smoke_scenario(
            report,
            full_training_assessment=full_training_assessment,
        )
    )
    framework_diagnosis = _build_framework_diagnosis(report)
    lines = [
        "# Framework Optimization",
        "",
        f"- created_at: `{report.created_at}`",
        f"- symbol: `{report.symbol}`",
        f"- best_variant: `{report.best_variant}`",
        f"- best_score: `{report.best_score}`",
        f"- evaluation_role: `{report.evaluation_role}`",
        f"- test_evaluated: `{report.test_evaluated}`",
        f"- candidate_ready_for_handoff: `{report.candidate_ready_for_handoff}`",
        f"- candidate_frozen_id: `{report.candidate_frozen_id}`",
        "",
        "## Readiness",
        "",
        f"- liquidation_data_ready: `{readiness['data_ready']}`",
        f"- liquidation_promotion_ready: `{readiness['promotion_ready']}`",
        f"- blocked_by: `{readiness['blocked_by']}`",
        f"- detail: `{readiness['detail']}`",
        f"- deribit_enabled: `{report.deribit_status['enabled']}`",
        f"- deribit_observation_ready: `{report.deribit_status['observation_ready']}`",
        f"- deribit_research_ready: `{report.deribit_status['research_ready']}`",
        f"- split_contract_version: `{report.split_contract['version']}`",
        "",
        "## Training Inputs",
        "",
        f"- primary_truth_source: `{report.training_input_contract.get('primary_truth_source')}`",
        f"- historical_extension_candidate_contract: `{report.training_input_contract.get('historical_extension_candidate_contract')}`",
        f"- execution_framework: `{report.training_input_contract.get('execution_framework')}`",
        f"- state_feature_sources: `{report.training_input_contract.get('state_feature_sources')}`",
        f"- signal_stack_contract: `{report.training_input_contract.get('signal_stack_contract')}`",
        f"- signal_independence_contract: `{report.training_input_contract.get('signal_independence_contract')}`",
        f"- etf_flow_source: `{report.search_status.get('etf_flow_source')}`",
        f"- etf_flow_source_confidence: `{report.search_status.get('etf_flow_source_confidence')}`",
        f"- etf_flow_last_success_at: `{report.search_status.get('etf_flow_last_success_at')}`",
        f"- search_space_mode: `{report.search_status.get('search_space_mode')}`",
        f"- baseline_anchored_search: `{report.search_status.get('baseline_anchored_search')}`",
        f"- baseline_anchor_variant: `{report.search_status.get('baseline_anchor_variant')}`",
        "",
        "## Search Status",
        "",
        f"- optuna_available: `{report.search_status.get('optuna_available')}`",
        f"- optuna_trials_requested: `{report.search_status.get('optuna_trials_requested')}`",
        f"- rl_episodes_requested: `{report.search_status.get('rl_episodes_requested')}`",
        f"- optuna_candidate_found: `{report.search_status.get('optuna_candidate_found')}`",
        f"- rl_candidate_found: `{report.search_status.get('rl_candidate_found')}`",
        f"- liquidation_in_search: `{report.search_status.get('liquidation_in_search')}`",
        f"- liquidation_blocked_by: `{report.search_status.get('liquidation_blocked_by')}`",
        f"- search_space_profile: `{report.search_status.get('search_space_profile')}`",
        f"- train_candidate_diagnostics: `{report.search_status.get('train_candidate_diagnostics')}`",
        f"- empty_shortlist_reason: `{report.search_status.get('empty_shortlist_reason')}`",
        f"- no_tuned_candidate_reason: `{report.search_status.get('no_tuned_candidate_reason')}`",
        f"- historical_extension_train_enabled: `{report.search_status.get('historical_extension_train_enabled')}`",
        f"- historical_extension_mode: `{report.search_status.get('historical_extension_mode')}`",
        f"- mixed_era_declared: `{report.search_status.get('mixed_era_declared')}`",
        f"- train_truth_legs: `{report.search_status.get('train_truth_legs')}`",
        f"- spot_train_leg_blocked_by: `{report.search_status.get('spot_train_leg_blocked_by')}`",
        f"- dev_gate_used_this_round: `{report.dev_gate_used_this_round}`",
        f"- dev_gate_last_round: `{report.dev_gate_last_round}`",
        f"- dev_gate_interval: `{report.dev_gate_interval}`",
        "",
    ]
    lines.extend(
        [
            "## Development Handoff",
            "",
            f"- decision: `{full_training_assessment['decision']}`",
            f"- ready: `{report.candidate_ready_for_handoff}`",
            f"- blockers: `{full_training_assessment['blockers']}`",
            f"- frozen_params: `{report.frozen_params}`",
            "",
            "## Post-Smoke Scenario",
            "",
            f"- scenario: `{post_smoke_scenario['scenario']}`",
            f"- category: `{post_smoke_scenario['category']}`",
            f"- should_open_full_training: `{post_smoke_scenario['should_open_full_training']}`",
            f"- next_action: `{post_smoke_scenario['next_action']}`",
            "",
            "## Framework Diagnosis",
            "",
            f"- current_state: `{framework_diagnosis['current_state']}`",
            f"- dev_primary_improved: `{framework_diagnosis['dev_primary_improved']}`",
            f"- hostile_window_regressed: `{framework_diagnosis['hostile_window_regressed']}`",
            f"- hostile_regression_driver: `{framework_diagnosis['hostile_regression_driver']}`",
            f"- future_architecture_direction: `{framework_diagnosis['future_architecture_direction']}`",
            f"- recommended_next_steps: `{framework_diagnosis['recommended_next_steps']}`",
            "",
        ]
    )
    if report.signal_audit_summary:
        lines.extend(
            [
                "## Signal Audit",
                "",
                f"- active_signal_count: `{report.signal_audit_summary.get('active_signal_count')}`",
                f"- redundant_pair_count: `{report.signal_audit_summary.get('redundant_pair_count')}`",
                f"- same_role_redundant_pair_count: `{report.signal_audit_summary.get('same_role_redundant_pair_count')}`",
                f"- cross_role_overlap_pair_count: `{report.signal_audit_summary.get('cross_role_overlap_pair_count')}`",
                f"- recommended_actions: `{report.signal_audit_summary.get('recommended_actions')}`",
                f"- top_direction_weights: `{report.signal_audit_summary.get('top_direction_weights')}`",
                f"- json_path: `{report.signal_audit_summary.get('json_path')}`",
                f"- active_stack_json_path: `{report.signal_audit_summary.get('active_stack_json_path')}`",
                f"- candidate_inventory_json_path: `{report.signal_audit_summary.get('candidate_inventory_json_path')}`",
                "",
            ]
        )
    for item in [report.current, report.paulwei_baseline, report.optuna_best, report.rl_best]:
        if item is None:
            continue
        lines.extend(
            [
                f"## {item.variant}",
                "",
                f"- robust_score: `{item.robust_score}`",
                f"- selection_split: `{item.selection_split}`",
                f"- dev_sharpe: `{item.dev_sharpe}`",
                f"- avg_dev_sharpe: `{item.avg_dev_sharpe}`",
                f"- avg_dev_ratio: `{item.avg_dev_ratio}`",
                f"- weak_window_sharpe: `{item.weak_window_sharpe}`",
                f"- dev_drawdown: `{item.dev_drawdown}`",
                f"- test_sharpe: `{item.test_sharpe}`",
                f"- test_evaluated: `{item.test_evaluated}`",
                f"- promotion_candidate: `{item.promotion_candidate}`",
                f"- promotion_failure_reason: `{item.promotion_failure_reason}`",
                f"- failed_gate_metrics: `{item.failed_gate_metrics}`",
                f"- blocked_by: `{item.blocked_by}`",
                "",
            ]
        )
    return "\n".join(lines)


def write_search_report(path: Path, report: SearchReport) -> None:
    payload = asdict(report)
    search_profile = report.search_status.get("search_space_profile") or {}
    search_space_mode = report.search_status.get("search_space_mode", search_profile.get("search_space_mode"))
    baseline_anchored_search = report.search_status.get(
        "baseline_anchored_search",
        search_profile.get("baseline_anchored_search"),
    )
    baseline_anchor_variant = report.search_status.get(
        "baseline_anchor_variant",
        search_profile.get("baseline_anchor_variant"),
    )
    full_training_assessment = (
        _assess_full_training_readiness(
            report,
            smoke_completed=report.smoke_completed,
        )
        if report.evaluation_role == "acceptance"
        else {
            "ready": False,
            "decision": "acceptance_required" if report.candidate_ready_for_handoff else "defer_handoff",
            "blockers": list(report.handoff_blockers or []),
            "warnings": [],
            "gates": (report.dev_gate_summary or {}).get("gates", {}),
            "best_tuned_variant": report.best_variant if report.candidate_ready_for_handoff else None,
        }
    )
    post_smoke_scenario = (
        {
            "scenario": "scenario_development_handoff_ready"
            if report.candidate_ready_for_handoff
            else "scenario_development_train_iteration",
            "category": "development_handoff" if report.candidate_ready_for_handoff else "train_iteration",
            "should_open_full_training": False,
            "next_action": "handoff_frozen_candidate_to_acceptance"
            if report.candidate_ready_for_handoff
            else "continue_train_search_until_dev_gate",
            "blockers": list(report.handoff_blockers or []),
        }
        if report.evaluation_role == "development"
        else _classify_post_smoke_scenario(
            report,
            full_training_assessment=full_training_assessment,
        )
    )
    framework_diagnosis = _build_framework_diagnosis(report)
    hostile_regression = _hostile_regression_summary(report)
    no_tuned_candidate_reason = _no_tuned_candidate_reason(report)
    best_summary = next(
        (
            item
            for item in [report.current, report.paulwei_baseline, report.optuna_best, report.rl_best]
            if item is not None and item.variant == report.best_variant
        ),
        None,
    )
    payload["data_sufficiency"] = {
        "deribit_status": payload["deribit_status"],
        "liquidation_readiness": payload["readiness"],
        "training_window_coverage": payload["training_window_coverage"],
        "training_input_contract": payload["training_input_contract"],
        "split_contract": payload["split_contract"],
    }
    payload["signal_combination_audit"] = report.signal_audit_summary
    payload["optuna_tuned"] = asdict(report.optuna_best) if report.optuna_best else None
    payload["rl_tuned"] = asdict(report.rl_best) if report.rl_best else None
    payload["full_training_assessment"] = full_training_assessment
    payload["post_smoke_scenario"] = post_smoke_scenario
    payload["framework_diagnosis"] = framework_diagnosis
    payload["dev_primary_improved"] = hostile_regression["dev_primary_improved"]
    payload["hostile_window_regressed"] = hostile_regression["hostile_window_regressed"]
    payload["hostile_regression_driver"] = hostile_regression["hostile_regression_driver"]
    payload["no_tuned_candidate_reason"] = no_tuned_candidate_reason
    payload["candidate_existence_recovered"] = bool(
        report.optuna_best is not None
        and no_tuned_candidate_reason not in {"all_negative_primary_metric", "stage1_fail_fast_rejected"}
        and not hostile_regression["hostile_window_regressed"]
    )
    payload["full_training_allowed"] = (
        full_training_assessment["ready"] if report.evaluation_role == "acceptance" else False
    )
    payload["full_training_decision"] = full_training_assessment["decision"]
    payload["full_training_blockers"] = full_training_assessment["blockers"]
    payload["search_space_mode"] = search_space_mode
    payload["baseline_anchored_search"] = baseline_anchored_search
    payload["baseline_anchor_variant"] = baseline_anchor_variant
    payload["stage1_execution_mode"] = report.search_status.get("stage1_execution_mode")
    payload["train_candidate_diagnostics"] = report.search_status.get("train_candidate_diagnostics")
    payload["spot_era_context_replay_audit"] = report.search_status.get("spot_era_context_replay_audit")
    payload["empty_shortlist_reason"] = report.search_status.get("empty_shortlist_reason")
    payload["historical_extension_train_enabled"] = report.training_input_contract.get(
        "historical_extension_train_enabled"
    )
    payload["historical_extension_mode"] = report.training_input_contract.get("historical_extension_mode")
    payload["mixed_era_declared"] = report.training_input_contract.get("mixed_era_declared")
    payload["train_truth_legs"] = report.training_input_contract.get("train_truth_legs")
    payload["full_unified_truth"] = report.training_input_contract.get(
        "historical_extension_candidate_contract", {}
    ).get("full_unified_truth")
    payload["evaluation_role"] = report.evaluation_role
    payload["test_access_allowed"] = report.test_access_allowed
    payload["candidate_ready_for_handoff"] = report.candidate_ready_for_handoff
    payload["candidate_frozen_id"] = report.candidate_frozen_id
    payload["frozen_params"] = report.frozen_params
    payload["handoff_blockers"] = report.handoff_blockers
    payload["dev_gate_summary"] = report.dev_gate_summary
    payload["dev_gate_used_this_round"] = report.dev_gate_used_this_round
    payload["dev_gate_last_round"] = report.dev_gate_last_round
    payload["dev_gate_interval"] = report.dev_gate_interval
    payload["full_stage2_executed_this_round"] = report.full_stage2_executed_this_round
    payload["campaign_execution_contract"] = {
        "stage1_execution_mode": report.search_status.get("stage1_execution_mode"),
        "authoritative_verdict_execution_mode": report.search_status.get("authoritative_verdict_execution_mode"),
        "search_space_mode": search_space_mode,
        "baseline_anchored_search": baseline_anchored_search,
        "baseline_anchor_variant": baseline_anchor_variant,
        "macro_state_sources": report.search_status.get("macro_state_sources", []),
        "state_data_confidence": report.search_status.get("state_data_confidence"),
        "etf_flow_source": report.search_status.get("etf_flow_source"),
        "etf_flow_source_confidence": report.search_status.get("etf_flow_source_confidence"),
        "etf_flow_last_success_at": report.search_status.get("etf_flow_last_success_at"),
        "train_candidate_diagnostics": report.search_status.get("train_candidate_diagnostics"),
        "spot_era_context_replay_audit": report.search_status.get("spot_era_context_replay_audit"),
        "empty_shortlist_reason": report.search_status.get("empty_shortlist_reason"),
        "historical_extension_train_enabled": report.search_status.get("historical_extension_train_enabled"),
        "historical_extension_mode": report.search_status.get("historical_extension_mode"),
        "mixed_era_declared": report.search_status.get("mixed_era_declared"),
        "train_truth_legs": report.search_status.get("train_truth_legs"),
        "spot_train_leg_blocked_by": report.search_status.get("spot_train_leg_blocked_by"),
        "full_unified_truth": report.search_status.get("full_unified_truth"),
    }
    payload["historical_leaderboard"] = [
        asdict(report.current),
        asdict(report.paulwei_baseline),
        asdict(report.optuna_best) if report.optuna_best else None,
        asdict(report.rl_best) if report.rl_best else None,
    ]
    comparison_to_current = None
    comparison_to_paulwei = None
    comparison_to_public = None
    if best_summary is not None:
        comparison_to_current = {
            "variant": best_summary.variant,
            "drawdown_within_hard_cap": best_summary.dev_drawdown <= report.current.dev_drawdown * 1.05,
            "drawdown_within_preferred_band": best_summary.dev_drawdown <= report.current.dev_drawdown * 1.05,
            "worst_dev_sharpe_not_worse": best_summary.worst_dev_sharpe >= report.current.worst_dev_sharpe,
            "negative_worst_windows_not_worse": (
                best_summary.negative_dev_worst_windows <= report.current.negative_dev_worst_windows
            ),
        }
        comparison_to_paulwei = {
            "variant": best_summary.variant,
            "drawdown_within_hard_cap": best_summary.dev_drawdown <= report.paulwei_baseline.dev_drawdown + 0.03,
            "drawdown_within_preferred_band": (
                best_summary.dev_drawdown <= report.paulwei_baseline.dev_drawdown + 0.03
            ),
            "worst_dev_sharpe_not_worse": best_summary.worst_dev_sharpe >= report.paulwei_baseline.worst_dev_sharpe,
            "negative_worst_windows_not_worse": (
                best_summary.negative_dev_worst_windows <= report.paulwei_baseline.negative_dev_worst_windows
            ),
        }
        if best_summary.gap_to_paulwei_public is not None:
            comparison_to_public = {
                "variant": best_summary.variant,
                "baseline_gate_pass_public": best_summary.baseline_gate_pass_public,
                "gap_to_paulwei_public": best_summary.gap_to_paulwei_public,
            }
    verdict_kind = "blocked_by_data_readiness" if payload["readiness"]["blocked_by"] else "promote_candidate_found"
    if best_summary is not None and not best_summary.promotion_candidate:
        verdict_kind = (
            "no_promote_candidate_found_param_side"
            if best_summary.promotion_failure_reason == "avg_test_sharpe"
            else "no_promote_candidate_found_architecture_side"
        )
    payload["final_verdict"] = {
        "best_variant": report.best_variant,
        "candidate_ready_for_test": report.candidate_ready_for_handoff,
        "candidate_ready_for_handoff": report.candidate_ready_for_handoff,
        "candidate_frozen_id": report.candidate_frozen_id,
        "promote_to_paper_live_candidate": bool(best_summary.promotion_candidate)
        if best_summary and report.test_evaluated
        else False,
        "test_evaluated": report.test_evaluated,
        "evaluation_role": report.evaluation_role,
        "report_mode": report.report_mode,
        "report_degraded_due_to_memory": report.report_degraded_due_to_memory,
        "blocked_by": payload["readiness"]["blocked_by"],
        "promotion_failure_reason": best_summary.promotion_failure_reason if best_summary else None,
        "failed_gate_metrics": best_summary.failed_gate_metrics if best_summary else None,
        "split_contract_version": payload["split_contract"]["version"],
        "train_window": payload["split_contract"]["windows"]["train"],
        "dev_window": payload["split_contract"]["windows"]["dev"],
        "test_window": payload["split_contract"]["windows"]["test"],
        "liquidation_in_search": payload["search_status"].get("liquidation_in_search"),
        "search_space_profile": payload["search_status"].get("search_space_profile"),
        "search_space_mode": search_space_mode,
        "baseline_anchored_search": baseline_anchored_search,
        "baseline_anchor_variant": baseline_anchor_variant,
        "stage1_execution_mode": payload["search_status"].get("stage1_execution_mode"),
        "authoritative_verdict_execution_mode": payload["search_status"].get("authoritative_verdict_execution_mode"),
        "macro_state_sources": payload["search_status"].get("macro_state_sources", []),
        "state_data_confidence": payload["search_status"].get("state_data_confidence"),
        "etf_flow_source": payload["search_status"].get("etf_flow_source"),
        "etf_flow_source_confidence": payload["search_status"].get("etf_flow_source_confidence"),
        "etf_flow_last_success_at": payload["search_status"].get("etf_flow_last_success_at"),
        "train_candidate_diagnostics": payload["search_status"].get("train_candidate_diagnostics"),
        "spot_era_context_replay_audit": payload["search_status"].get("spot_era_context_replay_audit"),
        "empty_shortlist_reason": payload["search_status"].get("empty_shortlist_reason"),
        "historical_extension_train_enabled": payload["training_input_contract"].get(
            "historical_extension_train_enabled"
        ),
        "historical_extension_mode": payload["training_input_contract"].get("historical_extension_mode"),
        "mixed_era_declared": payload["training_input_contract"].get("mixed_era_declared"),
        "train_truth_legs": payload["training_input_contract"].get("train_truth_legs"),
        "full_unified_truth": payload["training_input_contract"]
        .get("historical_extension_candidate_contract", {})
        .get("full_unified_truth"),
        "spot_train_leg_blocked_by": payload["training_input_contract"]
        .get("historical_extension_candidate_contract", {})
        .get("spot_train_leg_blocked_by"),
        "no_tuned_candidate_reason": no_tuned_candidate_reason,
        "verdict_kind": verdict_kind,
        "candidate_generation_failed": report.optuna_best is None and report.rl_best is None,
        "candidate_generation_failure_driver": no_tuned_candidate_reason
        if report.optuna_best is None and report.rl_best is None
        else None,
        "comparison_to_current": comparison_to_current,
        "comparison_to_paulwei_baseline": comparison_to_paulwei,
        "comparison_to_paulwei_public": comparison_to_public,
        "dev_primary_improved": hostile_regression["dev_primary_improved"],
        "hostile_window_regressed": hostile_regression["hostile_window_regressed"],
        "hostile_regression_driver": hostile_regression["hostile_regression_driver"],
        "baseline_gate_pass_current": best_summary.baseline_gate_pass_current if best_summary else False,
        "baseline_gate_pass_public": best_summary.baseline_gate_pass_public if best_summary else False,
        "gap_to_current": best_summary.gap_to_current if best_summary else None,
        "gap_to_paulwei_public": best_summary.gap_to_paulwei_public if best_summary else None,
        "candidate_score": best_summary.candidate_score if best_summary else None,
        "weakest_window_penalty": best_summary.weakest_window_penalty if best_summary else None,
        "hostile_fragility_penalty": best_summary.hostile_fragility_penalty if best_summary else None,
        "next_data_priority": (
            "liquidation_history"
            if not payload["readiness"]["promotion_ready"]
            else "deribit_history"
            if not payload["deribit_status"]["research_ready"]
            else "none"
        ),
        "full_training_ready": full_training_assessment["ready"] if report.evaluation_role == "acceptance" else False,
        "full_training_decision": full_training_assessment["decision"],
        "full_training_blockers": full_training_assessment["blockers"],
        "post_smoke_scenario": post_smoke_scenario["scenario"],
        "post_smoke_next_action": post_smoke_scenario["next_action"],
    }
    rl_only_candidate = report.optuna_best is None and report.rl_best is not None
    payload["promotion_path_status"] = "open" if payload["candidate_existence_recovered"] else "blocked"
    payload["failure_class"] = (
        "hostile_window_quality_failure"
        if not payload["candidate_existence_recovered"]
        and (hostile_regression["hostile_regression_driver"] == "weak_window_2023_08" or rl_only_candidate)
        else None
    )
    payload["primary_driver"] = (
        hostile_regression["hostile_regression_driver"] if payload["failure_class"] is not None else None
    )
    payload["loop_stop_reason"] = (
        "search_space_exhausted_without_candidate"
        if payload["promotion_path_status"] == "blocked"
        and payload["failure_class"] == "hostile_window_quality_failure"
        else None
    )
    payload["final_verdict"]["promotion_path_status"] = payload["promotion_path_status"]
    payload["final_verdict"]["failure_class"] = payload["failure_class"]
    payload["final_verdict"]["primary_driver"] = payload["primary_driver"]
    payload["final_verdict"]["candidate_existence_recovered"] = payload["candidate_existence_recovered"]
    payload["final_verdict"]["loop_stop_reason"] = payload["loop_stop_reason"]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
