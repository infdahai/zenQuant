import unittest

from zenquant.domain import IndicatorCoverage, IndicatorPack, MarketState, Timeframe
from zenquant.infra import InMemoryJetStream
from zenquant.state import StateEngine


class StateEngineTests(unittest.TestCase):
    def test_state_engine_emits_four_dimensional_state(self) -> None:
        bus = InMemoryJetStream()
        StateEngine(bus)

        states: list[MarketState] = []
        bus.subscribe("state.market.>", lambda _subject, payload: states.append(payload))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            IndicatorPack(
                ts_ns=10,
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=10,
                indicator_version="pack.v1",
                formula_hash="sha256:test",
                quality_flags=["OK"],
                coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
                values={
                    "realized_vol": 0.9,
                    "cvd_slope": 0.4,
                    "funding_oi_z": 2.2,
                    "zen_pulse": 68.0,
                    "structure_state": "UPTREND",
                    "momentum_roc_z": 1.1,
                },
                components={},
            ),
        )

        self.assertEqual(len(states), 1)
        state = states[0]
        self.assertEqual(state.directional_state, "UPTREND")
        self.assertEqual(state.volatility_state, "NORMAL")
        self.assertEqual(state.positioning_state, "CROWDED_LONG")
        self.assertEqual(state.quality_state, "TRADABLE")
        self.assertEqual(state.regime, "TREND")
        self.assertGreaterEqual(state.transition_risk, 0.0)
        self.assertLessEqual(state.transition_risk, 1.0)

    def test_vix_z_upgrades_volatility_to_high(self) -> None:
        bus = InMemoryJetStream()
        StateEngine(bus)

        states: list[MarketState] = []
        bus.subscribe("state.market.>", lambda _subject, payload: states.append(payload))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            IndicatorPack(
                ts_ns=20,
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=20,
                indicator_version="pack.v1",
                formula_hash="sha256:test",
                quality_flags=["OK"],
                coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
                values={
                    "realized_vol": 0.8,  # NORMAL by itself
                    "cvd_slope": 0.1,
                    "funding_oi_z": 0.0,
                    "zen_pulse": 55.0,
                    "structure_state": "NEUTRAL",
                    "momentum_roc_z": 0.0,
                    "vix_z": 2.5,  # Should upgrade to HIGH
                },
                components={},
            ),
        )

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].volatility_state, "HIGH")

    def test_vix_z_extreme_produces_distorted(self) -> None:
        bus = InMemoryJetStream()
        StateEngine(bus)

        states: list[MarketState] = []
        bus.subscribe("state.market.>", lambda _subject, payload: states.append(payload))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            IndicatorPack(
                ts_ns=30,
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=30,
                indicator_version="pack.v1",
                formula_hash="sha256:test",
                quality_flags=["OK"],
                coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
                values={
                    "realized_vol": 0.8,
                    "cvd_slope": 0.1,
                    "funding_oi_z": 0.0,
                    "zen_pulse": 55.0,
                    "structure_state": "NEUTRAL",
                    "momentum_roc_z": 0.0,
                    "vix_z": 3.5,  # Should produce DISTORTED
                },
                components={},
            ),
        )

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].volatility_state, "DISTORTED")

    def test_macro_stale_produces_noisy_quality(self) -> None:
        bus = InMemoryJetStream()
        StateEngine(bus)

        states: list[MarketState] = []
        bus.subscribe("state.market.>", lambda _subject, payload: states.append(payload))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            IndicatorPack(
                ts_ns=40,
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=40,
                indicator_version="pack.v1",
                formula_hash="sha256:test",
                quality_flags=["MACRO_STALE"],
                coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
                values={
                    "realized_vol": 0.8,
                    "cvd_slope": 0.1,
                    "funding_oi_z": 0.0,
                    "zen_pulse": 55.0,
                    "structure_state": "NEUTRAL",
                    "momentum_roc_z": 0.0,
                },
                components={},
            ),
        )

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].quality_state, "NOISY")

    def test_bearish_grind_promotes_transition_to_downtrend_bias(self) -> None:
        bus = InMemoryJetStream()
        StateEngine(bus)

        states: list[MarketState] = []
        bus.subscribe("state.market.>", lambda _subject, payload: states.append(payload))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            IndicatorPack(
                ts_ns=50,
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=50,
                indicator_version="pack.v1",
                formula_hash="sha256:test",
                quality_flags=["OK"],
                coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
                values={
                    "realized_vol": 0.9,
                    "cvd_slope": -0.03,
                    "funding_oi_z": -0.2,
                    "zen_pulse": 48.0,
                    "structure_state": "NEUTRAL",
                    "momentum_roc_z": -0.16,
                    "cycle_position": 0.98,
                },
                components={},
            ),
        )

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].directional_state, "TRANSITION")
        self.assertLess(states[0].direction_signal, -0.5)
        self.assertGreaterEqual(states[0].directional_confidence, 0.5)

    def test_leverage_pressure_and_liquidation_follow_through_raise_stress_reasons(self) -> None:
        bus = InMemoryJetStream()
        StateEngine(bus)

        states: list[MarketState] = []
        bus.subscribe("state.market.>", lambda _subject, payload: states.append(payload))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            IndicatorPack(
                ts_ns=60,
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=60,
                indicator_version="pack.v1",
                formula_hash="sha256:test",
                quality_flags=["OK"],
                coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
                values={
                    "realized_vol": 1.1,
                    "cvd_slope": -0.03,
                    "funding_oi_z": 1.2,
                    "zen_pulse": 48.0,
                    "structure_state": "DOWNTREND",
                    "momentum_roc_z": -0.4,
                    "carry_stress_score": 0.65,
                    "leverage_pressure_score": 0.78,
                    "rv_term_ratio": 1.42,
                    "vol_of_vol": 0.16,
                    "liq_intensity_z": 2.7,
                    "liq_imbalance": 0.35,
                    "liq_follow_through_score": 0.72,
                    "options_surface_dislocation": 0.62,
                },
                components={},
            ),
        )

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].directional_state, "DOWNTREND")
        self.assertIn("LIQ_CASCADE_CONTINUATION", states[0].reason_codes)
        self.assertIn("LEVERAGE_QUALITY_CROWDED_LONG", states[0].reason_codes)
        self.assertIn("VOL_STRUCTURE_STRESSED", states[0].reason_codes)


if __name__ == "__main__":
    unittest.main()
