#!/usr/bin/env python3
"""Run multi-round framework research with cross-round inheritance."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.research.loop_policy import (
    plan_next_iteration,
    project_seed_trials_to_mode,
)


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_seed_trials(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = _read_json(path)
    if payload is None:
        raise ValueError(f"initial_seed_trials_file_not_found:{path}")
    if not isinstance(payload, list):
        raise ValueError("initial_seed_trials_file must contain a JSON list")
    resolved: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("initial_seed_trials_file entries must be JSON objects")
        resolved.append(dict(item))
    return resolved


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{_ts()}] {message}\n")


def _round_dir(root: Path, round_index: int) -> Path:
    return root / f"round_{round_index:03d}"


def _seed_file(run_dir: Path) -> Path:
    return run_dir / "seed_trials.json"


def _loop_lock_file(loop_dir: Path) -> Path:
    return loop_dir / "loop.lock"


def _campaign_lock_file(run_dir: Path) -> Path:
    return run_dir / "campaign.lock"


def _handoff_candidate_path(loop_dir: Path) -> Path:
    return loop_dir / "handoff_candidate.json"


def _training_monitor_path(loop_dir: Path) -> Path:
    return loop_dir / "training_monitor.json"


def _training_history_path(loop_dir: Path) -> Path:
    return loop_dir / "training_history.jsonl"


def _set_current_round_pointer(loop_dir: Path, run_dir: Path) -> None:
    pointer = loop_dir / "current_round"
    if pointer.exists() or pointer.is_symlink():
        pointer.unlink()
    try:
        pointer.symlink_to(run_dir.resolve())
    except OSError:
        pointer.write_text(str(run_dir.resolve()), encoding="utf-8")


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock(path: Path) -> dict[str, Any] | None:
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else None


def _release_lock(path: Path, *, owner_pid: int | None = None) -> None:
    payload = _read_lock(path)
    if payload is None:
        return
    if owner_pid is not None and int(payload.get("pid", 0) or 0) != owner_pid:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _acquire_lock(path: Path, payload: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    existing = _read_lock(path)
    if existing is not None:
        existing_pid = int(existing.get("pid", 0) or 0)
        if _pid_is_alive(existing_pid):
            return False, existing
        _release_lock(path)
    _write_json(path, payload)
    return True, None


def _update_loop_status(path: Path, **kwargs: Any) -> dict[str, Any]:
    payload = _read_json(path) or {}
    payload.update(kwargs)
    payload["updated_at"] = _ts()
    _write_json(path, payload)
    return payload


def _append_history(path: Path, item: dict[str, Any]) -> list[dict[str, Any]]:
    history = _read_json(path)
    records = history if isinstance(history, list) else []
    records.append(item)
    _write_json(path, records)
    return records


def _append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def _write_handoff_candidate(loop_dir: Path, history_item: dict[str, Any]) -> None:
    _write_json(
        _handoff_candidate_path(loop_dir),
        {
            "round": history_item["round"],
            "budget": history_item["budget"],
            "run_dir": history_item["run_dir"],
            "candidate_ready_for_handoff": history_item["candidate_ready_for_handoff"],
            "candidate_frozen_id": history_item["candidate_frozen_id"],
            "development_report_path": f"{history_item['run_dir']}/framework_optimization.json",
            "updated_at": _ts(),
        },
    )


def _current_campaign_snapshot(run_dir: Path) -> dict[str, Any]:
    status = _read_json(run_dir / "campaign_status.json") or {}
    return {
        "started_at": status.get("started_at"),
        "phase": status.get("phase"),
        "status": status.get("status"),
        "optuna_trials_completed": status.get("optuna_trials_completed"),
        "stage1_optuna_trials_total": status.get("stage1_optuna_trials_total"),
        "rl_episodes_completed": status.get("rl_episodes_completed"),
        "rl_episodes_requested": status.get("rl_episodes_requested"),
        "stage1_runtime_s": status.get("stage1_runtime_s"),
        "stage1_last_completed_unit": status.get("stage1_last_completed_unit"),
        "stage1_progress_completed": status.get("stage1_progress_completed"),
        "stage1_progress_total": status.get("stage1_progress_total"),
        "stage1_actual_steps": status.get("stage1_actual_steps"),
        "stage1_requested_steps": status.get("stage1_requested_steps"),
        "stage1_best_value_so_far": status.get("stage1_best_value_so_far"),
        "stage1_subphase": status.get("stage1_subphase"),
        "stage1_pending_trials": status.get("stage1_pending_trials"),
        "stage1_max_workers": status.get("stage1_max_workers"),
        "stage1_target_inflight": status.get("stage1_target_inflight"),
        "stage1_active_workers_estimate": status.get("stage1_active_workers_estimate"),
        "stage1_scheduler_mode": status.get("stage1_scheduler_mode"),
        "stage1_underfilled": status.get("stage1_underfilled"),
        "stage1_underfilled_reason": status.get("stage1_underfilled_reason"),
        "stage1_underfilled_started_at": status.get("stage1_underfilled_started_at"),
        "stage1_underfilled_duration_s": status.get("stage1_underfilled_duration_s"),
        "stage1_avg_trial_runtime_s": status.get("stage1_avg_trial_runtime_s"),
        "stage1_avg_context_build_s": status.get("stage1_avg_context_build_s"),
        "stage1_avg_replay_run_s": status.get("stage1_avg_replay_run_s"),
        "stage1_cache_hit_rate": status.get("stage1_cache_hit_rate"),
        "stage1_avg_event_loop_s": status.get("stage1_avg_event_loop_s"),
        "stage1_avg_metrics_finalize_s": status.get("stage1_avg_metrics_finalize_s"),
        "stage1_avg_diagnostics_finalize_s": status.get("stage1_avg_diagnostics_finalize_s"),
        "stage1_terminated_early_rate": status.get("stage1_terminated_early_rate"),
        "rl_budget_mode": status.get("rl_budget_mode"),
        "rl_wall_clock_elapsed_s": status.get("rl_wall_clock_elapsed_s"),
        "rl_stop_reason": status.get("rl_stop_reason"),
        "rl_active_envs": status.get("rl_active_envs"),
        "rl_avg_episode_runtime_s": status.get("rl_avg_episode_runtime_s"),
        "rl_cache_hit_rate": status.get("rl_cache_hit_rate"),
        "rl_avg_event_loop_s": status.get("rl_avg_event_loop_s"),
        "stage2_subphase": status.get("stage2_subphase"),
        "stage2_progress_completed": status.get("stage2_progress_completed"),
        "stage2_progress_total": status.get("stage2_progress_total"),
        "stage2_last_completed_unit": status.get("stage2_last_completed_unit"),
        "stage2_compare_window_units_completed": status.get("stage2_compare_window_units_completed"),
        "stage2_compare_window_units_total": status.get("stage2_compare_window_units_total"),
        "stage2_compare_validation_units_completed": status.get("stage2_compare_validation_units_completed"),
        "stage2_compare_validation_units_total": status.get("stage2_compare_validation_units_total"),
        "stage2_compare_workers": status.get("stage2_compare_workers"),
        "stage2_compare_validation_workers": status.get("stage2_compare_validation_workers"),
        "failure_reason": status.get("failure_reason"),
        "blocked_by": status.get("blocked_by"),
        "full_training_allowed": status.get("full_training_allowed"),
        "full_training_blockers": status.get("full_training_blockers"),
        "post_smoke_scenario": status.get("post_smoke_scenario"),
        "campaign_updated_at": status.get("updated_at"),
    }


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _seconds_between(start: Any, end: Any) -> float | None:
    start_dt = _parse_ts(start)
    end_dt = _parse_ts(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _recent_history_records(loop_dir: Path) -> list[dict[str, Any]]:
    history = _read_json(loop_dir / "loop_history.json")
    return history if isinstance(history, list) else []


def _process_metrics(loop_pid: int | None, campaign_pid: int | None) -> tuple[float, int]:
    tracked = {pid for pid in (loop_pid, campaign_pid) if pid is not None and pid > 0}
    if not tracked:
        return 0.0, 0
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,%cpu=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0.0, len(tracked)
    rows: dict[int, tuple[int, float]] = {}
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            cpu = float(parts[2])
        except ValueError:
            continue
        rows[pid] = (ppid, cpu)
    included = set(tracked)
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _cpu) in rows.items():
            if pid in included:
                continue
            if ppid in included:
                included.add(pid)
                changed = True
    total_cpu = sum(rows.get(pid, (0, 0.0))[1] for pid in included)
    return round(total_cpu, 2), sum(1 for pid in included if pid in rows)


def _cpu_expected_mode(*, budget: str | None, phase: str | None, stage2_subphase: str | None) -> str:
    if phase == "stage1_rl":
        return "single_core_expected"
    if phase == "stage1_optuna_search":
        return "parallel_expected" if budget in {"fast", "high"} else "single_core_expected"
    if phase == "stage2_full_verdict" and stage2_subphase == "stage2_prepare_variants":
        return "parallel_expected" if budget in {"fast", "high"} else "single_core_expected"
    if phase == "stage2_full_verdict" and stage2_subphase == "stage2_compare_windows":
        return "parallel_expected" if budget in {"fast", "high"} else "single_core_expected"
    if phase == "stage2_full_verdict" and stage2_subphase == "stage2_compare_validation":
        return "parallel_expected" if budget in {"fast", "high"} else "single_core_expected"
    return "single_core_expected"


def _eta_seconds(*, completed: int, total: int, runtime_s: float) -> int | None:
    if completed <= 0 or total <= completed or runtime_s <= 0:
        return None
    throughput = completed / runtime_s
    if throughput <= 0:
        return None
    return round((total - completed) / throughput)


def _promotion_health(records: list[dict[str, Any]]) -> str:
    if not records:
        return "flat"
    latest = records[-1]
    if latest.get("validation_timeout_count", 0) or latest.get("prevalidation_rejection_count", 0):
        return "blocked"
    if len(records) == 1:
        return "flat"
    prev = records[-2]
    latest_score = _safe_float(latest.get("dev_score"))
    prev_score = _safe_float(prev.get("dev_score"))
    latest_dd = _safe_float(latest.get("dev_drawdown"))
    prev_dd = _safe_float(prev.get("dev_drawdown"))
    latest_worst = _safe_int(latest.get("negative_dev_worst_windows"))
    prev_worst = _safe_int(prev.get("negative_dev_worst_windows"))
    if latest_score >= prev_score + 0.15 and latest_dd <= prev_dd + 0.02 and latest_worst <= prev_worst:
        return "improving"
    if latest_score <= prev_score - 0.15 or latest_dd > prev_dd + 0.03 or latest_worst > prev_worst:
        return "regressing"
    return "flat"


def _recent_rounds(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recent = []
    for item in records[-5:]:
        recent.append(
            {
                "round": item.get("round"),
                "budget": item.get("budget"),
                "train_score": item.get("train_score"),
                "dev_score": item.get("dev_score"),
                "best_variant": item.get("best_variant"),
                "candidate_ready_for_handoff": item.get("candidate_ready_for_handoff"),
                "promotion_health": item.get("promotion_health"),
                "round_runtime_s": item.get("round_runtime_s"),
            }
        )
    return recent


def _write_training_monitor(loop_dir: Path) -> dict[str, Any]:
    loop_status = _read_json(loop_dir / "loop_status.json") or {}
    records = _recent_history_records(loop_dir)
    current_run_dir = loop_status.get("current_run_dir")
    snapshot = _current_campaign_snapshot(Path(current_run_dir)) if isinstance(current_run_dir, str) and current_run_dir else {}
    loop_pid = _safe_int(loop_status.get("loop_pid") or loop_status.get("lock_owner_pid") or 0)
    campaign_pid = _safe_int(loop_status.get("current_campaign_pid") or 0)
    cpu_utilization_estimate, active_process_count = _process_metrics(loop_pid, campaign_pid)
    expected_mode = _cpu_expected_mode(
        budget=loop_status.get("current_budget"),
        phase=loop_status.get("current_phase"),
        stage2_subphase=snapshot.get("stage2_subphase"),
    )
    campaign_updated_at = snapshot.get("campaign_updated_at") or loop_status.get("updated_at")
    freshness = _seconds_between(campaign_updated_at, _ts())
    stage1_completed = _safe_int(snapshot.get("stage1_progress_completed") or snapshot.get("optuna_trials_completed"))
    stage1_total = _safe_int(snapshot.get("stage1_progress_total"))
    stage2_completed = _safe_int(snapshot.get("stage2_progress_completed"))
    stage2_total = _safe_int(snapshot.get("stage2_progress_total"))
    compare_window_units_completed = _safe_int(snapshot.get("stage2_compare_window_units_completed"))
    compare_window_units_total = _safe_int(snapshot.get("stage2_compare_window_units_total"))
    compare_validation_units_completed = _safe_int(snapshot.get("stage2_compare_validation_units_completed"))
    compare_validation_units_total = _safe_int(snapshot.get("stage2_compare_validation_units_total"))
    optuna_trials_total = _safe_int(snapshot.get("stage1_optuna_trials_total") or snapshot.get("optuna_trials_requested"))
    stage1_pending_trials = _safe_int(snapshot.get("stage1_pending_trials"))
    stage1_max_workers = _safe_int(snapshot.get("stage1_max_workers"))
    stage1_target_inflight = _safe_int(snapshot.get("stage1_target_inflight"))
    stage1_active_workers_estimate = _safe_int(snapshot.get("stage1_active_workers_estimate"))
    underfilled_started_at = snapshot.get("stage1_underfilled_started_at")
    underfilled_duration_s = _safe_float(snapshot.get("stage1_underfilled_duration_s"))
    stage1_runtime_s = _safe_float(loop_status.get("stage1_runtime_s"))
    stage2_runtime_s = _safe_float(loop_status.get("stage2_runtime_s"))
    current_phase = str(loop_status.get("current_phase") or "")
    round_eta_seconds = None
    if current_phase.startswith("stage1"):
        round_eta_seconds = _eta_seconds(completed=stage1_completed, total=stage1_total, runtime_s=stage1_runtime_s)
    elif current_phase == "stage2_full_verdict":
        round_eta_seconds = _eta_seconds(completed=stage2_completed, total=stage2_total, runtime_s=stage2_runtime_s)
    best_train_score_so_far = max((_safe_float(item.get("train_score")) for item in records), default=0.0)
    best_train_sharpe_so_far = max((_safe_float(item.get("train_sharpe")) for item in records), default=0.0)
    best_dev_score_so_far = max((_safe_float(item.get("dev_score")) for item in records), default=0.0)
    best_dev_drawdown_so_far = min(
        (_safe_float(item.get("dev_drawdown"), float("inf")) for item in records if item.get("dev_drawdown") is not None),
        default=0.0,
    )
    best_candidate_variant_so_far = None
    if records:
        best_record = max(records, key=lambda item: _safe_float(item.get("dev_score")))
        best_candidate_variant_so_far = best_record.get("best_variant")
    dev_gate_pass_count = sum(1 for item in records if item.get("dev_gate_used_this_round") and item.get("candidate_ready_for_handoff"))
    dev_gate_fail_count = sum(1 for item in records if item.get("dev_gate_used_this_round") and not item.get("candidate_ready_for_handoff"))
    prevalidation_rejection_count = sum(_safe_int(item.get("prevalidation_rejection_count")) for item in records)
    validation_timeout_count = sum(_safe_int(item.get("validation_timeout_count")) for item in records)
    stalled = bool(
        freshness is not None
        and (
            (current_phase.startswith("stage1") and freshness > 180.0)
            or (current_phase == "stage2_full_verdict" and freshness > 300.0)
        )
    )
    stalled_reason = None
    if stalled:
        stalled_reason = f"heartbeat_stale:{int(freshness or 0)}s"
    stage1_work_conserving = current_phase.startswith("stage1") and snapshot.get("stage1_scheduler_mode") == "work_conserving"
    cpu_underutilized = False if stage1_work_conserving else (
        expected_mode == "parallel_expected" and cpu_utilization_estimate < 120.0
    )
    cpu_underutilized_reason = None
    if cpu_underutilized:
        cpu_underutilized_reason = (
            f"{current_phase or 'unknown_phase'}:{snapshot.get('stage2_subphase') or snapshot.get('stage1_subphase') or 'unknown_subphase'}"
        )
    if stage1_work_conserving:
        sustained_underfilled_candidate = (
            stage1_target_inflight >= 2
            and stage1_completed < stage1_total
            and stage1_pending_trials <= max(0, stage1_target_inflight - 2)
            and stage1_active_workers_estimate <= max(0, stage1_target_inflight - 2)
            and cpu_utilization_estimate < max(120.0, 80.0 * stage1_target_inflight)
            and not stalled
        )
        started_at = underfilled_started_at
        if sustained_underfilled_candidate and not started_at:
            started_at = _ts()
        if not sustained_underfilled_candidate:
            started_at = None
            underfilled_duration_s = 0.0
        elif started_at:
            underfilled_duration_s = _seconds_between(started_at, _ts()) or 0.0
        if (
            sustained_underfilled_candidate
            and underfilled_duration_s >= 15.0
        ):
            cpu_underutilized = True
            cpu_underutilized_reason = "stage1_parallelism_not_materializing"
        snapshot["stage1_underfilled_candidate"] = sustained_underfilled_candidate
        snapshot["stage1_underfilled_started_at"] = started_at
        snapshot["stage1_underfilled_duration_s"] = round(underfilled_duration_s, 2)
        snapshot["stage1_underfilled"] = bool(
            sustained_underfilled_candidate and underfilled_duration_s >= 15.0
        )
        if not snapshot["stage1_underfilled"]:
            snapshot["stage1_underfilled_reason"] = None
    alerts: list[str] = []
    if stalled and stalled_reason is not None:
        alerts.append(stalled_reason)
    if cpu_underutilized and cpu_underutilized_reason is not None:
        alerts.append(f"cpu_underutilized:{cpu_underutilized_reason}")
    if records and _promotion_health(records) == "regressing":
        alerts.append("dev_score_regressing")
    if validation_timeout_count > 0:
        alerts.append("repeated_validation_timeout")
    if prevalidation_rejection_count > 0:
        alerts.append("repeated_prevalidation_rejection")
    monitor = {
        "run_id": loop_dir.name,
        "loop_state": loop_status.get("loop_state"),
        "current_round": loop_status.get("current_round"),
        "current_budget": loop_status.get("current_budget"),
        "current_phase": loop_status.get("current_phase"),
        "current_status": loop_status.get("current_status"),
        "elapsed_wall_time_s": _seconds_between((_read_lock(_loop_lock_file(loop_dir)) or {}).get("started_at"), _ts()),
        "round_runtime_s": loop_status.get("round_runtime_s"),
        "stage1_runtime_s": loop_status.get("stage1_runtime_s"),
        "stage2_runtime_s": loop_status.get("stage2_runtime_s"),
        "optuna_trials_completed": snapshot.get("optuna_trials_completed"),
        "optuna_trials_total": optuna_trials_total,
        "rl_steps_completed": snapshot.get("stage1_actual_steps") or snapshot.get("rl_episodes_completed"),
        "rl_steps_requested": snapshot.get("stage1_requested_steps") or snapshot.get("rl_episodes_requested"),
        "stage1_pending_trials": stage1_pending_trials,
        "stage1_max_workers": stage1_max_workers,
        "stage1_target_inflight": stage1_target_inflight,
        "stage1_active_workers_estimate": stage1_active_workers_estimate,
        "stage1_scheduler_mode": snapshot.get("stage1_scheduler_mode"),
        "stage1_underfilled": bool(snapshot.get("stage1_underfilled")),
        "stage1_underfilled_reason": snapshot.get("stage1_underfilled_reason"),
        "stage1_underfilled_candidate": bool(snapshot.get("stage1_underfilled_candidate")),
        "stage1_underfilled_started_at": snapshot.get("stage1_underfilled_started_at"),
        "stage1_underfilled_duration_s": snapshot.get("stage1_underfilled_duration_s"),
        "stage1_avg_trial_runtime_s": snapshot.get("stage1_avg_trial_runtime_s"),
        "stage1_avg_context_build_s": snapshot.get("stage1_avg_context_build_s"),
        "stage1_avg_replay_run_s": snapshot.get("stage1_avg_replay_run_s"),
        "stage1_cache_hit_rate": snapshot.get("stage1_cache_hit_rate"),
        "stage1_avg_event_loop_s": snapshot.get("stage1_avg_event_loop_s"),
        "stage1_avg_metrics_finalize_s": snapshot.get("stage1_avg_metrics_finalize_s"),
        "stage1_avg_diagnostics_finalize_s": snapshot.get("stage1_avg_diagnostics_finalize_s"),
        "stage1_terminated_early_rate": snapshot.get("stage1_terminated_early_rate"),
        "rl_budget_mode": snapshot.get("rl_budget_mode"),
        "rl_wall_clock_elapsed_s": snapshot.get("rl_wall_clock_elapsed_s"),
        "rl_stop_reason": snapshot.get("rl_stop_reason"),
        "rl_active_envs": snapshot.get("rl_active_envs"),
        "rl_avg_episode_runtime_s": snapshot.get("rl_avg_episode_runtime_s"),
        "rl_cache_hit_rate": snapshot.get("rl_cache_hit_rate"),
        "rl_avg_event_loop_s": snapshot.get("rl_avg_event_loop_s"),
        "stage2_progress_completed": snapshot.get("stage2_progress_completed"),
        "stage2_progress_total": snapshot.get("stage2_progress_total"),
        "compare_window_units_completed": compare_window_units_completed,
        "compare_window_units_total": compare_window_units_total,
        "compare_validation_units_completed": compare_validation_units_completed,
        "compare_validation_units_total": compare_validation_units_total,
        "stage2_compare_workers": snapshot.get("stage2_compare_workers"),
        "stage2_compare_validation_workers": snapshot.get("stage2_compare_validation_workers"),
        "stage2_subphase_eta_seconds": _eta_seconds(
            completed=stage2_completed,
            total=stage2_total,
            runtime_s=stage2_runtime_s,
        )
        if current_phase == "stage2_full_verdict"
        else None,
        "round_eta_seconds": round_eta_seconds,
        "campaign_eta_seconds": round_eta_seconds,
        "heartbeat_freshness_seconds": freshness,
        "stalled": stalled,
        "stalled_reason": stalled_reason,
        "cpu_expected_mode": expected_mode,
        "cpu_utilization_estimate": cpu_utilization_estimate,
        "cpu_underutilized": cpu_underutilized,
        "cpu_underutilized_reason": cpu_underutilized_reason,
        "stage2_compare_stalled": bool(
            stalled and snapshot.get("stage2_subphase") in {"stage2_compare_windows", "stage2_compare_validation"}
        ),
        "stage2_compare_stalled_reason": (
            stalled_reason if snapshot.get("stage2_subphase") in {"stage2_compare_windows", "stage2_compare_validation"} else None
        ),
        "active_process_count": active_process_count,
        "slowest_subphase": snapshot.get("stage2_subphase") or snapshot.get("stage1_subphase"),
        "best_train_score_so_far": round(best_train_score_so_far, 4),
        "best_train_sharpe_so_far": round(best_train_sharpe_so_far, 4),
        "best_dev_score_so_far": round(best_dev_score_so_far, 4),
        "best_dev_drawdown_so_far": round(best_dev_drawdown_so_far, 4) if isinstance(best_dev_drawdown_so_far, float) else None,
        "best_candidate_variant_so_far": best_candidate_variant_so_far,
        "candidate_improvement_trend": _promotion_health(records),
        "dev_gate_pass_count": dev_gate_pass_count,
        "dev_gate_fail_count": dev_gate_fail_count,
        "prevalidation_rejection_rate": round(prevalidation_rejection_count / max(1, len(records)), 4),
        "validation_timeout_rate": round(validation_timeout_count / max(1, len(records)), 4),
        "candidate_ready_for_handoff": loop_status.get("candidate_ready_for_handoff"),
        "alerts": alerts,
        "recent_rounds": _recent_rounds(records),
        "updated_at": _ts(),
    }
    _write_json(_training_monitor_path(loop_dir), monitor)
    return monitor


def _load_round_artifacts(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    final_report = _read_json(run_dir / "framework_optimization.json") or {}
    stage1_payload = _read_json(run_dir / "checkpoints" / "stage1_search.json") or {}
    campaign_status = _read_json(run_dir / "campaign_status.json") or {}
    return final_report, stage1_payload, campaign_status


def _build_campaign_command(
    args: argparse.Namespace,
    run_dir: Path,
    *,
    budget: str,
    seed_trials_file: Path | None,
    resume: bool,
    development_dev_gate: bool,
    dev_gate_last_round: int | None,
    dev_gate_interval: int | None,
) -> list[str]:
    command = [
        sys.executable,
        "scripts/run_framework_campaign.py",
        "--root",
        args.root,
        "--symbol",
        args.symbol,
        "--liquidation-dir",
        args.liquidation_dir,
        "--output-dir",
        str(run_dir),
        "--budget",
        budget,
    ]
    if seed_trials_file is not None and seed_trials_file.exists():
        command.extend(["--seed-trials-file", str(seed_trials_file)])
    if development_dev_gate:
        command.append("--development-dev-gate")
    if dev_gate_last_round is not None:
        command.extend(["--dev-gate-last-round", str(dev_gate_last_round)])
    if dev_gate_interval is not None:
        command.extend(["--dev-gate-interval", str(dev_gate_interval)])
    if resume:
        command.append("--resume")
    return command


def _active_round_from_loop(loop_dir: Path) -> Path | None:
    loop_status = _read_json(loop_dir / "loop_status.json") or {}
    current_run_dir = loop_status.get("current_run_dir")
    if isinstance(current_run_dir, str) and current_run_dir:
        return Path(current_run_dir)
    pointer = loop_dir / "current_round"
    if pointer.is_symlink():
        return pointer.resolve()
    if pointer.exists():
        try:
            return Path(pointer.read_text(encoding="utf-8").strip())
        except Exception:
            return None
    return None


def _launch_round(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    budget: str,
    seed_trials: list[dict[str, Any]],
    loop_status_path: Path,
    loop_log_path: Path,
    round_index: int,
    max_rounds: int,
    adaptation_policy: str,
    resume: bool,
    development_dev_gate: bool,
    dev_gate_last_round: int | None,
    dev_gate_interval: int | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    run_dir.mkdir(parents=True, exist_ok=True)
    campaign_lock_path = _campaign_lock_file(run_dir)
    round_started_at = time.time()
    seed_trials_file: Path | None = None
    if seed_trials:
        seed_trials_file = _seed_file(run_dir)
        _write_json(seed_trials_file, seed_trials)  # type: ignore[arg-type]

    command = _build_campaign_command(
        args,
        run_dir,
        budget=budget,
        seed_trials_file=seed_trials_file,
        resume=resume,
        development_dev_gate=development_dev_gate,
        dev_gate_last_round=dev_gate_last_round,
        dev_gate_interval=dev_gate_interval,
    )
    _append_log(
        loop_log_path,
        (
            f"round_started round={round_index}/{max_rounds} budget={budget} "
            f"seed_trials={len(seed_trials)} adaptation_policy={adaptation_policy} "
            f"dev_gate={development_dev_gate} resume={resume}"
        ),
    )
    lock_payload = {
        "pid": None,
        "launcher_pid": os.getpid(),
        "round": round_index,
        "budget": budget,
        "started_at": _ts(),
        "run_dir": str(run_dir.resolve()),
    }
    existing_lock = _read_lock(campaign_lock_path)
    if existing_lock is not None:
        existing_pid = int(existing_lock.get("pid", 0) or 0)
        if _pid_is_alive(existing_pid):
            _append_log(
                loop_log_path,
                f"round_already_running round={round_index} budget={budget} pid={existing_pid}",
            )
            _update_loop_status(
                loop_status_path,
                current_round=round_index,
                max_rounds=max_rounds,
                current_budget=budget,
                current_run_dir=str(run_dir),
                current_phase=None,
                current_status="blocked",
                current_blockers=[],
                current_scenario=None,
                adaptation_policy=adaptation_policy,
                seed_trial_count=len(seed_trials),
                current_campaign_pid=existing_pid,
                loop_state="blocked",
                stop_reason=f"round_already_running:{round_index}",
            )
            raise RuntimeError(f"round_already_running pid={existing_pid} run_dir={run_dir}")
        _release_lock(campaign_lock_path)
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "ZENQUANT_LOOP_LAUNCHER_PID": str(os.getpid()),
            "ZENQUANT_LOOP_RUN_DIR": str(run_dir.resolve()),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    lock_payload["pid"] = process.pid
    _write_json(campaign_lock_path, lock_payload)
    _append_log(
        loop_log_path,
        f"round_started round={round_index}/{max_rounds} budget={budget} launcher_pid={os.getpid()} campaign_pid={process.pid}",
    )
    last_progress_log_at = 0.0
    last_progress_signature: tuple[Any, ...] | None = None
    stage2_started_at: float | None = None
    try:
        while True:
            snapshot = _current_campaign_snapshot(run_dir)
            if snapshot.get("phase") == "stage2_full_verdict" and stage2_started_at is None:
                stage2_started_at = time.time()
            round_runtime_s = round(time.time() - round_started_at, 2)
            stage1_runtime_s = round(float(snapshot.get("stage1_runtime_s") or 0.0), 2)
            stage2_runtime_s = round(time.time() - stage2_started_at, 2) if stage2_started_at is not None else 0.0
            _update_loop_status(
                loop_status_path,
                current_round=round_index,
                max_rounds=max_rounds,
                current_budget=budget,
                current_run_dir=str(run_dir),
                current_phase=snapshot.get("phase"),
                current_status=snapshot.get("status"),
                current_blockers=snapshot.get("full_training_blockers"),
                current_scenario=snapshot.get("post_smoke_scenario"),
                adaptation_policy=adaptation_policy,
                seed_trial_count=len(seed_trials),
                current_campaign_pid=process.pid,
                round_runtime_s=round_runtime_s,
                stage1_runtime_s=stage1_runtime_s,
                stage2_runtime_s=stage2_runtime_s,
                loop_state="campaign_running",
            )
            _write_training_monitor(loop_status_path.parent)
            progress_signature = (
                snapshot.get("phase"),
                snapshot.get("status"),
                snapshot.get("optuna_trials_completed"),
                snapshot.get("stage1_runtime_s"),
                snapshot.get("stage1_last_completed_unit"),
                snapshot.get("stage2_subphase"),
                snapshot.get("stage2_progress_completed"),
                snapshot.get("stage2_progress_total"),
                snapshot.get("stage2_last_completed_unit"),
            )
            now = time.time()
            if progress_signature != last_progress_signature or (now - last_progress_log_at) >= 30.0:
                _append_log(
                    loop_log_path,
                    (
                        f"round_progress round={round_index} phase={snapshot.get('phase')} status={snapshot.get('status')} "
                        f"optuna_completed={snapshot.get('optuna_trials_completed')} "
                        f"round_runtime_s={round_runtime_s} "
                        f"stage1_runtime_s={stage1_runtime_s} "
                        f"stage1_unit={snapshot.get('stage1_last_completed_unit')} "
                        f"stage2_subphase={snapshot.get('stage2_subphase')} "
                        f"stage2_runtime_s={stage2_runtime_s} "
                        f"stage2_progress={snapshot.get('stage2_progress_completed')}/{snapshot.get('stage2_progress_total')} "
                        f"stage2_unit={snapshot.get('stage2_last_completed_unit')}"
                    ),
                )
                last_progress_signature = progress_signature
                last_progress_log_at = now
            if process.poll() is not None:
                break
            time.sleep(max(1.0, float(args.poll_interval)))

        exit_code = process.wait()
        final_report, stage1_payload, campaign_status = _load_round_artifacts(run_dir)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        _release_lock(campaign_lock_path, owner_pid=process.pid)
    if exit_code != 0:
        snapshot = _current_campaign_snapshot(run_dir)
        _append_log(
            loop_log_path,
            (
                f"round_failed round={round_index} budget={budget} exit_code={exit_code} "
                f"phase={snapshot.get('phase')} status={snapshot.get('status')}"
            ),
        )
        _update_loop_status(
            loop_status_path,
            current_round=round_index,
            max_rounds=max_rounds,
            current_budget=budget,
            current_run_dir=str(run_dir),
            current_phase=snapshot.get("phase"),
            current_status=snapshot.get("status"),
            current_blockers=snapshot.get("full_training_blockers"),
            current_scenario=snapshot.get("post_smoke_scenario"),
            adaptation_policy=adaptation_policy,
            seed_trial_count=len(seed_trials),
            current_campaign_pid=process.pid,
            round_runtime_s=round(time.time() - round_started_at, 2),
            stage1_runtime_s=round(float(snapshot.get("stage1_runtime_s") or 0.0), 2),
            stage2_runtime_s=round(time.time() - stage2_started_at, 2) if stage2_started_at is not None else 0.0,
            loop_state="failed",
            stop_reason=f"campaign_round_failed:{round_index}",
        )
        _write_training_monitor(loop_status_path.parent)
        raise RuntimeError(f"campaign_round_failed exit_code={exit_code} run_dir={run_dir}")
    if not final_report:
        raise RuntimeError(f"campaign_round_missing_final_report run_dir={run_dir}")
    _write_training_monitor(loop_status_path.parent)
    return final_report, stage1_payload, campaign_status


def _resume_state(loop_dir: Path) -> tuple[int, dict[str, Any] | None]:
    loop_status = _read_json(loop_dir / "loop_status.json") or {}
    history = _read_json(loop_dir / "loop_history.json")
    records = history if isinstance(history, list) else []
    if loop_status.get("loop_state") == "stopped":
        return int(loop_status.get("current_round", 0) or 0), None
    if not records:
        return 0, None
    return len(records), records[-1]


def _mode_for_budget(budget: str) -> str:
    return "baseline_anchored_train_only" if budget == "smoke" else "execution_first_v2_correlation_constrained"


def _should_use_dev_gate(
    *,
    round_index: int,
    max_rounds: int,
    current_budget: str,
    previous_budget: str | None,
    interval: int,
) -> bool:
    if round_index == 1 or round_index == max_rounds:
        return True
    if previous_budget is not None and previous_budget != current_budget:
        return True
    return round_index % interval == 0 or current_budget == "fast"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run multi-round framework research loop")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--liquidation-dir", default="data/raw/merged_liquidations/aggregated")
    parser.add_argument("--output-dir", default="results/framework_optimization_loop")
    parser.add_argument("--campaign-output-root", default=None)
    parser.add_argument("--start-budget", choices=("smoke", "fast"), default="smoke")
    parser.add_argument("--max-rounds", type=int, default=8)
    parser.add_argument("--stop-on-gate-pass", action="store_true")
    parser.add_argument("--allow-fast-progression", action="store_true", default=True)
    parser.add_argument("--allow-high-progression", action="store_true", default=False)
    parser.add_argument("--anchor-ratio", type=float, default=0.2)
    parser.add_argument("--exploit-ratio", type=float, default=0.4)
    parser.add_argument("--explore-ratio", type=float, default=0.4)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--initial-seed-trials-file", default=None)
    args = parser.parse_args()

    if args.resume and args.initial_seed_trials_file:
        raise SystemExit("--resume and --initial-seed-trials-file are mutually exclusive")

    loop_dir = Path(args.output_dir)
    campaign_root = Path(args.campaign_output_root) if args.campaign_output_root else loop_dir / "runs"
    loop_dir.mkdir(parents=True, exist_ok=True)
    campaign_root.mkdir(parents=True, exist_ok=True)
    loop_status_path = loop_dir / "loop_status.json"
    loop_history_path = loop_dir / "loop_history.json"
    loop_log_path = loop_dir / "loop.log"
    loop_lock_path = _loop_lock_file(loop_dir)
    dev_gate_interval = 3

    loop_lock_payload = {
        "pid": os.getpid(),
        "started_at": _ts(),
        "cmd": " ".join(sys.argv),
        "output_dir": str(loop_dir.resolve()),
        "campaign_output_root": str(campaign_root.resolve()),
    }
    acquired, existing_lock = _acquire_lock(loop_lock_path, loop_lock_payload)
    if not acquired:
        active_pid = int((existing_lock or {}).get("pid", 0) or 0)
        print(f"loop_already_running pid={active_pid}", file=sys.stderr)
        raise SystemExit(1)

    active_round = _active_round_from_loop(loop_dir)
    if args.resume and active_round is not None:
        round_lock = _read_lock(_campaign_lock_file(active_round))
        round_pid = int((round_lock or {}).get("pid", 0) or 0)
        if round_lock is not None and _pid_is_alive(round_pid):
            _release_lock(loop_lock_path, owner_pid=os.getpid())
            print(f"round_already_running pid={round_pid} run_dir={active_round}", file=sys.stderr)
            raise SystemExit(1)

    try:
        _append_log(loop_log_path, f"loop_started pid={os.getpid()}")

        start_round = 1
        previous_record: dict[str, Any] | None = None
        if args.resume:
            completed_rounds, previous_record = _resume_state(loop_dir)
            if previous_record is None and completed_rounds > 0:
                return
            start_round = completed_rounds + 1

        current_budget = args.start_budget
        previous_budget: str | None = None
        seed_trials: list[dict[str, Any]] = []
        adaptation_policy = "initial_baseline_anchored"
        if previous_record is not None:
            current_budget = str(previous_record.get("next_budget", current_budget))
            previous_budget = str(previous_record.get("budget", current_budget))
            seed_trials = project_seed_trials_to_mode(
                list(previous_record.get("next_seed_trials") or []),
                mode=_mode_for_budget(current_budget),
            )
            adaptation_policy = str(previous_record.get("next_adaptation_policy", adaptation_policy))
        elif args.initial_seed_trials_file:
            seed_trials = project_seed_trials_to_mode(
                _read_seed_trials(Path(args.initial_seed_trials_file)),
                mode=_mode_for_budget(current_budget),
            )

        _update_loop_status(
            loop_status_path,
            loop_state="running",
            current_round=start_round - 1,
            max_rounds=args.max_rounds,
            current_budget=current_budget,
            current_run_dir=None,
            current_phase=None,
            current_status=None,
            current_blockers=[],
            current_scenario=None,
            stop_reason=None,
            should_open_fast_or_full_training=False,
            allow_fast_progression=args.allow_fast_progression,
            allow_high_progression=args.allow_high_progression,
            loop_pid=os.getpid(),
            lock_owner_pid=os.getpid(),
            lock_acquired_at=loop_lock_payload["started_at"],
            current_campaign_pid=None,
            dev_gate_last_round=previous_record.get("round") if previous_record and previous_record.get("dev_gate_used_this_round") else None,
            dev_gate_interval=dev_gate_interval,
        )
        _write_training_monitor(loop_dir)

        for round_index in range(start_round, args.max_rounds + 1):
            run_dir = _round_dir(campaign_root, round_index)
            _set_current_round_pointer(loop_dir, run_dir)
            development_dev_gate = _should_use_dev_gate(
                round_index=round_index,
                max_rounds=args.max_rounds,
                current_budget=current_budget,
                previous_budget=previous_budget,
                interval=dev_gate_interval,
            )
            final_report, stage1_payload, campaign_status = _launch_round(
                args,
                run_dir=run_dir,
                budget=current_budget,
                seed_trials=seed_trials,
                loop_status_path=loop_status_path,
                loop_log_path=loop_log_path,
                round_index=round_index,
                max_rounds=args.max_rounds,
                adaptation_policy=adaptation_policy,
                resume=args.resume and run_dir.exists(),
                development_dev_gate=development_dev_gate,
                dev_gate_last_round=previous_record.get("round") if previous_record and previous_record.get("dev_gate_used_this_round") else None,
                dev_gate_interval=dev_gate_interval,
            )
            plan = plan_next_iteration(
                round_index=round_index,
                max_rounds=args.max_rounds,
                current_budget=current_budget,
                final_report=final_report,
                stage1_payload=stage1_payload,
                anchor_ratio=args.anchor_ratio,
                exploit_ratio=args.exploit_ratio,
                explore_ratio=args.explore_ratio,
                allow_fast_progression=args.allow_fast_progression,
            )
            best_variant = final_report.get("best_variant")
            best_summary = None
            if isinstance(best_variant, str):
                best_summary = final_report.get(
                    "current"
                    if best_variant == "current"
                    else "paulwei_baseline"
                    if best_variant == "paulwei_baseline"
                    else "optuna_best"
                    if best_variant == "optuna_tuned"
                    else "rl_best"
                    if best_variant == "rl_tuned"
                    else ""
                )
            prevalidation_rejection_count = sum(
                1
                for candidate in (final_report.get("optuna_best"), final_report.get("rl_best"))
                if isinstance(candidate, dict) and candidate.get("blocked_by") == "pre_validation_rejection"
            )
            validation_timeout_count = sum(
                1
                for candidate in (final_report.get("optuna_best"), final_report.get("rl_best"))
                if isinstance(candidate, dict) and candidate.get("blocked_by") == "validation_timeout"
            )
            round_runtime_s = _seconds_between(campaign_status.get("started_at"), campaign_status.get("updated_at"))
            history_item = {
                "round": round_index,
                "budget": current_budget,
                "run_dir": str(run_dir),
                "post_smoke_scenario": (final_report.get("post_smoke_scenario") or {}).get("scenario"),
                "full_training_allowed": bool((final_report.get("full_training_assessment") or {}).get("ready", False)),
                "full_training_blockers": (final_report.get("full_training_assessment") or {}).get("blockers", []),
                "warnings": (final_report.get("full_training_assessment") or {}).get("warnings", []),
                "best_variant": final_report.get("best_variant"),
                "best_score": final_report.get("best_score"),
                "train_score": _safe_float((stage1_payload.get("search_status") or {}).get("stage1_best_score")),
                "train_sharpe": _safe_float((final_report.get("train_candidate_diagnostics") or {}).get("max_train_sharpe")),
                "dev_score": _safe_float(final_report.get("best_score")),
                "dev_drawdown": _safe_float((best_summary or {}).get("dev_drawdown")) if isinstance(best_summary, dict) else None,
                "negative_dev_worst_windows": _safe_int((best_summary or {}).get("negative_dev_worst_windows")) if isinstance(best_summary, dict) else None,
                "train_candidate_diagnostics": final_report.get("train_candidate_diagnostics"),
                "candidate_ready_for_handoff": bool(final_report.get("candidate_ready_for_handoff", False)),
                "candidate_frozen_id": final_report.get("candidate_frozen_id"),
                "dev_gate_used_this_round": bool(final_report.get("dev_gate_used_this_round", development_dev_gate)),
                "adaptation_policy": adaptation_policy,
                "round_runtime_s": round_runtime_s,
                "stage1_runtime_s": _safe_float(campaign_status.get("stage1_runtime_s")),
                "stage2_runtime_s": _safe_float((loop_status := _read_json(loop_status_path) or {}).get("stage2_runtime_s")),
                "prevalidation_rejection_count": prevalidation_rejection_count,
                "validation_timeout_count": validation_timeout_count,
                "campaign_status": {
                    "phase": campaign_status.get("phase"),
                    "status": campaign_status.get("status"),
                    "campaign_pid": campaign_status.get("campaign_pid"),
                },
                "next_budget": plan.next_budget,
                "next_adaptation_policy": plan.adaptation_policy,
                "next_seed_trials": plan.all_seed_trials,
                "loop_progression_blocked": plan.loop_progression_blocked,
                "should_open_fast_or_full_training": plan.should_open_fast_or_full_training,
            }
            history_item["promotion_health"] = _promotion_health(_recent_history_records(loop_dir) + [history_item])
            _append_history(loop_history_path, history_item)
            _append_jsonl(_training_history_path(loop_dir), history_item)
            if history_item["candidate_ready_for_handoff"]:
                _write_handoff_candidate(loop_dir, history_item)
            _append_log(
                loop_log_path,
                (
                    f"round_completed round={round_index} scenario={history_item['post_smoke_scenario']} "
                    f"next_budget={plan.next_budget} blockers={history_item['full_training_blockers']}"
                ),
            )
            _update_loop_status(
                loop_status_path,
                current_round=round_index,
                current_budget=current_budget,
                current_run_dir=str(run_dir),
                current_phase=campaign_status.get("phase"),
                current_status=campaign_status.get("status"),
                current_blockers=history_item["full_training_blockers"],
                current_scenario=history_item["post_smoke_scenario"],
                should_open_fast_or_full_training=plan.should_open_fast_or_full_training,
                loop_progression_blocked=plan.loop_progression_blocked,
                next_budget=plan.next_budget,
                next_adaptation_policy=plan.adaptation_policy,
                current_campaign_pid=campaign_status.get("campaign_pid"),
                candidate_ready_for_handoff=history_item["candidate_ready_for_handoff"],
                candidate_frozen_id=history_item["candidate_frozen_id"],
                dev_gate_used_this_round=history_item["dev_gate_used_this_round"],
                dev_gate_last_round=round_index if history_item["dev_gate_used_this_round"] else (previous_record.get("round") if previous_record and previous_record.get("dev_gate_used_this_round") else None),
                dev_gate_interval=dev_gate_interval,
                loop_state="running",
            )
            _write_training_monitor(loop_dir)
            if plan.stop or (args.stop_on_gate_pass and plan.should_open_fast_or_full_training):
                stop_reason = plan.stop_reason or "gate_passed"
                _append_log(loop_log_path, f"loop_stopped round={round_index} reason={stop_reason}")
                _update_loop_status(
                    loop_status_path,
                    loop_state="stopped",
                    stop_reason=stop_reason,
                )
                _write_training_monitor(loop_dir)
                break
            previous_budget = current_budget
            current_budget = plan.next_budget
            seed_trials = plan.all_seed_trials
            adaptation_policy = plan.adaptation_policy
            previous_record = history_item
        else:
            _append_log(loop_log_path, "loop_stopped reason=max_rounds_reached")
            _update_loop_status(
                loop_status_path,
                loop_state="stopped",
                stop_reason="max_rounds_reached",
            )
            _write_training_monitor(loop_dir)
    except KeyboardInterrupt:
        _append_log(loop_log_path, "loop_interrupted")
        _update_loop_status(
            loop_status_path,
            loop_state="failed",
            stop_reason="interrupted",
            current_status="interrupted",
        )
        _write_training_monitor(loop_dir)
        raise
    finally:
        _release_lock(loop_lock_path, owner_pid=os.getpid())

    history = _read_json(loop_history_path)
    records = history if isinstance(history, list) else []
    summary_lines = [
        "# Framework Research Loop",
        "",
        f"- rounds_completed: `{len(records)}`",
        f"- stop_reason: `{(_read_json(loop_status_path) or {}).get('stop_reason')}`",
        "",
    ]
    for item in records:
        summary_lines.extend(
            [
                f"## Round {item['round']}",
                "",
                f"- budget: `{item['budget']}`",
                f"- scenario: `{item['post_smoke_scenario']}`",
                f"- blockers: `{item['full_training_blockers']}`",
                f"- next_budget: `{item['next_budget']}`",
                f"- adaptation_policy: `{item['adaptation_policy']}`",
                "",
            ]
        )
    (loop_dir / "loop_summary.md").write_text("\n".join(summary_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
