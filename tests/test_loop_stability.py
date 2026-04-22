from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zenquant.research.loop_stability import build_loop_stability_review, write_loop_stability_review


class LoopStabilityReviewTests(unittest.TestCase):
    def test_build_loop_stability_review_classifies_stale_and_completed_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            stale_loop = root / "framework_optimization_loop_v31"
            stale_run = stale_loop / "runs" / "round_002"
            stale_run.mkdir(parents=True, exist_ok=True)
            (stale_loop / "loop_status.json").write_text(
                json.dumps(
                    {
                        "loop_state": "campaign_running",
                        "current_run_dir": str(stale_run),
                        "stop_reason": None,
                    }
                ),
                encoding="utf-8",
            )
            (stale_run / "campaign_status.json").write_text(
                json.dumps({"phase": "stage2_full_verdict", "status": "running", "rl_episodes_completed": 4}),
                encoding="utf-8",
            )

            completed_loop = root / "framework_optimization_loop_v32_fg"
            completed_run = completed_loop / "runs" / "round_001"
            completed_run.mkdir(parents=True, exist_ok=True)
            (completed_loop / "loop_status.json").write_text(
                json.dumps(
                    {
                        "loop_state": "stopped",
                        "current_run_dir": str(completed_run),
                        "stop_reason": "max_rounds_reached",
                    }
                ),
                encoding="utf-8",
            )
            (completed_run / "campaign_status.json").write_text(
                json.dumps({"phase": "stage3_complete", "status": "completed", "rl_episodes_completed": 4}),
                encoding="utf-8",
            )
            (completed_run / "framework_optimization.json").write_text(json.dumps({"best_variant": "current"}), encoding="utf-8")

            payload = build_loop_stability_review(root)

        loops = {item["loop_id"]: item for item in payload["loops"]}
        self.assertEqual(loops["framework_optimization_loop_v31"]["terminal_classification"], "stale_running_state")
        self.assertEqual(loops["framework_optimization_loop_v31"]["failure_cluster"], "stage2_durability")
        self.assertTrue(loops["framework_optimization_loop_v31"]["stage1_rl_progress_seen"])
        self.assertEqual(loops["framework_optimization_loop_v32_fg"]["terminal_classification"], "completed")
        self.assertEqual(loops["framework_optimization_loop_v32_fg"]["last_completed_round"], 1)

    def test_write_loop_stability_review_writes_json_and_md(self) -> None:
        payload = {
            "generated_at": "2026-04-22T00:00:00+00:00",
            "results_root": "results",
            "loop_count": 1,
            "loops": [
                {
                    "loop_id": "framework_optimization_loop_v32_fg",
                    "last_completed_round": 1,
                    "terminal_classification": "completed",
                    "failure_cluster": "unknown_or_completed",
                    "stale_state_detected": False,
                    "bootstrap_status_present": True,
                    "stage1_rl_progress_seen": True,
                    "stage2_terminal_state_written": True,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = write_loop_stability_review(payload, out)
            self.assertTrue(Path(written["json_path"]).exists())
            self.assertTrue(Path(written["md_path"]).exists())


if __name__ == "__main__":
    unittest.main()
