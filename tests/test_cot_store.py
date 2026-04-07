"""Unit tests for CotDataStore point-in-time lookups."""

import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

WEEK_NS = 7 * 24 * 3600 * 1_000_000_000


def _write_cot_parquet(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict({k: [r[k] for r in records] for k in records[0]})
    pq.write_table(table, path)


def _make_cot_row(
    week: int,
    asset_mgr_net: int = 1000,
    leveraged_net: int = -500,
    dealer_net: int = 200,
    total_oi: int = 50000,
) -> dict:
    return {
        "report_date_ns": week * WEEK_NS,
        "market_name": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
        "asset_mgr_long": max(0, asset_mgr_net),
        "asset_mgr_short": max(0, -asset_mgr_net),
        "asset_mgr_net": asset_mgr_net,
        "leveraged_long": max(0, leveraged_net),
        "leveraged_short": max(0, -leveraged_net),
        "leveraged_net": leveraged_net,
        "dealer_long": max(0, dealer_net),
        "dealer_short": max(0, -dealer_net),
        "dealer_net": dealer_net,
        "total_oi": total_oi,
    }


class CotStoreTests(unittest.TestCase):
    def test_empty_store(self) -> None:
        from zenquant.data import CotDataStore, CotStoreConfig

        store = CotDataStore(CotStoreConfig(cot_path=Path("/nonexistent")))
        store.load()
        self.assertFalse(store.loaded)
        snap = store.lookup(WEEK_NS * 10)
        self.assertEqual(snap.asset_mgr_net, 0)

    def test_point_in_time(self) -> None:
        from zenquant.data import CotDataStore, CotStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cot.parquet"
            records = [
                _make_cot_row(1, asset_mgr_net=1000, leveraged_net=-500),
                _make_cot_row(2, asset_mgr_net=1500, leveraged_net=-800),
                _make_cot_row(3, asset_mgr_net=2000, leveraged_net=-200),
            ]
            _write_cot_parquet(path, records)

            store = CotDataStore(CotStoreConfig(cot_path=path))
            store.load()
            self.assertTrue(store.loaded)

            # Before any data
            snap = store.lookup(0)
            self.assertEqual(snap.asset_mgr_net, 0)

            # Between week 1 and 2
            snap = store.lookup(int(1.5 * WEEK_NS))
            self.assertEqual(snap.asset_mgr_net, 1000)
            self.assertEqual(snap.leveraged_net, -500)

            # After week 3
            snap = store.lookup(4 * WEEK_NS)
            self.assertEqual(snap.asset_mgr_net, 2000)

    def test_staleness_flag(self) -> None:
        from zenquant.data import CotDataStore, CotStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cot.parquet"
            _write_cot_parquet(path, [_make_cot_row(1)])

            store = CotDataStore(CotStoreConfig(cot_path=path))
            store.load()

            # Within staleness
            snap = store.lookup(int(1.5 * WEEK_NS))
            self.assertNotIn("COT_STALE", snap.quality_flags)

            # Beyond staleness (>7 days)
            snap = store.lookup(10 * WEEK_NS)
            self.assertIn("COT_STALE", snap.quality_flags)

    def test_z_score_extreme_positioning(self) -> None:
        from zenquant.data import CotDataStore, CotStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cot.parquet"
            # 20 normal weeks + 1 extreme
            records = []
            for i in range(20):
                records.append(
                    _make_cot_row(
                        i + 1,
                        asset_mgr_net=1000 + (i % 5) * 100,
                        leveraged_net=-500 + (i % 3) * 50,
                    )
                )
            # Extreme crowding
            records.append(_make_cot_row(21, asset_mgr_net=10000, leveraged_net=-5000))
            _write_cot_parquet(path, records)

            store = CotDataStore(CotStoreConfig(cot_path=path))
            store.load()

            snap = store.lookup(22 * WEEK_NS)
            self.assertGreater(snap.asset_mgr_z, 2.0)


if __name__ == "__main__":
    unittest.main()
