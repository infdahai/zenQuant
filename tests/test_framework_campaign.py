import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CAMPAIGN = _load_module("framework_campaign_module", "scripts/run_framework_campaign.py")


class FrameworkCampaignTests(unittest.TestCase):
    def test_campaign_blocks_when_optuna_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            argv = [
                "run_framework_campaign.py",
                "--output-dir",
                str(out),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch("importlib.util.find_spec", return_value=None),
            ):
                CAMPAIGN.main()

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocked_by"], "optuna_unavailable")

    def test_campaign_resumes_from_stage1_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            checkpoints = out / "checkpoints"
            checkpoints.mkdir(parents=True, exist_ok=True)
            stage1 = checkpoints / "stage1_search.json"
            stage1.write_text(
                json.dumps(
                    {
                        "optuna_params": {"w_momentum": 0.3},
                        "rl_params": {"w_momentum": 0.4},
                        "search_status": {
                            "optuna_trials_completed": 7,
                            "rl_episodes_completed": 5,
                            "budget": "smoke",
                            "optuna_available": True,
                            "train_only_search": True,
                            "dev_only_ranking": True,
                            "split_contract_version": "btc_temporal_v1",
                        },
                        "split_contract": {"version": "btc_temporal_v1"},
                    }
                ),
                encoding="utf-8",
            )

            class FakeEvaluator:
                def __init__(self, *_args, **_kwargs) -> None:
                    self.readiness = {"promotion_ready": False}
                    self.search_param_grid = {"w_momentum": [0.2, 0.3]}

                def final_report(self, **_kwargs):
                    class Report:
                        best_variant = "current"
                        best_score = 1.0
                        report_mode = "full"

                    return Report()

            argv = ["run_framework_campaign.py", "--output-dir", str(out), "--resume"]
            with (
                patch.object(sys, "argv", argv),
                patch("importlib.util.find_spec", return_value=object()),
                patch.object(CAMPAIGN, "FrameworkEvaluator", FakeEvaluator),
                patch.object(CAMPAIGN, "write_search_report") as write_report,
                patch.object(CAMPAIGN, "build_markdown_report", return_value="# ok"),
                patch.object(CAMPAIGN, "_resolved_budget", return_value=(5, 5)),
            ):
                CAMPAIGN.main()

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["optuna_trials_completed"], 7)
        self.assertEqual(payload["rl_episodes_completed"], 5)
        self.assertTrue(payload["resume_supported"])
        write_report.assert_called_once()

    def test_campaign_archives_prior_outputs_on_fresh_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            out.mkdir(parents=True, exist_ok=True)
            (out / "campaign_status.json").write_text('{"status":"completed"}', encoding="utf-8")
            (out / "framework_optimization.json").write_text("{}", encoding="utf-8")
            (out / "framework_optimization.md").write_text("# old", encoding="utf-8")
            checkpoints = out / "checkpoints"
            checkpoints.mkdir()
            (checkpoints / "stage1_search.json").write_text("{}", encoding="utf-8")

            argv = ["run_framework_campaign.py", "--output-dir", str(out)]
            with (
                patch.object(sys, "argv", argv),
                patch("importlib.util.find_spec", return_value=None),
            ):
                CAMPAIGN.main()

            archive_root = out.parent / "archive" / "dev_runs"
            archived_runs = list(archive_root.iterdir())
            self.assertEqual(len(archived_runs), 1)
            archived = archived_runs[0]
            self.assertTrue((archived / "campaign_status.json").exists())
            self.assertTrue((archived / "framework_optimization.json").exists())
            self.assertTrue((archived / "checkpoints" / "stage1_search.json").exists())

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "blocked")

    def test_campaign_invalidates_resume_on_split_contract_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            checkpoints = out / "checkpoints"
            checkpoints.mkdir(parents=True, exist_ok=True)
            (checkpoints / "stage0_readiness.json").write_text(
                json.dumps({"split_contract": {"version": "btc_temporal_v0"}}),
                encoding="utf-8",
            )

            argv = ["run_framework_campaign.py", "--output-dir", str(out), "--resume"]
            with patch.object(sys, "argv", argv):
                CAMPAIGN.main()

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocked_by"], "invalidated_checkpoint")
        self.assertEqual(payload["invalidated_reason"], "split_contract_mismatch")
        self.assertEqual(payload["split_contract_version"], "btc_temporal_v1")
        self.assertFalse((out / "checkpoints" / "stage0_readiness.json").exists())

    def test_campaign_invalidates_resume_when_stage1_is_not_train_dev_disciplined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            checkpoints = out / "checkpoints"
            checkpoints.mkdir(parents=True, exist_ok=True)
            (checkpoints / "stage1_search.json").write_text(
                json.dumps(
                    {
                        "search_status": {
                            "split_contract_version": "btc_temporal_v1",
                            "train_only_search": False,
                            "dev_only_ranking": True,
                        },
                        "split_contract": {"version": "btc_temporal_v1"},
                    }
                ),
                encoding="utf-8",
            )

            argv = ["run_framework_campaign.py", "--output-dir", str(out), "--resume"]
            with patch.object(sys, "argv", argv):
                CAMPAIGN.main()

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocked_by"], "invalidated_checkpoint")
        self.assertEqual(payload["invalidated_reason"], "checkpoint_not_train_only_search")


if __name__ == "__main__":
    unittest.main()
