#!/usr/bin/env python3
"""Run a resumable framework-optimization campaign with authoritative status output."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.auto_research import SearchStageTimeoutError
from zenquant.research.framework_search import (
    CandidateSummary,
    FrameworkEvaluator,
    SearchReport,
    _assess_full_training_readiness,
    _build_framework_diagnosis,
    build_markdown_report,
    run_optuna_search,
    write_search_report,
)
from zenquant.research.rl_tuner import train_tuner
from zenquant.research.signal_combination import generate_signal_combination_audit
from zenquant.research.splits import default_btc_split_spec, split_summary_json
from zenquant.strategy import ARCStrategyConfig


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _update_status(path: Path, **kwargs: Any) -> dict[str, Any]:
    payload = _read_json(path) or {}
    payload.update(kwargs)
    payload["updated_at"] = _ts()
    _write_json(path, payload)
    return payload


def _append_campaign_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{_ts()}] {message}\n")


def _initialize_running_status(
    path: Path,
    *,
    budget: str,
    optuna_trials: int,
    rl_episodes: int,
    started_at: str,
) -> dict[str, Any]:
    payload = {
        "phase": "stage0_readiness",
        "status": "running",
        "budget": budget,
        "optuna_trials_requested": optuna_trials,
        "rl_episodes_requested": rl_episodes,
        "optuna_trials_completed": 0,
        "rl_episodes_completed": 0,
        "started_at": started_at,
        "updated_at": _ts(),
        "blocked_by": None,
        "liquidation_in_search": None,
        "liquidation_blocked_by": None,
        "search_space_profile": None,
        "search_space_mode": None,
        "baseline_anchored_search": False,
        "baseline_anchor_variant": None,
        "resume_supported": True,
        "split_contract": split_summary_json(default_btc_split_spec()),
        "split_contract_version": default_btc_split_spec().version,
        "authoritative_promote_path": True,
        "test_evaluated": False,
        "invalidated_reason": None,
        "full_training_allowed": False,
        "full_training_decision": "defer_full_training",
        "full_training_blockers": ["smoke_campaign_incomplete"],
        "report_mode": None,
        "report_degraded_due_to_memory": False,
        "stage1_execution_mode": None,
        "authoritative_verdict_execution_mode": "5m_enabled",
        "stage1_current_trial": 0,
        "stage1_last_trial_at": None,
        "stage1_best_value_so_far": None,
        "stage1_best_params_so_far": None,
        "stage1_runtime_s": 0.0,
        "stage1_slow_trial": False,
        "stage1_slow_reason": None,
        "stage1_subphase": None,
        "stage1_progress_completed": 0,
        "stage1_progress_total": 0,
        "stage1_last_progress_at": None,
        "stage1_last_completed_unit": None,
        "stage1_failure_reason": None,
        "cme_positioning_in_state_features": False,
        "etf_flow_source": None,
        "etf_flow_source_confidence": "missing",
        "etf_flow_last_success_at": None,
        "macro_state_sources": [],
        "state_data_confidence": "minimal",
        "stage2_subphase": None,
        "stage2_progress_completed": 0,
        "stage2_progress_total": 0,
        "stage2_last_progress_at": None,
        "stage2_last_completed_unit": None,
        "stage2_failure_reason": None,
        "train_candidate_diagnostics": None,
        "empty_shortlist_reason": None,
    }
    _write_json(path, payload)
    return payload


def _resolved_budget(budget: str, optuna_trials: int | None, rl_episodes: int | None) -> tuple[int, int]:
    defaults = {
        "smoke": (5, 5),
        "fast": (50, 50),
        "high": (150, 100),
    }
    default_optuna, default_rl = defaults[budget]
    return optuna_trials or default_optuna, rl_episodes or default_rl


def _state_data_confidence(
    *,
    etf_flow: bool,
    deribit: bool,
    hyperliquid: bool,
    cme_positioning: bool,
) -> str:
    ready_count = sum(int(flag) for flag in (etf_flow, deribit, hyperliquid, cme_positioning))
    if ready_count >= 4:
        return "high"
    if ready_count >= 2:
        return "partial"
    return "minimal"


def _resolved_search_space_contract(
    evaluator: Any,
    *,
    budget: str,
) -> tuple[dict[str, Any], str, bool, str]:
    profile = dict(getattr(evaluator, "search_space_profile", {}) or {})
    search_space_mode = str(
        profile.get(
            "search_space_mode",
            "baseline_anchored_train_only" if budget == "smoke" else "execution_first_v2_correlation_constrained",
        )
    )
    baseline_anchored_search = bool(
        profile.get("baseline_anchored_search", search_space_mode == "baseline_anchored_train_only")
    )
    baseline_anchor_variant = str(profile.get("baseline_anchor_variant", "paulwei_baseline"))
    profile.setdefault("search_space_mode", search_space_mode)
    profile.setdefault("baseline_anchored_search", baseline_anchored_search)
    profile.setdefault("baseline_anchor_variant", baseline_anchor_variant)
    return profile, search_space_mode, baseline_anchored_search, baseline_anchor_variant


def _placeholder_summary(name: str) -> CandidateSummary:
    return CandidateSummary(
        variant=name,
        robust_score=0.0,
        selection_split="train",
        dev_sharpe=0.0,
        dev_pnl=0.0,
        dev_drawdown=0.0,
        avg_dev_sharpe=0.0,
        avg_dev_ratio=0.0,
        worst_dev_sharpe=0.0,
        weak_window_sharpe=0.0,
        negative_dev_worst_windows=0,
        promotion_candidate=False,
        blocked_by=None,
        failed_gate_metrics=None,
        promotion_failure_reason=None,
    )


def _archive_prior_outputs(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    archive_root = output_dir.parent / "archive" / "dev_runs" / f"{output_dir.name}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    archive_root.parent.mkdir(parents=True, exist_ok=True)
    for name in ("campaign_status.json", "framework_optimization.json", "framework_optimization.md", "campaign.log", "checkpoints"):
        source = output_dir / name
        if source.exists():
            target = archive_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))


def _invalidate_resume_state(output_dir: Path, status_path: Path, *, reason: str) -> None:
    _archive_prior_outputs(output_dir)
    _write_json(
        status_path,
        {
            "phase": "stage0_readiness",
            "status": "blocked",
            "blocked_by": "invalidated_checkpoint",
            "invalidated_reason": reason,
            "updated_at": _ts(),
            "resume_supported": True,
            "split_contract": split_summary_json(default_btc_split_spec()),
            "split_contract_version": default_btc_split_spec().version,
            "authoritative_promote_path": True,
            "test_evaluated": False,
        },
    )


def _resume_invalidated_reason(output_dir: Path, *, split_spec_version: str) -> str | None:
    checkpoint_dir = output_dir / "checkpoints"
    readiness_payload = _read_json(checkpoint_dir / "stage0_readiness.json") or {}
    stage1_payload = _read_json(checkpoint_dir / "stage1_search.json") or {}
    stage2_payload = _read_json(checkpoint_dir / "stage2_full_report.json") or {}
    status_payload = _read_json(output_dir / "campaign_status.json") or {}

    seen_versions = [
        readiness_payload.get("split_contract", {}).get("version"),
        stage1_payload.get("split_contract", {}).get("version"),
        stage1_payload.get("search_status", {}).get("split_contract_version"),
        status_payload.get("split_contract", {}).get("version"),
        status_payload.get("split_contract_version"),
    ]
    for version in seen_versions:
        if version is not None and version != split_spec_version:
            return "split_contract_mismatch"

    if stage1_payload:
        search_status = stage1_payload.get("search_status", {})
        if search_status.get("train_only_search") is not True:
            return "checkpoint_not_train_only_search"
        if search_status.get("dev_only_ranking") is not False:
            return "checkpoint_not_train_selection_only"

    if stage2_payload.get("test_evaluated") is True:
        return "checkpoint_already_consumed_frozen_test"

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run resumable framework optimization campaign")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", default="results/framework_optimization")
    parser.add_argument("--budget", choices=("smoke", "fast", "high"), default="high")
    parser.add_argument("--optuna-trials", type=int)
    parser.add_argument("--rl-episodes", type=int)
    parser.add_argument("--liquidation-dir", default="data/raw/merged_liquidations/aggregated")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not args.resume:
        _archive_prior_outputs(output_dir)
    else:
        invalidated_reason = _resume_invalidated_reason(
            output_dir,
            split_spec_version=default_btc_split_spec().version,
        )
        if invalidated_reason is not None:
            _invalidate_resume_state(output_dir, output_dir / "campaign_status.json", reason=invalidated_reason)
            print("blocked_by=invalidated_checkpoint")
            print(f"invalidated_reason={invalidated_reason}")
            return
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "campaign_status.json"
    log_path = output_dir / "campaign.log"

    optuna_trials, rl_episodes = _resolved_budget(args.budget, args.optuna_trials, args.rl_episodes)
    if args.budget == "smoke":
        rl_episodes = 0
    started_at = (_read_json(status_path) or {}).get("started_at", _ts()) if args.resume else _ts()
    _initialize_running_status(
        status_path,
        budget=args.budget,
        optuna_trials=optuna_trials,
        rl_episodes=rl_episodes,
        started_at=started_at,
    )
    _append_campaign_log(log_path, f"campaign_started budget={args.budget} resume={args.resume}")

    try:
        if importlib.util.find_spec("optuna") is None:
            _update_status(
                status_path,
                phase="stage1_search",
                status="blocked",
                blocked_by="optuna_unavailable",
            )
            _append_campaign_log(log_path, "campaign_blocked blocked_by=optuna_unavailable")
            print("blocked_by=optuna_unavailable")
            return

        base_config = HistoricalBacktestConfig(
            root_dir=Path(args.root),
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
            liquidation_dir=Path(args.liquidation_dir),
            execution_timeframe=Timeframe.TF_5M,
            context_timeframe=Timeframe.TF_1H,
            strategy_config=ARCStrategyConfig(execution_timeframe=Timeframe.TF_5M),
        )
        split_spec = default_btc_split_spec()
        search_space_mode = "baseline_anchored_train_only" if args.budget == "smoke" else "execution_first_v2_correlation_constrained"
        evaluator = FrameworkEvaluator(base_config, split_spec=split_spec, search_space_mode=search_space_mode)
        resolved_search_space_profile, resolved_search_space_mode, resolved_baseline_anchored_search, resolved_baseline_anchor_variant = _resolved_search_space_contract(
            evaluator,
            budget=args.budget,
        )
        interim_report = SearchReport(
            created_at=_ts(),
            symbol=base_config.symbol,
            readiness=evaluator.readiness,
            deribit_status=evaluator._deribit_status,
            split_contract=split_summary_json(split_spec),
            training_window_coverage=evaluator._training_window_coverage,
            training_input_contract=evaluator.training_input_contract,
            search_status={
                "search_space_profile": resolved_search_space_profile,
                "search_space_mode": resolved_search_space_mode,
                "baseline_anchored_search": resolved_baseline_anchored_search,
                "baseline_anchor_variant": resolved_baseline_anchor_variant,
                "liquidation_in_search": evaluator.readiness["promotion_ready"],
                "liquidation_blocked_by": None if evaluator.readiness["promotion_ready"] else "not_promotion_ready",
                "stage1_execution_mode": "1h_context_only" if args.budget == "smoke" else "5m_enabled",
                "authoritative_verdict_execution_mode": "5m_enabled",
                "macro_state_sources": [],
                "state_data_confidence": "minimal",
            },
            current=_placeholder_summary("current"),
            paulwei_baseline=_placeholder_summary("paulwei_baseline"),
            optuna_best=None,
            rl_best=None,
            best_variant="paulwei_baseline",
            best_score=0.0,
            best_params=None,
            comparison_payload={},
            report_mode="fast",
            report_degraded_due_to_memory=False,
            smoke_completed=False,
        )
        interim_training_assessment = _assess_full_training_readiness(interim_report, smoke_completed=False)
        interim_diagnosis = _build_framework_diagnosis(interim_report)
        state_sources = evaluator.training_input_contract["state_feature_sources"]
        macro_state_sources = [
            source
            for source, enabled in (
                ("hyperliquid", state_sources["hyperliquid_in_state_features"]),
                ("deribit", state_sources["deribit_in_state_features"]),
                ("etf_flow", state_sources["etf_flow_in_state_features"]),
                ("cme_positioning", state_sources.get("cme_positioning_in_state_features", False)),
            )
            if enabled
        ]
        state_data_confidence = _state_data_confidence(
            etf_flow=state_sources["etf_flow_in_state_features"],
            deribit=state_sources["deribit_in_state_features"],
            hyperliquid=state_sources["hyperliquid_in_state_features"],
            cme_positioning=state_sources.get("cme_positioning_in_state_features", False),
        )

        readiness_checkpoint = checkpoint_dir / "stage0_readiness.json"
        if not (args.resume and readiness_checkpoint.exists()):
            _write_json(
                readiness_checkpoint,
                {
                    "created_at": _ts(),
                    "readiness": evaluator.readiness,
                    "search_param_grid": evaluator.search_param_grid,
                    "split_contract": split_summary_json(split_spec),
                    "full_training_assessment": interim_training_assessment,
                    "framework_diagnosis": interim_diagnosis,
                },
            )
        _update_status(
            status_path,
            full_training_allowed=interim_training_assessment["ready"],
            full_training_decision=interim_training_assessment["decision"],
            full_training_blockers=interim_training_assessment["blockers"],
            search_space_mode=resolved_search_space_mode,
            baseline_anchored_search=resolved_baseline_anchored_search,
            baseline_anchor_variant=resolved_baseline_anchor_variant,
            authoritative_verdict_execution_mode="5m_enabled",
            cme_positioning_in_state_features=state_sources.get("cme_positioning_in_state_features", False),
            etf_flow_source=state_sources.get("etf_flow_source"),
            etf_flow_source_confidence=state_sources.get("etf_flow_source_confidence"),
            etf_flow_last_success_at=state_sources.get("etf_flow_last_success_at"),
            macro_state_sources=macro_state_sources,
            state_data_confidence=state_data_confidence,
        )
        _append_campaign_log(
            log_path,
            "stage0_readiness complete "
            f"etf_flow_in_state={state_sources['etf_flow_in_state_features']} "
            f"deribit_state={state_sources['deribit_in_state_features']}",
        )

        stage1_path = checkpoint_dir / "stage1_search.json"
        stage1_payload = _read_json(stage1_path) if args.resume else None
        if stage1_payload is None:
            stage1_execution_mode = "1h_context_only" if args.budget == "smoke" else "5m_enabled"
            smoke_ranking_top_n = 1 if args.budget == "smoke" else min(5, optuna_trials)
            smoke_rl_timeout_s = 600.0 if args.budget == "smoke" else None
            smoke_ranking_candidate_timeout_s = 240.0 if args.budget == "smoke" else None
            stage1_state: dict[str, Any] = {
                "optuna_params": None,
                "optuna_trials_payload": [],
                "optuna_trials_completed": 0,
                "train_candidate_diagnostics": {
                    "raw_trial_count": 0,
                    "raw_nonzero_trade_trials": 0,
                    "eligible_trade_count_trials": 0,
                    "max_train_trade_count": 0,
                    "max_train_sharpe": 0.0,
                },
                "empty_shortlist_reason": None,
                "rl_result": None,
                "rl_params": None,
                "rl_failure_reason": None,
                "used_5m_fallback": False,
            }

            def _write_minimal_stage1_checkpoint(
                *,
                search_outcome: str,
                optuna_params: dict[str, Any] | None = None,
                rl_params: dict[str, Any] | None = None,
                rl_result: dict[str, Any] | None = None,
                optuna_trials_payload: list[dict[str, Any]] | None = None,
                failure_reason: str | None = None,
                failure_detail: str | None = None,
                optuna_trials_completed: int = 0,
                rl_episodes_completed: int = 0,
            ) -> dict[str, Any]:
                payload = {
                    "created_at": _ts(),
                    "optuna_params": optuna_params,
                    "rl_params": rl_params,
                    "rl_result": rl_result,
                    "candidate_counts": {
                        "optuna_trials_completed": optuna_trials_completed,
                        "rl_episodes_completed": rl_episodes_completed,
                    },
                    "search_status": {
                        "budget": args.budget,
                        "optuna_available": True,
                        "optuna_trials_requested": optuna_trials,
                        "rl_episodes_requested": rl_episodes,
                        "optuna_trials_completed": optuna_trials_completed,
                        "rl_episodes_completed": rl_episodes_completed,
                        "optuna_candidate_found": optuna_params is not None,
                        "rl_candidate_found": rl_params is not None,
                        "liquidation_in_search": evaluator.readiness["promotion_ready"],
                        "liquidation_blocked_by": None if evaluator.readiness["promotion_ready"] else "not_promotion_ready",
                        "search_space_profile": resolved_search_space_profile,
                        "search_space_mode": resolved_search_space_mode,
                        "baseline_anchored_search": resolved_baseline_anchored_search,
                        "baseline_anchor_variant": resolved_baseline_anchor_variant,
                        "five_min_feature_aggregation_enabled": evaluator.training_input_contract["execution_framework"]["five_min_feature_aggregation_enabled"],
                        "five_min_direct_execution_enabled": evaluator.training_input_contract["execution_framework"]["five_min_direct_execution_enabled"],
                        "hyperliquid_in_state_features": evaluator.training_input_contract["state_feature_sources"]["hyperliquid_in_state_features"],
                        "deribit_in_state_features": evaluator.training_input_contract["state_feature_sources"]["deribit_in_state_features"],
                        "etf_flow_in_state_features": evaluator.training_input_contract["state_feature_sources"]["etf_flow_in_state_features"],
                        "cme_positioning_in_state_features": state_sources.get("cme_positioning_in_state_features", False),
                        "etf_flow_source": state_sources.get("etf_flow_source"),
                        "etf_flow_source_confidence": state_sources.get("etf_flow_source_confidence"),
                        "etf_flow_last_success_at": state_sources.get("etf_flow_last_success_at"),
                        "stage1_execution_mode": stage1_execution_mode,
                        "authoritative_verdict_execution_mode": "5m_enabled",
                        "macro_state_sources": macro_state_sources,
                        "state_data_confidence": state_data_confidence,
                        "split_contract_version": split_spec.version,
                        "candidate_sources": [
                            "auto_research_train_only",
                            *(["auto_research_train_only_5m_fallback"] if stage1_state.get("used_5m_fallback") else []),
                            *(([(rl_result or {}).get("candidate_source", "rl_train_shortlist")]) if rl_episodes > 0 else []),
                        ],
                        "train_only_search": True,
                        "dev_only_ranking": False,
                        "dev_diagnostics_only": True,
                        "search_outcome": search_outcome,
                        "train_candidate_diagnostics": dict(stage1_state.get("train_candidate_diagnostics") or {}),
                        "empty_shortlist_reason": stage1_state.get("empty_shortlist_reason"),
                        "failure_reason": failure_reason,
                        "failure_detail": failure_detail,
                    },
                    "top_optuna_trials": (optuna_trials_payload or [])[:5],
                    "split_contract": split_summary_json(split_spec),
                }
                _write_json(stage1_path, payload)
                return payload

            def _persist_stage1_partial(
                *,
                search_outcome: str = "partial",
                optuna_params: dict[str, Any] | None = None,
                rl_params: dict[str, Any] | None = None,
                rl_result: dict[str, Any] | None = None,
                optuna_trials_payload: list[dict[str, Any]] | None = None,
                failure_reason: str | None = None,
                failure_detail: str | None = None,
                optuna_trials_completed: int = 0,
                rl_episodes_completed: int = 0,
            ) -> dict[str, Any]:
                return _write_minimal_stage1_checkpoint(
                    search_outcome=search_outcome,
                    optuna_params=optuna_params,
                    rl_params=rl_params,
                    rl_result=rl_result,
                    optuna_trials_payload=optuna_trials_payload,
                    failure_reason=failure_reason,
                    failure_detail=failure_detail,
                    optuna_trials_completed=optuna_trials_completed,
                    rl_episodes_completed=rl_episodes_completed,
                )

            def _stage1_heartbeat(**heartbeat: Any) -> None:
                current_trial = int(heartbeat.get("current_trial", heartbeat.get("current_candidate", 0)) or 0)
                completed_trials = int(
                    heartbeat.get("completed_trials", heartbeat.get("completed_candidates", heartbeat.get("completed_steps", 0)))
                    or 0
                )
                total_progress = int(
                    heartbeat.get("total_trials", heartbeat.get("total_candidates", heartbeat.get("total_steps", 0))) or 0
                )
                runtime_s = round(float(heartbeat.get("runtime_s", 0.0) or 0.0), 2)
                slow_trial = runtime_s >= 180.0 and current_trial > 0
                event = heartbeat.get("event")
                subphase = "stage1_optuna_search"
                normalized_completed = completed_trials
                normalized_total = total_progress
                if event in {"ranking_candidate_started", "ranking_candidate_completed"}:
                    subphase = "stage1_dev_validation"
                elif event in {"robustness_seed_started", "robustness_seed_completed"}:
                    subphase = "stage1_dev_robustness"
                elif event in {"rl_progress", "rl_rank_dev_started", "rl_completed"}:
                    subphase = "stage1_rl"
                    normalized_total = max(0, rl_episodes)
                    normalized_completed = min(max(0, completed_trials), normalized_total)
                elif event == "stage1_finalize":
                    subphase = "stage1_finalize"
                if subphase == "stage1_optuna_search" and event == "trial_completed":
                    stage1_state["optuna_trials_completed"] = max(
                        int(stage1_state.get("optuna_trials_completed", 0) or 0),
                        normalized_completed,
                    )
                _update_status(
                    status_path,
                    phase=subphase,
                    status="running",
                    optuna_trials_completed=(
                        int(stage1_state.get("optuna_trials_completed", 0) or 0)
                        if subphase != "stage1_optuna_search"
                        else max(int(stage1_state.get("optuna_trials_completed", 0) or 0), normalized_completed)
                    ),
                    rl_episodes_completed=normalized_completed if subphase == "stage1_rl" else 0,
                    stage1_execution_mode=stage1_execution_mode,
                    authoritative_verdict_execution_mode="5m_enabled",
                    stage1_current_trial=current_trial,
                    stage1_last_trial_at=_ts() if heartbeat.get("event") == "trial_completed" else None,
                    stage1_best_value_so_far=heartbeat.get("best_value_so_far"),
                    stage1_best_params_so_far=heartbeat.get("best_params_so_far"),
                    stage1_runtime_s=runtime_s,
                    stage1_slow_trial=slow_trial,
                    stage1_slow_reason="five_min_train_backtest_heavy" if slow_trial else None,
                    stage1_subphase=subphase,
                    stage1_progress_completed=normalized_completed,
                    stage1_progress_total=normalized_total,
                    stage1_last_progress_at=_ts(),
                    stage1_last_completed_unit=event,
                    stage1_failure_reason=None,
                    search_space_profile=resolved_search_space_profile,
                    search_space_mode=resolved_search_space_mode,
                    baseline_anchored_search=resolved_baseline_anchored_search,
                    baseline_anchor_variant=resolved_baseline_anchor_variant,
                    five_min_feature_aggregation_enabled=evaluator.training_input_contract["execution_framework"]["five_min_feature_aggregation_enabled"],
                    five_min_direct_execution_enabled=evaluator.training_input_contract["execution_framework"]["five_min_direct_execution_enabled"],
                    hyperliquid_in_state_features=state_sources["hyperliquid_in_state_features"],
                    deribit_in_state_features=state_sources["deribit_in_state_features"],
                    etf_flow_in_state_features=state_sources["etf_flow_in_state_features"],
                    cme_positioning_in_state_features=state_sources.get("cme_positioning_in_state_features", False),
                    etf_flow_source=state_sources.get("etf_flow_source"),
                    etf_flow_source_confidence=state_sources.get("etf_flow_source_confidence"),
                    etf_flow_last_success_at=state_sources.get("etf_flow_last_success_at"),
                    macro_state_sources=macro_state_sources,
                    state_data_confidence=state_data_confidence,
                    train_candidate_diagnostics=dict(stage1_state.get("train_candidate_diagnostics") or {}),
                    empty_shortlist_reason=stage1_state.get("empty_shortlist_reason"),
                )
                if event in {
                    "trial_completed",
                    "ranking_candidate_completed",
                    "robustness_seed_completed",
                    "rl_progress",
                    "rl_completed",
                    "stage1_finalize",
                }:
                    _append_campaign_log(
                        log_path,
                        f"{subphase} event={event} completed={normalized_completed}/{normalized_total}",
                    )
                if event in {"trial_completed", "ranking_candidate_completed", "rl_progress", "rl_completed"}:
                    _persist_stage1_partial(
                        search_outcome="partial",
                        optuna_params=stage1_state["optuna_params"],
                        rl_params=stage1_state["rl_params"],
                        rl_result=stage1_state["rl_result"],
                        optuna_trials_payload=stage1_state["optuna_trials_payload"],
                        optuna_trials_completed=int(stage1_state.get("optuna_trials_completed", 0) or 0),
                        rl_episodes_completed=normalized_completed if subphase == "stage1_rl" else 0,
                    )

            _update_status(
                status_path,
                phase="stage1_optuna_search",
                status="running",
                stage1_execution_mode=stage1_execution_mode,
                authoritative_verdict_execution_mode="5m_enabled",
                stage1_subphase="stage1_optuna_search",
                stage1_progress_completed=0,
                stage1_progress_total=optuna_trials,
                search_space_profile=resolved_search_space_profile,
                search_space_mode=resolved_search_space_mode,
                baseline_anchored_search=resolved_baseline_anchored_search,
                baseline_anchor_variant=resolved_baseline_anchor_variant,
                five_min_feature_aggregation_enabled=evaluator.training_input_contract["execution_framework"]["five_min_feature_aggregation_enabled"],
                five_min_direct_execution_enabled=evaluator.training_input_contract["execution_framework"]["five_min_direct_execution_enabled"],
                hyperliquid_in_state_features=state_sources["hyperliquid_in_state_features"],
                deribit_in_state_features=state_sources["deribit_in_state_features"],
                etf_flow_in_state_features=state_sources["etf_flow_in_state_features"],
                cme_positioning_in_state_features=state_sources.get("cme_positioning_in_state_features", False),
                etf_flow_source=state_sources.get("etf_flow_source"),
                etf_flow_source_confidence=state_sources.get("etf_flow_source_confidence"),
                etf_flow_last_success_at=state_sources.get("etf_flow_last_success_at"),
                macro_state_sources=macro_state_sources,
                state_data_confidence=state_data_confidence,
                train_candidate_diagnostics=dict(stage1_state.get("train_candidate_diagnostics") or {}),
                empty_shortlist_reason=stage1_state.get("empty_shortlist_reason"),
            )
            _append_campaign_log(log_path, "stage1_optuna_search started")

            def _run_stage1_optuna_search(
                *,
                n_trials_override: int | None = None,
                search_execution_timeframe: Timeframe,
                search_context_timeframe: Timeframe | None,
                trial_timeout_s: float | None,
                stage_total_timeout_s: float | None,
                ranking_timeout_s: float | None,
                ranking_top_n: int,
                ranking_candidate_timeout_s: float | None,
            ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
                result = run_optuna_search(
                    evaluator,
                    n_trials=n_trials_override or optuna_trials,
                    status_callback=_stage1_heartbeat,
                    search_execution_timeframe=search_execution_timeframe,
                    search_context_timeframe=search_context_timeframe,
                    trial_timeout_s=trial_timeout_s,
                    stage_total_timeout_s=stage_total_timeout_s,
                    ranking_timeout_s=ranking_timeout_s,
                    ranking_top_n=ranking_top_n,
                    ranking_candidate_timeout_s=ranking_candidate_timeout_s,
                )
                if len(result) == 2:
                    best_params, ranked = result
                    diagnostics = {
                        "train_candidate_diagnostics": {
                            "raw_trial_count": len(ranked),
                            "raw_nonzero_trade_trials": sum(
                                1 for item in ranked if int((item.get("train_metrics") or item.get("metrics") or {}).get("trade_count", 0) or 0) > 0
                            ),
                            "eligible_trade_count_trials": len(ranked),
                            "max_train_trade_count": max(
                                (int((item.get("train_metrics") or item.get("metrics") or {}).get("trade_count", 0) or 0) for item in ranked),
                                default=0,
                            ),
                            "max_train_sharpe": max(
                                (float((item.get("train_metrics") or item.get("metrics") or {}).get("sharpe", 0.0) or 0.0) for item in ranked),
                                default=0.0,
                            ),
                        },
                        "empty_shortlist_reason": None if ranked else "all_negative_primary_metric",
                    }
                    return best_params, ranked, diagnostics
                return result
            try:
                optuna_params, optuna_trials_payload, optuna_diagnostics = _run_stage1_optuna_search(
                    search_execution_timeframe=Timeframe.TF_1H if args.budget == "smoke" else evaluator._base_config.execution_timeframe,
                    search_context_timeframe=Timeframe.TF_1H if args.budget == "smoke" else evaluator._base_config.context_timeframe,
                    trial_timeout_s=720.0 if args.budget == "smoke" else None,
                    stage_total_timeout_s=1800.0 if args.budget == "smoke" else None,
                    ranking_timeout_s=600.0 if args.budget == "smoke" else None,
                    ranking_top_n=smoke_ranking_top_n,
                    ranking_candidate_timeout_s=smoke_ranking_candidate_timeout_s,
                )
            except SearchStageTimeoutError as exc:
                _write_minimal_stage1_checkpoint(
                    search_outcome="failed",
                    failure_reason="stage_runtime_or_search_issue",
                    failure_detail=str(exc),
                    optuna_trials_completed=0,
                    rl_episodes_completed=0,
                )
                _update_status(
                    status_path,
                    phase="stage1_optuna_search",
                    status="failed",
                    blocked_by="stage_runtime_or_search_issue",
                    failure_reason="stage_runtime_or_search_issue",
                    failure_detail=str(exc),
                    stage1_execution_mode=stage1_execution_mode,
                    stage1_subphase="stage1_optuna_search",
                    stage1_failure_reason="stage_runtime_or_search_issue",
                )
                _append_campaign_log(log_path, f"stage1_optuna_search failed reason={exc}")
                raise
            except Exception as exc:
                _write_minimal_stage1_checkpoint(
                    search_outcome="failed",
                    failure_reason="stage_runtime_or_search_issue",
                    failure_detail=str(exc),
                    optuna_trials_completed=0,
                    rl_episodes_completed=0,
                )
                _update_status(
                    status_path,
                    phase="stage1_optuna_search",
                    status="failed",
                    blocked_by="stage_runtime_or_search_issue",
                    failure_reason="stage_runtime_or_search_issue",
                    failure_detail=str(exc),
                    stage1_execution_mode=stage1_execution_mode,
                    stage1_subphase="stage1_optuna_search",
                    stage1_failure_reason="stage_runtime_or_search_issue",
                )
                _append_campaign_log(log_path, f"stage1_optuna_search failed reason={exc}")
                raise
            if args.budget == "smoke" and optuna_params is None and not optuna_trials_payload:
                stage1_state["used_5m_fallback"] = True
                stage1_execution_mode = "1h_context_only_with_5m_fallback"
                _append_campaign_log(
                    log_path,
                    "stage1_optuna_search fallback_to_5m reason=empty_train_shortlist n_trials=1",
                )
                _update_status(
                    status_path,
                    phase="stage1_optuna_search",
                    status="running",
                    stage1_execution_mode=stage1_execution_mode,
                    stage1_subphase="stage1_optuna_search",
                    stage1_last_progress_at=_ts(),
                    stage1_last_completed_unit="fallback_to_5m_search",
                    optuna_trials_completed=int(stage1_state.get("optuna_trials_completed", 0) or 0),
                )
                try:
                    optuna_params, optuna_trials_payload, optuna_diagnostics = _run_stage1_optuna_search(
                        n_trials_override=1,
                        search_execution_timeframe=evaluator._base_config.execution_timeframe,
                        search_context_timeframe=evaluator._base_config.context_timeframe,
                        trial_timeout_s=240.0,
                        stage_total_timeout_s=300.0,
                        ranking_timeout_s=360.0,
                        ranking_top_n=1,
                        ranking_candidate_timeout_s=360.0,
                    )
                except SearchStageTimeoutError as exc:
                    _append_campaign_log(log_path, f"stage1_optuna_search fallback_5m_failed reason={exc}")
                    raise
                except Exception as exc:
                    _append_campaign_log(log_path, f"stage1_optuna_search fallback_5m_failed reason={exc}")
                    raise
            stage1_state["train_candidate_diagnostics"] = dict(optuna_diagnostics.get("train_candidate_diagnostics") or {})
            stage1_state["empty_shortlist_reason"] = optuna_diagnostics.get("empty_shortlist_reason")
            stage1_state["optuna_params"] = optuna_params
            stage1_state["optuna_trials_payload"] = optuna_trials_payload
            _persist_stage1_partial(
                search_outcome="partial",
                optuna_params=optuna_params,
                rl_params=None,
                rl_result=None,
                optuna_trials_payload=optuna_trials_payload,
                optuna_trials_completed=int(stage1_state.get("optuna_trials_completed", 0) or 0),
                rl_episodes_completed=0,
            )
            rl_result = None
            if rl_episodes > 0:
                _update_status(
                    status_path,
                    phase="stage1_rl",
                    status="running",
                    optuna_trials_completed=len(optuna_trials_payload),
                    candidate_sources=["auto_research_train_only"],
                    train_only_search=True,
                    dev_only_ranking=False,
                    liquidation_in_search=evaluator.readiness["promotion_ready"],
                    liquidation_blocked_by=None if evaluator.readiness["promotion_ready"] else "not_promotion_ready",
                    search_space_profile=resolved_search_space_profile,
                    search_space_mode=resolved_search_space_mode,
                    baseline_anchored_search=resolved_baseline_anchored_search,
                    baseline_anchor_variant=resolved_baseline_anchor_variant,
                    five_min_feature_aggregation_enabled=evaluator.training_input_contract["execution_framework"]["five_min_feature_aggregation_enabled"],
                    five_min_direct_execution_enabled=evaluator.training_input_contract["execution_framework"]["five_min_direct_execution_enabled"],
                    hyperliquid_in_state_features=evaluator.training_input_contract["state_feature_sources"]["hyperliquid_in_state_features"],
                    deribit_in_state_features=evaluator.training_input_contract["state_feature_sources"]["deribit_in_state_features"],
                    etf_flow_in_state_features=evaluator.training_input_contract["state_feature_sources"]["etf_flow_in_state_features"],
                    cme_positioning_in_state_features=state_sources.get("cme_positioning_in_state_features", False),
                    etf_flow_source=state_sources.get("etf_flow_source"),
                    etf_flow_source_confidence=state_sources.get("etf_flow_source_confidence"),
                    etf_flow_last_success_at=state_sources.get("etf_flow_last_success_at"),
                    stage1_execution_mode=stage1_execution_mode,
                    authoritative_verdict_execution_mode="5m_enabled",
                    macro_state_sources=macro_state_sources,
                    state_data_confidence=state_data_confidence,
                    resume_supported=True,
                    full_training_allowed=interim_training_assessment["ready"],
                    full_training_decision=interim_training_assessment["decision"],
                    full_training_blockers=interim_training_assessment["blockers"],
                    stage1_subphase="stage1_rl",
                    stage1_progress_completed=0,
                    stage1_progress_total=rl_episodes,
                    stage1_last_progress_at=_ts(),
                    stage1_last_completed_unit="ranking_completed",
                    train_candidate_diagnostics=dict(stage1_state.get("train_candidate_diagnostics") or {}),
                    empty_shortlist_reason=stage1_state.get("empty_shortlist_reason"),
                )
                _append_campaign_log(log_path, "stage1_rl started")
                try:
                    rl_result = train_tuner(
                        total_timesteps=rl_episodes,
                        verbose=0,
                        param_grid=evaluator.search_param_grid,
                        split_spec=split_spec,
                        base_config=base_config,
                        selection_split="train",
                        status_callback=_stage1_heartbeat,
                        total_timeout_s=smoke_rl_timeout_s,
                    )
                except Exception as exc:
                    stage1_state["rl_failure_reason"] = str(exc)
                    stage1_payload = _write_minimal_stage1_checkpoint(
                        search_outcome="partial",
                        optuna_params=optuna_params,
                        rl_params=None,
                        rl_result=None,
                        optuna_trials_payload=optuna_trials_payload,
                        failure_reason="stage1_rl_sidecar_failed",
                        failure_detail=str(exc),
                        optuna_trials_completed=len(optuna_trials_payload),
                        rl_episodes_completed=0,
                    )
                    _update_status(
                        status_path,
                        phase="stage1_rl",
                        status="running",
                        blocked_by=None,
                        failure_reason="stage1_rl_sidecar_failed",
                        failure_detail=str(exc),
                        stage1_subphase="stage1_rl",
                        stage1_failure_reason="stage1_rl_sidecar_failed",
                        full_training_allowed=False,
                        full_training_decision="defer_full_training",
                        full_training_blockers=["smoke_campaign_incomplete", "stage1_rl_sidecar_failed"],
                    )
                    _append_campaign_log(log_path, f"stage1_rl non_blocking_failure reason={exc}")
                    rl_result = None
            else:
                _append_campaign_log(log_path, "stage1_rl skipped budget=smoke")
            rl_params = rl_result.get("best_params") if rl_result else None
            stage1_state["rl_result"] = rl_result
            stage1_state["rl_params"] = rl_params
            _persist_stage1_partial(
                search_outcome="partial",
                optuna_params=optuna_params,
                rl_params=rl_params,
                rl_result=rl_result,
                optuna_trials_payload=optuna_trials_payload,
                optuna_trials_completed=int(stage1_state.get("optuna_trials_completed", 0) or 0),
                rl_episodes_completed=min(int((rl_result or {}).get("search_status", {}).get("episodes_completed", 0)), rl_episodes),
            )
            stage1_payload = _write_minimal_stage1_checkpoint(
                search_outcome="success" if (optuna_params is not None or rl_params is not None) else "empty",
                optuna_params=optuna_params,
                rl_params=rl_params,
                rl_result=rl_result,
                optuna_trials_payload=optuna_trials_payload,
                optuna_trials_completed=int(stage1_state.get("optuna_trials_completed", 0) or 0),
                rl_episodes_completed=min(int((rl_result or {}).get("search_status", {}).get("episodes_completed", 0)), rl_episodes),
            )
            optuna_summary = None
            rl_summary = None
            if optuna_trials_payload:
                top_optuna_summary = optuna_trials_payload[0].get("summary")
                if top_optuna_summary and {
                    "variant",
                    "selection_split",
                    "dev_sharpe",
                    "dev_pnl",
                    "dev_drawdown",
                    "avg_dev_sharpe",
                    "avg_dev_ratio",
                    "worst_dev_sharpe",
                    "weak_window_sharpe",
                    "negative_dev_worst_windows",
                    "promotion_candidate",
                }.issubset(top_optuna_summary):
                    optuna_summary = CandidateSummary(**top_optuna_summary)
            if rl_result and rl_result.get("train_metrics"):
                train_metrics = rl_result["train_metrics"]
                rl_summary = CandidateSummary(
                    variant="rl_tuned",
                    robust_score=round(float(train_metrics.get("score", 0.0) or 0.0), 4),
                    selection_split="train",
                    dev_sharpe=float(train_metrics.get("sharpe", 0.0) or 0.0),
                    dev_pnl=float(train_metrics.get("pnl", 0.0) or 0.0),
                    dev_drawdown=float(train_metrics.get("max_drawdown", 0.0) or 0.0),
                    avg_dev_sharpe=float(train_metrics.get("sharpe", 0.0) or 0.0),
                    avg_dev_ratio=1.0,
                    worst_dev_sharpe=float(train_metrics.get("sharpe", 0.0) or 0.0),
                    weak_window_sharpe=float(train_metrics.get("sharpe", 0.0) or 0.0),
                    negative_dev_worst_windows=0,
                    promotion_candidate=False,
                )
            if optuna_summary is None and rl_summary is None:
                stage1_payload["search_status"]["full_training_allowed"] = False
                stage1_payload["search_status"]["full_training_decision"] = "defer_full_training"
                stage1_payload["search_status"]["full_training_blockers"] = [
                    "smoke_campaign_incomplete",
                    "deribit_observation_not_ready",
                    "no_tuned_candidate_from_smoke",
                ]
                stage1_best_score = 0.0
            else:
                stage1_report = SearchReport(
                    created_at=_ts(),
                    symbol=base_config.symbol,
                    readiness=evaluator.readiness,
                    deribit_status=evaluator._deribit_status,
                    split_contract=split_summary_json(split_spec),
                    training_window_coverage=evaluator._training_window_coverage,
                    training_input_contract=evaluator.training_input_contract,
                    search_status=stage1_payload["search_status"],
                    current=evaluator._evaluate_variant(evaluator._current_variant, fast=True),
                    paulwei_baseline=evaluator._evaluate_variant(evaluator._paulwei_variant, fast=True),
                    optuna_best=optuna_summary,
                    rl_best=rl_summary,
                    best_variant=(
                        "optuna_tuned"
                        if optuna_summary and (not rl_summary or optuna_summary.robust_score >= rl_summary.robust_score)
                        else "rl_tuned"
                        if rl_summary
                        else "paulwei_baseline"
                    ),
                    best_score=max(
                        [
                            summary.robust_score
                            for summary in (optuna_summary, rl_summary)
                            if summary is not None
                        ],
                        default=0.0,
                    ),
                    best_params=optuna_params if optuna_summary is not None else rl_params,
                    comparison_payload={},
                    report_mode="fast",
                    report_degraded_due_to_memory=False,
                    smoke_completed=False,
                )
                stage1_assessment = _assess_full_training_readiness(stage1_report, smoke_completed=False)
                stage1_payload["search_status"]["full_training_allowed"] = stage1_assessment["ready"]
                stage1_payload["search_status"]["full_training_decision"] = stage1_assessment["decision"]
                stage1_payload["search_status"]["full_training_blockers"] = stage1_assessment["blockers"]
                stage1_best_score = stage1_report.best_score
            if stage1_state["rl_failure_reason"]:
                stage1_payload["search_status"]["failure_reason"] = "stage1_rl_sidecar_failed"
                stage1_payload["search_status"]["failure_detail"] = stage1_state["rl_failure_reason"]
            _stage1_heartbeat(
                event="stage1_finalize",
                completed_steps=min(int((rl_result or {}).get("search_status", {}).get("episodes_completed", 0)), rl_episodes),
                total_steps=rl_episodes,
                runtime_s=float((_read_json(status_path) or {}).get("stage1_runtime_s", 0.0)),
                best_params_so_far=stage1_payload["optuna_params"] or stage1_payload["rl_params"],
                best_value_so_far=stage1_best_score,
            )
            _write_json(stage1_path, stage1_payload)
        _update_status(
            status_path,
            phase="stage1_search",
            status="running",
            optuna_trials_completed=int(stage1_payload["search_status"]["optuna_trials_completed"]),
            rl_episodes_completed=int(stage1_payload["search_status"]["rl_episodes_completed"]),
            liquidation_in_search=stage1_payload["search_status"].get("liquidation_in_search"),
            liquidation_blocked_by=stage1_payload["search_status"].get("liquidation_blocked_by"),
            search_space_profile=stage1_payload["search_status"].get("search_space_profile"),
            five_min_feature_aggregation_enabled=stage1_payload["search_status"].get(
                "five_min_feature_aggregation_enabled"
            ),
            five_min_direct_execution_enabled=stage1_payload["search_status"].get("five_min_direct_execution_enabled"),
            hyperliquid_in_state_features=stage1_payload["search_status"].get("hyperliquid_in_state_features"),
            deribit_in_state_features=stage1_payload["search_status"].get("deribit_in_state_features"),
            etf_flow_in_state_features=stage1_payload["search_status"].get("etf_flow_in_state_features"),
            cme_positioning_in_state_features=stage1_payload["search_status"].get("cme_positioning_in_state_features"),
            etf_flow_source=stage1_payload["search_status"].get("etf_flow_source"),
            etf_flow_source_confidence=stage1_payload["search_status"].get("etf_flow_source_confidence"),
            etf_flow_last_success_at=stage1_payload["search_status"].get("etf_flow_last_success_at"),
            stage1_execution_mode=stage1_payload["search_status"].get("stage1_execution_mode"),
            authoritative_verdict_execution_mode=stage1_payload["search_status"].get(
                "authoritative_verdict_execution_mode"
            ),
            macro_state_sources=stage1_payload["search_status"].get("macro_state_sources"),
            state_data_confidence=stage1_payload["search_status"].get("state_data_confidence"),
            candidate_sources=stage1_payload["search_status"].get("candidate_sources"),
            train_only_search=stage1_payload["search_status"].get("train_only_search"),
            dev_only_ranking=stage1_payload["search_status"].get("dev_only_ranking"),
            dev_diagnostics_only=stage1_payload["search_status"].get("dev_diagnostics_only"),
            search_space_mode=stage1_payload["search_status"].get("search_space_mode"),
            baseline_anchored_search=stage1_payload["search_status"].get("baseline_anchored_search"),
            baseline_anchor_variant=stage1_payload["search_status"].get("baseline_anchor_variant"),
            resume_supported=True,
            full_training_allowed=stage1_payload["search_status"].get("full_training_allowed", False),
            full_training_decision=stage1_payload["search_status"].get(
                "full_training_decision", "defer_full_training"
            ),
            full_training_blockers=stage1_payload["search_status"].get(
                "full_training_blockers", ["smoke_campaign_incomplete"]
            ),
            stage1_subphase="stage1_finalize",
            stage1_progress_completed=int(stage1_payload["search_status"]["rl_episodes_completed"]),
            stage1_progress_total=rl_episodes,
            stage1_last_progress_at=_ts(),
            stage1_last_completed_unit=stage1_payload["search_status"].get("search_outcome"),
            train_candidate_diagnostics=stage1_payload["search_status"].get("train_candidate_diagnostics"),
            empty_shortlist_reason=stage1_payload["search_status"].get("empty_shortlist_reason"),
        )

        stage2_path = checkpoint_dir / "stage2_full_report.json"
        final_report_payload = _read_json(stage2_path) if args.resume else None
        if final_report_payload is None:
            stage2_started_at = datetime.now(UTC).timestamp()
            stage2_state = {
                "completed": 0,
                "total": 0,
                "last_progress_at": stage2_started_at,
                "last_unit": None,
            }

            def _stage2_timeout_check() -> None:
                now = datetime.now(UTC).timestamp()
                total_timeout = 900.0 if args.budget == "smoke" else 3600.0
                current_subphase = (_read_json(status_path) or {}).get("stage2_subphase")
                if args.budget == "smoke":
                    no_progress_timeout = 840.0 if current_subphase == "stage2_prepare_variants" else 300.0
                else:
                    no_progress_timeout = 900.0
                if (now - stage2_started_at) > total_timeout:
                    raise SearchStageTimeoutError("stage2_total_timeout")
                if (now - stage2_state["last_progress_at"]) > no_progress_timeout:
                    raise SearchStageTimeoutError(
                        f"stage2_no_progress_timeout: subphase={( _read_json(status_path) or {}).get('stage2_subphase')} "
                        f"last_unit={stage2_state['last_unit']}"
                    )

            def _stage2_heartbeat(**heartbeat: Any) -> None:
                _stage2_timeout_check()
                event = heartbeat.get("event", "stage2_progress")
                subphase = "stage2_full_verdict"
                if event in {"stage2_prepare_variants_started", "stage2_prepare_variants_progress"}:
                    subphase = "stage2_prepare_variants"
                elif event in {
                    "stage2_compare_windows_started",
                    "comparison_window_completed",
                    "comparison_validation_completed",
                }:
                    subphase = "stage2_compare_windows"
                elif event in {"comparison_ranking_completed", "stage2_rank_variants_completed"}:
                    subphase = "stage2_rank_variants"
                elif event in {"stage2_write_artifacts_started", "stage2_write_artifacts_completed"}:
                    subphase = "stage2_write_artifacts"

                completed = int(heartbeat.get("completed_units", stage2_state["completed"]) or 0)
                total = int(heartbeat.get("total_units", stage2_state["total"]) or 0)
                stage2_state["completed"] = completed
                stage2_state["total"] = total
                stage2_state["last_progress_at"] = datetime.now(UTC).timestamp()
                stage2_state["last_unit"] = heartbeat.get("window_label") or heartbeat.get("variant") or event
                _update_status(
                    status_path,
                    phase="stage2_full_verdict",
                    status="running",
                    stage2_subphase=subphase,
                    stage2_progress_completed=completed,
                    stage2_progress_total=total,
                    stage2_last_progress_at=_ts(),
                    stage2_last_completed_unit=stage2_state["last_unit"],
                    stage2_failure_reason=None,
                    full_training_allowed=False,
                    full_training_decision="defer_full_training",
                    full_training_blockers=["smoke_campaign_incomplete"],
                )
                if event != "stage2_prepare_variants_progress" or completed == total:
                    _append_campaign_log(
                        log_path,
                        f"{subphase} event={event} completed={completed}/{total} unit={stage2_state['last_unit']}",
                    )

            _update_status(
                status_path,
                phase="stage2_full_verdict",
                status="running",
                full_training_allowed=False,
                full_training_decision="defer_full_training",
                full_training_blockers=["smoke_campaign_incomplete"],
                stage2_subphase="stage2_prepare_variants",
                stage2_progress_completed=0,
                stage2_progress_total=0,
                stage2_last_progress_at=_ts(),
                stage2_last_completed_unit=None,
                stage2_failure_reason=None,
            )
            _append_campaign_log(log_path, "stage2_full_verdict started")
            smoke_stage2 = args.budget == "smoke"
            try:
                if stage1_payload.get("search_status", {}).get("search_outcome") == "empty":
                    report = SearchReport(
                        created_at=_ts(),
                        symbol=base_config.symbol,
                        readiness=evaluator.readiness,
                        deribit_status=evaluator._deribit_status,
                        split_contract=split_summary_json(split_spec),
                        training_window_coverage=evaluator._training_window_coverage,
                        training_input_contract=evaluator.training_input_contract,
                        search_status=stage1_payload.get("search_status"),
                        current=_placeholder_summary("current"),
                        paulwei_baseline=_placeholder_summary("paulwei_baseline"),
                        optuna_best=None,
                        rl_best=None,
                        best_variant="paulwei_baseline",
                        best_score=0.0,
                        best_params=None,
                        comparison_payload={},
                        report_mode="fast",
                        report_degraded_due_to_memory=False,
                        smoke_completed=True,
                    )
                    _stage2_heartbeat(event="stage2_prepare_variants_progress", variant="no_tuned_candidate", completed_units=4, total_units=4)
                else:
                    report = evaluator.final_report(
                        optuna_params=stage1_payload.get("optuna_params"),
                        rl_params=stage1_payload.get("rl_params"),
                        search_status=stage1_payload.get("search_status"),
                        full=not smoke_stage2,
                        evaluate_test=not smoke_stage2,
                        include_comparison_payload=True,
                        status_callback=_stage2_heartbeat,
                        comparison_compute_mode="smoke_minimal" if smoke_stage2 else "full",
                    )
                _stage2_timeout_check()
            except SearchStageTimeoutError as exc:
                _update_status(
                    status_path,
                    phase="stage2_full_verdict",
                    status="failed",
                    blocked_by="stage_runtime_or_search_issue",
                    failure_reason="stage2_runtime_or_search_issue",
                    failure_detail=str(exc),
                    stage2_failure_reason=str(exc),
                )
                _append_campaign_log(log_path, f"stage2_full_verdict failed reason={exc}")
                raise
            if smoke_stage2:
                report.smoke_completed = True
            _stage2_heartbeat(event="stage2_rank_variants_completed", completed_units=stage2_state["completed"], total_units=stage2_state["total"])
            _append_campaign_log(log_path, "stage2_signal_audit started")
            report.signal_audit_summary = generate_signal_combination_audit(
                data_root=base_config.root_dir,
                symbol=base_config.symbol,
                output_dir=output_dir.parent / "signal_combination",
                profile_path=base_config.profile_path or Path("config/profiles/default.toml"),
            )
            _stage2_heartbeat(
                event="stage2_write_artifacts_started",
                completed_units=stage2_state["completed"] + 1,
                total_units=max(stage2_state["total"] + 2, 2),
            )
            final_json = output_dir / "framework_optimization.json"
            final_md = output_dir / "framework_optimization.md"
            write_search_report(final_json, report)
            final_md.write_text(build_markdown_report(report), encoding="utf-8")
            final_json_payload = _read_json(final_json) or {}
            final_report_payload = {
                "created_at": _ts(),
                "best_variant": report.best_variant,
                "best_score": report.best_score,
                "report_mode": report.report_mode,
                "report_degraded_due_to_memory": bool(getattr(report, "report_degraded_due_to_memory", False)),
                "test_evaluated": bool(getattr(report, "test_evaluated", False)),
                "json_path": str(final_json),
                "md_path": str(final_md),
                "signal_audit_summary": report.signal_audit_summary,
                "full_training_assessment": final_json_payload.get("full_training_assessment"),
                "post_smoke_scenario": final_json_payload.get("post_smoke_scenario"),
            }
            _write_json(stage2_path, final_report_payload)
            _stage2_heartbeat(
                event="stage2_write_artifacts_completed",
                completed_units=stage2_state["total"],
                total_units=stage2_state["total"],
            )
            _append_campaign_log(
                log_path,
                f"stage2_full_verdict complete best_variant={final_report_payload.get('best_variant')}",
            )

        post_smoke_scenario = final_report_payload.get("post_smoke_scenario") or {}
        _update_status(
            status_path,
            phase="stage3_complete",
            status="completed",
            blocked_by=None,
            best_variant=final_report_payload.get("best_variant"),
            best_score=final_report_payload.get("best_score"),
            report_mode=final_report_payload.get("report_mode"),
            report_degraded_due_to_memory=bool(final_report_payload.get("report_degraded_due_to_memory", False)),
            test_evaluated=bool(final_report_payload.get("test_evaluated")),
            json_path=final_report_payload.get("json_path"),
            md_path=final_report_payload.get("md_path"),
            resume_supported=True,
            full_training_allowed=bool((final_report_payload.get("full_training_assessment") or {}).get("ready", False)),
            full_training_decision=(final_report_payload.get("full_training_assessment") or {}).get(
                "decision", "defer_full_training"
            ),
            full_training_blockers=(final_report_payload.get("full_training_assessment") or {}).get("blockers", []),
            post_smoke_scenario=post_smoke_scenario.get("scenario"),
            post_smoke_category=post_smoke_scenario.get("category"),
            post_smoke_next_action=post_smoke_scenario.get("next_action"),
            stage2_subphase="stage3_complete",
        )
        _append_campaign_log(log_path, f"campaign_completed scenario={post_smoke_scenario.get('scenario')}")
        print(json.dumps(_read_json(status_path), indent=2))
    except SearchStageTimeoutError as exc:
        reason = str(exc)
        blocked_by = "stage1_optuna_timeout" if "stage1" in reason else "stage_runtime_or_search_issue"
        _update_status(
            status_path,
            status="failed",
            blocked_by=blocked_by,
            failure_reason=reason,
            detail=reason,
        )
        _append_campaign_log(log_path, f"campaign_failed timeout={reason}")
        raise
    except Exception:
        _update_status(
            status_path,
            status="failed",
            blocked_by="campaign_exception",
            detail=traceback.format_exc(limit=10),
        )
        _append_campaign_log(log_path, "campaign_failed campaign_exception")
        raise


if __name__ == "__main__":
    main()
