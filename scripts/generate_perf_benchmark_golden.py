#!/usr/bin/env python3
"""Generate synthetic stage-2 review cases from the Python reference."""

import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "perf_benchmark_reference", Path(__file__).resolve().parent.parent / "zenquant/research/perf_benchmark.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
build_stage2_benchmark_review = module.build_stage2_benchmark_review

cases = [
    {
        "failed": {"status": "running", "phase": "stage1", "optuna_trials_completed": 3},
        "baseline": {
            "stage1_runtime_s": 188.33,
            "stage1_avg_indicator_step_s": 33.7124,
            "stage2_prepare_variant_miss_reasons": {"old": 1},
        },
        "rerun": {
            "status": "completed",
            "stage1_runtime_s": 183.44,
            "stage1_avg_indicator_step_s": 36.566,
            "stage2_prepare_variant_cache_hits": 2,
            "stage2_prepare_variant_miss_reasons": {"cache": 3},
        },
        "report_present": True,
    },
    {
        "failed": {},
        "baseline": {"stage1_avg_indicator_step_s": 2},
        "rerun": {"status": "completed", "stage1_avg_indicator_step_s": 1.5},
        "report_present": False,
    },
    {
        "failed": {},
        "baseline": {"stage1_avg_indicator_step_s": 2},
        "rerun": {"status": "completed", "stage1_avg_indicator_step_s": 1.4999},
        "report_present": False,
    },
    {
        "failed": {},
        "baseline": {},
        "rerun": {"status": "running", "stage2_prepare_variant_replay_reused": 4},
        "report_present": False,
    },
    {"failed": {}, "baseline": {}, "rerun": {"status": "completed"}, "report_present": False},
]
for c in cases:
    c["expected"] = build_stage2_benchmark_review(
        failed_status=c["failed"],
        baseline_status=c["baseline"],
        rerun_status=c["rerun"],
        rerun_report={} if c["report_present"] else None,
    )
    del c["expected"]["generated_at"]
parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
path = Path("internal/research/testdata/perf_benchmark_golden.json")
encoded = json.dumps(cases, indent=2) + "\n"
if args.check:
    if path.read_text() != encoded:
        raise SystemExit("benchmark fixture is stale")
else:
    path.write_text(encoded)
