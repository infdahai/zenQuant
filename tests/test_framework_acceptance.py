import importlib.util
import json
import sys
import tempfile
import unittest
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


ACCEPTANCE = _load_module("framework_acceptance_module", "scripts/run_framework_acceptance.py")


class FrameworkAcceptanceScriptTests(unittest.TestCase):
    def test_reports_missing_development_report_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "run_framework_acceptance.py",
                        "--output-dir",
                        str(Path(tmp) / "acceptance"),
                        "--development-report",
                        str(missing),
                    ],
                ),
                self.assertRaises(SystemExit) as exc,
            ):
                ACCEPTANCE.main()

        self.assertEqual(str(exc.exception), f"development_report_not_found:{missing}")

    def test_requires_handoff_ready_development_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "framework_optimization.json"
            report.write_text(
                json.dumps(
                    {
                        "evaluation_role": "development",
                        "candidate_ready_for_handoff": False,
                        "candidate_frozen_id": None,
                        "frozen_params": None,
                        "test_evaluated": False,
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "run_framework_acceptance.py",
                        "--output-dir",
                        str(Path(tmp) / "acceptance"),
                        "--development-report",
                        str(report),
                    ],
                ),
                self.assertRaises(SystemExit) as exc,
            ):
                ACCEPTANCE.main()

        self.assertEqual(str(exc.exception), "development_report_not_ready_for_handoff")

    def test_rejects_legacy_development_report_without_handoff_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "framework_optimization.json"
            report.write_text(
                json.dumps(
                    {
                        "best_variant": "current",
                        "test_evaluated": False,
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "run_framework_acceptance.py",
                        "--output-dir",
                        str(Path(tmp) / "acceptance"),
                        "--development-report",
                        str(report),
                    ],
                ),
                self.assertRaises(SystemExit) as exc,
            ):
                ACCEPTANCE.main()

        self.assertTrue(str(exc.exception).startswith("development_report_not_handoff_capable:missing="))


if __name__ == "__main__":
    unittest.main()
