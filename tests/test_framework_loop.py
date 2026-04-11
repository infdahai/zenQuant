import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LOOP = _load_module("framework_loop_module", "scripts/run_framework_research_loop.py")
WATCH = _load_module("watch_framework_loop_module", "scripts/watch_framework_loop.py")


class FrameworkLoopScriptTests(unittest.TestCase):
    def test_acquire_lock_replaces_stale_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "loop.lock"
            lock_path.write_text(json.dumps({"pid": 999999, "started_at": "old"}), encoding="utf-8")

            acquired, existing = LOOP._acquire_lock(lock_path, {"pid": os.getpid(), "started_at": "now"})
            payload = json.loads(lock_path.read_text(encoding="utf-8"))

        self.assertTrue(acquired)
        self.assertIsNone(existing)
        self.assertEqual(payload["pid"], os.getpid())

    def test_main_rejects_when_loop_lock_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp) / "framework_optimization_loop"
            loop_dir.mkdir(parents=True, exist_ok=True)
            (loop_dir / "loop.lock").write_text(
                json.dumps({"pid": os.getpid(), "started_at": "now"}),
                encoding="utf-8",
            )
            with (
                unittest.mock.patch(
                    "sys.argv",
                    ["run_framework_research_loop.py", "--output-dir", str(loop_dir), "--resume"],
                ),
                self.assertRaises(SystemExit) as exc,
            ):
                LOOP.main()

        self.assertEqual(exc.exception.code, 1)

    def test_build_campaign_command_includes_seed_trials_and_resume(self) -> None:
        class Args:
            root = "data/raw/binance"
            symbol = "BTCUSDT"
            liquidation_dir = "data/raw/merged_liquidations/aggregated"

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "framework_loop_round"
            seed_file = Path(tmp) / "seed_trials.json"
            seed_file.write_text("[]", encoding="utf-8")
            command = LOOP._build_campaign_command(Args(), run_dir, budget="smoke", seed_trials_file=seed_file, resume=True)

        self.assertIn("scripts/run_framework_campaign.py", command)
        self.assertIn("--seed-trials-file", command)
        self.assertIn(str(seed_file), command)
        self.assertIn("--resume", command)

    def test_watch_snapshot_reads_loop_and_campaign_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            run_dir = loop_dir / "runs" / "round_001"
            run_dir.mkdir(parents=True, exist_ok=True)
            (loop_dir / "loop_status.json").write_text(
                json.dumps({"current_round": 1, "current_budget": "smoke"}),
                encoding="utf-8",
            )
            (loop_dir / "loop.log").write_text("[ts] loop started\n", encoding="utf-8")
            (run_dir / "campaign_status.json").write_text(
                json.dumps({"phase": "stage1_optuna_search", "status": "running"}),
                encoding="utf-8",
            )
            (run_dir / "campaign.log").write_text("[ts] campaign started\n", encoding="utf-8")
            (loop_dir / "current_round").symlink_to(run_dir)

            snapshot = WATCH._snapshot(loop_dir)

        self.assertEqual(snapshot["loop_status"]["current_round"], 1)
        self.assertEqual(snapshot["campaign_status"]["phase"], "stage1_optuna_search")
        self.assertIn("loop started", snapshot["loop_log_tail"][0])
        self.assertIn("campaign started", snapshot["campaign_log_tail"][0])

    def test_watch_main_prints_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            (loop_dir / "loop_status.json").write_text(json.dumps({"current_round": 2}), encoding="utf-8")
            buffer = io.StringIO()
            with (
                redirect_stdout(buffer),
                unittest.mock.patch("sys.argv", ["watch_framework_loop.py", "--output-dir", str(loop_dir)]),
            ):
                WATCH.main()
            rendered = json.loads(buffer.getvalue())

        self.assertEqual(rendered["loop_status"]["current_round"], 2)

    def test_launch_round_marks_loop_failed_when_campaign_fails(self) -> None:
        class FakeProcess:
            pid = 43210

            def poll(self):
                return 1

            def wait(self):
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            run_dir = loop_dir / "runs" / "round_001"
            loop_status_path = loop_dir / "loop_status.json"
            loop_log_path = loop_dir / "loop.log"
            args = Namespace(
                root="data/raw/binance",
                symbol="BTCUSDT",
                liquidation_dir="data/raw/merged_liquidations/aggregated",
                poll_interval=0.0,
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "campaign_status.json").write_text(
                json.dumps(
                    {
                        "phase": "stage1_optuna_search",
                        "status": "failed",
                        "full_training_blockers": ["no_tuned_candidate_from_smoke"],
                    }
                ),
                encoding="utf-8",
            )

            with (
                unittest.mock.patch.object(LOOP.subprocess, "Popen", return_value=FakeProcess()),
                self.assertRaisesRegex(RuntimeError, "campaign_round_failed"),
            ):
                LOOP._launch_round(
                    args,
                    run_dir=run_dir,
                    budget="fast",
                    seed_trials=[],
                    loop_status_path=loop_status_path,
                    loop_log_path=loop_log_path,
                    round_index=1,
                    max_rounds=8,
                    adaptation_policy="hostile_window_defensive",
                    resume=False,
                )

            loop_status = json.loads(loop_status_path.read_text(encoding="utf-8"))

        self.assertEqual(loop_status["loop_state"], "failed")
        self.assertEqual(loop_status["stop_reason"], "campaign_round_failed:1")

    def test_launch_round_rejects_when_campaign_lock_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            run_dir = loop_dir / "runs" / "round_001"
            loop_status_path = loop_dir / "loop_status.json"
            loop_log_path = loop_dir / "loop.log"
            args = Namespace(
                root="data/raw/binance",
                symbol="BTCUSDT",
                liquidation_dir="data/raw/merged_liquidations/aggregated",
                poll_interval=0.0,
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "campaign.lock").write_text(
                json.dumps({"pid": os.getpid(), "launcher_pid": 123, "round": 1}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "round_already_running"):
                LOOP._launch_round(
                    args,
                    run_dir=run_dir,
                    budget="fast",
                    seed_trials=[],
                    loop_status_path=loop_status_path,
                    loop_log_path=loop_log_path,
                    round_index=1,
                    max_rounds=8,
                    adaptation_policy="hostile_window_defensive",
                    resume=False,
                )

            loop_status = json.loads(loop_status_path.read_text(encoding="utf-8"))

        self.assertEqual(loop_status["loop_state"], "blocked")
        self.assertEqual(loop_status["stop_reason"], "round_already_running:1")

    def test_resume_projects_legacy_seed_trials_to_current_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            (loop_dir / "loop_history.json").write_text(
                json.dumps(
                    [
                        {
                            "round": 4,
                            "next_budget": "fast",
                            "next_seed_trials": [
                                {
                                    "vol_shock_threshold": 0.95,
                                    "flip_min_confidence": 0.5,
                                }
                            ],
                            "next_adaptation_policy": "hostile_window_defensive",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            completed_rounds, previous = LOOP._resume_state(loop_dir)
            self.assertEqual(completed_rounds, 1)
            self.assertIsNotNone(previous)

            current_budget = str(previous.get("next_budget", "smoke"))
            projected = LOOP.project_seed_trials_to_mode(
                list(previous.get("next_seed_trials") or []),
                mode=LOOP._mode_for_budget(current_budget),
            )

        self.assertEqual(projected[0]["vol_shock_threshold"], 0.9)
        self.assertEqual(projected[0]["flip_min_confidence"], 0.55)


if __name__ == "__main__":
    unittest.main()
