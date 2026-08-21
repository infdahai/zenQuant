import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from zenquant.data.deribit_status import collect_deribit_status, write_deribit_status


def _snapshot_name(ts: datetime) -> str:
    return f"options_chain_{ts.strftime('%Y%m%dT%H%M%SZ')}.parquet"


def _write_snapshot(
    path: Path,
    ts: datetime,
    *,
    float_source: bool = False,
    float_expiry: bool = False,
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    source_ns = int(ts.timestamp() * 1_000_000_000)
    rows = []
    for dte in (7, 30, 90):
        for option_type in ("call", "put"):
            rows.append(
                {
                    "snapshot_ts_ns": float(source_ns) if float_source else source_ns,
                    "expiry_ts_ms": (
                        float(int((ts + timedelta(days=dte)).timestamp() * 1_000))
                        if float_expiry
                        else int((ts + timedelta(days=dte)).timestamp() * 1_000)
                    ),
                    "strike": 100_000.0,
                    "option_type": option_type,
                    "underlying_price": 100_000.0,
                    "open_interest": 100.0,
                    "mark_iv": 60.0,
                }
            )
    pq.write_table(pa.Table.from_pylist(rows), path)


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
                _write_snapshot(target / _snapshot_name(ts), ts)
            status = collect_deribit_status(root)

        self.assertTrue(status.enabled)
        self.assertTrue(status.observation_ready)
        self.assertFalse(status.stale)
        self.assertTrue(status.research_ready)
        self.assertIsNone(status.blocked_by)
        self.assertEqual(status.next_action, "keep_collector_running")
        self.assertGreaterEqual(status.snapshot_count_24h, 5)
        self.assertGreaterEqual(status.snapshot_count_7d, 35)
        self.assertEqual(status.expected_snapshot_count_24h, 6)
        self.assertGreaterEqual(status.snapshot_coverage_ratio_24h, 5 / 6)
        self.assertEqual(status.expected_snapshot_count_7d, 42)
        self.assertGreaterEqual(status.snapshot_coverage_ratio_7d, 35 / 42)
        self.assertEqual(status.invalid_snapshot_count_7d, 0)

    def test_unreadable_or_schema_incomplete_snapshots_do_not_count_as_ready(self) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "BTC" / "2026-04"
            target.mkdir(parents=True, exist_ok=True)
            _write_snapshot(target / _snapshot_name(now), now)
            bad_ts = now - timedelta(hours=4)
            (target / _snapshot_name(bad_ts)).write_text("not parquet", encoding="utf-8")
            status = collect_deribit_status(root)

        self.assertEqual(status.snapshot_count_7d, 1)
        self.assertEqual(status.invalid_snapshot_count_7d, 1)
        self.assertFalse(status.observation_ready)
        self.assertEqual(status.blocked_by, "deribit_snapshot_integrity_incomplete")

    def test_fractional_typed_embedded_timestamp_is_not_a_valid_snapshot(self) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "BTC" / "2026-04"
            target.mkdir(parents=True, exist_ok=True)
            path = target / _snapshot_name(now)
            _write_snapshot(path, now, float_source=True)
            status = collect_deribit_status(root)

        self.assertEqual(status.snapshot_count_7d, 0)
        self.assertEqual(status.invalid_snapshot_count_7d, 1)
        self.assertEqual(status.blocked_by, "deribit_snapshot_integrity_incomplete")

    def test_fractional_typed_expiry_is_not_a_valid_snapshot(self) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "BTC" / "2026-04"
            target.mkdir(parents=True, exist_ok=True)
            path = target / _snapshot_name(now)
            _write_snapshot(path, now, float_expiry=True)
            status = collect_deribit_status(root)

        self.assertEqual(status.snapshot_count_7d, 0)
        self.assertEqual(status.invalid_snapshot_count_7d, 1)
        self.assertEqual(status.blocked_by, "deribit_snapshot_integrity_incomplete")

    def test_write_status_creates_json_artifact(self) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "BTC" / "2026-04"
            target.mkdir(parents=True, exist_ok=True)
            _write_snapshot(target / _snapshot_name(now), now)
            path = write_deribit_status(root)

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(path.name.endswith("_collector_status.json"))
        self.assertIn("last_success_ts", payload)
        self.assertIn("snapshot_count_24h", payload)
        self.assertIn("blocked_by", payload)
        self.assertIn("next_action", payload)


if __name__ == "__main__":
    unittest.main()
