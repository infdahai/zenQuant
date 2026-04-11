"""Framework-level historical optimizer with shared anti-overfit scoring."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from typing import Any

from zenquant.data.deribit_status import collect_deribit_status
from zenquant.data.liquidation_pipeline import liquidation_readiness
from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktestConfig
from zenquant.historical.storage import read_parquet_rows
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
    paulwei_risk_baseline,
)
from zenquant.research.framework_params import (
    baseline_anchor_seed_trials,
    build_execution_config,
    build_indicator_config,
    build_replay_toggles,
    build_risk_config,
    build_signal_weight_overrides,
    build_strategy_config,
    param_grid_for_mode,
    search_space_profile,
)
from zenquant.research.splits import DatasetSplitSpec, default_btc_split_spec, split_summary_json


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
        where_sql=(
            "close_time_ns >= ? AND close_time_ns < ?"
            if start_ns is not None and end_ns is not None
            else ""
        ),
        params=((start_ns, end_ns) if start_ns is not None and end_ns is not None else ()),
        order_by="close_time_ns ASC",
    )
    return bool(rows)


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


@dataclass(slots=True, frozen=True)
class BacktestSnapshot:
    sharpe: float
    net_pnl: float
    max_drawdown: float
    win_rate: float


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


def _best_tuned_candidate(report: SearchReport) -> CandidateSummary | None:
    tuned = [candidate for candidate in (report.optuna_best, report.rl_best) if candidate is not None]
    if not tuned:
        return None
    return max(tuned, key=lambda candidate: candidate.robust_score)


def _assess_full_training_readiness(
    report: SearchReport,
    *,
    smoke_completed: bool,
) -> dict[str, Any]:
    execution_contract = report.training_input_contract.get("execution_framework", {})
    state_sources = report.training_input_contract.get("state_feature_sources", {})
    search_profile = report.search_status.get("search_space_profile") or {}
    direction_policy = search_profile.get("direction_weight_policy", {})
    baseline = report.paulwei_baseline
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
    if report.search_status.get("liquidation_in_search") and not report.readiness.get("promotion_ready"):
        data_failures.append("liquidation_search_enabled_before_promotion_ready")

    model_failures: list[str] = []
    if tuned is None:
        model_failures.append("no_tuned_candidate_from_smoke")
    else:
        if tuned.worst_dev_sharpe < baseline.worst_dev_sharpe:
            model_failures.append("worst_dev_sharpe_worse_than_paulwei")
        if tuned.negative_dev_worst_windows > baseline.negative_dev_worst_windows:
            model_failures.append("negative_worst_windows_increased")
        if tuned.dev_drawdown > baseline.dev_drawdown + 0.03:
            model_failures.append("drawdown_worse_than_baseline_relative_gate")

    search_failures: list[str] = []
    if not direction_policy.get("correlation_constrained"):
        search_failures.append("direction_weights_not_correlation_constrained")
    if direction_policy.get("capped_direction_signal") != "crowding_signal":
        search_failures.append("redundant_signal_not_frozen_or_capped")
    if direction_policy.get("demoted_signal") != "funding_alpha_signal":
        search_failures.append("redundant_pair_constraint_missing")
    if direction_policy.get("demoted_signal_role") != "state":
        search_failures.append("redundant_pair_constraint_missing")

    blockers = [*system_failures, *data_failures, *model_failures, *search_failures]
    return {
        "ready": not blockers,
        "decision": "allow_full_training" if not blockers else "defer_full_training",
        "recommended_budget": {"optuna_trials": 80, "rl_episodes": 40} if not blockers else None,
        "gates": {
            "system_gate": {"passed": not system_failures, "failures": system_failures},
            "data_gate": {"passed": not data_failures, "failures": data_failures},
            "model_gate": {"passed": not model_failures, "failures": model_failures},
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
    assessment = full_training_assessment or _assess_full_training_readiness(report, smoke_completed=report.smoke_completed)
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
    baseline = report.paulwei_baseline
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
                "paulwei_worst_dev_sharpe": baseline.worst_dev_sharpe,
                "tuned_worst_dev_sharpe": tuned.worst_dev_sharpe if tuned is not None else None,
                "tuned_negative_worst_windows": tuned.negative_dev_worst_windows if tuned is not None else None,
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
        self._summary_cache: dict[tuple[str, bool], CandidateSummary] = {}
        self._dev_windows = [
            *default_comparison_windows(self._split_spec, "dev"),
            *filtered_worst_period_windows(self._split_spec, "dev"),
        ]
        self._test_windows = [
            *default_comparison_windows(self._split_spec, "test"),
            *filtered_worst_period_windows(self._split_spec, "test"),
        ]
        self._fast_dev_wf_windows = default_walk_forward_windows(split_spec=self._split_spec, comparison_split="dev")[:2]
        self._full_dev_wf_windows = default_walk_forward_windows(split_spec=self._split_spec, comparison_split="dev")
        self._current_variant = default_variant_specs()[0]
        self._paulwei_variant = default_variant_specs()[1]
        self._deribit_status = asdict(collect_deribit_status(base_config.options_dir, "BTC"))
        self._training_window_coverage = {
            "train_window": split_summary_json(self._split_spec)["windows"]["train"],
            "dev_windows": [window.label for window in self._dev_windows],
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
                "one_hour_train_source": "native_1h" if one_hour_train_direct else ("derived_from_5m" if one_hour_train_available else "missing"),
                "four_hour_train_available": four_hour_train_available,
            }
        )
        etf_flow_metadata = _etf_flow_source_metadata(base_config.macro_dir)
        self._training_input_contract = {
            "primary_truth_source": "binance",
            "execution_framework": {
                "primary_timeframe": "4h",
                "entry_optimization_timeframe": "1h",
                "final_execution_timeframe": "5m" if base_config.execution_timeframe == Timeframe.TF_5M else None,
                "five_min_feature_aggregation_enabled": five_min_train_available,
                "one_hour_bars_available": one_hour_train_available,
                "one_hour_bars_source": (
                    "native_1h" if one_hour_train_direct else ("derived_from_5m" if one_hour_train_available else "missing")
                ),
                "four_hour_bars_available": four_hour_train_available,
                "five_min_direct_execution_enabled": base_config.execution_timeframe == Timeframe.TF_5M,
            },
            "state_feature_sources": {
                "deribit_in_state_features": bool(
                    self._deribit_status["enabled"] and self._deribit_status["snapshot_count_7d"] > 0
                ),
                "hyperliquid_in_state_features": bool(self._readiness["data_ready"]),
                "etf_flow_in_state_features": (base_config.macro_dir / "btc_etf_netflow" / "data.parquet").exists(),
                "etf_flow_source": etf_flow_metadata["etf_flow_source"],
                "etf_flow_source_confidence": etf_flow_metadata["etf_flow_source_confidence"],
                "etf_flow_last_success_at": etf_flow_metadata["etf_flow_last_success_at"],
                "cme_positioning_in_state_features": (Path("data/raw/cftc") / "cot_btc.parquet").exists(),
                "liquidation_in_search": bool(self._readiness["promotion_ready"]),
            },
            "signal_stack_contract": {
                "direction_state_execution_split_enabled": True,
                "state_signals_do_not_vote_direction": True,
                "execution_modifiers_do_not_flip_direction": True,
            },
        }
        self._search_param_grid = param_grid_for_mode(
            mode=search_space_mode,
            include_liquidation_toggles=self._readiness["promotion_ready"],
        )
        self._search_space_profile = search_space_profile(
            include_liquidation_toggles=self._readiness["promotion_ready"],
            mode=search_space_mode,
        )
        self._paulwei_fast: CandidateSummary | None = None
        self._current_fast: CandidateSummary | None = None

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
        if (
            (requested_liquidation or requested_state_overlay or requested_liq_guard)
            and not self._readiness["data_ready"]
        ):
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
    ) -> SearchReport:
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

        def _variant_progress(variant_name: str, variant_index: int):
            def _emit(step_completed: int, unit: str) -> None:
                if status_callback is None:
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
            best_variant_spec = next(variant for variant in [self._current_variant, self._paulwei_variant, optuna_variant, rl_variant] if variant is not None and variant.name == best_name)
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
            window_count=len(self._dev_windows),
            walk_forward_window_count=len(self._full_dev_wf_windows if full else self._fast_dev_wf_windows),
        )
        comparison_payload: dict[str, Any] = {}
        if include_comparison_payload:
            if status_callback is not None:
                status_callback(event="stage2_compare_windows_started")
            comparison_payload = _build_comparison_payload(
                self._base_config,
                variants=[variant for variant in variants if variant is not None],
                windows=self._dev_windows,
                walk_forward_windows=self._full_dev_wf_windows if full else self._fast_dev_wf_windows,
                split_spec=self._split_spec,
                comparison_split="dev",
                report_mode=report_mode,
                comparison_compute_mode=comparison_compute_mode,
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
        return SearchReport(
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
            best_params=optuna_params if best_name == "optuna_tuned" else rl_params if best_name == "rl_tuned" else None,
            comparison_payload=comparison_payload,
            test_comparison_payload=test_payload,
            test_evaluated=evaluate_test,
            report_mode=report_mode if full else "fast",
            report_degraded_due_to_memory=report_degraded_due_to_memory if full else False,
            smoke_completed=full,
        )

    def _evaluate_variant(
        self,
        variant: VariantSpec,
        *,
        fast: bool,
        progress_callback: Any | None = None,
    ) -> CandidateSummary:
        cache_key = (self._variant_key(variant), fast)
        cached = self._summary_cache.get(cache_key)
        if cached is not None:
            if progress_callback is not None:
                progress_callback(4, "cached")
            return cached
        windows = self._dev_windows
        wf_windows = self._fast_dev_wf_windows if fast else self._full_dev_wf_windows

        dev_window = next(window for window in windows if window.label == "dev")
        dev_result = self._run_variant_backtest_cached(
            variant,
            start_ns=dev_window.start_ns,
            end_ns=dev_window.end_ns,
        )
        if progress_callback is not None:
            progress_callback(1, "dev")
        weak_window = next(window for window in windows if window.label == "2023-08")
        weak_result = self._run_variant_backtest_cached(variant, start_ns=weak_window.start_ns, end_ns=weak_window.end_ns)
        if progress_callback is not None:
            progress_callback(2, "2023-08")
        worst_periods = [window for window in windows if window.label not in {"dev", "2023-08"}]
        worst_results = [
            self._run_variant_backtest_cached(variant, start_ns=window.start_ns, end_ns=window.end_ns)
            for window in worst_periods
        ]
        if progress_callback is not None:
            progress_callback(3, "worst_windows")
        wf_metrics = self._run_validation_bundle_cached(variant, wf_windows)
        if progress_callback is not None:
            progress_callback(4, "validation_bundle")
        negative_worst_windows = sum(1 for result in worst_results if result.sharpe < 0)

        baseline = None
        if variant.name != "paulwei_baseline":
            baseline = self._get_paulwei_fast_summary()
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
        )
        gate_metrics, failure_reason = _promotion_diagnostics(
            avg_test_sharpe=wf_metrics["avg_test_sharpe"],
            worst_test_sharpe=wf_metrics["worst_test_sharpe"],
            overall_drawdown=dev_result.max_drawdown,
            negative_worst_windows=negative_worst_windows,
            baseline=baseline,
        )
        promote = all(gate_metrics.values())
        summary = CandidateSummary(
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
        )
        self._summary_cache[cache_key] = summary
        return summary

    def _evaluate_test_acceptance(self, variant: VariantSpec, summary: CandidateSummary) -> CandidateSummary:
        test_window = next(window for window in self._test_windows if window.label == "test")
        test_result = self._run_variant_backtest_cached(variant, start_ns=test_window.start_ns, end_ns=test_window.end_ns)
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
        )
        self._backtest_cache[key] = snapshot
        return snapshot

    def _run_validation_bundle_cached(self, variant: VariantSpec, windows: list[WindowSpec]) -> dict[str, float]:
        key = (self._variant_key(variant), tuple(window.label for window in windows))
        cached = self._validation_cache.get(key)
        if cached is not None:
            return cached
        result = _run_validation_bundle(self._base_config, variant, windows, run_backtest=self._run_variant_backtest_cached)
        self._validation_cache[key] = result
        return result

    @staticmethod
    def _variant_key(variant: VariantSpec) -> str:
        payload = json.dumps(asdict(variant), sort_keys=True, default=str)
        return sha1(payload.encode("utf-8")).hexdigest()

    def _get_paulwei_fast_summary(self) -> CandidateSummary:
        if self._paulwei_fast is None:
            self._paulwei_fast = self._evaluate_variant(self._paulwei_variant, fast=True)
        return self._paulwei_fast

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
            )
    else:
        run = run_backtest
    test_sharpes: list[float] = []
    ratios: list[float] = []
    for window in windows:
        train_end = window.start_ns
        assert train_end is not None
        train_start = _shift_months(train_end, -24)
        train_result = run(variant, start_ns=train_start, end_ns=train_end)
        test_result = run(variant, start_ns=window.start_ns, end_ns=window.end_ns)
        ratio = test_result.sharpe / train_result.sharpe if abs(train_result.sharpe) > 0.01 else 0.0
        test_sharpes.append(test_result.sharpe)
        ratios.append(ratio)
    return {
        "avg_test_sharpe": round(sum(test_sharpes) / len(test_sharpes), 4),
        "avg_oos_ratio": round(sum(ratios) / len(ratios), 4),
        "worst_test_sharpe": round(min(test_sharpes), 4),
    }


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
) -> float:
    capped_oos_ratio = max(-1.0, min(2.0, avg_oos_ratio))
    score = (
        0.34 * avg_test_sharpe
        + 0.28 * worst_test_sharpe
        + 0.08 * capped_oos_ratio
        + 0.05 * overall_sharpe
        + 0.04 * weak_window_sharpe
        + 0.01 * (overall_pnl / 1_000_000.0)
        - 3.0 * max(0.0, overall_drawdown - 0.10)
        - 1.25 * negative_worst_windows
    )
    if baseline is not None:
        score -= 10.0 * max(0.0, overall_drawdown - (baseline.dev_drawdown + 0.03))
        score -= 4.5 * max(0.0, baseline.worst_dev_sharpe - worst_test_sharpe)
        score -= 2.0 * max(0, negative_worst_windows - baseline.negative_dev_worst_windows)
    return score


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
        "overall_drawdown": overall_drawdown <= baseline.dev_drawdown + 0.03,
        "drawdown_preferred_band": overall_drawdown <= baseline.dev_drawdown + 0.03,
        "negative_worst_windows": negative_worst_windows <= baseline.negative_dev_worst_windows,
    }
    for key in ("worst_test_sharpe", "overall_drawdown", "negative_worst_windows", "avg_test_sharpe"):
        if not gate_metrics[key]:
            return gate_metrics, key
    return gate_metrics, None


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
        status_callback=status_callback,
    )


def _train_candidate_summary_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(candidate.get("metrics", {}))
    score = float(metrics.get("score", metrics.get("sharpe", 0.0)) or 0.0)
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
    stage1_batch_size: int = 1,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    try:
        import optuna  # noqa: F401
    except ImportError:
        return None, [], {
            "train_candidate_diagnostics": {
                "raw_trial_count": 0,
                "raw_nonzero_trade_trials": 0,
                "eligible_trade_count_trials": 0,
                "max_train_trade_count": 0,
                "max_train_sharpe": 0.0,
            },
            "empty_shortlist_reason": "all_negative_primary_metric",
        }
    effective_execution_timeframe = search_execution_timeframe or evaluator._base_config.execution_timeframe
    resolved_seed_trials = (
        baseline_anchor_seed_trials()[:n_trials]
        if evaluator.search_space_profile.get("baseline_anchored_search", False)
        else []
    )
    if seed_trials:
        resolved_seed_trials.extend(dict(trial) for trial in seed_trials)
    resolved_seed_trials = _dedupe_seed_trials(resolved_seed_trials)[:n_trials]
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
        stage1_parallel_mode=stage1_parallel_mode,
        stage1_max_workers=stage1_max_workers,
        stage1_batch_size=stage1_batch_size,
    )
    sweep = run_search_stage(worker_config)
    ranking_started_at = datetime.now(UTC).timestamp()
    ranked: list[dict[str, Any]] = []
    total_candidates = len(sweep.top_n)
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
        summary = CandidateSummary(**_train_candidate_summary_payload(candidate))
        ranked.append(
            {
                "params": params,
                "train_metrics": candidate["metrics"],
                "summary": asdict(summary),
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
                best_params_so_far=max(ranked, key=lambda item: item["summary"]["robust_score"])["params"] if ranked else None,
                runtime_s=datetime.now(UTC).timestamp() - ranking_started_at,
            )
    ranked.sort(key=lambda item: item["summary"]["robust_score"], reverse=True)
    best = ranked[0] if ranked else None
    diagnostics = {
        "train_candidate_diagnostics": dict(getattr(sweep, "train_candidate_diagnostics", {}) or {}),
        "empty_shortlist_reason": getattr(sweep, "empty_shortlist_reason", None),
    }
    return (best["params"] if best else None), ranked, diagnostics


def build_markdown_report(report: SearchReport) -> str:
    full_training_assessment = _assess_full_training_readiness(
        report,
        smoke_completed=report.smoke_completed,
    )
    post_smoke_scenario = _classify_post_smoke_scenario(
        report,
        full_training_assessment=full_training_assessment,
    )
    framework_diagnosis = _build_framework_diagnosis(report)
    lines = [
        "# Framework Optimization",
        "",
        f"- created_at: `{report.created_at}`",
        f"- symbol: `{report.symbol}`",
        f"- best_variant: `{report.best_variant}`",
        f"- best_score: `{report.best_score}`",
        f"- test_evaluated: `{report.test_evaluated}`",
        "",
        "## Readiness",
        "",
        f"- liquidation_data_ready: `{report.readiness['data_ready']}`",
        f"- liquidation_promotion_ready: `{report.readiness['promotion_ready']}`",
        f"- blocked_by: `{report.readiness['blocked_by']}`",
        f"- detail: `{report.readiness['detail']}`",
        f"- deribit_enabled: `{report.deribit_status['enabled']}`",
        f"- deribit_observation_ready: `{report.deribit_status['observation_ready']}`",
        f"- deribit_research_ready: `{report.deribit_status['research_ready']}`",
        f"- split_contract_version: `{report.split_contract['version']}`",
        "",
        "## Training Inputs",
        "",
        f"- primary_truth_source: `{report.training_input_contract.get('primary_truth_source')}`",
        f"- execution_framework: `{report.training_input_contract.get('execution_framework')}`",
        f"- state_feature_sources: `{report.training_input_contract.get('state_feature_sources')}`",
        f"- signal_stack_contract: `{report.training_input_contract.get('signal_stack_contract')}`",
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
        "",
    ]
    lines.extend(
        [
            "## Full Training Assessment",
            "",
            f"- decision: `{full_training_assessment['decision']}`",
            f"- ready: `{full_training_assessment['ready']}`",
            f"- blockers: `{full_training_assessment['blockers']}`",
            f"- recommended_budget: `{full_training_assessment['recommended_budget']}`",
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
                f"- recommended_actions: `{report.signal_audit_summary.get('recommended_actions')}`",
                f"- top_direction_weights: `{report.signal_audit_summary.get('top_direction_weights')}`",
                f"- json_path: `{report.signal_audit_summary.get('json_path')}`",
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
    full_training_assessment = _assess_full_training_readiness(
        report,
        smoke_completed=report.smoke_completed,
    )
    post_smoke_scenario = _classify_post_smoke_scenario(
        report,
        full_training_assessment=full_training_assessment,
    )
    framework_diagnosis = _build_framework_diagnosis(report)
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
    payload["full_training_allowed"] = full_training_assessment["ready"]
    payload["full_training_decision"] = full_training_assessment["decision"]
    payload["full_training_blockers"] = full_training_assessment["blockers"]
    payload["search_space_mode"] = search_space_mode
    payload["baseline_anchored_search"] = baseline_anchored_search
    payload["baseline_anchor_variant"] = baseline_anchor_variant
    payload["stage1_execution_mode"] = report.search_status.get("stage1_execution_mode")
    payload["train_candidate_diagnostics"] = report.search_status.get("train_candidate_diagnostics")
    payload["empty_shortlist_reason"] = report.search_status.get("empty_shortlist_reason")
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
        "empty_shortlist_reason": report.search_status.get("empty_shortlist_reason"),
    }
    payload["historical_leaderboard"] = [
        asdict(report.current),
        asdict(report.paulwei_baseline),
        asdict(report.optuna_best) if report.optuna_best else None,
        asdict(report.rl_best) if report.rl_best else None,
    ]
    comparison_to_current = None
    comparison_to_paulwei = None
    if best_summary is not None:
        comparison_to_current = {
            "variant": best_summary.variant,
            "drawdown_within_hard_cap": best_summary.dev_drawdown <= report.current.dev_drawdown + 0.03,
            "drawdown_within_preferred_band": best_summary.dev_drawdown <= report.current.dev_drawdown + 0.03,
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
    verdict_kind = "blocked_by_data_readiness" if payload["readiness"]["blocked_by"] else "promote_candidate_found"
    if best_summary is not None and not best_summary.promotion_candidate:
        verdict_kind = (
            "no_promote_candidate_found_param_side"
            if best_summary.promotion_failure_reason == "avg_test_sharpe"
            else "no_promote_candidate_found_architecture_side"
        )
    payload["final_verdict"] = {
        "best_variant": report.best_variant,
        "candidate_ready_for_test": bool(best_summary.promotion_candidate) if best_summary and not report.test_evaluated else False,
        "promote_to_paper_live_candidate": bool(best_summary.promotion_candidate) if best_summary and report.test_evaluated else False,
        "test_evaluated": report.test_evaluated,
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
        "empty_shortlist_reason": payload["search_status"].get("empty_shortlist_reason"),
        "verdict_kind": verdict_kind,
        "comparison_to_current": comparison_to_current,
        "comparison_to_paulwei_baseline": comparison_to_paulwei,
        "next_data_priority": (
            "liquidation_history"
            if not payload["readiness"]["promotion_ready"]
            else "deribit_history"
            if not payload["deribit_status"]["research_ready"]
            else "none"
        ),
        "full_training_ready": full_training_assessment["ready"],
        "full_training_decision": full_training_assessment["decision"],
        "full_training_blockers": full_training_assessment["blockers"],
        "post_smoke_scenario": post_smoke_scenario["scenario"],
        "post_smoke_next_action": post_smoke_scenario["next_action"],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
