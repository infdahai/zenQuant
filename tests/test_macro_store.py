import tempfile
import unittest
from pathlib import Path

from zenquant.data.macro_store import MacroDataStore, MacroDataStoreConfig


def _write_parquet(path: Path, records: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict({k: [r[k] for r in records] for k in records[0]})
    pq.write_table(table, path)


DAY_NS = 86_400 * 1_000_000_000


class MacroDataStoreTests(unittest.TestCase):
    def _make_store(
        self,
        tmpdir: str,
        vix: list[tuple[int, float]] | None = None,
        dxy: list[tuple[int, float]] | None = None,
        yields: list[tuple[int, float]] | None = None,
        etf_flow: list[tuple[int, float]] | None = None,
        cme_cot: list[dict] | None = None,
    ) -> MacroDataStore:
        macro_dir = Path(tmpdir)
        if vix:
            _write_parquet(macro_dir / "vix" / "data.parquet", [{"ts_ns": t, "value": v} for t, v in vix])
        if dxy:
            _write_parquet(macro_dir / "dxy" / "data.parquet", [{"ts_ns": t, "value": v} for t, v in dxy])
        if yields:
            _write_parquet(macro_dir / "treasury_10y" / "data.parquet", [{"ts_ns": t, "value": v} for t, v in yields])
        if etf_flow:
            _write_parquet(macro_dir / "btc_etf_netflow" / "data.parquet", [{"ts_ns": t, "value": v} for t, v in etf_flow])
        if cme_cot:
            _write_parquet(Path(tmpdir) / "cftc" / "cot_btc.parquet", cme_cot)
        store = MacroDataStore(
            MacroDataStoreConfig(
                macro_dir=macro_dir,
                cftc_path=Path(tmpdir) / "cftc" / "cot_btc.parquet",
                z_window=60,
                robust_z_min_samples=5,
            )
        )
        store.load()
        return store

    def test_lookup_returns_latest_before_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vix_data = [(i * DAY_NS, 20.0 + i) for i in range(1, 11)]
            store = self._make_store(tmpdir, vix=vix_data)

            # Cutoff at day 5.5 should return day 5 value (25.0)
            snap = store.lookup(int(5.5 * DAY_NS))
            self.assertAlmostEqual(snap.vix, 25.0)

    def test_no_lookahead_bias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vix_data = [
                (10 * DAY_NS, 20.0),
                (20 * DAY_NS, 30.0),
                (30 * DAY_NS, 40.0),
            ]
            store = self._make_store(tmpdir, vix=vix_data)

            # Cutoff at day 15 should NOT see day 20 or 30 data
            snap = store.lookup(15 * DAY_NS)
            self.assertAlmostEqual(snap.vix, 20.0)

    def test_z_scores_with_known_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Varying series with an extreme outlier at the end
            import random

            rng = random.Random(42)
            vix_data = [(i * DAY_NS, 20.0 + rng.gauss(0, 2)) for i in range(1, 31)]
            vix_data.append((31 * DAY_NS, 50.0))  # extreme outlier
            store = self._make_store(tmpdir, vix=vix_data)

            snap = store.lookup(31 * DAY_NS)
            # Outlier should produce a large positive z-score
            self.assertGreater(snap.vix_z, 2.0)

    def test_macro_stale_flag_when_data_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vix_data = [(1 * DAY_NS, 20.0)]
            store = self._make_store(tmpdir, vix=vix_data)

            # 10 days later — well past staleness threshold
            snap = store.lookup(10 * DAY_NS)
            self.assertIn("MACRO_STALE", snap.quality_flags)

    def test_no_stale_flag_for_recent_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vix_data = [(10 * DAY_NS, 20.0)]
            store = self._make_store(tmpdir, vix=vix_data)

            # Same day — not stale
            snap = store.lookup(10 * DAY_NS)
            self.assertNotIn("MACRO_STALE", snap.quality_flags)

    def test_empty_store_returns_zero_zscores(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._make_store(tmpdir)
            snap = store.lookup(100 * DAY_NS)

            self.assertIsNone(snap.vix)
            self.assertAlmostEqual(snap.vix_z, 0.0)
            self.assertIsNone(snap.dxy)
            self.assertAlmostEqual(snap.dxy_z, 0.0)
            self.assertEqual(snap.quality_flags, [])

    def test_weekend_forward_fill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Friday data, no Saturday/Sunday data
            friday_ns = 5 * DAY_NS
            vix_data = [(friday_ns, 22.0)]
            store = self._make_store(tmpdir, vix=vix_data)

            # Sunday lookup should forward-fill Friday's value
            sunday_ns = 7 * DAY_NS
            snap = store.lookup(sunday_ns)
            self.assertAlmostEqual(snap.vix, 22.0)
            # Within 3-day staleness window — no MACRO_STALE
            self.assertNotIn("MACRO_STALE", snap.quality_flags)

    def test_before_any_data_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vix_data = [(10 * DAY_NS, 20.0)]
            store = self._make_store(tmpdir, vix=vix_data)

            snap = store.lookup(5 * DAY_NS)
            self.assertIsNone(snap.vix)
            self.assertAlmostEqual(snap.vix_z, 0.0)

    def test_multiple_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            n = 30
            vix_data = [(i * DAY_NS, 20.0 + i * 0.1) for i in range(1, n + 1)]
            dxy_data = [(i * DAY_NS, 100.0 + i * 0.5) for i in range(1, n + 1)]
            yield_data = [(i * DAY_NS, 4.0 + i * 0.01) for i in range(1, n + 1)]
            store = self._make_store(tmpdir, vix=vix_data, dxy=dxy_data, yields=yield_data)

            snap = store.lookup(n * DAY_NS)
            self.assertIsNotNone(snap.vix)
            self.assertIsNotNone(snap.dxy)
            self.assertIsNotNone(snap.yield_10y)

    def test_etf_flow_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            flows = [
                (1 * DAY_NS, 100_000_000.0),
                (2 * DAY_NS, 120_000_000.0),
                (3 * DAY_NS, -50_000_000.0),
                (4 * DAY_NS, -75_000_000.0),
                (5 * DAY_NS, -25_000_000.0),
            ]
            store = self._make_store(tmpdir, etf_flow=flows)

            snap = store.lookup(5 * DAY_NS)
            self.assertAlmostEqual(snap.btc_etf_netflow, -25_000_000.0)
            self.assertAlmostEqual(snap.btc_etf_flow_3d, -150_000_000.0)
            self.assertAlmostEqual(snap.btc_etf_flow_5d, 70_000_000.0)
            self.assertEqual(snap.btc_etf_negative_streak, 3)

    def test_cme_positioning_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cot_rows = [
                {"report_date_ns": 1 * DAY_NS, "asset_mgr_net": 1000.0, "leveraged_net": -500.0, "dealer_net": -100.0, "total_oi": 10_000.0},
                {"report_date_ns": 8 * DAY_NS, "asset_mgr_net": 1200.0, "leveraged_net": -700.0, "dealer_net": -200.0, "total_oi": 11_000.0},
                {"report_date_ns": 15 * DAY_NS, "asset_mgr_net": 1800.0, "leveraged_net": 900.0, "dealer_net": -300.0, "total_oi": 14_000.0},
            ]
            store = self._make_store(tmpdir, cme_cot=cot_rows)

            snap = store.lookup(15 * DAY_NS)
            self.assertAlmostEqual(snap.cme_asset_mgr_net, 1800.0)
            self.assertAlmostEqual(snap.cme_lev_money_net, 900.0)
            self.assertAlmostEqual(snap.cme_total_oi, 14_000.0)


if __name__ == "__main__":
    unittest.main()
