import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from zenquant.data import LiquidationDataStore, LiquidationStoreConfig
from zenquant.domain import (
    Candle,
    IndicatorCoverage,
    IndicatorPack,
    MarketSnapshot,
    TargetPosition,
    Timeframe,
)
from zenquant.indicator import IndicatorEngine, IndicatorEngineConfig, IndicatorStore
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay
from zenquant.state import StateEngine

HOUR_NS = 3600 * 1_000_000_000


def _write_liq_parquet(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict({k: [r[k] for r in records] for k in records[0]})
    pq.write_table(table, path)


def _make_bucket(
    ts_ns: int,
    long_usd: float,
    short_usd: float,
    max_single: float,
) -> dict:
    total = long_usd + short_usd
    imbalance = (long_usd - short_usd) / total if total > 0 else 0.0
    return {
        "bucket_start_ns": ts_ns,
        "bucket_end_ns": ts_ns + 4 * HOUR_NS,
        "symbol": "BTCUSDT",
        "long_liq_count": 10,
        "short_liq_count": 5,
        "long_liq_usd": long_usd,
        "short_liq_usd": short_usd,
        "total_liq_usd": total,
        "max_single_liq_usd": max_single,
        "liq_imbalance": round(imbalance, 4),
    }


def _pack(
    *,
    liq_intensity_z: float = 0.0,
    liq_imbalance: float = 0.0,
    liq_stale: bool = False,
    structure_state: str = "NEUTRAL",
    momentum_roc_z: float = -0.14,
    cvd_slope: float = -0.02,
    zen_pulse: float = 48.5,
) -> IndicatorPack:
    return IndicatorPack(
        ts_ns=10,
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=10,
        indicator_version="pack.v1",
        formula_hash="sha256:test",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
        values={
            "realized_vol": 0.95,
            "cvd_slope": cvd_slope,
            "funding_oi_z": -0.2,
            "zen_pulse": zen_pulse,
            "structure_state": structure_state,
            "momentum_roc_z": momentum_roc_z,
            "cycle_position": 0.99,
            "last_price": 28_000.0,
            "liq_intensity_z": liq_intensity_z,
            "liq_imbalance": liq_imbalance,
            "liq_stale": liq_stale,
            "liq_max_single_share": 0.40,
        },
        components={},
    )


class LiquidationStateOverlayTests(unittest.TestCase):
    def test_state_overlay_promotes_bearish_transition_to_downtrend(self) -> None:
        bus = InMemoryJetStream()
        StateEngine(bus)

        states = []
        bus.subscribe("state.market.>", lambda _subject, payload: states.append(payload))
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _pack(liq_intensity_z=3.1, liq_imbalance=0.42))

        self.assertEqual(len(states), 1)
        state = states[0]
        self.assertEqual(state.directional_state, "DOWNTREND")
        self.assertEqual(state.regime, "STRESS")
        self.assertIn("LIQUIDATION_CASCADE", state.reason_codes)
        self.assertIn("LIQ_LONGS_FLUSHED", state.reason_codes)

    def test_stale_liquidation_signal_does_not_override_state(self) -> None:
        bus = InMemoryJetStream()
        StateEngine(bus)

        states = []
        bus.subscribe("state.market.>", lambda _subject, payload: states.append(payload))
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(liq_intensity_z=3.1, liq_imbalance=0.42, liq_stale=True),
        )

        self.assertEqual(len(states), 1)
        state = states[0]
        self.assertEqual(state.directional_state, "TRANSITION")
        self.assertEqual(state.regime, "RANGE")
        self.assertNotIn("LIQUIDATION_CASCADE", state.reason_codes)


