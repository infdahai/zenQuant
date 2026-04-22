#!/usr/bin/env python3
"""Compare the failed and rerun stage2 benchmark runs and write a review artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.perf_benchmark import build_stage2_benchmark_review, write_stage2_benchmark_review


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Review stage2 benchmark rerun vs baseline")
    parser.add_argument(
        "--failed-status",
        type=Path,
        default=Path("results/perf_stage2_benchmark_fast_current/campaign_status.json"),
    )
    parser.add_argument(
        "--baseline-status",
        type=Path,
        default=Path("results/framework_optimization_loop_v27/runs/round_003/campaign_status.json"),
    )
    parser.add_argument(
        "--rerun-status",
        type=Path,
        default=Path("results/perf_stage2_benchmark_fast_rerun/campaign_status.json"),
    )
    parser.add_argument(
        "--rerun-report",
        type=Path,
        default=Path("results/perf_stage2_benchmark_fast_rerun/framework_optimization.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/perf_stage2_benchmark_fast_rerun"),
    )
    args = parser.parse_args()

    payload = build_stage2_benchmark_review(
        failed_status=_read_json(args.failed_status),
        baseline_status=_read_json(args.baseline_status),
        rerun_status=_read_json(args.rerun_status),
        rerun_report=_read_json(args.rerun_report) if args.rerun_report.exists() else None,
    )
    written = write_stage2_benchmark_review(payload=payload, output_dir=args.output_dir)
    print(json.dumps({"review": written, "classification": payload["classification"]}, indent=2))


if __name__ == "__main__":
    main()
