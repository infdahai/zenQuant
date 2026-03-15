import unittest

from zenquant.domain import MarketState, PlaybookSelection, Timeframe
from zenquant.strategy import SizingEngine


def _state(
    *,
    volatility_state: str = "NORMAL",
    quality_state: str = "TRADABLE",
    positioning_state: str = "HEALTHY",
    directional_confidence: float = 0.8,
    volatility_confidence: float = 0.8,
    positioning_confidence: float = 0.8,
    quality_confidence: float = 0.9,
) -> MarketState:
    return MarketState(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        directional_state="UPTREND",
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
        transition_risk=0.1,
        quality_flags=["OK"],
        reason_codes=["REGIME_TREND"],
    )


def _playbook(max_leverage: float = 3.0, confidence: float = 0.82) -> PlaybookSelection:
    return PlaybookSelection(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        playbook_id="BULL_CONTINUATION",
        playbook_confidence=confidence,
        allowed_directions=[1.0, 0.0],
        max_leverage=max_leverage,
        trailing_stop_atr_mult=2.5,
        allow_pyramid=True,
        max_pyramid_count=2,
        time_decay_bars=None,
        selection_reason="Clean uptrend",
        reason_codes=["PLAYBOOK_BULL_CONTINUATION"],
    )


class SizingEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SizingEngine()

    def test_base_size_uses_realized_vol_floor(self) -> None:
        self.assertAlmostEqual(
            self.engine._base_size(equity=100_000.0, realized_vol=0.0, target_vol=0.6),
            4.0,
        )
        self.assertAlmostEqual(
            self.engine._base_size(equity=100_000.0, realized_vol=0.6, target_vol=0.6),
            1.0,
        )
        self.assertAlmostEqual(
            self.engine._base_size(equity=100_000.0, realized_vol=1.2, target_vol=0.6),
            0.5,
        )

    def test_confidence_multiplier_blends_state_and_playbook(self) -> None:
        state = _state(
            directional_confidence=0.81,
            volatility_confidence=0.64,
            positioning_confidence=0.81,
            quality_confidence=1.0,
        )
        playbook = _playbook(confidence=0.9)
        expected_state_conf = state.overall_confidence()
        self.assertAlmostEqual(
            self.engine._confidence_multiplier(state, playbook),
            (expected_state_conf + 0.9) / 2.0,
        )

    def test_stress_discount_combines_crowding_volatility_and_quality(self) -> None:
        state = _state(volatility_state="HIGH", quality_state="DEGRADED")
        discount = self.engine._stress_discount(
            state=state,
            indicators={"realized_vol": 1.1, "funding_oi_z": 2.0, "target_vol": 0.6},
        )
        self.assertLess(discount, 1.0)
        self.assertGreater(discount, 0.0)

    def test_execution_haircut_follows_quality_state(self) -> None:
        self.assertEqual(self.engine._execution_haircut(_state(quality_state="TRADABLE")), 1.0)
        self.assertEqual(self.engine._execution_haircut(_state(quality_state="NOISY")), 0.9)
        self.assertEqual(self.engine._execution_haircut(_state(quality_state="DEGRADED")), 0.7)
        self.assertEqual(self.engine._execution_haircut(_state(quality_state="UNSAFE")), 0.0)

    def test_compute_caps_final_leverage_at_playbook_limit(self) -> None:
        decision = self.engine.compute(
            state=_state(),
            playbook=_playbook(max_leverage=1.0),
            equity=100_000.0,
            indicators={"realized_vol": 0.05, "funding_oi_z": 0.1},
        )
        self.assertAlmostEqual(decision.final_leverage, 1.0)
        self.assertIn("SIZE_MAX_LEVERAGE_CAP", decision.reason_codes)

    def test_compute_applies_execution_haircut_and_quality_reason(self) -> None:
        decision = self.engine.compute(
            state=_state(quality_state="NOISY"),
            playbook=_playbook(),
            equity=100_000.0,
            indicators={"realized_vol": 0.4, "funding_oi_z": 2.1},
        )
        self.assertLess(decision.execution_haircut, 1.0)
        self.assertIn("CROWDING_DISCOUNT", decision.reason_codes)
        self.assertIn("QUALITY_DEGRADED", decision.reason_codes)


if __name__ == "__main__":
    unittest.main()
