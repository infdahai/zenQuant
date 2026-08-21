import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from zenquant.data.deribit_status import collect_deribit_status, write_deribit_status


def _snapshot_name(ts: datetime) -> str:
    return f"options_chain_{ts.strftime('%Y%m%dT%H%M%SZ')}.parquet"


class DeribitCollectorStatusTests(unittest.TestCase):
    def test_reports_missing_snapshots_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = collect_deribit_status(Path(tmp))

        self.assertFalse(status.enabled)
        self.assertTrue(status.stale)
        self.assertEqual(status.last_error, "no_deribit_snapshots_found")
        self.assertEqual(status.blocked_by, "no_deribit_snapshots_found")
        self.assertEqual(status.next_action, "start_collector")

    def test_reports_observation_ready_when_recent_snapshots_are_dense(self) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "BTC" / "2026-04"
            target.mkdir(parents=True, exist_ok=True)
            for index in range(42):
                ts = now - timedelta(hours=index * 4)
                (target / _snapshot_name(ts)).write_text("ok", encoding="utf-8")
            status = collect_deribit_status(root)

        self.assertTrue(status.enabled)
        self.assertTrue(status.observation_ready)
        self.assertFalse(status.stale)
        self.assertEqual(status.blocked_by, "deribit_sample_window_incomplete")
        self.assertEqual(status.next_action, "keep_collector_running")
        self.assertGreaterEqual(status.snapshot_count_24h, 5)
        self.assertGreaterEqual(status.snapshot_count_7d, 35)
        self.assertEqual(status.expected_snapshot_count_24h, 6)
        self.assertGreaterEqual(status.snapshot_coverage_ratio_24h, 5 / 6)
        self.assertEqual(status.expected_snapshot_count_7d, 42)
        self.assertGreaterEqual(status.snapshot_coverage_ratio_7d, 35 / 42)

    def test_write_status_creates_json_artifact(self) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "BTC" / "2026-04"
            target.mkdir(parents=True, exist_ok=True)
            (target / _snapshot_name(now)).write_text("ok", encoding="utf-8")
            path = write_deribit_status(root)

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(path.name.endswith("_collector_status.json"))
        self.assertIn("last_success_ts", payload)
        self.assertIn("snapshot_count_24h", payload)
        self.assertIn("blocked_by", payload)
        self.assertIn("next_action", payload)


if __name__ == "__main__":
    unittest.main()
