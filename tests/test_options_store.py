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


def _options_state_chain(source_ns: int, *, spot: float) -> list[dict]:
    rows: list[dict] = []
    for dte in (7, 30, 90):
        for strike in (80.0, 100.0, 120.0):
            for option_type in ("call", "put"):
                rows.append(
                    {
                        "snapshot_ts_ns": source_ns,
                        "currency": "BTC",
                        "instrument_name": f"BTC-{dte}D-{strike:g}-{option_type[0].upper()}",
                        "expiry_ts_ms": (source_ns + dte * DAY_NS) // 1_000_000,
                        "strike": strike,
                        "option_type": option_type,
                        "underlying_price": spot,
                        "open_interest": 100.0,
                        "mark_iv": 60.0,
                    }
                )
    return rows


class OptionsDataStoreTests(unittest.TestCase):
    def test_store_asof_never_selects_a_snapshot_one_nanosecond_after_cutoff(self) -> None:
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        cutoff_ns = 1_728_000_000_000_000_000
        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            month_dir = options_dir / "BTC" / "2026-04"
            _write_parquet(month_dir / "current.parquet", _options_state_chain(cutoff_ns, spot=100.0))
            _write_parquet(month_dir / "future.parquet", _options_state_chain(cutoff_ns + 1, spot=999.0))
            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()
            state = store.lookup_options_state(
                cutoff_ns,
                realized_vol_30d=0.45,
                realized_vol_source_timestamp_ns=cutoff_ns,
            )

        self.assertEqual(state.source_timestamp_ns, cutoff_ns)
        self.assertEqual(state.underlying_price, 100.0)

    def test_legacy_store_metrics_accept_exact_integer_timestamp_strings(self) -> None:
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        cutoff_ns = 1_728_000_000_000_000_000
        rows = _options_state_chain(cutoff_ns, spot=100.0)
        for row in rows:
            row["snapshot_ts_ns"] = str(row["snapshot_ts_ns"])
            row["expiry_ts_ms"] = str(row["expiry_ts_ms"])
        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            _write_parquet(options_dir / "BTC" / "2026-04" / "strings.parquet", rows)
            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()
            snapshot = store.lookup(cutoff_ns)

        self.assertIsNotNone(snapshot.atm_iv_30d)
        self.assertIsNotNone(snapshot.term_structure_slope)

    def test_legacy_lookup_rejects_float_cutoff_instead_of_rounding_it(self) -> None:
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        cutoff_ns = 1_728_000_000_000_000_000
        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            _write_parquet(
                options_dir / "BTC" / "2026-04" / "current.parquet",
                _options_state_chain(cutoff_ns, spot=100.0),
            )
            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()
            with self.assertRaisesRegex(ValueError, "exact integer cutoff"):
                store.lookup(float(cutoff_ns))  # type: ignore[arg-type]

    def test_lookup_malformed_chain_returns_unknown_instead_of_skipping_or_raising(self) -> None:
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            source_ns = 2 * DAY_NS
            rows = []
            for dte in (7, 30, 90):
                for strike in (80.0, 100.0, 120.0):
                    for option_type in ("call", "put"):
                        rows.append(
                            {
                                "snapshot_ts_ns": source_ns,
                                "currency": "BTC",
                                "instrument_name": f"BTC-{dte}-{strike}-{option_type}",
                                "expiry_ts_ms": (source_ns + dte * DAY_NS) // 1_000_000,
                                "strike": strike,
                                "option_type": option_type,
                                "underlying_price": 100.0,
                                "open_interest": "100",
                                "mark_iv": 60.0,
                            }
                        )
            rows[0]["open_interest"] = "bad"
            _write_parquet(options_dir / "BTC" / "2026-04" / "malformed.parquet", rows)
            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()
            cutoff = source_ns + 4 * 3_600 * 1_000_000_000
            state = store.lookup_options_state(
                cutoff,
                realized_vol_30d=0.45,
                realized_vol_source_timestamp_ns=cutoff,
            )

        self.assertEqual(state.trend_quality, "UNKNOWN")
        self.assertEqual(state.shadow_risk_multiplier, 1.0)
        self.assertIn("OPTIONS_MALFORMED_OPEN_INTEREST", state.quality_flags)

    def test_indicator_runtime_exposes_proxy_without_25delta_mislabel(self) -> None:
        from zenquant.data import OptionsSnapshot
        from zenquant.domain import Candle, MarketSnapshot, Timeframe
        from zenquant.indicator import IndicatorEngine, IndicatorStore
        from zenquant.infra import InMemoryJetStream

        snapshot = OptionsSnapshot(
            atm_iv_30d=0.60,
            atm_iv_z=0.0,
            term_structure_slope=0.1,
            term_slope_z=0.0,
            put_call_moneyness_skew_proxy=0.05,
            skew_z=0.0,
            underlying_price=100.0,
        )
        options_store = type("OptionsStore", (), {"lookup": lambda self, _cutoff: snapshot})()
        engine = IndicatorEngine(
            bus=InMemoryJetStream(),
            store=IndicatorStore(),
            options_store=options_store,
        )
        candle = Candle(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            bar_start_ns=0,
            bar_end_ns=4 * 3_600 * 1_000_000_000,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
            trade_count=1,
        )
        market = MarketSnapshot(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            cutoff_time_ns=candle.bar_end_ns,
            last_price=100.0,
            bid_depth=100.0,
            ask_depth=100.0,
            book_age_ms=0,
            venue_count=1,
            cvd_delta=0.0,
            funding_oi_weighted=None,
            oi_coverage=0.0,
            staleness_ms=0,
            quality_flags=[],
        )
        values, _flags = engine._compute_slow_feature_enrichment(
            history_key=(candle.symbol, candle.timeframe),
            candle=candle,
            snapshot=market,
            candles=[candle],
            structure_state="RANGE",
        )
        self.assertEqual(values["put_call_moneyness_skew_proxy"], 0.05)
        self.assertNotIn("put_call_skew_25d", values)

    def test_options_state_lookup_is_point_in_time_and_future_invariant(self) -> None:
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            month_dir = options_dir / "BTC" / "2026-04"

            def write_day(day: int, spot: float) -> None:
                source_ns = day * DAY_NS
                rows: list[dict] = []
                for dte, iv in ((7, 50.0), (30, 60.0), (90, 70.0)):
                    for strike in (70.0, 85.0, 100.0, 115.0, 130.0):
                        for option_type in ("call", "put"):
                            rows.append(
                                {
                                    "snapshot_ts_ns": source_ns,
                                    "currency": "BTC",
                                    "instrument_name": f"BTC-{dte}D-{strike:g}-{option_type[0].upper()}",
                                    "expiry_ts_ms": (source_ns + dte * DAY_NS) // 1_000_000,
                                    "expiry_date": "2026-04-01",
                                    "strike": strike,
                                    "option_type": option_type,
                                    "underlying_price": spot,
                                    "open_interest": 100.0,
                                    "volume_usd": 1000.0,
                                    "mark_iv": iv,
                                    "bid_price": 0.1,
                                    "ask_price": 0.15,
                                    "mid_price": 0.125,
                                    "underlying_index": "BTC",
                                    "settlement_period": "day",
                                }
                            )
                _write_parquet(month_dir / f"options_chain_day_{day}.parquet", rows)

            write_day(2, 100.0)
            write_day(3, 105.0)

            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()
            cutoff = 3 * DAY_NS
            first = store.lookup_options_state(
                cutoff,
                realized_vol_30d=0.45,
                realized_vol_source_timestamp_ns=cutoff,
            )
            self.assertEqual(first.source_timestamp_ns, 3 * DAY_NS)
            self.assertAlmostEqual(first.underlying_price, 105.0)

            # Adding and loading a future source cannot change the earlier state.
            write_day(4, 80.0)
            store.load()
            second = store.lookup_options_state(
                cutoff,
                realized_vol_30d=0.45,
                realized_vol_source_timestamp_ns=cutoff,
            )
            self.assertEqual(first, second)
            self.assertEqual(first.options_state_id, second.options_state_id)

    def test_missing_options_state_is_unknown_with_neutral_shadow_multiplier(self) -> None:
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=Path(tmpdir)))
            store.load()
            cutoff = 4 * 3_600 * 1_000_000_000
            state = store.lookup_options_state(
                cutoff,
                realized_vol_30d=0.45,
                realized_vol_source_timestamp_ns=cutoff,
            )

        self.assertEqual(state.trend_quality, "UNKNOWN")
        self.assertEqual(state.shadow_risk_multiplier, 1.0)
        self.assertIn("OPTIONS_MISSING", state.quality_flags)

    def test_options_state_lookup_uses_asof_24h_history(self) -> None:
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            month_dir = options_dir / "BTC" / "2026-04"
            for day, spot in ((2, 100.0), (3, 110.0)):
                source_ns = day * DAY_NS
                rows = []
                for dte in (7, 30, 90):
                    for strike in (75.0, 90.0, 100.0, 110.0, 125.0):
                        for option_type in ("call", "put"):
                            rows.append(
                                {
                                    "snapshot_ts_ns": source_ns,
                                    "currency": "BTC",
                                    "instrument_name": f"BTC-{dte}D-{strike:g}-{option_type[0].upper()}",
                                    "expiry_ts_ms": (source_ns + dte * DAY_NS) // 1_000_000,
                                    "expiry_date": "2026-04-01",
                                    "strike": strike,
                                    "option_type": option_type,
                                    "underlying_price": spot,
                                    "open_interest": 100.0,
                                    "volume_usd": 1000.0,
                                    "mark_iv": 60.0,
                                    "bid_price": 0.1,
                                    "ask_price": 0.15,
                                    "mid_price": 0.125,
                                    "underlying_index": "BTC",
                                    "settlement_period": "day",
                                }
                            )
                _write_parquet(month_dir / f"options_chain_day_{day}.parquet", rows)

            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()
            state = store.lookup_options_state(
                3 * DAY_NS,
                realized_vol_30d=0.45,
                realized_vol_source_timestamp_ns=3 * DAY_NS,
            )
            self.assertAlmostEqual(state.spot_return_24h or 0.0, 0.10)
            self.assertNotIn("OPTIONS_24H_HISTORY_MISSING", state.quality_flags)

    def test_legacy_skew_is_explicitly_named_moneyness_proxy(self) -> None:
        from zenquant.data import OptionsSnapshot

        snapshot = OptionsSnapshot(
            atm_iv_30d=0.60,
            atm_iv_z=0.0,
            term_structure_slope=0.1,
            term_slope_z=0.0,
            put_call_moneyness_skew_proxy=0.05,
            skew_z=0.0,
            underlying_price=100.0,
        )
        self.assertEqual(snapshot.put_call_moneyness_skew_proxy, 0.05)
        self.assertEqual(snapshot.put_call_skew_25d, 0.05)

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

    def test_load_skips_derived_factor_parquet(self) -> None:
        """Derived factor cache is not an options chain snapshot."""
        from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            options_dir = Path(tmpdir)
            month_dir = options_dir / "BTC" / "2026-04"
            snapshot_ts = DAY_NS
            _write_parquet(
                month_dir / "options_chain_20260401T000000Z.parquet",
                [
                    {
                        "snapshot_ts_ns": snapshot_ts,
                        "currency": "BTC",
                        "instrument_name": "BTC-1D-60000-C",
                        "expiry_ts_ms": (snapshot_ts + 30 * DAY_NS) // 1_000_000,
                        "expiry_date": "2026-04-01",
                        "strike": 60000.0,
                        "option_type": "call",
                        "underlying_price": 68000.0,
                        "open_interest": 100.0,
                        "volume_usd": 1000.0,
                        "mark_iv": 55.0,
                        "bid_price": 0.1,
                        "ask_price": 0.15,
                        "mid_price": 0.125,
                        "underlying_index": "BTC",
                        "settlement_period": "day",
                    }
                ],
            )
            _write_parquet(
                options_dir / "BTC" / "derived_options_regime_factors.parquet",
                [
                    {
                        "option_snapshot_ts": snapshot_ts,
                        "iv_level_30d": 55.0,
                        "put_call_skew_25d": 1.2,
                    }
                ],
            )

            store = OptionsDataStore(OptionsDataStoreConfig(options_dir=options_dir))
            store.load()

            self.assertTrue(store.loaded)
            self.assertEqual(len(store._timestamps), 1)

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
