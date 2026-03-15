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


if __name__ == "__main__":
    unittest.main()