class LiquidationRiskGuardTests(unittest.TestCase):
    def test_risk_guard_reduces_leverage_on_extreme_liquidation_pressure(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        risk.prime_indicator(_pack(liq_intensity_z=3.2, liq_imbalance=0.35))

        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _subject, payload: decisions.append(payload))
        bus.publish(
            "intent.target_position.arc.BTCUSDT_PERP",
            TargetPosition(
                intent_id="liq-risk",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=10,
                target_qty=1.0,
                target_leverage=2.0,
                reduce_only=False,
                regime="RANGE",
                regime_confidence=0.6,
                reason_codes=["TEST"],
            ),
        )

        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertIn("LIQUIDATION_PRESSURE", decision.flags)
        self.assertIn("LIQ_TRANSITION_GUARD", decision.flags)
        self.assertLess(decision.lev_cap, 6.0)

    def test_stale_liquidation_signal_does_not_trigger_risk_guard(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        risk.prime_indicator(_pack(liq_intensity_z=3.2, liq_imbalance=0.35, liq_stale=True))

        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _subject, payload: decisions.append(payload))
        bus.publish(
            "intent.target_position.arc.BTCUSDT_PERP",
            TargetPosition(
                intent_id="liq-risk-stale",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=10,
                target_qty=1.0,
                target_leverage=2.0,
                reduce_only=False,
                regime="RANGE",
                regime_confidence=0.6,
                reason_codes=["TEST"],
            ),
        )

        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertNotIn("LIQUIDATION_PRESSURE", decision.flags)
        self.assertNotIn("LIQ_TRANSITION_GUARD", decision.flags)


class LiquidationStoreIndicatorTests(unittest.TestCase):
    def test_indicator_engine_injects_liquidation_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agg_dir = Path(tmpdir)
            records = [_make_bucket(4 * HOUR_NS, long_usd=400_000, short_usd=100_000, max_single=200_000)]
            _write_liq_parquet(agg_dir / "2024-01" / "test.parquet", records)

            store = LiquidationDataStore(LiquidationStoreConfig(liquidation_dir=agg_dir))
            store.load()

            bus = InMemoryJetStream()
            engine = IndicatorEngine(
                bus=bus,
                store=IndicatorStore(),
                config=IndicatorEngineConfig(expected_symbols=("BTCUSDT_PERP",)),
                liquidation_store=store,
            )
            self.assertIsNotNone(engine)

            packs: list[IndicatorPack] = []
            bus.subscribe(
                "indicator.>",
                lambda _subject, payload: packs.append(payload) if isinstance(payload, IndicatorPack) else None,
            )
            bus.publish(
                "md.candle.4h.BTCUSDT_PERP",
                Candle(
                    symbol="BTCUSDT_PERP",
                    timeframe=Timeframe.TF_4H,
                    bar_start_ns=2 * HOUR_NS,
                    bar_end_ns=6 * HOUR_NS,
                    open=28_500.0,
                    high=28_600.0,
                    low=27_900.0,
                    close=28_000.0,
                    volume=120.0,
                    trade_count=200,
                ),
            )
            bus.publish(
                "md.snapshot.4h.BTCUSDT_PERP",
                MarketSnapshot(
                    symbol="BTCUSDT_PERP",
                    timeframe=Timeframe.TF_4H,
                    cutoff_time_ns=6 * HOUR_NS,
                    last_price=28_000.0,
                    bid_depth=100_000.0,
                    ask_depth=80_000.0,
                    book_age_ms=50,
                    venue_count=1,
                    cvd_delta=-40_000.0,
                    funding_oi_weighted=-0.0001,
                    oi_coverage=1.0,
                    staleness_ms=0,
                    quality_flags=["OK"],
                    vpin=0.05,
                    large_trade_count=2,
                ),
            )

        self.assertEqual(len(packs), 1)
        pack = packs[0]
        self.assertGreater(pack.values["liq_total_usd"], 0.0)
        self.assertGreaterEqual(pack.values["liq_intensity_z"], 0.0)
        self.assertGreater(pack.values["liq_max_single_share"], 0.0)
        self.assertFalse(pack.values["liq_stale"])

    def test_store_exposes_staleness_flag_for_indicator_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agg_dir = Path(tmpdir)
            records = [_make_bucket(4 * HOUR_NS, long_usd=400_000, short_usd=100_000, max_single=200_000)]
            _write_liq_parquet(agg_dir / "2024-01" / "test.parquet", records)

            store = LiquidationDataStore(
                LiquidationStoreConfig(liquidation_dir=agg_dir, staleness_threshold_ns=4 * HOUR_NS)
            )
            store.load()
            fresh = store.lookup(6 * HOUR_NS)
            stale = store.lookup(20 * HOUR_NS)

        self.assertNotIn("LIQ_STALE", fresh.quality_flags)
        self.assertIn("LIQ_STALE", stale.quality_flags)


if __name__ == "__main__":
    unittest.main()
