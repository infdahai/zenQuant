#!/usr/bin/env python3
"""Run a resumable framework-optimization campaign with authoritative status output."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import signal
import sys
import threading
import traceback
from dataclasses import asdict, replace
from datetime import UTC, datetime
from hashlib import sha1
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


def _read_seed_trials(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("seed_trials_file must contain a JSON list")
    resolved: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("seed_trials_file entries must be JSON objects")
        resolved.append(dict(item))
    return resolved


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


@contextlib.contextmanager
def _stage2_watchdog(initial_timeout_s: float | None) -> Any:
    if initial_timeout_s is None or initial_timeout_s <= 0:
        yield lambda *_args, **_kwargs: None
        return
    if threading.current_thread() is not threading.main_thread():
        yield lambda *_args, **_kwargs: None
        return

    timeout_detail = {"detail": "stage2_prepare_variants_no_progress_timeout"}
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handle_timeout(_signum: int, _frame: Any) -> None:
        raise SearchStageTimeoutError(str(timeout_detail["detail"]))

    def _reset(timeout_s: float | None, detail: str) -> None:
        timeout_detail["detail"] = detail
        if timeout_s is None or timeout_s <= 0:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            return
        signal.setitimer(signal.ITIMER_REAL, float(timeout_s))

    signal.signal(signal.SIGALRM, _handle_timeout)
    _reset(initial_timeout_s, timeout_detail["detail"])
    try:
        yield _reset
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def _best_effort_bootstrap_failure_status(
    *,
    status_path: Path,
    log_path: Path,
    budget: str,
    optuna_trials: int,
    rl_episodes: int,
    started_at: str,
    output_dir: Path,
    failure_reason: str,
    detail: str,
) -> None:
    if not status_path.exists():
        _write_json(
            status_path,
            {
                "phase": "stage0_readiness",
                "status": "failed",
                "budget": budget,
                "optuna_trials_requested": optuna_trials,
                "stage1_optuna_trials_total": optuna_trials,
                "rl_episodes_requested": rl_episodes,
                "optuna_trials_completed": 0,
                "rl_episodes_completed": 0,
                "started_at": started_at,
                "updated_at": _ts(),
                "resume_supported": True,
                "full_training_allowed": False,
                "full_training_decision": "defer_full_training",
                "full_training_blockers": ["smoke_campaign_incomplete"],
            },
        )
    _update_status(
        status_path,
        status="failed",
        blocked_by="campaign_bootstrap_exception",
        failure_reason=failure_reason,
        detail=detail,
        campaign_pid=os.getpid(),
        launcher_pid=int(os.environ.get("ZENQUANT_LOOP_LAUNCHER_PID", "0") or 0) or None,
        campaign_launcher_log_path=str(output_dir / "campaign_launcher.log"),
    )
    _append_campaign_log(log_path, f"campaign_failed campaign_bootstrap_exception reason={failure_reason}")


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
        "stage1_optuna_trials_total": optuna_trials,
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
        "launcher_pid": None,
        "campaign_pid": None,
        "split_contract": split_summary_json(default_btc_split_spec()),
        "split_contract_version": default_btc_split_spec().version,
        "authoritative_promote_path": True,
        "evaluation_role": "development",
        "test_access_allowed": False,
        "test_evaluated": False,
        "candidate_ready_for_handoff": False,
        "candidate_frozen_id": None,
        "frozen_params": None,
        "handoff_blockers": [],
        "dev_gate_used_this_round": False,
        "dev_gate_last_round": None,
        "dev_gate_interval": None,
        "full_stage2_executed_this_round": False,
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
        "stage1_search_mode": "primary_1h" if budget == "smoke" else "primary_5m",
        "stage1_primary_optuna_trials_completed": 0,
        "stage1_primary_optuna_trials_total": optuna_trials,
        "stage1_fallback_optuna_trials_completed": 0,
        "stage1_fallback_optuna_trials_total": 0,
        "stage1_progress_completed": 0,
        "stage1_progress_total": 0,
        "stage1_avg_trial_runtime_s": 0.0,
        "stage1_avg_context_build_s": 0.0,
        "stage1_avg_replay_run_s": 0.0,
        "stage1_cache_hit_rate": 0.0,
        "stage1_avg_event_loop_s": 0.0,
        "stage1_avg_metrics_finalize_s": 0.0,
        "stage1_avg_diagnostics_finalize_s": 0.0,
        "stage1_terminated_early_rate": 0.0,
        "stage1_budget_mode": "trial_count_primary",
        "stage1_stop_reason": None,
        "stage1_avg_indicator_step_s": 0.0,
        "stage1_avg_strategy_step_s": 0.0,
        "stage1_avg_risk_step_s": 0.0,
        "stage1_avg_execution_step_s": 0.0,
        "stage1_avg_accounting_step_s": 0.0,
        "rl_budget_mode": None,
        "rl_wall_clock_elapsed_s": 0.0,
        "rl_stop_reason": None,
        "rl_active_envs": 0,
        "rl_avg_episode_runtime_s": 0.0,
        "rl_cache_hit_rate": 0.0,
        "rl_avg_event_loop_s": 0.0,
        "rl_device": None,
        "rl_device_resolved_from": None,
        "rl_device_fallback_reason": None,
        "rl_device_preference": None,
        "rl_execution_mode": None,
        "rl_vec_env_type": None,
        "rl_platform_guard_applied": False,
        "rl_init_completed": False,
        "rl_first_progress_seen": False,
        "rl_init_timeout_s": None,
        "rl_first_progress_timeout_s": None,
        "rl_failure_driver": None,
        "rl_cleanup_completed": None,
        "rl_cleanup_warnings": [],
        "stage2_mp_start_method": None,
        "validation_pool_mode": None,
        "stage1_cpu_hotspot_summary": None,
        "stage1_indicator_hotspot_summary": None,
        "trial_replay_cache_hit_rate": 0.0,
        "replay_context_cache_hit_rate": 0.0,
        "window_snapshot_cache_hit_rate": 0.0,
        "validation_cache_hit_rate": 0.0,
        "stage2_prepare_variant_cache_hits": 0,
        "stage2_prepare_variant_replay_reused": 0,
        "stage2_prepare_variant_miss_reasons": {},
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
        "stage2_prepare_timeout_s": None,
        "stage2_prepare_deadlock_suspected": False,
        "stage2_prepare_failure_driver": None,
        "stage2_prepare_last_active_unit": None,
        "train_candidate_diagnostics": None,
        "empty_shortlist_reason": None,
        "candidate_checkpoint_written": False,
        "candidate_checkpoint_status": None,
        "candidate_checkpoint_variant": None,
        "candidate_checkpoint_score": None,
        "candidate_checkpoint_origin_phase": None,
    }
    _write_json(path, payload)
    return payload


def _resolved_budget(budget: str, optuna_trials: int | None, rl_episodes: int | None) -> tuple[int, int]:
    defaults = {
        "smoke": (5, 5),
        "fast": (50, 20),
        "high": (150, 60),
    }
    default_optuna, default_rl = defaults[budget]
    return optuna_trials or default_optuna, rl_episodes or default_rl


def _stage2_budget_profile(budget: str) -> dict[str, Any]:
    profiles = {
        "smoke": {
            "total": 780.0,
            "subphases": {
                "stage2_prepare_variants": 420.0,
                "stage2_compare_windows": 360.0,
                "stage2_compare_validation": 180.0,
                "stage2_rank_variants": 120.0,
                "stage2_write_artifacts": 120.0,
            },
            "no_progress": {
                "stage2_prepare_variants": 300.0,
                "stage2_compare_windows": 240.0,
                "stage2_compare_validation": 180.0,
                "stage2_rank_variants": 120.0,
                "stage2_write_artifacts": 120.0,
            },
        },
        "fast": {
            "total": 3600.0,
            "subphases": {
                "stage2_prepare_variants": 1200.0,
                "stage2_compare_windows": 900.0,
                "stage2_compare_validation": 900.0,
                "stage2_rank_variants": 300.0,
                "stage2_write_artifacts": 300.0,
            },
            "no_progress": {
                "stage2_prepare_variants": 600.0,
                "stage2_compare_windows": 300.0,
                "stage2_compare_validation": 300.0,
                "stage2_rank_variants": 180.0,
                "stage2_write_artifacts": 180.0,
            },
        },
        "high": {
            "total": 3600.0,
            "subphases": {
                "stage2_prepare_variants": 1800.0,
                "stage2_compare_windows": 1800.0,
                "stage2_compare_validation": 1800.0,
                "stage2_rank_variants": 600.0,
                "stage2_write_artifacts": 600.0,
            },
            "no_progress": {
                "stage2_prepare_variants": 900.0,
                "stage2_compare_windows": 600.0,
                "stage2_compare_validation": 600.0,
                "stage2_rank_variants": 300.0,
                "stage2_write_artifacts": 300.0,
            },
        },
    }
    return profiles[budget]


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


def _stage1_cpu_hotspot_summary(stage1_state: dict[str, Any], *, timing_count: int) -> dict[str, Any] | None:
    if timing_count <= 0:
        return None
    step_avgs = {
        "indicator": float(stage1_state.get("indicator_step_sum_s", 0.0) or 0.0) / timing_count,
        "strategy": float(stage1_state.get("strategy_step_sum_s", 0.0) or 0.0) / timing_count,
        "risk": float(stage1_state.get("risk_step_sum_s", 0.0) or 0.0) / timing_count,
        "execution": float(stage1_state.get("execution_step_sum_s", 0.0) or 0.0) / timing_count,
        "accounting": float(stage1_state.get("accounting_step_sum_s", 0.0) or 0.0) / timing_count,
    }
    dominant_step, dominant_avg = max(step_avgs.items(), key=lambda item: item[1])
    replay_avg = float(stage1_state.get("replay_run_sum_s", 0.0) or 0.0) / timing_count
    return {
        "dominant_step": dominant_step,
        "dominant_step_avg_s": round(dominant_avg, 4),
        "replay_run_avg_s": round(replay_avg, 4),
        "dominant_step_share_of_replay": round((dominant_avg / replay_avg) if replay_avg > 0 else 0.0, 4),
    }


def _stage1_indicator_hotspot_summary(stage1_state: dict[str, Any], *, timing_count: int) -> dict[str, Any] | None:
    if timing_count <= 0:
        return None
    indicator_avg = float(stage1_state.get("indicator_step_sum_s", 0.0) or 0.0) / timing_count
    replay_avg = float(stage1_state.get("replay_run_sum_s", 0.0) or 0.0) / timing_count
    summary = {
        "indicator_step_avg_s": round(indicator_avg, 4),
        "replay_run_avg_s": round(replay_avg, 4),
        "indicator_share_of_replay": round((indicator_avg / replay_avg) if replay_avg > 0 else 0.0, 4),
    }
    layer_totals = dict(stage1_state.get("indicator_layer_timing_totals") or {})
    if layer_totals:
        dominant_layer, dominant_total = max(layer_totals.items(), key=lambda item: float(item[1] or 0.0))
        summary["dominant_indicator_layer"] = dominant_layer
        summary["dominant_indicator_layer_avg_s"] = round(float(dominant_total or 0.0) / timing_count, 4)
    return summary


def _update_stage2_prepare_cache_metrics(stage2_state: dict[str, Any], *, event: str, unit_label: str) -> None:
    if event != "stage2_prepare_variant_validation_progress":
        return
    stage2_state["prepare_variant_cache_observations"] = int(
        stage2_state.get("prepare_variant_cache_observations", 0) or 0
    ) + 1
    if unit_label.startswith("cache_hit:"):
        stage2_state["prepare_variant_cache_hits"] = int(stage2_state.get("prepare_variant_cache_hits", 0) or 0) + 1
    if unit_label.startswith(("cache_hit:", "replay_reused:", "baseline_summary_")):
        stage2_state["prepare_variant_replay_reused"] = int(stage2_state.get("prepare_variant_replay_reused", 0) or 0) + 1
    if unit_label.startswith("cache_miss:"):
        reason = unit_label.split("cache_miss:", 1)[1] or "unknown"
        miss_reasons = dict(stage2_state.get("prepare_variant_miss_reasons") or {})
        miss_reasons[reason] = int(miss_reasons.get(reason, 0) or 0) + 1
        stage2_state["prepare_variant_miss_reasons"] = miss_reasons


def _stage2_failure_driver(
    failure_reason: str,
    *,
    rl_device: str | None,
    stage2_mp_start_method: str | None,
) -> tuple[bool, str]:
    normalized = str(failure_reason or "")
    if "summary_scoring" in normalized and "exception" in normalized:
        return False, "stage2_prepare_summary_scoring_exception"
    if "validation_pool_broken" in normalized:
        return False, "stage2_prepare_pool_broken" if "stage2_prepare" in normalized else "stage2_compare_pool_broken"
    if "child_process_exited" in normalized:
        return False, (
            "stage2_prepare_child_process_exited" if "stage2_prepare" in normalized else "stage2_compare_child_process_exited"
        )
    if "validation_runtime_failure" in normalized:
        return False, "stage2_compare_runtime_exception"
    if "stage2_prepare_variant_runtime_failure" in normalized:
        return False, "stage2_prepare_runtime_exception"
    if "stage2_prepare" not in normalized:
        return False, "stage2_compare_timeout" if "timeout" in normalized else "stage2_runtime_or_search_issue"
    if rl_device == "mps" and stage2_mp_start_method == "spawn":
        return True, "stage2_prepare_deadlock_after_rl_mps"
    if "no_progress_timeout" in normalized:
        return True, "stage2_prepare_worker_unresponsive"
    if "timeout" in normalized:
        return False, "stage2_prepare_timeout"
    return False, "stage2_runtime_or_search_issue"


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


def _candidate_frozen_id(best_variant: str, best_params: dict[str, Any] | None) -> str | None:
    if not best_params:
        return None
    payload = json.dumps({"variant": best_variant, "params": best_params}, sort_keys=True, separators=(",", ":"))
    return sha1(payload.encode("utf-8")).hexdigest()[:12]


def _round_index_from_output_dir(output_dir: Path) -> int | None:
    name = output_dir.name
    if not name.startswith("round_"):
        return None
    suffix = name.removeprefix("round_")
    return int(suffix) if suffix.isdigit() else None


def _candidate_checkpoint_path(output_dir: Path) -> Path:
    return output_dir / "checkpoints" / "candidate_checkpoint.json"


def _candidate_checkpoint_payload(
    *,
    round_index: int | None,
    budget: str,
    split_contract_version: str,
    search_space_mode: str | None,
    stage1_execution_mode: str | None,
    train_only_search: bool | None,
    train_candidate_diagnostics: dict[str, Any] | None,
    stage1_best_score: float | None,
    stage1_best_params_so_far: dict[str, Any] | None,
    optuna_summary: dict[str, Any] | None,
    rl_summary: dict[str, Any] | None,
    optuna_params: dict[str, Any] | None,
    rl_params: dict[str, Any] | None,
    checkpoint_status: str,
    blocked_by: str | None,
    failure_reason: str | None,
    origin_phase: str,
    invalidated_reason: str | None = None,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    if isinstance(optuna_params, dict) and optuna_params:
        candidates.append(
            {
                "candidate_source": "optuna",
                "candidate_variant": "optuna_tuned",
                "params": dict(optuna_params),
                "summary": dict(optuna_summary or {}),
                "score": float((optuna_summary or {}).get("robust_score", stage1_best_score or 0.0) or 0.0),
            }
        )
    if isinstance(rl_params, dict) and rl_params:
        candidates.append(
            {
                "candidate_source": "rl",
                "candidate_variant": "rl_tuned",
                "params": dict(rl_params),
                "summary": dict(rl_summary or {}),
                "score": float((rl_summary or {}).get("robust_score", stage1_best_score or 0.0) or 0.0),
            }
        )
    if not candidates:
        return None
    selected = max(candidates, key=lambda item: item["score"])
    return {
        "created_at": _ts(),
        "round": round_index,
        "budget": budget,
        "split_contract_version": split_contract_version,
        "search_space_mode": search_space_mode,
        "stage1_execution_mode": stage1_execution_mode,
        "train_only_search": train_only_search,
        "candidate_source": selected["candidate_source"],
        "candidate_variant": selected["candidate_variant"],
        "params": selected["params"],
        "train_candidate_diagnostics": dict(train_candidate_diagnostics or {}),
        "stage1_best_score": stage1_best_score,
        "stage1_best_params_so_far": dict(stage1_best_params_so_far or selected["params"]),
        "optuna_summary": dict(optuna_summary or {}) or None,
        "rl_summary": dict(rl_summary or {}) or None,
        "selected_checkpoint_candidate": {
            "candidate_source": selected["candidate_source"],
            "candidate_variant": selected["candidate_variant"],
            "score": selected["score"],
        },
        "checkpoint_status": checkpoint_status,
        "blocked_by": blocked_by,
        "failure_reason": failure_reason,
        "origin_phase": origin_phase,
        "invalidated_reason": invalidated_reason,
    }


def _write_candidate_checkpoint(output_dir: Path, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    path = _candidate_checkpoint_path(output_dir)
    _write_json(path, payload)
    return payload


def _write_development_iteration_report(
    *,
    output_dir: Path,
    split_spec: Any,
    base_config: HistoricalBacktestConfig,
    evaluator: FrameworkEvaluator,
    stage1_payload: dict[str, Any],
) -> dict[str, Any]:
    search_status = dict(stage1_payload.get("search_status") or {})
    optuna_params = stage1_payload.get("optuna_params")
    rl_params = stage1_payload.get("rl_params")
    best_variant = "optuna_tuned" if optuna_params else "rl_tuned" if rl_params else "paulwei_baseline"
    best_params = optuna_params if optuna_params is not None else rl_params
    frozen_id = _candidate_frozen_id(best_variant, best_params)
    candidate_exists = best_params is not None
    handoff_blockers = ["dev_gate_not_run_this_round"] if candidate_exists else ["no_tuned_candidate_from_smoke"]
    payload = {
        "created_at": _ts(),
        "symbol": base_config.symbol,
        "best_variant": best_variant,
        "best_score": float(search_status.get("stage1_best_score", 0.0) or 0.0),
        "evaluation_role": "development",
        "test_access_allowed": False,
        "test_evaluated": False,
        "candidate_ready_for_handoff": False,
        "candidate_frozen_id": None,
        "frozen_params": None,
        "handoff_blockers": handoff_blockers,
        "dev_gate_summary": None,
        "dev_gate_used_this_round": False,
        "dev_gate_last_round": search_status.get("dev_gate_last_round"),
        "dev_gate_interval": search_status.get("dev_gate_interval"),
        "full_stage2_executed_this_round": False,
        "readiness": evaluator.readiness,
        "deribit_status": evaluator._deribit_status,
        "split_contract": split_summary_json(split_spec),
        "training_window_coverage": evaluator._training_window_coverage,
        "training_input_contract": evaluator.training_input_contract,
        "search_status": search_status,
        "optuna_tuned": stage1_payload.get("optuna_trials_payload", [{}])[0].get("summary") if stage1_payload.get("optuna_trials_payload") else None,
        "rl_tuned": (stage1_payload.get("rl_result") or {}).get("summary"),
        "train_candidate_diagnostics": search_status.get("train_candidate_diagnostics"),
        "empty_shortlist_reason": search_status.get("empty_shortlist_reason"),
        "full_training_assessment": {
            "ready": False,
            "decision": "defer_handoff",
            "blockers": handoff_blockers,
            "warnings": [],
        },
        "full_training_allowed": False,
        "full_training_decision": "defer_handoff",
        "full_training_blockers": handoff_blockers,
        "post_smoke_scenario": {
            "scenario": "scenario_development_train_iteration" if candidate_exists else "scenario_b_no_tuned_candidate",
            "category": "train_iteration" if candidate_exists else "search_candidate_quality",
            "should_open_full_training": False,
            "next_action": "continue_train_search_until_dev_gate" if candidate_exists else "tighten_train_search_and_rerun_smoke",
            "blockers": handoff_blockers,
        },
        "final_verdict": {
            "best_variant": best_variant,
            "candidate_ready_for_test": False,
            "candidate_ready_for_handoff": False,
            "candidate_frozen_id": None,
            "promote_to_paper_live_candidate": False,
            "test_evaluated": False,
            "evaluation_role": "development",
            "blocked_by": evaluator.readiness.get("blocked_by"),
        },
    }
    final_json = output_dir / "framework_optimization.json"
    final_md = output_dir / "framework_optimization.md"
    _write_json(final_json, payload)
    final_md.write_text(
        "\n".join(
            [
                "# Framework Optimization",
                "",
                "- evaluation_role: `development`",
                "- dev_gate_used_this_round: `False`",
                f"- best_variant: `{best_variant}`",
                "- candidate_ready_for_handoff: `False`",
                f"- candidate_preview_frozen_id: `{frozen_id}`",
                f"- handoff_blockers: `{handoff_blockers}`",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "created_at": payload["created_at"],
        "best_variant": best_variant,
        "best_score": payload["best_score"],
        "report_mode": "train_only",
        "report_degraded_due_to_memory": False,
        "test_evaluated": False,
        "evaluation_role": "development",
        "test_access_allowed": False,
        "candidate_ready_for_handoff": False,
        "candidate_frozen_id": None,
        "json_path": str(final_json),
        "md_path": str(final_md),
        "full_training_assessment": payload["full_training_assessment"],
        "post_smoke_scenario": payload["post_smoke_scenario"],
    }


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
    parser.add_argument("--seed-trials-file", default=None)
    parser.add_argument("--development-dev-gate", action="store_true")
    parser.add_argument("--dev-gate-last-round", type=int, default=None)
    parser.add_argument("--dev-gate-interval", type=int, default=None)
    parser.add_argument(
        "--fast-worker-profile",
        choices=("fast_low", "fast_default", "fast_high_cpu"),
        default="fast_default",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    status_path = output_dir / "campaign_status.json"
    log_path = output_dir / "campaign.log"
    optuna_trials, rl_episodes = _resolved_budget(args.budget, args.optuna_trials, args.rl_episodes)
    started_at = (_read_json(status_path) or {}).get("started_at", _ts()) if args.resume else _ts()
    bootstrap_guard_armed = True

    try:
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

        stage1_parallel_mode = "serial" if args.budget == "smoke" else "cpu_parallel"
        if args.budget == "smoke":
            stage1_max_workers = None
        elif args.budget == "fast":
            fast_worker_cap = {
                "fast_low": 4,
                "fast_default": 6,
                "fast_high_cpu": 8,
            }[args.fast_worker_profile]
            stage1_max_workers = max(1, min((os.cpu_count() or 4) - 2, fast_worker_cap))
        else:
            stage1_max_workers = max(1, min((os.cpu_count() or 4) - 2, 8))
        stage1_target_inflight = 1 if args.budget == "smoke" else stage1_max_workers
        stage1_batch_size = 1 if args.budget == "smoke" else 4 if args.budget == "fast" else 6
        extra_seed_trials = _read_seed_trials(Path(args.seed_trials_file)) if args.seed_trials_file else []
        enable_stage1_rl = args.budget in {"fast", "high"}
        if args.budget == "smoke":
            rl_episodes = 0
        _initialize_running_status(
            status_path,
            budget=args.budget,
            optuna_trials=optuna_trials,
            rl_episodes=rl_episodes,
            started_at=started_at,
        )
        launcher_pid = int(os.environ.get("ZENQUANT_LOOP_LAUNCHER_PID", "0") or 0) or None
        campaign_pid = os.getpid()
        _update_status(
            status_path,
            launcher_pid=launcher_pid,
            campaign_pid=campaign_pid,
            evaluation_role="development",
            test_access_allowed=False,
            dev_gate_used_this_round=bool(args.development_dev_gate),
            dev_gate_last_round=args.dev_gate_last_round,
            dev_gate_interval=args.dev_gate_interval,
        )
        _append_campaign_log(log_path, f"campaign_started budget={args.budget} resume={args.resume} pid={campaign_pid}")
        bootstrap_guard_armed = False

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
                "stage1_execution_mode": (
                    "1h_context_only"
                    if args.budget == "smoke"
                    else "5m_lightweight_train_search"
                ),
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
            evaluation_role="development",
            test_access_allowed=False,
            dev_gate_used_this_round=bool(args.development_dev_gate),
            dev_gate_last_round=args.dev_gate_last_round,
            dev_gate_interval=args.dev_gate_interval,
            full_stage2_executed_this_round=bool(args.development_dev_gate),
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
                ("mvrv", state_sources.get("mvrv_in_state_features", False)),
                ("deribit_block_flow", state_sources.get("deribit_block_flow_in_state_features", False)),
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
            signal_independence_contract=evaluator.training_input_contract.get("signal_independence_contract"),
            historical_extension_candidate_present=evaluator.training_input_contract.get(
                "historical_extension_candidate_contract",
                {},
            ).get("present", False),
            historical_extension_candidate_source=evaluator.training_input_contract.get(
                "historical_extension_candidate_contract",
                {},
            ).get("source"),
            historical_extension_candidate_ready=evaluator.training_input_contract.get(
                "historical_extension_candidate_contract",
                {},
            ).get("ready", False),
            historical_extension_candidate_blocked_by=evaluator.training_input_contract.get(
                "historical_extension_candidate_contract",
                {},
            ).get("blocked_by"),
            historical_extension_train_enabled=evaluator.training_input_contract.get("historical_extension_train_enabled", False),
            historical_extension_mode=evaluator.training_input_contract.get("historical_extension_mode"),
            mixed_era_declared=evaluator.training_input_contract.get("mixed_era_declared", False),
            train_truth_legs=evaluator.training_input_contract.get("train_truth_legs", []),
            full_unified_truth=evaluator.training_input_contract.get("historical_extension_candidate_contract", {}).get(
                "full_unified_truth",
                False,
            ),
        )
        _append_campaign_log(
            log_path,
            "stage0_readiness complete "
            f"etf_flow_in_state={state_sources['etf_flow_in_state_features']} "
            f"deribit_state={state_sources['deribit_in_state_features']}",
        )

        stage1_path = checkpoint_dir / "stage1_search.json"
        stage1_payload = _read_json(stage1_path) if args.resume else None
        written_candidate_checkpoint = _read_json(_candidate_checkpoint_path(output_dir))
        stage1_state: dict[str, Any] = dict((stage1_payload or {}).get("search_status") or {})
        stage1_state.setdefault("rl_device", None)
        stage1_state.setdefault("rl_cleanup_completed", None)
        stage1_state.setdefault("rl_cleanup_warnings", [])
        stage1_state.setdefault("rl_failure_reason", None)
        stage1_state.setdefault("train_candidate_diagnostics", {})
        stage1_state.setdefault("empty_shortlist_reason", None)
        stage1_state.setdefault("no_tuned_candidate_reason", None)
        if stage1_payload is None:
            stage1_execution_mode = "1h_context_only" if args.budget == "smoke" else "5m_lightweight_train_search"
            smoke_ranking_top_n = 1 if args.budget == "smoke" else min(5, optuna_trials)
            smoke_rl_timeout_s = 600.0 if args.budget == "smoke" else None
            smoke_ranking_candidate_timeout_s = 30.0 if args.budget == "smoke" else None
            stage1_state = {
                "optuna_params": None,
                "optuna_trials_payload": [],
                "optuna_trials_completed": 0,
                "primary_optuna_trials_completed": 0,
                "primary_optuna_trials_total": optuna_trials,
                "fallback_optuna_trials_completed": 0,
                "fallback_optuna_trials_total": 0,
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
        "empty_shortlist_reason": None,
        "no_tuned_candidate_reason": None,
                "rl_result": None,
                "rl_params": None,
                "rl_failure_reason": None,
                "used_5m_fallback": False,
                "stage1_search_mode": "primary_1h" if args.budget == "smoke" else "primary_5m",
                "stage1_budget_mode": "trial_count_primary",
                "stage1_stop_reason": None,
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
                        "stage1_optuna_trials_total": optuna_trials,
                        "rl_episodes_requested": rl_episodes,
                        "optuna_trials_completed": optuna_trials_completed,
                        "rl_episodes_completed": rl_episodes_completed,
                        "optuna_candidate_found": optuna_params is not None,
                        "rl_candidate_found": rl_params is not None,
                        "stage1_search_mode": stage1_state.get("stage1_search_mode"),
                        "stage1_primary_optuna_trials_completed": stage1_state.get("primary_optuna_trials_completed", 0),
                        "stage1_primary_optuna_trials_total": stage1_state.get("primary_optuna_trials_total", optuna_trials),
                        "stage1_fallback_optuna_trials_completed": stage1_state.get("fallback_optuna_trials_completed", 0),
                        "stage1_fallback_optuna_trials_total": stage1_state.get("fallback_optuna_trials_total", 0),
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
                        "historical_extension_train_enabled": evaluator.training_input_contract.get(
                            "historical_extension_train_enabled",
                            False,
                        ),
                        "historical_extension_mode": evaluator.training_input_contract.get("historical_extension_mode"),
                        "mixed_era_declared": evaluator.training_input_contract.get("mixed_era_declared", False),
                        "train_truth_legs": evaluator.training_input_contract.get("train_truth_legs", []),
                        "full_unified_truth": evaluator.training_input_contract.get(
                            "historical_extension_candidate_contract",
                            {},
                        ).get("full_unified_truth", False),
                        "split_contract_version": split_spec.version,
                        "candidate_sources": [
                            "auto_research_train_only",
                            *(["auto_research_train_only_5m_fallback"] if stage1_state.get("used_5m_fallback") else []),
                            *(([(rl_result or {}).get("candidate_source", "rl_train_shortlist")]) if rl_episodes > 0 else []),
                        ],
                        "train_only_search": True,
                        "dev_only_ranking": False,
                        "dev_diagnostics_only": True,
                        "evaluation_role": "development",
                        "test_access_allowed": False,
                        "dev_gate_used_this_round": bool(args.development_dev_gate),
                        "dev_gate_last_round": args.dev_gate_last_round,
                        "dev_gate_interval": args.dev_gate_interval,
                        "search_outcome": search_outcome,
                        "train_candidate_diagnostics": dict(stage1_state.get("train_candidate_diagnostics") or {}),
                        "empty_shortlist_reason": stage1_state.get("empty_shortlist_reason"),
                        "no_tuned_candidate_reason": stage1_state.get("no_tuned_candidate_reason"),
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
                actual_steps = int(heartbeat.get("actual_steps", heartbeat.get("completed_steps", 0)) or 0)
                requested_steps = int(heartbeat.get("requested_steps", heartbeat.get("total_steps", 0)) or 0)
                completed_trials = int(
                    heartbeat.get("completed_trials", heartbeat.get("completed_candidates", heartbeat.get("completed_steps", 0)))
                    or 0
                )
                total_progress = int(
                    heartbeat.get("total_trials", heartbeat.get("total_candidates", heartbeat.get("total_steps", 0))) or 0
                )
                runtime_s = round(float(heartbeat.get("runtime_s", 0.0) or 0.0), 2)
                slow_trial = runtime_s >= 180.0 and current_trial > 0
                pending_trials = int(heartbeat.get("pending_trials", 0) or 0)
                heartbeat_max_workers = int(heartbeat.get("max_workers", stage1_max_workers or 1) or 1)
                heartbeat_target_inflight = int(
                    heartbeat.get("target_inflight", stage1_target_inflight or heartbeat_max_workers) or heartbeat_max_workers
                )
                stage1_active_workers_estimate = int(heartbeat.get("active_workers_estimate", pending_trials) or 0)
                stage1_scheduler_mode = heartbeat.get("scheduler_mode")
                raw_stage1_underfilled = heartbeat.get("underfilled")
                stage1_underfilled = bool(raw_stage1_underfilled) if raw_stage1_underfilled is not None else None
                stage1_underfilled_reason = heartbeat.get("underfilled_reason")
                stage1_budget_mode = heartbeat.get("stage1_budget_mode")
                stage1_stop_reason = heartbeat.get("stage1_stop_reason")
                event = heartbeat.get("event")
                subphase = "stage1_optuna_search"
                normalized_completed = completed_trials
                normalized_total = total_progress
                if event in {"ranking_candidate_started", "ranking_candidate_completed"}:
                    subphase = "stage1_dev_validation"
                elif event in {"robustness_seed_started", "robustness_seed_completed"}:
                    subphase = "stage1_dev_robustness"
                elif event in {"rl_init_completed", "rl_progress", "rl_rank_dev_started", "rl_completed"}:
                    subphase = "stage1_rl"
                    normalized_total = max(0, rl_episodes)
                    normalized_completed = min(max(0, completed_trials), normalized_total)
                    if heartbeat.get("rl_device") is not None:
                        stage1_state["rl_device"] = heartbeat.get("rl_device")
                        stage1_state["rl_device_resolved_from"] = heartbeat.get("rl_device_resolved_from")
                        stage1_state["rl_device_fallback_reason"] = heartbeat.get("rl_device_fallback_reason")
                        stage1_state["rl_device_preference"] = heartbeat.get("rl_device_preference")
                        stage1_state["rl_execution_mode"] = heartbeat.get("rl_execution_mode")
                        stage1_state["rl_vec_env_type"] = heartbeat.get("rl_vec_env_type")
                        if heartbeat.get("rl_platform_guard_applied") is not None:
                            stage1_state["rl_platform_guard_applied"] = bool(heartbeat.get("rl_platform_guard_applied"))
                        if heartbeat.get("rl_init_completed") is not None:
                            stage1_state["rl_init_completed"] = bool(heartbeat.get("rl_init_completed"))
                        if heartbeat.get("rl_first_progress_seen") is not None:
                            stage1_state["rl_first_progress_seen"] = bool(heartbeat.get("rl_first_progress_seen"))
                        if heartbeat.get("rl_init_timeout_s") is not None:
                            stage1_state["rl_init_timeout_s"] = heartbeat.get("rl_init_timeout_s")
                        if heartbeat.get("rl_first_progress_timeout_s") is not None:
                            stage1_state["rl_first_progress_timeout_s"] = heartbeat.get("rl_first_progress_timeout_s")
                        if "rl_failure_driver" in heartbeat:
                            stage1_state["rl_failure_driver"] = heartbeat.get("rl_failure_driver")
                        if "rl_cleanup_completed" in heartbeat:
                            stage1_state["rl_cleanup_completed"] = heartbeat.get("rl_cleanup_completed")
                        if heartbeat.get("rl_cleanup_warnings") is not None:
                            stage1_state["rl_cleanup_warnings"] = list(heartbeat.get("rl_cleanup_warnings") or [])
                    if event in {"rl_progress", "rl_completed"}:
                        stage1_state["rl_timing_count"] = int(stage1_state.get("rl_timing_count", 0) or 0) + 1
                        stage1_state["rl_episode_runtime_sum_s"] = float(stage1_state.get("rl_episode_runtime_sum_s", 0.0) or 0.0) + float(
                            heartbeat.get("rl_avg_episode_runtime_s", 0.0) or 0.0
                        )
                        stage1_state["rl_cache_hit_sum"] = float(stage1_state.get("rl_cache_hit_sum", 0.0) or 0.0) + float(
                            heartbeat.get("rl_cache_hit_rate", 0.0) or 0.0
                        )
                        stage1_state["rl_event_loop_sum_s"] = float(stage1_state.get("rl_event_loop_sum_s", 0.0) or 0.0) + float(
                            heartbeat.get("rl_avg_event_loop_s", 0.0) or 0.0
                        )
                elif event == "stage1_finalize":
                    subphase = "stage1_finalize"
                running_event = event in {"trial_running", "scheduler_tick"}
                completion_event = event == "trial_completed"
                if subphase == "stage1_optuna_search" and event == "trial_completed":
                    stage1_state["optuna_trials_completed"] = max(
                        int(stage1_state.get("optuna_trials_completed", 0) or 0),
                        normalized_completed,
                    )
                    if stage1_state.get("stage1_search_mode") == "fallback_5m":
                        stage1_state["fallback_optuna_trials_completed"] = max(
                            int(stage1_state.get("fallback_optuna_trials_completed", 0) or 0),
                            normalized_completed,
                        )
                    else:
                        stage1_state["primary_optuna_trials_completed"] = max(
                            int(stage1_state.get("primary_optuna_trials_completed", 0) or 0),
                            normalized_completed,
                        )
                    stage1_state["timing_count"] = int(stage1_state.get("timing_count", 0) or 0) + 1
                    stage1_state["trial_runtime_sum_s"] = float(stage1_state.get("trial_runtime_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("trial_runtime_s", 0.0) or 0.0
                    )
                    stage1_state["context_build_sum_s"] = float(stage1_state.get("context_build_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("replay_context_build_s", 0.0) or 0.0
                    )
                    stage1_state["replay_run_sum_s"] = float(stage1_state.get("replay_run_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("event_publish_run_s", 0.0) or 0.0
                    )
                    stage1_state["cache_hit_sum"] = float(stage1_state.get("cache_hit_sum", 0.0) or 0.0) + float(
                        heartbeat.get("cache_hit", 0.0) or 0.0
                    )
                    stage1_state["event_loop_sum_s"] = float(stage1_state.get("event_loop_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("event_loop_total_s", 0.0) or 0.0
                    )
                    stage1_state["metrics_finalize_sum_s"] = float(stage1_state.get("metrics_finalize_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("metrics_finalize_s", 0.0) or 0.0
                    )
                    stage1_state["diagnostics_finalize_sum_s"] = float(stage1_state.get("diagnostics_finalize_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("diagnostics_finalize_s", 0.0) or 0.0
                    )
                    stage1_state["terminated_early_sum"] = float(stage1_state.get("terminated_early_sum", 0.0) or 0.0) + float(
                        heartbeat.get("terminated_early", 0.0) or 0.0
                    )
                    stage1_state["indicator_step_sum_s"] = float(stage1_state.get("indicator_step_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("indicator_step_total_s", 0.0) or 0.0
                    )
                    stage1_state["strategy_step_sum_s"] = float(stage1_state.get("strategy_step_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("strategy_step_total_s", 0.0) or 0.0
                    )
                    stage1_state["risk_step_sum_s"] = float(stage1_state.get("risk_step_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("risk_step_total_s", 0.0) or 0.0
                    )
                    stage1_state["execution_step_sum_s"] = float(stage1_state.get("execution_step_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("execution_step_total_s", 0.0) or 0.0
                    )
                    stage1_state["accounting_step_sum_s"] = float(stage1_state.get("accounting_step_sum_s", 0.0) or 0.0) + float(
                        heartbeat.get("accounting_step_total_s", 0.0) or 0.0
                    )
                    stage1_state["replay_context_cache_hit_sum"] = float(
                        stage1_state.get("replay_context_cache_hit_sum", 0.0) or 0.0
                    ) + float(heartbeat.get("replay_context_cache_hit_rate", 0.0) or 0.0)
                    stage1_state["raw_source_rows_cache_hit_sum"] = float(
                        stage1_state.get("raw_source_rows_cache_hit_sum", 0.0) or 0.0
                    ) + float(heartbeat.get("raw_source_rows_cache_hit_rate", 0.0) or 0.0)
                    stage1_state["event_list_cache_hit_sum"] = float(
                        stage1_state.get("event_list_cache_hit_sum", 0.0) or 0.0
                    ) + float(heartbeat.get("event_list_cache_hit_rate", 0.0) or 0.0)
                    layer_timing = dict(heartbeat.get("indicator_layer_timing", {}) or {})
                    if layer_timing:
                        layer_totals = dict(stage1_state.get("indicator_layer_timing_totals") or {})
                        for layer_name, layer_value in layer_timing.items():
                            layer_totals[layer_name] = float(layer_totals.get(layer_name, 0.0) or 0.0) + float(
                                layer_value or 0.0
                            )
                        stage1_state["indicator_layer_timing_totals"] = layer_totals
                    if "indicator_fast_path_enabled" in heartbeat:
                        stage1_state["indicator_fast_path_enabled"] = bool(heartbeat.get("indicator_fast_path_enabled"))
                    if "slow_enrichment_deferred" in heartbeat:
                        stage1_state["slow_enrichment_deferred"] = bool(heartbeat.get("slow_enrichment_deferred"))
                if subphase == "stage1_optuna_search" and running_event:
                    stage1_state["stage1_underfilled"] = bool(stage1_underfilled)
                    stage1_state["stage1_underfilled_reason"] = stage1_underfilled_reason
                if stage1_budget_mode is not None:
                    stage1_state["stage1_budget_mode"] = stage1_budget_mode
                if stage1_stop_reason is not None:
                    stage1_state["stage1_stop_reason"] = stage1_stop_reason
                timing_count = int(stage1_state.get("timing_count", 0) or 0)
                rl_timing_count = int(stage1_state.get("rl_timing_count", 0) or 0)
                cpu_hotspot_summary = _stage1_cpu_hotspot_summary(stage1_state, timing_count=timing_count)
                indicator_hotspot_summary = _stage1_indicator_hotspot_summary(stage1_state, timing_count=timing_count)
                _update_status(
                    status_path,
                    phase=subphase,
                    status="running",
                    stage1_optuna_trials_total=(
                        stage1_state.get("fallback_optuna_trials_total", 1)
                        if stage1_state.get("stage1_search_mode") == "fallback_5m"
                        else optuna_trials
                    ),
                    stage1_search_mode=stage1_state.get("stage1_search_mode", "primary_1h" if args.budget == "smoke" else "primary_5m"),
                    stage1_primary_optuna_trials_completed=stage1_state.get("primary_optuna_trials_completed", 0),
                    stage1_primary_optuna_trials_total=stage1_state.get("primary_optuna_trials_total", optuna_trials),
                    stage1_fallback_optuna_trials_completed=stage1_state.get("fallback_optuna_trials_completed", 0),
                    stage1_fallback_optuna_trials_total=stage1_state.get("fallback_optuna_trials_total", 0),
                    optuna_trials_completed=(
                        int(stage1_state.get("optuna_trials_completed", 0) or 0)
                        if subphase != "stage1_optuna_search"
                        else max(int(stage1_state.get("optuna_trials_completed", 0) or 0), normalized_completed)
                    ),
                    rl_episodes_completed=normalized_completed if subphase == "stage1_rl" else 0,
                    stage1_execution_mode=stage1_execution_mode,
                    authoritative_verdict_execution_mode="5m_enabled",
                    stage1_current_trial=current_trial,
                    stage1_last_trial_at=_ts() if completion_event else None,
                    stage1_pending_trials=pending_trials if subphase == "stage1_optuna_search" else None,
                    stage1_max_workers=heartbeat_max_workers if subphase == "stage1_optuna_search" else None,
                    stage1_target_inflight=heartbeat_target_inflight if subphase == "stage1_optuna_search" else None,
                    stage1_active_workers_estimate=stage1_active_workers_estimate if subphase == "stage1_optuna_search" else None,
                    stage1_scheduler_mode=stage1_scheduler_mode if subphase == "stage1_optuna_search" else None,
                    stage1_underfilled=(
                        bool(stage1_state.get("stage1_underfilled", False))
                        if subphase == "stage1_optuna_search"
                        else None
                    ),
                    stage1_underfilled_reason=(
                        stage1_state.get("stage1_underfilled_reason")
                        if subphase == "stage1_optuna_search"
                        else None
                    ),
                    stage1_avg_trial_runtime_s=(
                        round(float(stage1_state.get("trial_runtime_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_context_build_s=(
                        round(float(stage1_state.get("context_build_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_replay_run_s=(
                        round(float(stage1_state.get("replay_run_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_cache_hit_rate=(
                        round(float(stage1_state.get("cache_hit_sum", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    trial_replay_cache_hit_rate=(
                        round(float(stage1_state.get("cache_hit_sum", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    replay_context_cache_hit_rate=(
                        round(float(stage1_state.get("replay_context_cache_hit_sum", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_event_loop_s=(
                        round(float(stage1_state.get("event_loop_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_metrics_finalize_s=(
                        round(float(stage1_state.get("metrics_finalize_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_diagnostics_finalize_s=(
                        round(float(stage1_state.get("diagnostics_finalize_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_indicator_step_s=(
                        round(float(stage1_state.get("indicator_step_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_strategy_step_s=(
                        round(float(stage1_state.get("strategy_step_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_risk_step_s=(
                        round(float(stage1_state.get("risk_step_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_execution_step_s=(
                        round(float(stage1_state.get("execution_step_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_avg_accounting_step_s=(
                        round(float(stage1_state.get("accounting_step_sum_s", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_terminated_early_rate=(
                        round(float(stage1_state.get("terminated_early_sum", 0.0) or 0.0) / timing_count, 4)
                        if timing_count > 0
                        else None
                    ),
                    stage1_budget_mode=stage1_state.get("stage1_budget_mode"),
                    stage1_stop_reason=stage1_state.get("stage1_stop_reason"),
                    rl_budget_mode=heartbeat.get("rl_budget_mode") if subphase == "stage1_rl" else None,
                    rl_wall_clock_elapsed_s=heartbeat.get("rl_wall_clock_elapsed_s") if subphase == "stage1_rl" else None,
                    rl_stop_reason=heartbeat.get("rl_stop_reason") if subphase == "stage1_rl" else None,
                    rl_active_envs=heartbeat.get("rl_active_envs") if subphase == "stage1_rl" else None,
                    rl_avg_episode_runtime_s=(
                        round(float(stage1_state.get("rl_episode_runtime_sum_s", 0.0) or 0.0) / rl_timing_count, 4)
                        if rl_timing_count > 0
                        else None
                    ),
                    rl_cache_hit_rate=(
                        round(float(stage1_state.get("rl_cache_hit_sum", 0.0) or 0.0) / rl_timing_count, 4)
                        if rl_timing_count > 0
                        else None
                    ),
                    rl_avg_event_loop_s=(
                        round(float(stage1_state.get("rl_event_loop_sum_s", 0.0) or 0.0) / rl_timing_count, 4)
                        if rl_timing_count > 0
                        else None
                    ),
                    rl_device=stage1_state.get("rl_device"),
                    rl_device_resolved_from=stage1_state.get("rl_device_resolved_from"),
                    rl_device_fallback_reason=stage1_state.get("rl_device_fallback_reason"),
                    rl_device_preference=stage1_state.get("rl_device_preference"),
                    rl_execution_mode=stage1_state.get("rl_execution_mode"),
                    rl_vec_env_type=stage1_state.get("rl_vec_env_type"),
                    rl_platform_guard_applied=stage1_state.get("rl_platform_guard_applied"),
                    rl_init_completed=stage1_state.get("rl_init_completed"),
                    rl_first_progress_seen=stage1_state.get("rl_first_progress_seen"),
                    rl_init_timeout_s=stage1_state.get("rl_init_timeout_s"),
                    rl_first_progress_timeout_s=stage1_state.get("rl_first_progress_timeout_s"),
                    rl_failure_driver=stage1_state.get("rl_failure_driver"),
                    rl_cleanup_completed=stage1_state.get("rl_cleanup_completed"),
                    rl_cleanup_warnings=stage1_state.get("rl_cleanup_warnings"),
                    stage1_cpu_hotspot_summary=cpu_hotspot_summary,
                    stage1_indicator_hotspot_summary=indicator_hotspot_summary,
                    indicator_fast_path_enabled=stage1_state.get("indicator_fast_path_enabled"),
                    slow_enrichment_deferred=stage1_state.get("slow_enrichment_deferred"),
                    stage1_best_value_so_far=heartbeat.get("best_value_so_far"),
                    stage1_best_params_so_far=heartbeat.get("best_params_so_far"),
                    stage1_runtime_s=runtime_s,
                    stage1_slow_trial=slow_trial,
                    stage1_slow_reason="five_min_train_backtest_heavy" if slow_trial else None,
                    stage1_subphase=subphase,
                    stage1_progress_completed=normalized_completed,
                    stage1_progress_total=normalized_total,
                    stage1_actual_steps=actual_steps if subphase == "stage1_rl" else None,
                    stage1_requested_steps=requested_steps if subphase == "stage1_rl" else None,
                    stage1_last_progress_at=_ts(),
                    stage1_last_completed_unit=(
                        "trial_completed"
                        if completion_event
                        else "trial_running"
                        if running_event
                        else event
                    ),
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
                    no_tuned_candidate_reason=stage1_state.get("no_tuned_candidate_reason"),
                )
                if event in {
                    "trial_completed",
                    "ranking_candidate_completed",
                    "robustness_seed_completed",
                    "rl_progress",
                    "rl_completed",
                    "stage1_finalize",
                }:
                    suffix = ""
                    if event in {"rl_progress", "rl_completed"}:
                        suffix = f" actual={actual_steps} requested={requested_steps or normalized_total}"
                    _append_campaign_log(
                        log_path,
                        f"{subphase} event={event} completed={normalized_completed}/{normalized_total}{suffix}",
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
                no_tuned_candidate_reason=stage1_state.get("no_tuned_candidate_reason"),
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
                seed_trials: list[dict[str, Any]] | None = None,
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
                    seed_trials=seed_trials,
                    stage1_parallel_mode=stage1_parallel_mode,
                    stage1_max_workers=stage1_max_workers,
                    stage1_target_inflight=stage1_target_inflight,
                    stage1_batch_size=stage1_batch_size,
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
                            "max_hostile_proxy_score": max(
                                (
                                    float((item.get("train_metrics") or item.get("metrics") or {}).get("hostile_proxy_score", 0.0) or 0.0)
                                    for item in ranked
                                ),
                                default=0.0,
                            ),
                            "hostile_proxy_penalty_applied": max(
                                (
                                    float(
                                        (item.get("train_metrics") or item.get("metrics") or {}).get(
                                            "hostile_proxy_penalty_applied",
                                            0.0,
                                        )
                                        or 0.0
                                    )
                                    for item in ranked
                                ),
                                default=0.0,
                            ),
                            "min_execution_fragility_score": min(
                                (
                                    float((item.get("train_metrics") or item.get("metrics") or {}).get("execution_fragility_score", 0.0) or 0.0)
                                    for item in ranked
                                ),
                                default=0.0,
                            ),
                            "min_inventory_path_dependence_score": min(
                                (
                                    float((item.get("train_metrics") or item.get("metrics") or {}).get("inventory_path_dependence_score", 0.0) or 0.0)
                                    for item in ranked
                                ),
                                default=0.0,
                            ),
                        },
                        "empty_shortlist_reason": None if ranked else "all_negative_primary_metric",
                        "no_tuned_candidate_reason": None if ranked else "all_negative_primary_metric",
                    }
                    return best_params, ranked, diagnostics
                return result
            try:
                optuna_params, optuna_trials_payload, optuna_diagnostics = _run_stage1_optuna_search(
                    search_execution_timeframe=Timeframe.TF_1H if args.budget == "smoke" else evaluator._base_config.execution_timeframe,
                    search_context_timeframe=Timeframe.TF_1H if args.budget == "smoke" else evaluator._base_config.context_timeframe,
                    trial_timeout_s=720.0 if args.budget == "smoke" else None,
                    stage_total_timeout_s=900.0 if args.budget == "smoke" else None,
                    ranking_timeout_s=120.0 if args.budget == "smoke" else None,
                    ranking_top_n=smoke_ranking_top_n,
                    ranking_candidate_timeout_s=smoke_ranking_candidate_timeout_s,
                    seed_trials=extra_seed_trials,
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
            stage1_state["primary_optuna_trials_completed"] = int(stage1_state.get("optuna_trials_completed", 0) or 0)
            stage1_state["primary_optuna_trials_total"] = optuna_trials
            if args.budget == "smoke" and optuna_params is None and not optuna_trials_payload:
                stage1_state["used_5m_fallback"] = True
                stage1_state["stage1_search_mode"] = "fallback_5m"
                stage1_state["fallback_optuna_trials_completed"] = 0
                stage1_state["fallback_optuna_trials_total"] = 1
                stage1_state["optuna_trials_completed"] = 0
                stage1_execution_mode = "1h_context_only_with_5m_fallback"
                _append_campaign_log(
                    log_path,
                    "stage1_optuna_search fallback_to_5m reason=empty_train_shortlist n_trials=1",
                )
                _update_status(
                    status_path,
                    phase="stage1_optuna_search",
                    status="running",
                    stage1_optuna_trials_total=1,
                    stage1_execution_mode=stage1_execution_mode,
                    stage1_subphase="stage1_optuna_search",
                    stage1_search_mode="fallback_5m",
                    stage1_primary_optuna_trials_completed=stage1_state.get("primary_optuna_trials_completed", 0),
                    stage1_primary_optuna_trials_total=stage1_state.get("primary_optuna_trials_total", optuna_trials),
                    stage1_fallback_optuna_trials_completed=0,
                    stage1_fallback_optuna_trials_total=1,
                    stage1_last_progress_at=_ts(),
                    stage1_last_completed_unit="fallback_to_5m_search",
                    optuna_trials_completed=0,
                    stage1_progress_completed=0,
                    stage1_progress_total=1,
                    stage1_current_trial=0,
                    stage1_pending_trials=0,
                    stage1_max_workers=1,
                    stage1_target_inflight=1,
                    stage1_active_workers_estimate=0,
                    stage1_scheduler_mode="serial_timeout_guarded",
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
                        seed_trials=extra_seed_trials,
                    )
                    stage1_state["fallback_optuna_trials_completed"] = int(stage1_state.get("optuna_trials_completed", 0) or 0)
                except SearchStageTimeoutError as exc:
                    _append_campaign_log(log_path, f"stage1_optuna_search fallback_5m_failed reason={exc}")
                    _update_status(
                        status_path,
                        stage1_optuna_trials_total=1,
                        stage1_search_mode="fallback_5m",
                        stage1_primary_optuna_trials_completed=stage1_state.get("primary_optuna_trials_completed", 0),
                        stage1_primary_optuna_trials_total=stage1_state.get("primary_optuna_trials_total", optuna_trials),
                        stage1_fallback_optuna_trials_completed=stage1_state.get("fallback_optuna_trials_completed", 0),
                        stage1_fallback_optuna_trials_total=1,
                        stage1_failure_reason=str(exc),
                    )
                    raise
                except Exception as exc:
                    _append_campaign_log(log_path, f"stage1_optuna_search fallback_5m_failed reason={exc}")
                    _update_status(
                        status_path,
                        stage1_optuna_trials_total=1,
                        stage1_search_mode="fallback_5m",
                        stage1_primary_optuna_trials_completed=stage1_state.get("primary_optuna_trials_completed", 0),
                        stage1_primary_optuna_trials_total=stage1_state.get("primary_optuna_trials_total", optuna_trials),
                        stage1_fallback_optuna_trials_completed=stage1_state.get("fallback_optuna_trials_completed", 0),
                        stage1_fallback_optuna_trials_total=1,
                        stage1_failure_reason=str(exc),
                    )
                    raise
            stage1_state["train_candidate_diagnostics"] = dict(optuna_diagnostics.get("train_candidate_diagnostics") or {})
            stage1_state["empty_shortlist_reason"] = optuna_diagnostics.get("empty_shortlist_reason")
            stage1_state["no_tuned_candidate_reason"] = optuna_diagnostics.get("no_tuned_candidate_reason")
            stage1_state["stage1_budget_mode"] = optuna_diagnostics.get("stage1_budget_mode", stage1_state.get("stage1_budget_mode"))
            stage1_state["stage1_stop_reason"] = optuna_diagnostics.get("stage1_stop_reason", stage1_state.get("stage1_stop_reason"))
            stage1_state["optuna_params"] = optuna_params
            stage1_state["optuna_trials_payload"] = optuna_trials_payload
            if stage1_state.get("stage1_stop_reason") == "wall_clock_budget_reached":
                _append_campaign_log(
                    log_path,
                    "stage1_optuna_search budget_reached mode=wall_clock_primary "
                    f"completed={int(stage1_state.get('optuna_trials_completed', 0) or 0)}/{optuna_trials}",
                )
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
                rl_base_config = (
                    replace(
                        base_config,
                        research_compute_mode="lightweight_train_search",
                        profiling_enabled=True,
                        early_stop_enabled=True,
                        early_stop_trade_check_ratio=0.25,
                        early_stop_min_trade_count=20,
                        early_stop_trade_floor_ratio=0.25,
                        early_stop_max_drawdown=0.35,
                        trial_runtime_budget_s=90.0,
                    )
                    if args.budget == "fast"
                    else replace(
                        base_config,
                        research_compute_mode="authoritative",
                        profiling_enabled=True,
                        early_stop_enabled=False,
                        trial_runtime_budget_s=None,
                    )
                )
                rl_budget_mode = "wall_clock_primary" if args.budget == "fast" else "episode_primary"
                rl_wall_clock_budget_s = 180.0 if args.budget == "fast" else smoke_rl_timeout_s
                rl_max_episodes = min(12, rl_episodes) if args.budget == "fast" else rl_episodes
                rl_n_envs = 3 if args.budget == "fast" else 1
                rl_device_preference = "auto"
                rl_execution_mode = "default"
                rl_vec_env_type = "subproc_vec_env" if rl_n_envs > 1 else "dummy_vec_env"
                rl_platform_guard_applied = False
                rl_init_timeout_s = 30.0 if args.budget == "fast" else 60.0
                rl_first_progress_timeout_s = 45.0 if args.budget == "fast" else 90.0
                if args.budget == "fast" and sys.platform == "darwin":
                    rl_n_envs = 1
                    rl_device_preference = "cpu"
                    rl_execution_mode = "single_env_cpu_guarded"
                    rl_vec_env_type = "dummy_vec_env"
                    rl_platform_guard_applied = True
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
                    stage1_progress_total=rl_max_episodes,
                    stage1_last_progress_at=_ts(),
                    stage1_last_completed_unit="ranking_completed",
                    rl_budget_mode=rl_budget_mode,
                    rl_active_envs=rl_n_envs,
                    rl_device_preference=rl_device_preference,
                    rl_execution_mode=rl_execution_mode,
                    rl_vec_env_type=rl_vec_env_type,
                    rl_platform_guard_applied=rl_platform_guard_applied,
                    rl_init_completed=False,
                    rl_first_progress_seen=False,
                    rl_init_timeout_s=rl_init_timeout_s,
                    rl_first_progress_timeout_s=rl_first_progress_timeout_s,
                    rl_failure_driver=None,
                    train_candidate_diagnostics=dict(stage1_state.get("train_candidate_diagnostics") or {}),
                    empty_shortlist_reason=stage1_state.get("empty_shortlist_reason"),
                    no_tuned_candidate_reason=stage1_state.get("no_tuned_candidate_reason"),
                )
                _append_campaign_log(log_path, "stage1_rl started")
                try:
                    rl_result = train_tuner(
                        total_timesteps=rl_max_episodes,
                        verbose=0,
                        param_grid=evaluator.search_param_grid,
                        split_spec=split_spec,
                        base_config=rl_base_config,
                        selection_split="train",
                        status_callback=_stage1_heartbeat,
                        total_timeout_s=rl_wall_clock_budget_s,
                        budget_mode=rl_budget_mode,
                        wall_clock_budget_s=rl_wall_clock_budget_s,
                        max_episodes=rl_max_episodes,
                        n_envs=rl_n_envs,
                        seed_params=optuna_params if args.budget == "fast" else None,
                        device_preference=rl_device_preference,
                        init_timeout_s=rl_init_timeout_s,
                        first_progress_timeout_s=rl_first_progress_timeout_s,
                    )
                except Exception as exc:
                    stage1_state["rl_failure_reason"] = str(exc)
                    rl_failure_driver = str(exc)
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
                        rl_failure_driver=rl_failure_driver,
                        rl_platform_guard_applied=rl_platform_guard_applied,
                        rl_device_preference=rl_device_preference,
                        rl_execution_mode=rl_execution_mode,
                        rl_vec_env_type=rl_vec_env_type,
                        rl_init_timeout_s=rl_init_timeout_s,
                        rl_first_progress_timeout_s=rl_first_progress_timeout_s,
                        full_training_allowed=False,
                        full_training_decision="defer_full_training",
                        full_training_blockers=["smoke_campaign_incomplete", "stage1_rl_sidecar_failed"],
                    )
                    _append_campaign_log(log_path, f"stage1_rl non_blocking_failure reason={exc}")
                    rl_result = None
                rl_search_status = dict((rl_result or {}).get("search_status") or {})
                if rl_search_status.get("rl_device") is not None:
                    stage1_state["rl_device"] = rl_search_status.get("rl_device")
                    stage1_state["rl_device_resolved_from"] = rl_search_status.get("rl_device_resolved_from")
                    stage1_state["rl_device_fallback_reason"] = rl_search_status.get("rl_device_fallback_reason")
                    stage1_state["rl_cleanup_completed"] = rl_search_status.get("rl_cleanup_completed")
                    stage1_state["rl_cleanup_warnings"] = list(rl_search_status.get("rl_cleanup_warnings") or [])
            else:
                skip_reason = (
                    "budget=smoke"
                    if args.budget == "smoke"
                    else "budget=disabled"
                )
                _append_campaign_log(log_path, f"stage1_rl skipped {skip_reason}")
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
                    "development_stage1_train_only",
                    "no_train_candidate_from_stage1",
                ]
                stage1_best_score = 0.0
            else:
                stage1_assessment = {
                    "ready": False,
                    "decision": "defer_full_training",
                    "blockers": ["development_stage1_train_only"],
                }
                stage1_best_score = max(
                    [
                        summary.robust_score
                        for summary in (optuna_summary, rl_summary)
                        if summary is not None
                    ],
                    default=0.0,
                )
                stage1_payload["search_status"]["full_training_allowed"] = stage1_assessment["ready"]
                stage1_payload["search_status"]["full_training_decision"] = stage1_assessment["decision"]
                stage1_payload["search_status"]["full_training_blockers"] = stage1_assessment["blockers"]
            stage1_payload["search_status"]["stage1_best_score"] = stage1_best_score
            stage1_payload["search_status"]["stage1_budget_mode"] = stage1_state.get("stage1_budget_mode")
            stage1_payload["search_status"]["stage1_stop_reason"] = stage1_state.get("stage1_stop_reason")
            candidate_checkpoint_payload = _candidate_checkpoint_payload(
                round_index=_round_index_from_output_dir(output_dir),
                budget=args.budget,
                split_contract_version=split_spec.version,
                search_space_mode=stage1_payload["search_status"].get("search_space_mode"),
                stage1_execution_mode=stage1_payload["search_status"].get("stage1_execution_mode"),
                train_only_search=stage1_payload["search_status"].get("train_only_search"),
                train_candidate_diagnostics=stage1_payload["search_status"].get("train_candidate_diagnostics"),
                stage1_best_score=stage1_best_score,
                stage1_best_params_so_far=stage1_payload.get("optuna_params") or stage1_payload.get("rl_params"),
                optuna_summary=asdict(optuna_summary) if optuna_summary is not None else None,
                rl_summary=asdict(rl_summary) if rl_summary is not None else None,
                optuna_params=stage1_payload.get("optuna_params"),
                rl_params=stage1_payload.get("rl_params"),
                checkpoint_status="usable",
                blocked_by=None,
                failure_reason=stage1_payload["search_status"].get("failure_reason"),
                origin_phase="stage1",
            )
            written_candidate_checkpoint = _write_candidate_checkpoint(output_dir, candidate_checkpoint_payload)
            if written_candidate_checkpoint is not None:
                _append_campaign_log(
                    log_path,
                    "candidate_checkpoint_written "
                    f"variant={written_candidate_checkpoint.get('candidate_variant')} origin=stage1",
                )
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
            stage1_optuna_trials_total=optuna_trials,
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
            stage1_budget_mode=stage1_payload["search_status"].get("stage1_budget_mode"),
            stage1_stop_reason=stage1_payload["search_status"].get("stage1_stop_reason"),
            stage1_avg_indicator_step_s=_read_json(status_path).get("stage1_avg_indicator_step_s") if _read_json(status_path) else None,
            stage1_avg_strategy_step_s=_read_json(status_path).get("stage1_avg_strategy_step_s") if _read_json(status_path) else None,
            stage1_avg_risk_step_s=_read_json(status_path).get("stage1_avg_risk_step_s") if _read_json(status_path) else None,
            stage1_avg_execution_step_s=_read_json(status_path).get("stage1_avg_execution_step_s") if _read_json(status_path) else None,
            stage1_avg_accounting_step_s=_read_json(status_path).get("stage1_avg_accounting_step_s") if _read_json(status_path) else None,
            train_candidate_diagnostics=stage1_payload["search_status"].get("train_candidate_diagnostics"),
            empty_shortlist_reason=stage1_payload["search_status"].get("empty_shortlist_reason"),
            no_tuned_candidate_reason=stage1_payload["search_status"].get("no_tuned_candidate_reason"),
            candidate_checkpoint_written=written_candidate_checkpoint is not None,
            candidate_checkpoint_status=(written_candidate_checkpoint or {}).get("checkpoint_status"),
            candidate_checkpoint_variant=(written_candidate_checkpoint or {}).get("candidate_variant"),
            candidate_checkpoint_score=((written_candidate_checkpoint or {}).get("selected_checkpoint_candidate") or {}).get("score"),
            candidate_checkpoint_origin_phase=(written_candidate_checkpoint or {}).get("origin_phase"),
        )

        stage2_path = checkpoint_dir / "stage2_full_report.json"
        final_report_payload = _read_json(stage2_path) if args.resume else None
        if final_report_payload is None:
            stage2_runtime_contract = dict(getattr(evaluator, "stage2_runtime_contract", {}) or {})
            stage2_started_at = datetime.now(UTC).timestamp()
            stage2_state = {
                "completed": 0,
                "total": 0,
                "last_progress_at": stage2_started_at,
                "last_unit": None,
                "current_subphase": "stage2_prepare_variants",
                "subphase_started_at": stage2_started_at,
                "prepare_variant_cache_hits": 0,
                "prepare_variant_replay_reused": 0,
                "prepare_variant_cache_observations": 0,
                "prepare_timeout_s": None,
            }

            stage2_budgets = {level: _stage2_budget_profile(level) for level in ("smoke", "fast", "high")}
            budget_key = "smoke" if args.budget == "smoke" else ("high" if args.budget == "high" else "fast")

            def stage2_watchdog_reset(*_args: Any, **_kwargs: Any) -> None:
                return None

            def _stage2_timeout_check() -> None:
                now = datetime.now(UTC).timestamp()
                current_subphase = stage2_state.get("current_subphase") or (_read_json(status_path) or {}).get("stage2_subphase")
                total_timeout = float(stage2_budgets[budget_key]["total"])
                subphase_timeout = float(stage2_budgets[budget_key]["subphases"].get(current_subphase, total_timeout))
                no_progress_timeout = float(stage2_budgets[budget_key]["no_progress"].get(current_subphase, subphase_timeout))
                if (now - stage2_started_at) > total_timeout:
                    raise SearchStageTimeoutError("stage2_total_timeout")
                if (now - float(stage2_state.get("subphase_started_at") or stage2_started_at)) > subphase_timeout:
                    raise SearchStageTimeoutError(
                        f"{current_subphase}_total_timeout: last_unit={stage2_state['last_unit']}"
                    )
                if (now - stage2_state["last_progress_at"]) > no_progress_timeout:
                    raise SearchStageTimeoutError(
                        f"{current_subphase}_no_progress_timeout: subphase={( _read_json(status_path) or {}).get('stage2_subphase')} "
                        f"last_unit={stage2_state['last_unit']}"
                    )

            def _stage2_heartbeat(**heartbeat: Any) -> None:
                _stage2_timeout_check()
                event = heartbeat.get("event", "stage2_progress")
                subphase = "stage2_full_verdict"
                if event in {
                    "stage2_prepare_variants_started",
                    "stage2_prepare_variants_progress",
                    "stage2_prepare_variant_unit_completed",
                    "stage2_prepare_variant_validation_progress",
                }:
                    subphase = "stage2_prepare_variants"
                elif event in {
                    "stage2_compare_windows_started",
                    "comparison_window_completed",
                }:
                    subphase = "stage2_compare_windows"
                elif event in {
                    "comparison_validation_started",
                    "comparison_validation_window_train_started",
                    "comparison_validation_window_train_completed",
                    "comparison_validation_window_test_started",
                    "comparison_validation_window_test_completed",
                    "comparison_validation_completed",
                }:
                    subphase = "stage2_compare_validation"
                elif event in {"comparison_ranking_completed", "stage2_rank_variants_completed"}:
                    subphase = "stage2_rank_variants"
                elif event in {"stage2_write_artifacts_started", "stage2_write_artifacts_completed"}:
                    subphase = "stage2_write_artifacts"

                now_ts = datetime.now(UTC).timestamp()
                if stage2_state.get("current_subphase") != subphase:
                    stage2_state["current_subphase"] = subphase
                    stage2_state["subphase_started_at"] = now_ts

                completed = int(heartbeat.get("completed_units", stage2_state["completed"]) or 0)
                total = int(heartbeat.get("total_units", stage2_state["total"]) or 0)
                stage2_state["completed"] = completed
                stage2_state["total"] = total
                stage2_state["last_progress_at"] = now_ts
                stage2_state["last_unit"] = (
                    heartbeat.get("unit")
                    or heartbeat.get("window_label")
                    or heartbeat.get("variant")
                    or event
                )
                unit_label = str(stage2_state["last_unit"] or "")
                _update_stage2_prepare_cache_metrics(stage2_state, event=event, unit_label=unit_label)
                no_progress_timeout = float(
                    stage2_budgets[budget_key]["no_progress"].get(
                        subphase,
                        stage2_budgets[budget_key]["subphases"].get(subphase, stage2_budgets[budget_key]["total"]),
                    )
                )
                if subphase == "stage2_prepare_variants":
                    stage2_state["prepare_timeout_s"] = float(
                        stage2_budgets[budget_key]["no_progress"].get("stage2_prepare_variants", 0.0)
                    )
                stage2_watchdog_reset(
                    no_progress_timeout,
                    f"{subphase}_no_progress_timeout: subphase={subphase} last_unit={stage2_state['last_unit']}",
                )
                _update_status(
                    status_path,
                    phase="stage2_full_verdict",
                    status="running",
                    stage2_subphase=subphase,
                    stage2_progress_completed=completed,
                    stage2_progress_total=total,
                    stage2_last_progress_at=_ts(),
                    stage2_last_completed_unit=stage2_state["last_unit"],
                    stage2_compare_window_units_completed=heartbeat.get(
                        "compare_window_units_completed",
                        heartbeat.get("window_units_completed"),
                    ),
                    stage2_compare_window_units_total=heartbeat.get(
                        "compare_window_units_total",
                        heartbeat.get("window_units_total"),
                    ),
                    stage2_compare_validation_units_completed=heartbeat.get(
                        "compare_validation_units_completed",
                        heartbeat.get("validation_units_completed"),
                    ),
                    stage2_compare_validation_units_total=heartbeat.get(
                        "compare_validation_units_total",
                        heartbeat.get("validation_units_total"),
                    ),
                    stage2_compare_workers=heartbeat.get("compare_workers"),
                    stage2_compare_validation_workers=heartbeat.get("validation_workers"),
                    stage2_prepare_timeout_s=stage2_state.get("prepare_timeout_s"),
                    stage2_prepare_last_active_unit=(
                        stage2_state["last_unit"] if subphase == "stage2_prepare_variants" else None
                    ),
                    stage2_mp_start_method=stage2_runtime_contract.get("stage2_mp_start_method"),
                    validation_pool_mode=stage2_runtime_contract.get("validation_pool_mode"),
                    stage2_prepare_variant_cache_hits=stage2_state.get("prepare_variant_cache_hits", 0),
                    stage2_prepare_variant_replay_reused=stage2_state.get("prepare_variant_replay_reused", 0),
                    stage2_prepare_variant_miss_reasons=stage2_state.get("prepare_variant_miss_reasons", {}),
                    window_snapshot_cache_hit_rate=(
                        round(
                            float(stage2_state.get("prepare_variant_cache_hits", 0) or 0)
                            / max(1, int(stage2_state.get("prepare_variant_cache_observations", 0) or 0)),
                            4,
                        )
                        if int(stage2_state.get("prepare_variant_cache_observations", 0) or 0) > 0
                        else 0.0
                    ),
                    validation_cache_hit_rate=(
                        round(
                            float(stage2_state.get("prepare_variant_cache_hits", 0) or 0)
                            / max(1, int(stage2_state.get("prepare_variant_cache_observations", 0) or 0)),
                            4,
                        )
                        if int(stage2_state.get("prepare_variant_cache_observations", 0) or 0) > 0
                        else 0.0
                    ),
                    stage2_failure_reason=None,
                    stage2_prepare_deadlock_suspected=False,
                    stage2_prepare_failure_driver=None,
                    full_training_allowed=False,
                    full_training_decision="defer_full_training",
                    full_training_blockers=["smoke_campaign_incomplete"],
                )
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
                stage2_prepare_timeout_s=float(stage2_budgets[budget_key]["no_progress"].get("stage2_prepare_variants", 0.0)),
                stage2_prepare_last_active_unit=None,
                stage2_prepare_deadlock_suspected=False,
                stage2_prepare_failure_driver=None,
                stage2_mp_start_method=stage2_runtime_contract.get("stage2_mp_start_method"),
                validation_pool_mode=stage2_runtime_contract.get("validation_pool_mode"),
            )
            _append_campaign_log(log_path, "stage2_full_verdict started")
            smoke_stage2 = args.budget == "smoke"
            try:
                initial_no_progress_timeout = float(
                    stage2_budgets[budget_key]["no_progress"].get("stage2_prepare_variants", 0.0)
                )
                with _stage2_watchdog(initial_no_progress_timeout) as _reset_stage2_watchdog:
                    stage2_watchdog_reset = _reset_stage2_watchdog
                    if not args.development_dev_gate:
                        final_report_payload = _write_development_iteration_report(
                            output_dir=output_dir,
                            split_spec=split_spec,
                            base_config=base_config,
                            evaluator=evaluator,
                            stage1_payload=stage1_payload,
                        )
                        report = None
                        _write_json(stage2_path, final_report_payload)
                        _stage2_heartbeat(
                            event="stage2_write_artifacts_completed",
                            completed_units=1,
                            total_units=1,
                        )
                        _append_campaign_log(
                            log_path,
                            f"stage2_full_verdict complete best_variant={final_report_payload.get('best_variant')}",
                        )
                    elif stage1_payload.get("search_status", {}).get("search_outcome") == "empty":
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
                            evaluation_role="development",
                            test_access_allowed=False,
                            dev_gate_used_this_round=True,
                            dev_gate_last_round=args.dev_gate_last_round,
                            dev_gate_interval=args.dev_gate_interval,
                            full_stage2_executed_this_round=True,
                        )
                        _stage2_heartbeat(event="stage2_prepare_variants_progress", variant="no_tuned_candidate", completed_units=4, total_units=4)
                    else:
                        full_stage2_budget = args.budget == "high"
                        report = evaluator.final_report(
                            optuna_params=stage1_payload.get("optuna_params"),
                            rl_params=stage1_payload.get("rl_params"),
                            search_status=stage1_payload.get("search_status"),
                            full=full_stage2_budget,
                            evaluate_test=False,
                            include_comparison_payload=True,
                            status_callback=_stage2_heartbeat,
                            comparison_compute_mode="smoke_minimal" if smoke_stage2 else "full",
                            evaluation_role="development",
                            test_access_allowed=False,
                            dev_gate_used_this_round=True,
                            dev_gate_last_round=args.dev_gate_last_round,
                            dev_gate_interval=args.dev_gate_interval,
                            full_stage2_executed_this_round=full_stage2_budget,
                        )
                _stage2_timeout_check()
            except SearchStageTimeoutError as exc:
                existing_checkpoint = written_candidate_checkpoint if isinstance(written_candidate_checkpoint, dict) else None
                deadlock_suspected, failure_driver = _stage2_failure_driver(
                    str(exc),
                    rl_device=stage1_state.get("rl_device"),
                    stage2_mp_start_method=stage2_runtime_contract.get("stage2_mp_start_method"),
                )
                if existing_checkpoint is not None:
                    failure_reason = str(exc)
                    degraded_blockers = {"prepare_timeout", "pre_validation_rejection", "validation_timeout", "compare_timeout"}
                    degraded_reason = next((item for item in degraded_blockers if item in failure_reason), None)
                    updated_payload = dict(existing_checkpoint)
                    updated_payload["created_at"] = _ts()
                    updated_payload["checkpoint_status"] = "degraded" if degraded_reason else updated_payload.get("checkpoint_status", "usable")
                    updated_payload["blocked_by"] = degraded_reason
                    updated_payload["failure_reason"] = failure_reason
                    updated_payload["origin_phase"] = (
                        "stage2_prepare"
                        if "stage2_prepare" in failure_reason or degraded_reason == "prepare_timeout"
                        else "stage2_compare"
                    )
                    written_candidate_checkpoint = _write_candidate_checkpoint(output_dir, updated_payload)
                    _append_campaign_log(
                        log_path,
                        f"candidate_checkpoint_updated blocked_by={updated_payload.get('blocked_by') or 'none'}",
                    )
                _update_status(
                    status_path,
                    phase="stage2_full_verdict",
                    status="failed",
                    blocked_by="stage_runtime_or_search_issue",
                    failure_reason="stage2_runtime_or_search_issue",
                    failure_detail=str(exc),
                    stage2_failure_reason=str(exc),
                    stage2_prepare_deadlock_suspected=deadlock_suspected,
                    stage2_prepare_failure_driver=failure_driver,
                    stage2_prepare_last_active_unit=stage2_state.get("last_unit"),
                    stage2_prepare_timeout_s=stage2_state.get("prepare_timeout_s"),
                    stage2_mp_start_method=stage2_runtime_contract.get("stage2_mp_start_method"),
                    validation_pool_mode=stage2_runtime_contract.get("validation_pool_mode"),
                    rl_device=stage1_state.get("rl_device"),
                    rl_cleanup_completed=stage1_state.get("rl_cleanup_completed"),
                    rl_cleanup_warnings=stage1_state.get("rl_cleanup_warnings"),
                    candidate_checkpoint_written=written_candidate_checkpoint is not None,
                    candidate_checkpoint_status=(written_candidate_checkpoint or {}).get("checkpoint_status"),
                    candidate_checkpoint_variant=(written_candidate_checkpoint or {}).get("candidate_variant"),
                    candidate_checkpoint_score=((written_candidate_checkpoint or {}).get("selected_checkpoint_candidate") or {}).get("score"),
                    candidate_checkpoint_origin_phase=(written_candidate_checkpoint or {}).get("origin_phase"),
                )
                _append_campaign_log(log_path, f"stage2_full_verdict failed reason={exc}")
                raise
            except Exception as exc:
                existing_checkpoint = written_candidate_checkpoint if isinstance(written_candidate_checkpoint, dict) else None
                failure_detail = f"{type(exc).__name__}: {exc}"
                deadlock_suspected, failure_driver = _stage2_failure_driver(
                    failure_detail,
                    rl_device=stage1_state.get("rl_device"),
                    stage2_mp_start_method=stage2_runtime_contract.get("stage2_mp_start_method"),
                )
                if existing_checkpoint is not None:
                    updated_payload = dict(existing_checkpoint)
                    updated_payload["created_at"] = _ts()
                    updated_payload["checkpoint_status"] = "degraded"
                    updated_payload["blocked_by"] = "stage_runtime_or_search_issue"
                    updated_payload["failure_reason"] = failure_detail
                    updated_payload["origin_phase"] = (
                        "stage2_prepare"
                        if stage2_state.get("subphase") == "stage2_prepare_variants"
                        else "stage2_compare"
                    )
                    written_candidate_checkpoint = _write_candidate_checkpoint(output_dir, updated_payload)
                    _append_campaign_log(
                        log_path,
                        "candidate_checkpoint_updated blocked_by=stage_runtime_or_search_issue",
                    )
                _update_status(
                    status_path,
                    phase="stage2_full_verdict",
                    status="failed",
                    blocked_by="stage_runtime_or_search_issue",
                    failure_reason="stage2_runtime_or_search_issue",
                    failure_detail=failure_detail,
                    stage2_failure_reason=failure_detail,
                    stage2_prepare_deadlock_suspected=deadlock_suspected,
                    stage2_prepare_failure_driver=failure_driver,
                    stage2_prepare_last_active_unit=stage2_state.get("last_unit"),
                    stage2_prepare_timeout_s=stage2_state.get("prepare_timeout_s"),
                    stage2_mp_start_method=stage2_runtime_contract.get("stage2_mp_start_method"),
                    validation_pool_mode=stage2_runtime_contract.get("validation_pool_mode"),
                    rl_device=stage1_state.get("rl_device"),
                    rl_cleanup_completed=stage1_state.get("rl_cleanup_completed"),
                    rl_cleanup_warnings=stage1_state.get("rl_cleanup_warnings"),
                    candidate_checkpoint_written=written_candidate_checkpoint is not None,
                    candidate_checkpoint_status=(written_candidate_checkpoint or {}).get("checkpoint_status"),
                    candidate_checkpoint_variant=(written_candidate_checkpoint or {}).get("candidate_variant"),
                    candidate_checkpoint_score=((written_candidate_checkpoint or {}).get("selected_checkpoint_candidate") or {}).get("score"),
                    candidate_checkpoint_origin_phase=(written_candidate_checkpoint or {}).get("origin_phase"),
                )
                _append_campaign_log(log_path, f"stage2_full_verdict failed exception={failure_detail}")
                raise
            if args.development_dev_gate and smoke_stage2:
                report.smoke_completed = True
            if args.development_dev_gate:
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
                final_json_payload["stage2_prepare_variant_cache_hits"] = stage2_state.get("prepare_variant_cache_hits", 0)
                final_json_payload["stage2_prepare_variant_replay_reused"] = stage2_state.get(
                    "prepare_variant_replay_reused",
                    0,
                )
                final_json_payload["stage2_prepare_variant_miss_reasons"] = stage2_state.get(
                    "prepare_variant_miss_reasons",
                    {},
                )
                final_json_payload["validation_cache_hit_rate"] = (
                    round(
                        float(stage2_state.get("prepare_variant_cache_hits", 0) or 0)
                        / max(1, int(stage2_state.get("prepare_variant_cache_observations", 0) or 0)),
                        4,
                    )
                    if int(stage2_state.get("prepare_variant_cache_observations", 0) or 0) > 0
                    else 0.0
                )
                final_json_payload["window_snapshot_cache_hit_rate"] = final_json_payload["validation_cache_hit_rate"]
                final_json_payload["stage2_mp_start_method"] = stage2_runtime_contract.get("stage2_mp_start_method")
                final_json_payload["validation_pool_mode"] = stage2_runtime_contract.get("validation_pool_mode")
                final_json_payload["stage2_prepare_timeout_s"] = stage2_state.get("prepare_timeout_s")
                final_json_payload["stage2_prepare_last_active_unit"] = stage2_state.get("last_unit")
                final_json_payload["rl_device"] = stage1_state.get("rl_device")
                final_json_payload["rl_cleanup_completed"] = stage1_state.get("rl_cleanup_completed")
                final_json_payload["rl_cleanup_warnings"] = stage1_state.get("rl_cleanup_warnings")
                _write_json(final_json, final_json_payload)
                final_report_payload = {
                    "created_at": _ts(),
                    "best_variant": report.best_variant,
                    "best_score": report.best_score,
                    "report_mode": report.report_mode,
                    "report_degraded_due_to_memory": bool(getattr(report, "report_degraded_due_to_memory", False)),
                    "test_evaluated": bool(getattr(report, "test_evaluated", False)),
                    "evaluation_role": "development",
                    "test_access_allowed": False,
                    "candidate_ready_for_handoff": bool(final_json_payload.get("candidate_ready_for_handoff", False)),
                    "candidate_frozen_id": final_json_payload.get("candidate_frozen_id"),
                    "json_path": str(final_json),
                    "md_path": str(final_md),
                    "signal_audit_summary": report.signal_audit_summary,
                    "full_training_assessment": final_json_payload.get("full_training_assessment"),
                    "post_smoke_scenario": final_json_payload.get("post_smoke_scenario"),
                }
                existing_checkpoint = written_candidate_checkpoint if isinstance(written_candidate_checkpoint, dict) else None
                if existing_checkpoint is not None:
                    report_variant = existing_checkpoint.get("candidate_variant")
                    report_summary = None
                    if report_variant == "optuna_tuned" and report.optuna_best is not None:
                        report_summary = asdict(report.optuna_best)
                    elif report_variant == "rl_tuned" and report.rl_best is not None:
                        report_summary = asdict(report.rl_best)
                    blocked_by = report_summary.get("blocked_by") if isinstance(report_summary, dict) else None
                    updated_payload = dict(existing_checkpoint)
                    updated_payload["created_at"] = _ts()
                    updated_payload["checkpoint_status"] = "degraded" if blocked_by else "usable"
                    updated_payload["blocked_by"] = blocked_by
                    updated_payload["failure_reason"] = report_summary.get("promotion_failure_reason") if isinstance(report_summary, dict) else None
                    updated_payload["origin_phase"] = "stage2_prepare" if blocked_by in {"prepare_timeout", "pre_validation_rejection"} else "stage2_compare"
                    if isinstance(report_summary, dict):
                        if report_variant == "optuna_tuned":
                            updated_payload["optuna_summary"] = dict(report_summary)
                        elif report_variant == "rl_tuned":
                            updated_payload["rl_summary"] = dict(report_summary)
                    written_candidate_checkpoint = _write_candidate_checkpoint(output_dir, updated_payload)
                    _append_campaign_log(
                        log_path,
                        f"candidate_checkpoint_updated blocked_by={blocked_by or 'none'}",
                    )
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
            evaluation_role="development",
            test_access_allowed=False,
            test_evaluated=bool(final_report_payload.get("test_evaluated")),
            candidate_ready_for_handoff=bool(final_report_payload.get("candidate_ready_for_handoff", False)),
            candidate_frozen_id=final_report_payload.get("candidate_frozen_id"),
            frozen_params=(_read_json(Path(str(final_report_payload.get("json_path")))) or {}).get("frozen_params")
            if final_report_payload.get("json_path")
            else None,
            handoff_blockers=((final_report_payload.get("full_training_assessment") or {}).get("blockers", [])),
            dev_gate_used_this_round=bool(args.development_dev_gate),
            dev_gate_last_round=args.dev_gate_last_round,
            dev_gate_interval=args.dev_gate_interval,
            full_stage2_executed_this_round=bool(args.development_dev_gate),
            json_path=final_report_payload.get("json_path"),
            md_path=final_report_payload.get("md_path"),
            resume_supported=True,
            full_training_allowed=False,
            full_training_decision=(final_report_payload.get("full_training_assessment") or {}).get(
                "decision", "defer_handoff"
            ),
            full_training_blockers=(final_report_payload.get("full_training_assessment") or {}).get("blockers", []),
            post_smoke_scenario=post_smoke_scenario.get("scenario"),
            post_smoke_category=post_smoke_scenario.get("category"),
            post_smoke_next_action=post_smoke_scenario.get("next_action"),
            stage2_subphase="stage3_complete",
            candidate_checkpoint_written=written_candidate_checkpoint is not None,
            candidate_checkpoint_status=(written_candidate_checkpoint or {}).get("checkpoint_status"),
            candidate_checkpoint_variant=(written_candidate_checkpoint or {}).get("candidate_variant"),
            candidate_checkpoint_score=((written_candidate_checkpoint or {}).get("selected_checkpoint_candidate") or {}).get("score"),
            candidate_checkpoint_origin_phase=(written_candidate_checkpoint or {}).get("origin_phase"),
        )
        _append_campaign_log(log_path, f"campaign_completed scenario={post_smoke_scenario.get('scenario')}")
        print(json.dumps(_read_json(status_path), indent=2))
    except SearchStageTimeoutError as exc:
        reason = str(exc)
        blocked_by = "stage1_optuna_timeout" if "stage1" in reason else "stage_runtime_or_search_issue"
        if isinstance(locals().get("written_candidate_checkpoint"), dict):
            updated_payload = dict(written_candidate_checkpoint)
            updated_payload["created_at"] = _ts()
            updated_payload["failure_reason"] = reason
            if not updated_payload.get("origin_phase"):
                updated_payload["origin_phase"] = "stage1"
            written_candidate_checkpoint = _write_candidate_checkpoint(output_dir, updated_payload)
            _append_campaign_log(
                log_path,
                f"candidate_checkpoint_updated blocked_by={updated_payload.get('blocked_by') or 'none'}",
            )
        _update_status(
            status_path,
            status="failed",
            blocked_by=blocked_by,
            failure_reason=reason,
            detail=reason,
            candidate_checkpoint_written=isinstance(locals().get("written_candidate_checkpoint"), dict),
            candidate_checkpoint_status=(locals().get("written_candidate_checkpoint") or {}).get("checkpoint_status"),
            candidate_checkpoint_variant=(locals().get("written_candidate_checkpoint") or {}).get("candidate_variant"),
            candidate_checkpoint_score=(((locals().get("written_candidate_checkpoint") or {}).get("selected_checkpoint_candidate")) or {}).get("score"),
            candidate_checkpoint_origin_phase=(locals().get("written_candidate_checkpoint") or {}).get("origin_phase"),
        )
        _append_campaign_log(log_path, f"campaign_failed timeout={reason}")
        raise
    except Exception:
        existing_status = _read_json(status_path) or {}
        if existing_status.get("status") == "failed" and existing_status.get("blocked_by") in {
            "stage_runtime_or_search_issue",
            "stage1_rl_sidecar_failed",
            "campaign_bootstrap_exception",
        }:
            _append_campaign_log(log_path, "campaign_failed preserved_failed_status")
            raise
        failure_reason = f"{type(sys.exc_info()[1]).__name__}: {sys.exc_info()[1]}"
        if bootstrap_guard_armed:
            _best_effort_bootstrap_failure_status(
                status_path=status_path,
                log_path=log_path,
                budget=args.budget,
                optuna_trials=optuna_trials,
                rl_episodes=rl_episodes,
                started_at=started_at,
                output_dir=output_dir,
                failure_reason=failure_reason,
                detail=traceback.format_exc(limit=10),
            )
        else:
            _update_status(
                status_path,
                status="failed",
                blocked_by="campaign_exception",
                failure_reason=failure_reason,
                detail=traceback.format_exc(limit=10),
            )
            _append_campaign_log(log_path, "campaign_failed campaign_exception")
        raise


if __name__ == "__main__":
    main()
