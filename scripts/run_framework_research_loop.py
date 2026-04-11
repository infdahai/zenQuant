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


def _current_campaign_snapshot(run_dir: Path) -> dict[str, Any]:
    status = _read_json(run_dir / "campaign_status.json") or {}
    return {
        "phase": status.get("phase"),
        "status": status.get("status"),
        "optuna_trials_completed": status.get("optuna_trials_completed"),
        "rl_episodes_completed": status.get("rl_episodes_completed"),
        "stage1_last_completed_unit": status.get("stage1_last_completed_unit"),
        "stage2_subphase": status.get("stage2_subphase"),
        "stage2_progress_completed": status.get("stage2_progress_completed"),
        "stage2_progress_total": status.get("stage2_progress_total"),
        "stage2_last_completed_unit": status.get("stage2_last_completed_unit"),
        "full_training_allowed": status.get("full_training_allowed"),
        "full_training_blockers": status.get("full_training_blockers"),
        "post_smoke_scenario": status.get("post_smoke_scenario"),
        "campaign_updated_at": status.get("updated_at"),
    }


def _load_round_artifacts(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    final_report = _read_json(run_dir / "framework_optimization.json") or {}
    stage1_payload = _read_json(run_dir / "checkpoints" / "stage1_search.json") or {}
    campaign_status = _read_json(run_dir / "campaign_status.json") or {}
    return final_report, stage1_payload, campaign_status


def _build_campaign_command(args: argparse.Namespace, run_dir: Path, *, budget: str, seed_trials_file: Path | None, resume: bool) -> list[str]:
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
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    run_dir.mkdir(parents=True, exist_ok=True)
    campaign_lock_path = _campaign_lock_file(run_dir)
    seed_trials_file: Path | None = None
    if seed_trials:
        seed_trials_file = _seed_file(run_dir)
        _write_json(seed_trials_file, seed_trials)  # type: ignore[arg-type]

    command = _build_campaign_command(args, run_dir, budget=budget, seed_trials_file=seed_trials_file, resume=resume)
    _append_log(
        loop_log_path,
        (
            f"round_started round={round_index}/{max_rounds} budget={budget} "
            f"seed_trials={len(seed_trials)} adaptation_policy={adaptation_policy} resume={resume}"
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
    try:
        while True:
            snapshot = _current_campaign_snapshot(run_dir)
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
                loop_state="campaign_running",
            )
            progress_signature = (
                snapshot.get("phase"),
                snapshot.get("status"),
                snapshot.get("optuna_trials_completed"),
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
                        f"stage1_unit={snapshot.get('stage1_last_completed_unit')} "
                        f"stage2_subphase={snapshot.get('stage2_subphase')} "
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
            loop_state="failed",
            stop_reason=f"campaign_round_failed:{round_index}",
        )
        raise RuntimeError(f"campaign_round_failed exit_code={exit_code} run_dir={run_dir}")
    if not final_report:
        raise RuntimeError(f"campaign_round_missing_final_report run_dir={run_dir}")
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
    args = parser.parse_args()

    loop_dir = Path(args.output_dir)
    campaign_root = Path(args.campaign_output_root) if args.campaign_output_root else loop_dir / "runs"
    loop_dir.mkdir(parents=True, exist_ok=True)
    campaign_root.mkdir(parents=True, exist_ok=True)
    loop_status_path = loop_dir / "loop_status.json"
    loop_history_path = loop_dir / "loop_history.json"
    loop_log_path = loop_dir / "loop.log"
    loop_lock_path = _loop_lock_file(loop_dir)

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
        seed_trials: list[dict[str, Any]] = []
        adaptation_policy = "initial_baseline_anchored"
        if previous_record is not None:
            current_budget = str(previous_record.get("next_budget", current_budget))
            seed_trials = project_seed_trials_to_mode(
                list(previous_record.get("next_seed_trials") or []),
                mode=_mode_for_budget(current_budget),
            )
            adaptation_policy = str(previous_record.get("next_adaptation_policy", adaptation_policy))

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
        )

        for round_index in range(start_round, args.max_rounds + 1):
            run_dir = _round_dir(campaign_root, round_index)
            _set_current_round_pointer(loop_dir, run_dir)
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
                "train_candidate_diagnostics": final_report.get("train_candidate_diagnostics"),
                "adaptation_policy": adaptation_policy,
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
            _append_history(loop_history_path, history_item)
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
                loop_state="running",
            )
            if plan.stop or (args.stop_on_gate_pass and plan.should_open_fast_or_full_training):
                stop_reason = plan.stop_reason or "gate_passed"
                _append_log(loop_log_path, f"loop_stopped round={round_index} reason={stop_reason}")
                _update_loop_status(
                    loop_status_path,
                    loop_state="stopped",
                    stop_reason=stop_reason,
                )
                break
            current_budget = plan.next_budget
            seed_trials = plan.all_seed_trials
            adaptation_policy = plan.adaptation_policy
        else:
            _append_log(loop_log_path, "loop_stopped reason=max_rounds_reached")
            _update_loop_status(
                loop_status_path,
                loop_state="stopped",
                stop_reason="max_rounds_reached",
            )
    except KeyboardInterrupt:
        _append_log(loop_log_path, "loop_interrupted")
        _update_loop_status(
            loop_status_path,
            loop_state="failed",
            stop_reason="interrupted",
            current_status="interrupted",
        )
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
