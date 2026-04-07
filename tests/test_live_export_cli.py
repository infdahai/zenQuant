import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.app.live_export import _parse_horizons, main


class LiveExportCliTests(unittest.TestCase):
    def test_parse_horizons_rejects_empty_values(self) -> None:
        with self.assertRaises(ValueError):
            _parse_horizons("")

    def test_main_builds_config_and_prints_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            captured = {}

            def fake_export(config):
                captured["config"] = config

                class _Artifact:
                    run_dir = Path(tmp) / "paper-live-run"

                return _Artifact()

            with (
                patch("zenquant.app.live_export.export_live_training_data", side_effect=fake_export),
                patch("builtins.print") as mock_print,
            ):
                code = main(
                    [
                        "--dsn",
                        "postgresql://test",
                        "--symbol",
                        "BTCUSDT_PERP",
                        "--timeframe",
                        "4h",
                        "--output-root",
                        tmp,
                        "--horizons",
                        "1,6",
                        "--label",
                        "paper-live",
                        "--start-ns",
                        "100",
                        "--end-ns",
                        "200",
                    ]
                )

        self.assertEqual(code, 0)
        self.assertEqual(captured["config"].dsn, "postgresql://test")
        self.assertEqual(captured["config"].horizons, (1, 6))
        self.assertEqual(captured["config"].run_label, "paper-live")
        self.assertEqual(captured["config"].start_ns, 100)
        self.assertEqual(captured["config"].end_ns, 200)
        mock_print.assert_called_once()


if __name__ == "__main__":
    unittest.main()
