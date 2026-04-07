"""Unit tests for BasisDataStore — spot-perp premium/discount."""

import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HOUR_NS = 3600 * 1_000_000_000


def _write_klines(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict({k: [r[k] for r in records] for k in records[0]})
    pq.write_table(table, path)


def _make_kline(ts: int, close: float) -> dict:
    return {
        "open_time": ts // 1_000_000,
        "open_time_ns": ts,
        "close_time": (ts + 4 * HOUR_NS) // 1_000_000,
        "close_time_ns": ts + 4 * HOUR_NS,
        "open": close - 10,
        "high": close + 50,
        "low": close - 50,
        "close": close,
        "volume": 100.0,
        "trade_count": 500,
    }


class BasisStoreTests(unittest.TestCase):
    def test_empty_store(self) -> None:
        from zenquant.data import BasisDataStore, BasisStoreConfig

        store = BasisDataStore(BasisStoreConfig(
            spot_dir=Path("/nonexistent/spot"),
            perp_dir=Path("/nonexistent/perp"),
        ))
        store.load()
        self.assertFalse(store.loaded)
        snap = store.lookup(HOUR_NS * 100)
        self.assertEqual(snap.basis_pct, 0.0)

    def test_positive_basis(self) -> None:
        """Perp premium = positive basis."""
        from zenquant.data import BasisDataStore, BasisStoreConfig

        with tempfile.TemporaryDirectory() as tmp:
            spot_dir = Path(tmp) / "spot"
            perp_dir = Path(tmp) / "perp"

            # Spot at 40000, perp at 40100 (0.25% premium)
            _write_klines(spot_dir / "data.parquet", [
                _make_kline(i * 4 * HOUR_NS, 40000.0) for i in range(20)
            ])
            _write_klines(perp_dir / "data.parquet", [
                _make_kline(i * 4 * HOUR_NS, 40100.0) for i in range(20)
            ])

            store = BasisDataStore(BasisStoreConfig(spot_dir=spot_dir, perp_dir=perp_dir))
            store.load()
            self.assertTrue(store.loaded)

            snap = store.lookup(10 * 4 * HOUR_NS)
            self.assertGreater(snap.basis_pct, 0)
            self.assertAlmostEqual(snap.basis_pct, 0.0025, places=3)

    def test_point_in_time(self) -> None:
        """No lookahead — lookup returns data before cutoff only."""
        from zenquant.data import BasisDataStore, BasisStoreConfig

        with tempfile.TemporaryDirectory() as tmp:
            spot_dir = Path(tmp) / "spot"
            perp_dir = Path(tmp) / "perp"

            _write_klines(spot_dir / "data.parquet", [
                _make_kline(10 * 4 * HOUR_NS, 40000.0),
                _make_kline(20 * 4 * HOUR_NS, 41000.0),
            ])
            _write_klines(perp_dir / "data.parquet", [
                _make_kline(10 * 4 * HOUR_NS, 40200.0),
                _make_kline(20 * 4 * HOUR_NS, 40800.0),
            ])

            store = BasisDataStore(BasisStoreConfig(spot_dir=spot_dir, perp_dir=perp_dir))
            store.load()

            # Before any data
            snap = store.lookup(5 * 4 * HOUR_NS)
            self.assertEqual(snap.basis_pct, 0.0)

            # Between first and second
            snap = store.lookup(15 * 4 * HOUR_NS)
            self.assertAlmostEqual(snap.basis_pct, 0.005, places=3)  # 200/40000


if __name__ == "__main__":
    unittest.main()
