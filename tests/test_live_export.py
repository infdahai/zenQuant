import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.research.live_export import LiveExportConfig, export_live_training_data


class LiveExportTests(unittest.TestCase):
    def test_live_export_writes_artifacts_from_audit_rows(self) -> None:
        sample_rows = [
            {
                "symbol": "BTCUSDT_PERP",
                "timeframe": "4h",
                "cutoff_time_ns": 100,
                "close_price": 100.0,
                "playbook_id": "BULL_CONTINUATION",
                "exit_type": "NONE",
                "risk_flags": "OK",
                "target_reason_codes": "REGIME_TREND|TREND_LONG",
            },
            {
                "symbol": "BTCUSDT_PERP",
                "timeframe": "4h",
                "cutoff_time_ns": 200,
                "close_price": 105.0,
                "playbook_id": "BULL_CONTINUATION",
                "exit_type": "TRAILING",
                "risk_flags": "OK",
                "target_reason_codes": "REGIME_TREND|TREND_LONG",
            },
            {
                "symbol": "BTCUSDT_PERP",
                "timeframe": "4h",
                "cutoff_time_ns": 300,
                "close_price": 110.0,
                "playbook_id": "RANGE_LIGHT",
                "exit_type": "NONE",
                "risk_flags": "OK|VOL_OK",
                "target_reason_codes": "REGIME_RANGE|NO_DIRECTION",
            },
        ]

        class DummyConn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("zenquant.research.live_export.connect", return_value=DummyConn()),
                patch(
                    "zenquant.research.live_export._fetch_base_rows", return_value=[dict(row) for row in sample_rows]
                ),
            ):
                artifact = export_live_training_data(
                    LiveExportConfig(
                        dsn="postgresql://test",
                        output_root=Path(tmp),
                        run_label="paper",
                    )
                )

            self.assertTrue(artifact.manifest_path.exists())
            self.assertTrue(artifact.summary_path.exists())
            self.assertTrue(artifact.dataset_jsonl_path.exists())
            self.assertTrue(artifact.dataset_csv_path.exists())
            self.assertTrue(artifact.report_path.exists())
            self.assertEqual(artifact.row_count, 3)

            lines = artifact.dataset_jsonl_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertIn('"future_return_1": 0.05', lines[0])
            self.assertIn('"mfe_3": 0.1', lines[0])

            report = artifact.report_path.read_text(encoding="utf-8")
            self.assertIn("Research Run", report)
            self.assertIn("playbook_counts", report)


if __name__ == "__main__":
    unittest.main()
