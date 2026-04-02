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


if __name__ == "__main__":
    unittest.main()
