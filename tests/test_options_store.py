"""Unit tests for OptionsDataStore point-in-time lookups."""

import tempfile
import unittest
from pathlib import Path


def _write_parquet(path: Path, records: list[dict]) -> None:
    """Helper to write options chain parquet."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict({k: [r[k] for r in records] for k in records[0]})
    pq.write_table(table, path)


DAY_NS = 86_400 * 1_000_000_000


class OptionsDataStoreTests(unittest.TestCase):
    def test_load_snapshots(self) -> None:
        """Test that OptionsDataStore loads parquet snapshots."""
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            # Create 3 snapshots
            for i in range(1, 4):
                snapshot_ts = i * DAY_NS
                chain = [
                    {
                        "snapshot_ts_ns": snapshot_ts,
                        "currency": "BTC",
                        "instrument_name": f"BTC-{i}D-60000-C",
                        "expiry_ts_ms": (snapshot_ts + 30 * DAY_NS) // 1_000_000,
                        "expiry_date": "2026-04-01",
                        "strike": 60000.0,
                        "option_type": "call",
                        "underlying_price": 68000.0,
                        "open_interest": 100.0,
                        "volume_usd": 1000.0,
                        "mark_iv": 50.0 + i * 5,  # IV increasing
                        "bid_price": 0.1,
                        "ask_price": 0.15,
                        "mid_price": 0.125,
                        "underlying_index": "BTC",
                        "settlement_period": "day",
                    },
                    {
                        "snapshot_ts_ns": snapshot_ts,
                        "currency": "BTC",
                        "instrument_name": f"BTC-{i}D-60000-P",
                        "expiry_ts_ms": (snapshot_ts + 30 * DAY_NS) // 1_000_000,
                        "expiry_date": "2026-04-01",
                        "strike": 60000.0,
                        "option_type": "put",
                        "underlying_price": 68000.0,
                        "open_interest": 120.0,
                        "volume_usd": 1200.0,
                        "mark_iv": 60.0 + i * 5,  # Put IV higher (normal skew)
                        "bid_price": 0.08,
                        "ask_price": 0.12,
                        "mid_price": 0.10,
                        "underlying_index": "BTC",
                        "settlement_period": "day",
                    },
                ]
                month_dir = options_dir / "BTC" / "2026-04"
                _write_parquet(month_dir / f"chain_{i}.parquet", chain)

            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()

            self.assertTrue(store.loaded)
            self.assertEqual(len(store._timestamps), 3)

    def test_lookup_point_in_time(self) -> None:
        """Test that lookup returns latest snapshot before cutoff."""
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            # Create 2 snapshots
            for i in range(1, 3):
                snapshot_ts = i * DAY_NS
                chain = [
                    {
                        "snapshot_ts_ns": snapshot_ts,
                        "currency": "BTC",
                        "instrument_name": f"BTC-{i}D-65000-C",
                        "expiry_ts_ms": (snapshot_ts + 30 * DAY_NS) // 1_000_000,
                        "expiry_date": "2026-04-01",
                        "strike": 65000.0,
                        "option_type": "call",
                        "underlying_price": 68000.0,
                        "open_interest": 100.0,
                        "volume_usd": 1000.0,
                        "mark_iv": 50.0 + i * 10,  # 60 on day 1, 70 on day 2
                        "bid_price": 0.1,
                        "ask_price": 0.15,
                        "mid_price": 0.125,
                        "underlying_index": "BTC",
                        "settlement_period": "day",
                    }
                ]
                month_dir = options_dir / "BTC" / "2026-04"
                _write_parquet(month_dir / f"chain_{i}.parquet", chain)

            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()

            # Lookup at day 1.5 should return day 1 data
            snap = store.lookup(int(1.5 * DAY_NS))
            self.assertIsNotNone(snap.atm_iv_30d)
            self.assertAlmostEqual(snap.atm_iv_30d, 60.0, places=1)

            # Lookup at day 2.5 should return day 2 data
            snap2 = store.lookup(int(2.5 * DAY_NS))
            self.assertIsNotNone(snap2.atm_iv_30d)
            self.assertAlmostEqual(snap2.atm_iv_30d, 70.0, places=1)

    def test_no_lookahead_bias(self) -> None:
        """Test that lookup doesn't leak future data."""
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            # Create 2 snapshots: day 2 and day 4
            for i in [2, 4]:
                snapshot_ts = i * DAY_NS
                chain = [
                    {
                        "snapshot_ts_ns": snapshot_ts,
                        "currency": "BTC",
                        "instrument_name": f"BTC-{i}D-65000-C",
                        "expiry_ts_ms": (snapshot_ts + 30 * DAY_NS) // 1_000_000,
                        "expiry_date": "2026-04-01",
                        "strike": 65000.0,
                        "option_type": "call",
                        "underlying_price": 68000.0,
                        "open_interest": 100.0,
                        "volume_usd": 1000.0,
                        "mark_iv": float(i * 10),  # 20 on day 2, 40 on day 4
                        "bid_price": 0.1,
                        "ask_price": 0.15,
                        "mid_price": 0.125,
                        "underlying_index": "BTC",
                        "settlement_period": "day",
                    }
                ]
                month_dir = options_dir / "BTC" / "2026-04"
                _write_parquet(month_dir / f"chain_{i}.parquet", chain)

            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()

            # Lookup at day 3 should return day 2 data (NOT day 4)
            snap = store.lookup(3 * DAY_NS)
            self.assertIsNotNone(snap.atm_iv_30d)
            self.assertAlmostEqual(snap.atm_iv_30d, 20.0, places=1)  # Day 2, not day 4

    def test_before_any_data_returns_none(self) -> None:
        """Test lookup before any data returns None."""
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            # Create 1 snapshot at day 5
            snapshot_ts = 5 * DAY_NS
            chain = [
                {
                    "snapshot_ts_ns": snapshot_ts,
                    "currency": "BTC",
                    "instrument_name": "BTC-5D-65000-C",
                    "expiry_ts_ms": (snapshot_ts + 30 * DAY_NS) // 1_000_000,
                    "expiry_date": "2026-04-01",
                    "strike": 65000.0,
                    "option_type": "call",
                    "underlying_price": 68000.0,
                    "open_interest": 100.0,
                    "volume_usd": 1000.0,
                    "mark_iv": 50.0,
                    "bid_price": 0.1,
                    "ask_price": 0.15,
                    "mid_price": 0.125,
                    "underlying_index": "BTC",
                    "settlement_period": "day",
                }
            ]
            month_dir = options_dir / "BTC" / "2026-04"
            _write_parquet(month_dir / "chain_5.parquet", chain)

            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()

            # Lookup at day 2 (before day 5 snapshot) should return None
            snap = store.lookup(2 * DAY_NS)
            self.assertIsNone(snap.atm_iv_30d)
            self.assertEqual(snap.atm_iv_z, 0.0)

    def test_empty_store_returns_zero_zscores(self) -> None:
        """Test that empty store returns neutral snapshot."""
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()

            self.assertFalse(store.loaded)

            snap = store.lookup(100 * DAY_NS)
            self.assertIsNone(snap.atm_iv_30d)
            self.assertEqual(snap.atm_iv_z, 0.0)
            self.assertIsNone(snap.put_call_skew_25d)
            self.assertEqual(snap.skew_z, 0.0)
            self.assertEqual(snap.quality_flags, [])


if __name__ == "__main__":
    unittest.main()
