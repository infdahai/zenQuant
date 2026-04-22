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
from unittest import mock


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
                mock.patch(
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
            command = LOOP._build_campaign_command(
                Args(),
                run_dir,
                budget="smoke",
                seed_trials_file=seed_file,
                resume=True,
                development_dev_gate=True,
                dev_gate_last_round=3,
                dev_gate_interval=3,
            )

        self.assertIn("scripts/run_framework_campaign.py", command)
        self.assertIn("--seed-trials-file", command)
        self.assertIn(str(seed_file), command)
        self.assertIn("--resume", command)
        self.assertIn("--development-dev-gate", command)
        self.assertIn("--dev-gate-last-round", command)
        self.assertIn("--dev-gate-interval", command)

    def test_should_use_dev_gate_on_first_and_fast_rounds(self) -> None:
        self.assertTrue(
            LOOP._should_use_dev_gate(
                round_index=1,
                max_rounds=8,
                current_budget="smoke",
                previous_budget=None,
                interval=3,
            )
        )
        self.assertTrue(
            LOOP._should_use_dev_gate(
                round_index=5,
                max_rounds=8,
                current_budget="fast",
                previous_budget="smoke",
                interval=3,
            )
        )
        self.assertFalse(
            LOOP._should_use_dev_gate(
                round_index=2,
                max_rounds=8,
                current_budget="smoke",
                previous_budget="smoke",
                interval=3,
            )
        )

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
                mock.patch("sys.argv", ["watch_framework_loop.py", "--output-dir", str(loop_dir), "--format", "raw"]),
            ):
                WATCH.main()
            rendered = json.loads(buffer.getvalue())

        self.assertEqual(rendered["loop_status"]["current_round"], 2)

    def test_watch_snapshot_reads_training_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            (loop_dir / "training_monitor.json").write_text(
                json.dumps(
                    {
                        "run_id": "framework_optimization_loop_v7",
                        "loop_state": "campaign_running",
                        "current_round": 3,
                        "current_budget": "fast",
                        "current_phase": "stage1_optuna_search",
                        "current_status": "running",
                        "elapsed_wall_time_s": 120.0,
                        "round_runtime_s": 60.0,
                        "stage1_runtime_s": 55.0,
                        "stage2_runtime_s": 0.0,
                        "optuna_trials_completed": 10,
                        "optuna_trials_total": 50,
                        "rl_steps_completed": 0,
                        "rl_steps_requested": 20,
                        "stage2_progress_completed": 0,
                        "stage2_progress_total": 0,
                        "round_eta_seconds": 240,
                        "heartbeat_freshness_seconds": 5.0,
                        "stalled": False,
                        "stalled_reason": None,
                        "cpu_expected_mode": "parallel_expected",
                        "cpu_utilization_estimate": 180.0,
                        "active_process_count": 3,
                        "cpu_underutilized": False,
                        "cpu_underutilized_reason": None,
                        "best_train_score_so_far": 1.2,
                        "best_dev_score_so_far": 0.8,
                        "candidate_improvement_trend": "improving",
                        "best_candidate_variant_so_far": "optuna_tuned",
                        "dev_gate_pass_count": 1,
                        "dev_gate_fail_count": 2,
                        "candidate_ready_for_handoff": False,
                        "alerts": ["repeated_prevalidation_rejection"],
                        "recent_rounds": [{"round": 2, "budget": "fast", "train_score": 1.0, "dev_score": 0.7, "promotion_health": "flat", "best_variant": "current"}],
                    }
                ),
                encoding="utf-8",
            )
            rendered = WATCH._render_snapshot(loop_dir)

        self.assertIn("state: campaign_running round=3 budget=fast phase=stage1_optuna_search status=running", rendered)
        self.assertIn("cpu: mode=parallel_expected util=180.0", rendered)
        self.assertIn("alerts: ['repeated_prevalidation_rejection']", rendered)

    def test_watch_history_reads_training_history_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            (loop_dir / "training_history.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"round": 1, "budget": "smoke", "train_score": 0.2, "dev_score": 0.1, "promotion_health": "flat", "candidate_ready_for_handoff": False, "full_training_blockers": ["x"]}),
                        json.dumps({"round": 2, "budget": "fast", "train_score": 0.8, "dev_score": 0.6, "promotion_health": "improving", "candidate_ready_for_handoff": False, "full_training_blockers": ["y"]}),
                    ]
                ),
                encoding="utf-8",
            )
            rendered = WATCH._render_history(loop_dir)

        self.assertIn("round=1 budget=smoke", rendered)
        self.assertIn("round=2 budget=fast", rendered)

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
                mock.patch.object(LOOP.subprocess, "Popen", return_value=FakeProcess()),
                mock.patch.object(LOOP, "_write_training_monitor", return_value={}),
                self.assertRaisesRegex(RuntimeError, "campaign_failed_before_final_report"),
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
                    development_dev_gate=True,
                    dev_gate_last_round=None,
                    dev_gate_interval=3,
                )

            loop_status = json.loads(loop_status_path.read_text(encoding="utf-8"))

        self.assertEqual(loop_status["loop_state"], "failed")
        self.assertEqual(loop_status["stop_reason"], "campaign_failed_before_final_report:1")

    def test_launch_round_short_circuits_on_authoritative_failed_status(self) -> None:
        class FakeProcess:
            pid = 54321

            def __init__(self) -> None:
                self._terminated = False

            def poll(self):
                return 1 if self._terminated else None

            def wait(self, timeout=None):
                del timeout
                self._terminated = True
                return 1

            def terminate(self):
                self._terminated = True

            def kill(self):
                self._terminated = True

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
                        "failure_reason": "stage1_optuna_timeout: trial 1 exceeded 240s",
                        "blocked_by": "stage1_optuna_timeout",
                        "full_training_blockers": ["smoke_campaign_incomplete"],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(LOOP.subprocess, "Popen", return_value=FakeProcess()),
                mock.patch.object(LOOP, "_write_training_monitor", return_value={}),
                self.assertRaisesRegex(RuntimeError, "stage1_optuna_timeout"),
            ):
                LOOP._launch_round(
                    args,
                    run_dir=run_dir,
                    budget="smoke",
                    seed_trials=[],
                    loop_status_path=loop_status_path,
                    loop_log_path=loop_log_path,
                    round_index=1,
                    max_rounds=8,
                    adaptation_policy="initial_baseline_anchored",
                    resume=False,
                    development_dev_gate=True,
                    dev_gate_last_round=None,
                    dev_gate_interval=3,
                )

            loop_status = json.loads(loop_status_path.read_text(encoding="utf-8"))
            loop_log = loop_log_path.read_text(encoding="utf-8")

        self.assertEqual(loop_status["loop_state"], "failed")
        self.assertEqual(loop_status["stop_reason"], "stage1_optuna_timeout: trial 1 exceeded 240s:1")
        self.assertIn("round_failed round=1 budget=smoke exit_code=1", loop_log)

    def test_launch_round_classifies_bootstrap_failure_before_status(self) -> None:
        class FakeProcess:
            pid = 65432

            def poll(self):
                return 1

            def wait(self, timeout=None):
                del timeout
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

            with (
                mock.patch.object(LOOP.subprocess, "Popen", return_value=FakeProcess()),
                mock.patch.object(LOOP, "_write_training_monitor", return_value={}),
                self.assertRaisesRegex(RuntimeError, "campaign_bootstrap_failed_before_status"),
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
                    development_dev_gate=True,
                    dev_gate_last_round=None,
                    dev_gate_interval=3,
                )

            loop_status = json.loads(loop_status_path.read_text(encoding="utf-8"))
            launcher_log_exists = (run_dir / "campaign_launcher.log").exists()
            campaign_status_exists = (run_dir / "campaign_status.json").exists()

        self.assertEqual(loop_status["loop_state"], "failed")
        self.assertEqual(loop_status["stop_reason"], "campaign_bootstrap_failed_before_status:1")
        self.assertFalse(campaign_status_exists)
        self.assertTrue(launcher_log_exists)

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
                    development_dev_gate=True,
                    dev_gate_last_round=None,
                    dev_gate_interval=3,
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

    def test_read_seed_trials_rejects_non_list_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed_file = Path(tmp) / "seed_trials.json"
            seed_file.write_text(json.dumps({"bad": True}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must contain a JSON list"):
                LOOP._read_seed_trials(seed_file)

    def test_main_rejects_resume_with_initial_seed_trials_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp) / "framework_optimization_loop"
            seed_file = Path(tmp) / "seed_trials.json"
            seed_file.write_text("[]", encoding="utf-8")

            with (
                mock.patch(
                    "sys.argv",
                    [
                        "run_framework_research_loop.py",
                        "--output-dir",
                        str(loop_dir),
                        "--resume",
                        "--initial-seed-trials-file",
                        str(seed_file),
                    ],
                ),
                self.assertRaises(SystemExit) as exc,
            ):
                LOOP.main()

        self.assertEqual(
            str(exc.exception),
            "--resume, --initial-seed-trials-file, and --initial-candidate-checkpoint-file are mutually exclusive",
        )

    def test_main_rejects_resume_with_initial_candidate_checkpoint_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp) / "framework_optimization_loop"
            checkpoint_file = Path(tmp) / "candidate_checkpoint.json"
            checkpoint_file.write_text("{}", encoding="utf-8")

            with (
                mock.patch(
                    "sys.argv",
                    [
                        "run_framework_research_loop.py",
                        "--output-dir",
                        str(loop_dir),
                        "--resume",
                        "--initial-candidate-checkpoint-file",
                        str(checkpoint_file),
                    ],
                ),
                self.assertRaises(SystemExit) as exc,
            ):
                LOOP.main()

        self.assertEqual(
            str(exc.exception),
            "--resume, --initial-seed-trials-file, and --initial-candidate-checkpoint-file are mutually exclusive",
        )

    def test_main_uses_initial_seed_trials_for_first_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp) / "framework_optimization_loop"
            campaign_root = loop_dir / "runs"
            seed_file = Path(tmp) / "seed_trials.json"
            seed_payload = [
                {
                    "vol_shock_threshold": 0.95,
                    "flip_min_confidence": 0.5,
                }
            ]
            seed_file.write_text(json.dumps(seed_payload), encoding="utf-8")
            captured: dict[str, object] = {}

            def fake_launch_round(
                args,
                *,
                run_dir,
                budget,
                seed_trials,
                loop_status_path,
                loop_log_path,
                round_index,
                max_rounds,
                adaptation_policy,
                resume,
                development_dev_gate,
                dev_gate_last_round,
                dev_gate_interval,
            ):
                captured["seed_trials"] = seed_trials
                captured["round_index"] = round_index
                return (
                    {
                        "post_smoke_scenario": {
                            "scenario": "scenario_development_train_iteration",
                            "category": "train_iteration",
                            "should_open_full_training": False,
                            "next_action": "continue_train_search_until_dev_gate",
                            "blockers": ["x"],
                        },
                        "full_training_assessment": {"allowed": False, "blockers": ["x"], "warnings": []},
                        "best_variant": "current",
                        "best_score": 0.0,
                        "train_candidate_diagnostics": {"max_train_sharpe": 0.0},
                        "candidate_ready_for_handoff": False,
                        "candidate_frozen_id": None,
                        "dev_gate_used_this_round": False,
                    },
                    {"search_status": {"stage1_best_score": 0.0}},
                    {"phase": "stage3_complete", "status": "completed", "campaign_pid": 123},
                )

            def fake_plan_next_iteration(**kwargs):
                return Namespace(
                    next_budget="fast",
                    adaptation_policy="steady_state",
                    all_seed_trials=[],
                    loop_progression_blocked=False,
                    should_open_fast_or_full_training=False,
                    stop=True,
                    stop_reason="done",
                )

            with (
                mock.patch(
                    "sys.argv",
                    [
                        "run_framework_research_loop.py",
                        "--output-dir",
                        str(loop_dir),
                        "--campaign-output-root",
                        str(campaign_root),
                        "--initial-seed-trials-file",
                        str(seed_file),
                    ],
                ),
                mock.patch.object(LOOP, "_launch_round", side_effect=fake_launch_round),
                mock.patch.object(LOOP, "plan_next_iteration", side_effect=fake_plan_next_iteration),
                mock.patch.object(LOOP, "_write_training_monitor", return_value={}),
            ):
                LOOP.main()

        self.assertEqual(captured["round_index"], 1)
        self.assertEqual(captured["seed_trials"], seed_payload)

    def test_main_merges_external_candidate_checkpoint_into_first_round_seeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp) / "framework_optimization_loop"
            campaign_root = loop_dir / "runs"
            seed_file = Path(tmp) / "seed_trials.json"
            checkpoint_file = Path(tmp) / "candidate_checkpoint.json"
            seed_file.write_text(
                json.dumps(
                    [
                        {
                            "vol_shock_threshold": 0.95,
                            "flip_min_confidence": 0.5,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            checkpoint_file.write_text(
                json.dumps(
                    {
                        "round": 2,
                        "checkpoint_status": "usable",
                        "split_contract_version": "btc_temporal_v2_train_selection_only",
                        "search_space_mode": "baseline_anchored_train_only",
                        "train_only_search": True,
                        "candidate_variant": "optuna_tuned",
                        "params": {"w_momentum": 0.21, "flip_min_confidence": 0.61},
                        "selected_checkpoint_candidate": {"score": 1.7},
                    }
                ),
                encoding="utf-8",
            )
            captured: dict[str, object] = {}

            def fake_launch_round(
                args,
                *,
                run_dir,
                budget,
                seed_trials,
                loop_status_path,
                loop_log_path,
                round_index,
                max_rounds,
                adaptation_policy,
                resume,
                development_dev_gate,
                dev_gate_last_round,
                dev_gate_interval,
            ):
                del args, run_dir, budget, loop_status_path, loop_log_path, max_rounds, adaptation_policy, resume, development_dev_gate, dev_gate_last_round, dev_gate_interval
                captured["round_index"] = round_index
                captured["seed_trials"] = seed_trials
                return (
                    {
                        "post_smoke_scenario": {
                            "scenario": "scenario_development_train_iteration",
                            "category": "train_iteration",
                            "should_open_full_training": False,
                            "next_action": "continue_train_search_until_dev_gate",
                            "blockers": ["x"],
                        },
                        "full_training_assessment": {"allowed": False, "blockers": ["x"], "warnings": []},
                        "best_variant": "current",
                        "best_score": 0.0,
                        "train_candidate_diagnostics": {"max_train_sharpe": 0.0},
                        "candidate_ready_for_handoff": False,
                        "candidate_frozen_id": None,
                        "dev_gate_used_this_round": False,
                    },
                    {"search_status": {"stage1_best_score": 0.0}},
                    {"phase": "stage3_complete", "status": "completed", "campaign_pid": 123},
                )

            def fake_plan_next_iteration(**kwargs):
                return Namespace(
                    next_budget="fast",
                    adaptation_policy="steady_state",
                    all_seed_trials=[],
                    loop_progression_blocked=False,
                    should_open_fast_or_full_training=False,
                    stop=True,
                    stop_reason="done",
                )

            with (
                mock.patch(
                    "sys.argv",
                    [
                        "run_framework_research_loop.py",
                        "--output-dir",
                        str(loop_dir),
                        "--campaign-output-root",
                        str(campaign_root),
                        "--initial-seed-trials-file",
                        str(seed_file),
                        "--initial-candidate-checkpoint-file",
                        str(checkpoint_file),
                    ],
                ),
                mock.patch.object(LOOP, "_launch_round", side_effect=fake_launch_round),
                mock.patch.object(LOOP, "plan_next_iteration", side_effect=fake_plan_next_iteration),
                mock.patch.object(LOOP, "_write_training_monitor", return_value={}),
            ):
                LOOP.main()

        self.assertEqual(captured["round_index"], 1)
        self.assertEqual(captured["seed_trials"][0]["w_momentum"], 0.21)
        self.assertEqual(captured["seed_trials"][0]["flip_min_confidence"], 0.6)
        self.assertEqual(captured["seed_trials"][1]["vol_shock_threshold"], 0.95)

    def test_resume_prefers_candidate_checkpoint_for_seed_warm_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp) / "framework_optimization_loop"
            campaign_root = loop_dir / "runs"
            previous_run_dir = campaign_root / "round_004"
            (previous_run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
            (loop_dir / "loop_history.json").write_text(
                json.dumps(
                    [
                        {
                            "round": 4,
                            "budget": "smoke",
                            "run_dir": str(previous_run_dir),
                            "post_smoke_scenario": "scenario_development_train_iteration",
                            "full_training_blockers": [],
                            "adaptation_policy": "hostile_window_defensive",
                            "next_budget": "fast",
                            "next_seed_trials": [{"vol_shock_threshold": 0.95, "flip_min_confidence": 0.5}],
                            "next_adaptation_policy": "hostile_window_defensive",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (previous_run_dir / "checkpoints" / "candidate_checkpoint.json").write_text(
                json.dumps(
                    {
                        "round": 4,
                        "checkpoint_status": "usable",
                        "split_contract_version": "btc_temporal_v2_train_selection_only",
                        "search_space_mode": "execution_first_v2_correlation_constrained",
                        "train_only_search": True,
                        "candidate_variant": "optuna_tuned",
                        "params": {"w_momentum": 0.21, "flip_min_confidence": 0.61},
                        "selected_checkpoint_candidate": {"score": 1.7},
                    }
                ),
                encoding="utf-8",
            )
            captured: dict[str, object] = {}

            def fake_launch_round(
                args,
                *,
                run_dir,
                budget,
                seed_trials,
                loop_status_path,
                loop_log_path,
                round_index,
                max_rounds,
                adaptation_policy,
                resume,
                development_dev_gate,
                dev_gate_last_round,
                dev_gate_interval,
            ):
                del args, run_dir, budget, loop_status_path, loop_log_path, round_index, max_rounds, adaptation_policy, resume, development_dev_gate, dev_gate_last_round, dev_gate_interval
                captured["seed_trials"] = seed_trials
                return (
                    {
                        "post_smoke_scenario": {"scenario": "done", "category": "train_iteration", "should_open_full_training": False, "next_action": "stop", "blockers": []},
                        "full_training_assessment": {"allowed": False, "blockers": [], "warnings": []},
                        "best_variant": "current",
                        "best_score": 0.0,
                        "train_candidate_diagnostics": {"max_train_sharpe": 0.0},
                        "candidate_ready_for_handoff": False,
                        "candidate_frozen_id": None,
                        "dev_gate_used_this_round": False,
                    },
                    {"search_status": {"stage1_best_score": 0.0}},
                    {"phase": "stage3_complete", "status": "completed", "campaign_pid": 123},
                )

            def fake_plan_next_iteration(**kwargs):
                return Namespace(
                    next_budget="fast",
                    adaptation_policy="steady_state",
                    all_seed_trials=[],
                    loop_progression_blocked=False,
                    should_open_fast_or_full_training=False,
                    stop=True,
                    stop_reason="done",
                )

            with (
                mock.patch("sys.argv", ["run_framework_research_loop.py", "--output-dir", str(loop_dir), "--campaign-output-root", str(campaign_root), "--resume"]),
                mock.patch.object(LOOP, "_launch_round", side_effect=fake_launch_round),
                mock.patch.object(LOOP, "plan_next_iteration", side_effect=fake_plan_next_iteration),
                mock.patch.object(LOOP, "_write_training_monitor", return_value={}),
            ):
                LOOP.main()

        self.assertEqual(captured["seed_trials"][0]["w_momentum"], 0.21)
        self.assertEqual(captured["seed_trials"][0]["flip_min_confidence"], 0.65)
        self.assertEqual(captured["seed_trials"][1]["vol_shock_threshold"], 0.9)

    def test_write_handoff_candidate_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            history_item = {
                "round": 6,
                "budget": "fast",
                "run_dir": str(loop_dir / "runs" / "round_006"),
                "candidate_ready_for_handoff": True,
                "candidate_frozen_id": "abc123def456",
            }

            LOOP._write_handoff_candidate(loop_dir, history_item)
            payload = json.loads((loop_dir / "handoff_candidate.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["round"], 6)
        self.assertTrue(payload["candidate_ready_for_handoff"])
        self.assertEqual(payload["candidate_frozen_id"], "abc123def456")
        self.assertTrue(payload["development_report_path"].endswith("/framework_optimization.json"))

    def test_write_training_monitor_aggregates_recent_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            run_dir = loop_dir / "runs" / "round_002"
            run_dir.mkdir(parents=True, exist_ok=True)
            (loop_dir / "loop.lock").write_text(json.dumps({"started_at": "2026-04-12T00:00:00+00:00"}), encoding="utf-8")
            (loop_dir / "loop_status.json").write_text(
                json.dumps(
                    {
                        "loop_state": "campaign_running",
                        "current_round": 2,
                        "current_budget": "fast",
                        "current_phase": "stage2_full_verdict",
                        "current_status": "running",
                        "current_run_dir": str(run_dir),
                        "current_campaign_pid": 222,
                        "loop_pid": 111,
                        "round_runtime_s": 120.0,
                        "stage1_runtime_s": 60.0,
                        "stage2_runtime_s": 40.0,
                        "updated_at": "2026-04-12T00:02:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (loop_dir / "loop_history.json").write_text(
                json.dumps(
                    [
                        {
                            "round": 1,
                            "budget": "smoke",
                            "train_score": 0.5,
                            "train_sharpe": 0.4,
                            "dev_score": 0.3,
                            "dev_drawdown": 0.1,
                            "negative_dev_worst_windows": 1,
                            "best_variant": "current",
                            "candidate_ready_for_handoff": False,
                            "dev_gate_used_this_round": True,
                            "promotion_health": "flat",
                            "prevalidation_rejection_count": 1,
                            "validation_timeout_count": 0,
                            "round_runtime_s": 300.0,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (run_dir / "campaign_status.json").write_text(
                json.dumps(
                    {
                        "phase": "stage2_full_verdict",
                        "status": "running",
                        "optuna_trials_completed": 10,
                        "stage1_optuna_trials_total": 50,
                        "stage1_progress_total": 50,
                        "candidate_checkpoint_written": True,
                        "candidate_checkpoint_status": "usable",
                        "candidate_checkpoint_variant": "optuna_tuned",
                        "candidate_checkpoint_score": 1.3,
                        "candidate_checkpoint_origin_phase": "stage1",
                        "stage1_budget_mode": "wall_clock_primary",
                        "stage1_stop_reason": "wall_clock_budget_reached",
                        "stage1_avg_indicator_step_s": 0.4,
                        "stage1_avg_strategy_step_s": 0.5,
                        "stage1_avg_risk_step_s": 0.2,
                        "stage1_avg_execution_step_s": 0.6,
                        "stage1_avg_accounting_step_s": 0.1,
                        "stage2_subphase": "stage2_prepare_variants",
                        "stage2_progress_completed": 2,
                        "stage2_progress_total": 16,
                        "updated_at": "2026-04-12T00:02:05+00:00",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(LOOP, "_process_metrics", return_value=(140.0, 3)):
                payload = LOOP._write_training_monitor(loop_dir)

        self.assertEqual(payload["current_round"], 2)
        self.assertEqual(payload["cpu_expected_mode"], "parallel_expected")
        self.assertEqual(payload["optuna_trials_total"], 50)
        self.assertEqual(payload["best_train_score_so_far"], 0.5)
        self.assertEqual(payload["prevalidation_rejection_rate"], 1.0)
        self.assertEqual(payload["recent_rounds"][0]["round"], 1)
        self.assertTrue(payload["candidate_checkpoint_written"])
        self.assertEqual(payload["candidate_checkpoint_status"], "usable")
        self.assertEqual(payload["candidate_checkpoint_variant"], "optuna_tuned")
        self.assertEqual(payload["stage1_budget_mode"], "wall_clock_primary")
        self.assertEqual(payload["stage1_stop_reason"], "wall_clock_budget_reached")
        self.assertEqual(payload["stage1_avg_indicator_step_s"], 0.4)
        self.assertEqual(payload["stage1_avg_execution_step_s"], 0.6)

    def test_write_training_monitor_marks_compare_windows_parallel_expected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            run_dir = loop_dir / "runs" / "round_005"
            run_dir.mkdir(parents=True, exist_ok=True)
            (loop_dir / "loop.lock").write_text(json.dumps({"started_at": "2026-04-12T00:00:00+00:00"}), encoding="utf-8")
            (loop_dir / "loop_status.json").write_text(
                json.dumps(
                    {
                        "loop_state": "campaign_running",
                        "current_round": 5,
                        "current_budget": "fast",
                        "current_phase": "stage2_full_verdict",
                        "current_status": "running",
                        "current_run_dir": str(run_dir),
                        "current_campaign_pid": 222,
                        "loop_pid": 111,
                        "round_runtime_s": 120.0,
                        "stage1_runtime_s": 60.0,
                        "stage2_runtime_s": 40.0,
                        "updated_at": "2026-04-12T00:02:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "campaign_status.json").write_text(
                json.dumps(
                    {
                        "phase": "stage2_full_verdict",
                        "status": "running",
                        "optuna_trials_completed": 50,
                        "stage1_optuna_trials_total": 50,
                        "stage2_subphase": "stage2_compare_windows",
                        "stage2_progress_completed": 8,
                        "stage2_progress_total": 24,
                        "stage2_compare_window_units_completed": 8,
                        "stage2_compare_window_units_total": 24,
                        "updated_at": "2026-04-12T00:02:05+00:00",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(LOOP, "_process_metrics", return_value=(160.0, 3)):
                payload = LOOP._write_training_monitor(loop_dir)

        self.assertEqual(payload["cpu_expected_mode"], "parallel_expected")
        self.assertEqual(payload["compare_window_units_completed"], 8)
        self.assertEqual(payload["compare_window_units_total"], 24)

    def test_write_training_monitor_tracks_compare_validation_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            run_dir = loop_dir / "runs" / "round_005"
            run_dir.mkdir(parents=True, exist_ok=True)
            (loop_dir / "loop.lock").write_text(json.dumps({"started_at": "2026-04-12T00:00:00+00:00"}), encoding="utf-8")
            (loop_dir / "loop_status.json").write_text(
                json.dumps(
                    {
                        "loop_state": "campaign_running",
                        "current_round": 5,
                        "current_budget": "fast",
                        "current_phase": "stage2_full_verdict",
                        "current_status": "running",
                        "current_run_dir": str(run_dir),
                        "current_campaign_pid": 222,
                        "loop_pid": 111,
                        "round_runtime_s": 120.0,
                        "stage1_runtime_s": 60.0,
                        "stage2_runtime_s": 40.0,
                        "updated_at": "2026-04-12T00:02:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "campaign_status.json").write_text(
                json.dumps(
                    {
                        "phase": "stage2_full_verdict",
                        "status": "running",
                        "optuna_trials_completed": 50,
                        "stage1_optuna_trials_total": 50,
                        "stage2_subphase": "stage2_compare_validation",
                        "stage2_progress_completed": 14,
                        "stage2_progress_total": 24,
                        "stage2_compare_window_units_completed": 12,
                        "stage2_compare_window_units_total": 12,
                        "stage2_compare_validation_units_completed": 2,
                        "stage2_compare_validation_units_total": 8,
                        "stage2_compare_workers": 2,
                        "stage2_compare_validation_workers": 2,
                        "updated_at": "2026-04-12T00:02:05+00:00",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(LOOP, "_process_metrics", return_value=(160.0, 3)):
                payload = LOOP._write_training_monitor(loop_dir)

        self.assertEqual(payload["cpu_expected_mode"], "parallel_expected")
        self.assertEqual(payload["compare_validation_units_completed"], 2)
        self.assertEqual(payload["compare_validation_units_total"], 8)
        self.assertEqual(payload["stage2_compare_workers"], 2)
        self.assertEqual(payload["stage2_compare_validation_workers"], 2)

    def test_write_training_monitor_includes_stage1_scheduler_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            run_dir = loop_dir / "runs" / "round_005"
            run_dir.mkdir(parents=True, exist_ok=True)
            (loop_dir / "loop.lock").write_text(json.dumps({"started_at": "2026-04-12T00:00:00+00:00"}), encoding="utf-8")
            (loop_dir / "loop_status.json").write_text(
                json.dumps(
                    {
                        "loop_state": "campaign_running",
                        "current_round": 5,
                        "current_budget": "fast",
                        "current_phase": "stage1_optuna_search",
                        "current_status": "running",
                        "current_run_dir": str(run_dir),
                        "current_campaign_pid": 222,
                        "loop_pid": 111,
                        "round_runtime_s": 120.0,
                        "stage1_runtime_s": 90.0,
                        "updated_at": "2026-04-12T00:02:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "campaign_status.json").write_text(
                json.dumps(
                    {
                        "phase": "stage1_optuna_search",
                        "status": "running",
                        "optuna_trials_completed": 32,
                        "stage1_optuna_trials_total": 50,
                        "stage1_progress_completed": 32,
                        "stage1_progress_total": 50,
                        "stage1_pending_trials": 4,
                        "stage1_max_workers": 6,
                        "stage1_target_inflight": 6,
                        "stage1_active_workers_estimate": 4,
                        "stage1_scheduler_mode": "work_conserving",
                        "stage1_underfilled": True,
                        "stage1_underfilled_reason": "work_available_below_target_inflight",
                        "stage1_underfilled_started_at": "2026-04-12T00:02:09+00:00",
                        "stage1_underfilled_duration_s": 0.0,
                        "updated_at": "2026-04-12T00:02:05+00:00",
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(LOOP, "_process_metrics", return_value=(90.0, 3)),
                mock.patch.object(LOOP, "_ts", return_value="2026-04-12T00:02:10+00:00"),
            ):
                payload = LOOP._write_training_monitor(loop_dir)

        self.assertEqual(payload["stage1_pending_trials"], 4)
        self.assertEqual(payload["stage1_max_workers"], 6)
        self.assertEqual(payload["stage1_target_inflight"], 6)
        self.assertEqual(payload["stage1_scheduler_mode"], "work_conserving")
        self.assertFalse(payload["stage1_underfilled"])
        self.assertTrue(payload["stage1_underfilled_candidate"])
        self.assertFalse(payload["cpu_underutilized"])
        self.assertIsNone(payload["cpu_underutilized_reason"])
        self.assertEqual(payload["stage1_underfilled_duration_s"], 1.0)

    def test_write_training_monitor_marks_stage1_parallelism_not_materializing_after_sustained_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop_dir = Path(tmp)
            run_dir = loop_dir / "runs" / "round_005"
            run_dir.mkdir(parents=True, exist_ok=True)
            (loop_dir / "loop.lock").write_text(json.dumps({"started_at": "2026-04-12T00:00:00+00:00"}), encoding="utf-8")
            (loop_dir / "loop_status.json").write_text(
                json.dumps(
                    {
                        "loop_state": "campaign_running",
                        "current_round": 5,
                        "current_budget": "fast",
                        "current_phase": "stage1_optuna_search",
                        "current_status": "running",
                        "current_run_dir": str(run_dir),
                        "current_campaign_pid": 222,
                        "loop_pid": 111,
                        "round_runtime_s": 120.0,
                        "stage1_runtime_s": 90.0,
                        "updated_at": "2026-04-12T00:02:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "campaign_status.json").write_text(
                json.dumps(
                    {
                        "phase": "stage1_optuna_search",
                        "status": "running",
                        "optuna_trials_completed": 32,
                        "stage1_optuna_trials_total": 50,
                        "stage1_progress_completed": 32,
                        "stage1_progress_total": 50,
                        "stage1_pending_trials": 4,
                        "stage1_max_workers": 6,
                        "stage1_target_inflight": 6,
                        "stage1_active_workers_estimate": 4,
                        "stage1_scheduler_mode": "work_conserving",
                        "stage1_underfilled": True,
                        "stage1_underfilled_reason": "work_available_below_target_inflight",
                        "stage1_underfilled_started_at": "2026-04-12T00:01:50+00:00",
                        "stage1_underfilled_duration_s": 0.0,
                        "updated_at": "2026-04-12T00:02:05+00:00",
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(LOOP, "_process_metrics", return_value=(90.0, 3)),
                mock.patch.object(LOOP, "_ts", return_value="2026-04-12T00:02:10+00:00"),
            ):
                payload = LOOP._write_training_monitor(loop_dir)

        self.assertTrue(payload["cpu_underutilized"])
        self.assertEqual(payload["cpu_underutilized_reason"], "stage1_parallelism_not_materializing")
        self.assertEqual(payload["stage1_underfilled_duration_s"], 20.0)

    def test_launch_round_logs_runtime_progress_fields(self) -> None:
        class FakeProcess:
            pid = 54321

            def __init__(self) -> None:
                self._polls = 0

            def poll(self):
                self._polls += 1
                return 0 if self._polls > 1 else None

            def wait(self):
                return 0

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
                        "phase": "stage2_full_verdict",
                        "status": "running",
                        "stage1_runtime_s": 12.5,
                        "stage1_last_completed_unit": "success",
                        "stage2_subphase": "stage2_prepare_variants",
                        "stage2_progress_completed": 2,
                        "stage2_progress_total": 4,
                        "stage2_last_completed_unit": "current",
                        "full_training_blockers": [],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "framework_optimization.json").write_text(json.dumps({"best_variant": "current"}), encoding="utf-8")
            (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
            (run_dir / "checkpoints" / "stage1_search.json").write_text(json.dumps({}), encoding="utf-8")

            with (
                mock.patch.object(LOOP.subprocess, "Popen", return_value=FakeProcess()),
                mock.patch.object(LOOP, "_write_training_monitor", return_value={}),
            ):
                LOOP._launch_round(
                    args,
                    run_dir=run_dir,
                    budget="smoke",
                    seed_trials=[],
                    loop_status_path=loop_status_path,
                    loop_log_path=loop_log_path,
                    round_index=1,
                    max_rounds=8,
                    adaptation_policy="initial",
                    resume=False,
                    development_dev_gate=True,
                    dev_gate_last_round=None,
                    dev_gate_interval=3,
                )

            log_text = loop_log_path.read_text(encoding="utf-8")
            loop_status = json.loads(loop_status_path.read_text(encoding="utf-8"))

        self.assertIn("round_runtime_s=", log_text)
        self.assertIn("stage1_runtime_s=12.5", log_text)
        self.assertIn("stage2_runtime_s=", log_text)
        self.assertIn("round_runtime_s", loop_status)
        self.assertIn("stage2_runtime_s", loop_status)


if __name__ == "__main__":
    unittest.main()
