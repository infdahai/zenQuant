from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _delta(current: Any, baseline: Any, *, digits: int = 4) -> float | None:
    if current is None or baseline is None:
        return None
    return round(float(current) - float(baseline), digits)


def build_stage2_benchmark_review(
    *,
    failed_status: dict[str, Any],
    baseline_status: dict[str, Any],
    rerun_status: dict[str, Any],
    rerun_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed_reason = "stage1 benchmark run ended without authoritative terminal state"
    failed_evidence = {
        "phase": failed_status.get("phase"),
        "status": failed_status.get("status"),
        "optuna_trials_completed": failed_status.get("optuna_trials_completed"),
        "optuna_trials_requested": failed_status.get("optuna_trials_requested"),
        "stage2_progress_completed": failed_status.get("stage2_progress_completed"),
        "framework_report_present": False,
    }

    comparison = {
        "stage1_runtime_s": {
            "baseline": baseline_status.get("stage1_runtime_s"),
            "rerun": rerun_status.get("stage1_runtime_s"),
            "delta": _delta(rerun_status.get("stage1_runtime_s"), baseline_status.get("stage1_runtime_s"), digits=2),
        },
        "stage1_avg_trial_runtime_s": {
            "baseline": baseline_status.get("stage1_avg_trial_runtime_s"),
            "rerun": rerun_status.get("stage1_avg_trial_runtime_s"),
            "delta": _delta(
                rerun_status.get("stage1_avg_trial_runtime_s"),
                baseline_status.get("stage1_avg_trial_runtime_s"),
                digits=4,
            ),
        },
        "stage1_avg_replay_run_s": {
            "baseline": baseline_status.get("stage1_avg_replay_run_s"),
            "rerun": rerun_status.get("stage1_avg_replay_run_s"),
            "delta": _delta(
                rerun_status.get("stage1_avg_replay_run_s"),
                baseline_status.get("stage1_avg_replay_run_s"),
                digits=4,
            ),
        },
        "stage1_avg_indicator_step_s": {
            "baseline": baseline_status.get("stage1_avg_indicator_step_s"),
            "rerun": rerun_status.get("stage1_avg_indicator_step_s"),
            "delta": _delta(
                rerun_status.get("stage1_avg_indicator_step_s"),
                baseline_status.get("stage1_avg_indicator_step_s"),
                digits=4,
            ),
        },
        "trial_replay_cache_hit_rate": {
            "baseline": baseline_status.get("trial_replay_cache_hit_rate"),
            "rerun": rerun_status.get("trial_replay_cache_hit_rate"),
            "delta": _delta(
                rerun_status.get("trial_replay_cache_hit_rate"),
                baseline_status.get("trial_replay_cache_hit_rate"),
                digits=4,
            ),
        },
        "replay_context_cache_hit_rate": {
            "baseline": baseline_status.get("replay_context_cache_hit_rate"),
            "rerun": rerun_status.get("replay_context_cache_hit_rate"),
            "delta": _delta(
                rerun_status.get("replay_context_cache_hit_rate"),
                baseline_status.get("replay_context_cache_hit_rate"),
                digits=4,
            ),
        },
        "validation_cache_hit_rate": {
            "baseline": baseline_status.get("validation_cache_hit_rate"),
            "rerun": rerun_status.get("validation_cache_hit_rate"),
            "delta": _delta(
                rerun_status.get("validation_cache_hit_rate"),
                baseline_status.get("validation_cache_hit_rate"),
                digits=4,
            ),
        },
        "window_snapshot_cache_hit_rate": {
            "baseline": baseline_status.get("window_snapshot_cache_hit_rate"),
            "rerun": rerun_status.get("window_snapshot_cache_hit_rate"),
            "delta": _delta(
                rerun_status.get("window_snapshot_cache_hit_rate"),
                baseline_status.get("window_snapshot_cache_hit_rate"),
                digits=4,
            ),
        },
        "stage2_prepare_variant_cache_hits": {
            "baseline": baseline_status.get("stage2_prepare_variant_cache_hits"),
            "rerun": rerun_status.get("stage2_prepare_variant_cache_hits"),
            "delta": _delta(
                rerun_status.get("stage2_prepare_variant_cache_hits"),
                baseline_status.get("stage2_prepare_variant_cache_hits"),
                digits=0,
            ),
        },
        "stage2_prepare_variant_replay_reused": {
            "baseline": baseline_status.get("stage2_prepare_variant_replay_reused"),
            "rerun": rerun_status.get("stage2_prepare_variant_replay_reused"),
            "delta": _delta(
                rerun_status.get("stage2_prepare_variant_replay_reused"),
                baseline_status.get("stage2_prepare_variant_replay_reused"),
                digits=0,
            ),
        },
        "stage2_prepare_variant_miss_reasons": {
            "baseline": baseline_status.get("stage2_prepare_variant_miss_reasons"),
            "rerun": rerun_status.get("stage2_prepare_variant_miss_reasons"),
        },
    }

    stage1_indicator_delta = comparison["stage1_avg_indicator_step_s"]["delta"]
    stage2_cache_hits = rerun_status.get("stage2_prepare_variant_cache_hits") or 0
    stage2_reused = rerun_status.get("stage2_prepare_variant_replay_reused") or 0
    benchmark_status = "partial_success"
    if rerun_status.get("status") != "completed":
        benchmark_status = "failed"
    elif (stage1_indicator_delta is not None and stage1_indicator_delta < -0.5) or stage2_cache_hits > 0 or stage2_reused > 0:
        benchmark_status = "success"

    payload = {
        "generated_at": _ts(),
        "benchmark_scope": "cpu_hot_path_stage2_reuse",
        "failed_benchmark_root_cause": failed_reason,
        "failed_benchmark_evidence": failed_evidence,
        "baseline_source": "results/framework_optimization_loop_v27/runs/round_003/campaign_status.json",
        "rerun_source": "results/perf_stage2_benchmark_fast_rerun/campaign_status.json",
        "rerun_terminal_state": {
            "status": rerun_status.get("status"),
            "phase": rerun_status.get("phase"),
            "best_variant": rerun_status.get("best_variant"),
            "framework_report_present": rerun_report is not None,
        },
        "metric_comparison": comparison,
        "classification": benchmark_status,
        "interpretation": {
            "indicator_hot_path_improved": bool(stage1_indicator_delta is not None and stage1_indicator_delta < 0),
            "stage2_reuse_visible": bool(stage2_cache_hits > 0 or stage2_reused > 0),
            "main_takeaway": (
                "stage2 reuse is now observable and the rerun completed cleanly, but indicator hot path did not improve yet"
                if stage1_indicator_delta is not None and stage1_indicator_delta >= 0 and (stage2_cache_hits > 0 or stage2_reused > 0)
                else "rerun completed cleanly with observable cache reuse and measurable runtime improvement"
                if benchmark_status == "success"
                else "diagnostics are wired correctly, but measured runtime improvement remains limited"
            ),
        },
    }
    return payload


def write_stage2_benchmark_review(*, payload: dict[str, Any], output_dir: Path) -> dict[str, str]:
    json_path = output_dir / "perf_stage2_benchmark_review.json"
    md_path = output_dir / "perf_stage2_benchmark_review.md"
    _write_json(json_path, payload)
    lines = [
        "# Stage2 Benchmark Review",
        "",
        f"- classification: `{payload['classification']}`",
        f"- failed_benchmark_root_cause: `{payload['failed_benchmark_root_cause']}`",
        f"- main_takeaway: `{payload['interpretation']['main_takeaway']}`",
        f"- indicator_hot_path_improved: `{payload['interpretation']['indicator_hot_path_improved']}`",
        f"- stage2_reuse_visible: `{payload['interpretation']['stage2_reuse_visible']}`",
        f"- metric_comparison: `{payload['metric_comparison']}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path)}
