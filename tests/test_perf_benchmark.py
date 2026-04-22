from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zenquant.research.perf_benchmark import build_stage2_benchmark_review, write_stage2_benchmark_review


class Stage2BenchmarkReviewTests(unittest.TestCase):
    def test_build_stage2_benchmark_review_classifies_completed_rerun_with_stage2_reuse_as_success(self) -> None:
        failed = {
            "phase": "stage1_optuna_search",
            "status": "running",
            "optuna_trials_completed": 46,
            "optuna_trials_requested": 50,
            "stage2_progress_completed": 0,
        }
        baseline = {
            "stage1_runtime_s": 188.33,
            "stage1_avg_trial_runtime_s": 90.8472,
            "stage1_avg_replay_run_s": 90.1259,
            "stage1_avg_indicator_step_s": 33.7124,
            "trial_replay_cache_hit_rate": 0.8421,
            "replay_context_cache_hit_rate": None,
            "validation_cache_hit_rate": None,
            "window_snapshot_cache_hit_rate": None,
            "stage2_prepare_variant_cache_hits": None,
            "stage2_prepare_variant_replay_reused": None,
            "stage2_prepare_variant_miss_reasons": None,
        }
        rerun = {
            "status": "completed",
            "phase": "stage3_complete",
            "best_variant": "current",
            "stage1_runtime_s": 183.44,
            "stage1_avg_trial_runtime_s": 91.9781,
            "stage1_avg_replay_run_s": 90.126,
            "stage1_avg_indicator_step_s": 36.566,
            "trial_replay_cache_hit_rate": 0.7143,
            "replay_context_cache_hit_rate": 0.0,
            "validation_cache_hit_rate": 0.0645,
            "window_snapshot_cache_hit_rate": 0.0645,
            "stage2_prepare_variant_cache_hits": 2,
            "stage2_prepare_variant_replay_reused": 6,
            "stage2_prepare_variant_miss_reasons": {
                "summary_cache": 3,
                "window_snapshot:dev": 3,
            },
        }

        payload = build_stage2_benchmark_review(
            failed_status=failed,
            baseline_status=baseline,
            rerun_status=rerun,
            rerun_report={"best_variant": "current"},
        )

        self.assertEqual(payload["classification"], "success")
        self.assertEqual(
            payload["failed_benchmark_root_cause"],
            "stage1 benchmark run ended without authoritative terminal state",
        )
        self.assertTrue(payload["interpretation"]["stage2_reuse_visible"])
        self.assertFalse(payload["interpretation"]["indicator_hot_path_improved"])

    def test_write_stage2_benchmark_review_writes_json_and_md(self) -> None:
        payload = {
            "classification": "partial_success",
            "failed_benchmark_root_cause": "root_cause",
            "interpretation": {
                "main_takeaway": "takeaway",
                "indicator_hot_path_improved": False,
                "stage2_reuse_visible": True,
            },
            "metric_comparison": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = write_stage2_benchmark_review(payload=payload, output_dir=out)
            self.assertTrue(Path(written["json_path"]).exists())
            self.assertTrue(Path(written["md_path"]).exists())


if __name__ == "__main__":
    unittest.main()
