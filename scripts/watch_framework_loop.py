#!/usr/bin/env python3
"""Watch or print the current state of the framework research loop."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _tail_lines(path: Path, count: int = 20) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()[-count:]


def _current_round_path(loop_dir: Path) -> Path | None:
    pointer = loop_dir / "current_round"
    if not pointer.exists() and not pointer.is_symlink():
        return None
    if pointer.is_symlink():
        return pointer.resolve()
    try:
        return Path(pointer.read_text(encoding="utf-8").strip())
    except Exception:
        return pointer


def _snapshot(loop_dir: Path) -> dict[str, Any]:
    loop_status = _read_json(loop_dir / "loop_status.json") or {}
    round_dir = _current_round_path(loop_dir)
    campaign_status = _read_json(round_dir / "campaign_status.json") if round_dir is not None else None
    loop_lock = _read_json(loop_dir / "loop.lock")
    campaign_lock = _read_json(round_dir / "campaign.lock") if round_dir is not None else None
    return {
        "loop_status": loop_status,
        "loop_lock": loop_lock,
        "current_round_dir": str(round_dir) if round_dir is not None else None,
        "campaign_status": campaign_status,
        "campaign_lock": campaign_lock,
        "loop_log_tail": _tail_lines(loop_dir / "loop.log"),
        "campaign_log_tail": _tail_lines(round_dir / "campaign.log") if round_dir is not None else [],
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def _render_snapshot(loop_dir: Path) -> str:
    monitor = _read_json(loop_dir / "training_monitor.json") or {}
    if not monitor:
        return json.dumps(_snapshot(loop_dir), indent=2)
    recent_rounds = monitor.get("recent_rounds") or []
    lines = [
        f"run_id: {monitor.get('run_id')}",
        f"state: {monitor.get('loop_state')} round={monitor.get('current_round')} budget={monitor.get('current_budget')} phase={monitor.get('current_phase')} status={monitor.get('current_status')}",
        f"runtime: elapsed={monitor.get('elapsed_wall_time_s')} round={monitor.get('round_runtime_s')} stage1={monitor.get('stage1_runtime_s')} stage2={monitor.get('stage2_runtime_s')}",
        f"progress: optuna={monitor.get('optuna_trials_completed')}/{monitor.get('optuna_trials_total')} rl={monitor.get('rl_steps_completed')}/{monitor.get('rl_steps_requested')} stage2={monitor.get('stage2_progress_completed')}/{monitor.get('stage2_progress_total')}",
        f"eta: round={monitor.get('round_eta_seconds')}s heartbeat_freshness={monitor.get('heartbeat_freshness_seconds')}s stalled={monitor.get('stalled')} reason={monitor.get('stalled_reason')}",
        f"cpu: mode={monitor.get('cpu_expected_mode')} util={monitor.get('cpu_utilization_estimate')} active_processes={monitor.get('active_process_count')} underutilized={monitor.get('cpu_underutilized')} reason={monitor.get('cpu_underutilized_reason')}",
        f"quality: train_best={monitor.get('best_train_score_so_far')} dev_best={monitor.get('best_dev_score_so_far')} trend={monitor.get('candidate_improvement_trend')} best_variant={monitor.get('best_candidate_variant_so_far')}",
        f"gate: pass={monitor.get('dev_gate_pass_count')} fail={monitor.get('dev_gate_fail_count')} handoff_ready={monitor.get('candidate_ready_for_handoff')}",
        f"alerts: {monitor.get('alerts')}",
    ]
    if recent_rounds:
        lines.append("recent_rounds:")
        for item in recent_rounds:
            lines.append(
                f"- round={item.get('round')} budget={item.get('budget')} train={item.get('train_score')} dev={item.get('dev_score')} trend={item.get('promotion_health')} variant={item.get('best_variant')}"
            )
    return "\n".join(lines)


def _render_history(loop_dir: Path) -> str:
    records = _read_jsonl(loop_dir / "training_history.jsonl")
    if not records:
        return "[]"
    lines = []
    for item in records[-10:]:
        lines.append(
            f"round={item.get('round')} budget={item.get('budget')} train={item.get('train_score')} dev={item.get('dev_score')} trend={item.get('promotion_health')} handoff={item.get('candidate_ready_for_handoff')} blockers={item.get('full_training_blockers')}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch framework research loop status")
    parser.add_argument("--output-dir", default="results/framework_optimization_loop")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--format", choices=("snapshot", "history", "raw"), default="snapshot")
    args = parser.parse_args()

    loop_dir = Path(args.output_dir)
    while True:
        if args.format == "raw":
            print(json.dumps(_snapshot(loop_dir), indent=2))
        elif args.format == "history":
            print(_render_history(loop_dir))
        else:
            print(_render_snapshot(loop_dir))
        if not args.follow:
            return
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    main()
