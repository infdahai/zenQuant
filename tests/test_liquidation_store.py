"""Unit tests for LiquidationDataStore point-in-time lookups."""

import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HOUR_NS = 3600 * 1_000_000_000


def _write_liq_parquet(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict({k: [r[k] for r in records] for k in records[0]})
    pq.write_table(table, path)


def _make_bucket(
    ts_ns: int,
    symbol: str = "BTCUSDT",
    long_usd: float = 100_000.0,
    short_usd: float = 50_000.0,
    max_single: float = 20_000.0,
) -> dict:
    total = long_usd + short_usd
    imbalance = (long_usd - short_usd) / total if total > 0 else 0.0
    return {
        "bucket_start_ns": ts_ns,
        "bucket_end_ns": ts_ns + 4 * HOUR_NS,
        "symbol": symbol,
        "long_liq_count": 10,
        "short_liq_count": 5,
        "long_liq_usd": long_usd,
        "short_liq_usd": short_usd,
        "total_liq_usd": total,
        "max_single_liq_usd": max_single,
        "liq_imbalance": round(imbalance, 4),
    }


class LiquidationStoreTests(unittest.TestCase):
    def test_empty_store_returns_zero(self) -> None:
        from zenquant.data import LiquidationDataStore, LiquidationStoreConfig

        store = LiquidationDataStore(LiquidationStoreConfig(liquidation_dir=Path("/nonexistent")))
        store.load()
        snap = store.lookup(1_000_000_000_000)
        self.assertFalse(store.loaded)
        self.assertEqual(snap.total_liq_usd, 0.0)
        self.assertEqual(snap.liq_intensity_z, 0.0)

    def test_point_in_time_no_lookahead(self) -> None:
        from zenquant.data import LiquidationDataStore, LiquidationStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            agg_dir = Path(tmpdir)
            # Two buckets: t=100H and t=200H
            records = [
                _make_bucket(100 * HOUR_NS, long_usd=80_000, short_usd=40_000),
                _make_bucket(200 * HOUR_NS, long_usd=500_000, short_usd=200_000),
            ]
            _write_liq_parquet(agg_dir / "2024-01" / "liq_agg_test.parquet", records)

            store = LiquidationDataStore(LiquidationStoreConfig(liquidation_dir=agg_dir))
            store.load()
            self.assertTrue(store.loaded)

            # Before any data
            snap = store.lookup(50 * HOUR_NS)
            self.assertEqual(snap.total_liq_usd, 0.0)

            # Between first and second
            snap = store.lookup(150 * HOUR_NS)
            self.assertAlmostEqual(snap.total_liq_usd, 120_000.0)
            self.assertAlmostEqual(snap.long_liq_usd, 80_000.0)

            # After second
            snap = store.lookup(250 * HOUR_NS)
            self.assertAlmostEqual(snap.total_liq_usd, 700_000.0)

    def test_imbalance_calculation(self) -> None:
        from zenquant.data import LiquidationDataStore, LiquidationStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            agg_dir = Path(tmpdir)
            records = [
                _make_bucket(HOUR_NS, long_usd=300_000, short_usd=100_000),
            ]
            _write_liq_parquet(agg_dir / "2024-01" / "test.parquet", records)

            store = LiquidationDataStore(LiquidationStoreConfig(liquidation_dir=agg_dir))
            store.load()

            snap = store.lookup(2 * HOUR_NS)
            # (300k - 100k) / 400k = 0.5
            self.assertAlmostEqual(snap.liq_imbalance, 0.5, places=2)

    def test_staleness_flag(self) -> None:
        from zenquant.data import LiquidationDataStore, LiquidationStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            agg_dir = Path(tmpdir)
            records = [_make_bucket(HOUR_NS)]
            _write_liq_parquet(agg_dir / "2024-01" / "test.parquet", records)

            store = LiquidationDataStore(
                LiquidationStoreConfig(
                    liquidation_dir=agg_dir,
                    staleness_threshold_ns=4 * HOUR_NS,
                )
            )
            store.load()

            # Within staleness window
            snap = store.lookup(3 * HOUR_NS)
            self.assertNotIn("LIQ_STALE", snap.quality_flags)

            # Beyond staleness window
            snap = store.lookup(100 * HOUR_NS)
            self.assertIn("LIQ_STALE", snap.quality_flags)

    def test_z_score_with_sufficient_data(self) -> None:
        from zenquant.data import LiquidationDataStore, LiquidationStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            agg_dir = Path(tmpdir)
            # 20 normal buckets with natural variation + 1 extreme
            records = []
            for i in range(20):
                long_var = 80_000 + (i % 5) * 10_000  # 80k-120k variation
                short_var = 40_000 + (i % 3) * 5_000  # 40k-50k variation
                records.append(_make_bucket((i + 1) * 4 * HOUR_NS, long_usd=long_var, short_usd=short_var))
            # Extreme liquidation event
            records.append(_make_bucket(21 * 4 * HOUR_NS, long_usd=5_000_000, short_usd=2_000_000))

            _write_liq_parquet(agg_dir / "2024-01" / "test.parquet", records)

            store = LiquidationDataStore(LiquidationStoreConfig(liquidation_dir=agg_dir))
            store.load()

            snap = store.lookup(22 * 4 * HOUR_NS)
            # Extreme event should have high z-score
            self.assertGreater(snap.liq_intensity_z, 2.0)


if __name__ == "__main__":
    unittest.main()
