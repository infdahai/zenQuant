import unittest

from zenquant.domain import MarketState, Timeframe
from zenquant.strategy.playbook_engine import PlaybookEngine, PlaybookEngineConfig


def _state(
    *,
    directional_state: str = "NEUTRAL",
    directional_confidence: float = 0.7,
    volatility_state: str = "NORMAL",
    volatility_confidence: float = 0.8,
    positioning_state: str = "HEALTHY",
    positioning_confidence: float = 0.8,
    quality_state: str = "TRADABLE",
    quality_confidence: float = 0.9,
    transition_risk: float = 0.1,
) -> MarketState:
    return MarketState(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        directional_state=directional_state,
        directional_confidence=directional_confidence,
        volatility_state=volatility_state,
        volatility_confidence=volatility_confidence,
        positioning_state=positioning_state,
        positioning_confidence=positioning_confidence,
        quality_state=quality_state,
        quality_confidence=quality_confidence,
        regime="TREND",
        regime_confidence=0.8,
        direction_signal=1.0,
        transition_risk=transition_risk,
        quality_flags=["OK"],
        reason_codes=["REGIME_TREND"],
    )


class PlaybookEngineTests(unittest.TestCase):
    def test_selects_bull_continuation_for_clean_uptrend(self) -> None:
        playbook = PlaybookEngine().select(_state(directional_state="UPTREND", directional_confidence=0.85))
        self.assertEqual(playbook.playbook_id, "BULL_CONTINUATION")
        self.assertTrue(playbook.allow_pyramid)
        self.assertIn(1.0, playbook.allowed_directions)

    def test_selects_bear_defense_for_clean_downtrend(self) -> None:
        playbook = PlaybookEngine().select(
            _state(
                directional_state="DOWNTREND",
                directional_confidence=0.82,
            )
        )
        self.assertEqual(playbook.playbook_id, "BEAR_DEFENSE")
        self.assertFalse(playbook.allow_pyramid)
        self.assertIn(-1.0, playbook.allowed_directions)
        self.assertEqual(playbook.max_leverage, 2.25)

    def test_selects_range_light_for_neutral_tradable_state(self) -> None:
        playbook = PlaybookEngine().select(
            _state(
                directional_state="NEUTRAL",
                directional_confidence=0.35,
                volatility_state="LOW",
            )
        )
        self.assertEqual(playbook.playbook_id, "RANGE_LIGHT")
        self.assertEqual(playbook.time_decay_bars, 3)
        self.assertEqual(playbook.max_leverage, 1.5)

    def test_selects_capital_preservation_for_degraded_state(self) -> None:
        playbook = PlaybookEngine().select(
            _state(
                directional_state="UPTREND",
                quality_state="DEGRADED",
                transition_risk=0.8,
            )
        )
        self.assertEqual(playbook.playbook_id, "CAPITAL_PRESERVATION")
        self.assertTrue(playbook.is_reduce_only())

    def test_uptrend_with_crowded_long_falls_back_to_range_light(self) -> None:
        playbook = PlaybookEngine().select(
            _state(
                directional_state="UPTREND",
                directional_confidence=0.85,
                positioning_state="CROWDED_LONG",
            )
        )
        self.assertEqual(playbook.playbook_id, "RANGE_LIGHT")

    def test_transition_risk_preservation_handoff_is_explicit(self) -> None:
        playbook = PlaybookEngine().select(
            _state(
                directional_state="UPTREND",
                directional_confidence=0.85,
                transition_risk=0.7,
            )
        )
        self.assertEqual(playbook.playbook_id, "CAPITAL_PRESERVATION")
        self.assertTrue(playbook.is_reduce_only())

    def test_config_can_raise_preserve_capital_transition_threshold(self) -> None:
        engine = PlaybookEngine(PlaybookEngineConfig(preserve_capital_transition_risk=0.8))
        playbook = engine.select(
            _state(
                directional_state="UPTREND",
                directional_confidence=0.85,
                transition_risk=0.7,
            )
        )
        self.assertEqual(playbook.playbook_id, "BULL_CONTINUATION")

    def test_config_can_override_range_time_decay(self) -> None:
        engine = PlaybookEngine(PlaybookEngineConfig(range_time_decay_bars=5))
        playbook = engine.select(
            _state(
                directional_state="NEUTRAL",
                directional_confidence=0.35,
                volatility_state="LOW",
            )
        )
        self.assertEqual(playbook.playbook_id, "RANGE_LIGHT")
        self.assertEqual(playbook.time_decay_bars, 5)


if __name__ == "__main__":
    unittest.main()
