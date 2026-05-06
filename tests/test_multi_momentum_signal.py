"""Unit tests for multi_momentum_signal plugin — primary trend signal (weight=0.45)."""

import unittest


class MultiMomentumSignalTests(unittest.TestCase):
    """Verify multi-horizon momentum with structure confirmation."""

    def _compute(self, indicators, state=None, config=None):
        from zenquant.plugins.signals import multi_momentum_signal

        state = state or {"regime": "TREND"}
        config = config or {"momentum_threshold": 0.3, "signal_sign": 1.0, "use_single_fallback": True}
        return multi_momentum_signal.compute(indicators, state, config)

    # ── Regime filtering ──

    def test_stress_regime_neutral(self) -> None:
        """STRESS regime → neutral regardless of momentum."""
        result = self._compute(
            {"multi_momentum_z": 2.0, "structure_state": "UPTREND"},
            state={"regime": "STRESS"},
        )
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.confidence, 0.0)

    def test_range_regime_no_strong_signal(self) -> None:
        """RANGE regime → falls through to default (TREND gate blocks strong signals)."""
        result = self._compute(
            {"multi_momentum_z": 1.0, "structure_state": "UPTREND"},
            state={"regime": "RANGE"},
        )
        self.assertEqual(result.value, 0.0)

    def test_range_profit_run_is_opt_in(self) -> None:
        """Research profiles may opt into long profit-run intent inside RANGE."""
        result = self._compute(
            {"multi_momentum_z": 1.0, "structure_state": "UPTREND"},
            state={"regime": "RANGE"},
            config={
                "momentum_threshold": 0.3,
                "signal_sign": 1.0,
                "use_single_fallback": True,
                "range_profit_run_enabled": True,
                "range_profit_run_threshold": 0.2,
                "range_profit_run_value": 0.85,
                "range_profit_run_confidence": 0.65,
            },
        )

        self.assertEqual(result.value, 0.85)
        self.assertGreaterEqual(result.confidence, 0.65)
        self.assertIn("MULTI_MOM_RANGE_BULL_PROFIT_RUN", result.reason_codes)

    def test_range_profit_run_guard_blocks_unsafe_context(self) -> None:
        """Research profit-run intent must not fire in noisy bull-like windows."""
        result = self._compute(
            {"multi_momentum_z": 1.0, "structure_state": "UPTREND", "cycle_position": 0.86},
            state={
                "regime": "RANGE",
                "transition_risk": 0.62,
                "volatility_state": "DISTORTED",
                "quality_state": "TRADABLE",
            },
            config={
                "momentum_threshold": 0.3,
                "signal_sign": 1.0,
                "use_single_fallback": True,
                "range_profit_run_enabled": True,
                "range_profit_run_threshold": 0.2,
                "range_profit_run_value": 0.85,
                "range_profit_run_confidence": 0.65,
                "range_profit_run_max_transition_risk": 0.45,
                "range_profit_run_min_cycle_position": 0.95,
                "range_profit_run_block_distorted_volatility": True,
                "range_profit_run_block_unsafe_quality": True,
            },
        )

        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("MULTI_MOM_RANGE_BULL_PROFIT_RUN_GUARDED_TRANSITION_RISK", result.reason_codes)
        self.assertIn("MULTI_MOM_RANGE_BULL_PROFIT_RUN_GUARDED_CYCLE_POSITION", result.reason_codes)
        self.assertIn("MULTI_MOM_RANGE_BULL_PROFIT_RUN_GUARDED_VOLATILITY_STATE", result.reason_codes)

    def test_range_profit_run_cycle_fallback_is_opt_in(self) -> None:
        """Cycle context can provide profit-run intent when structure lags."""
        result = self._compute(
            {"multi_momentum_z": 0.25, "structure_state": "NEUTRAL", "cycle_position": 1.08},
            state={
                "regime": "RANGE",
                "transition_risk": 0.30,
                "directional_state": "TRANSITION",
                "directional_confidence": 0.50,
            },
            config={
                "momentum_threshold": 0.3,
                "signal_sign": 1.0,
                "use_single_fallback": True,
                "range_profit_run_enabled": True,
                "range_profit_run_threshold": 0.3,
                "range_profit_run_value": 0.75,
                "range_profit_run_confidence": 0.55,
                "range_profit_run_max_transition_risk": 0.45,
                "range_profit_run_cycle_fallback_enabled": True,
                "range_profit_run_cycle_threshold": 1.0,
                "range_profit_run_cycle_momentum_threshold": 0.10,
                "range_profit_run_block_confirmed_downtrend": True,
            },
        )

        self.assertEqual(result.value, 0.75)
        self.assertIn("CYCLE_PROFIT_RUN", result.reason_codes)

    def test_range_profit_run_cycle_fallback_blocks_confirmed_downtrend(self) -> None:
        """Cycle fallback must respect confirmed state downtrends."""
        result = self._compute(
            {"multi_momentum_z": 0.25, "structure_state": "NEUTRAL", "cycle_position": 1.08},
            state={
                "regime": "RANGE",
                "transition_risk": 0.30,
                "directional_state": "DOWNTREND",
                "directional_confidence": 0.65,
            },
            config={
                "momentum_threshold": 0.3,
                "signal_sign": 1.0,
                "use_single_fallback": True,
                "range_profit_run_enabled": True,
                "range_profit_run_threshold": 0.3,
                "range_profit_run_value": 0.75,
                "range_profit_run_confidence": 0.55,
                "range_profit_run_max_transition_risk": 0.45,
                "range_profit_run_cycle_fallback_enabled": True,
                "range_profit_run_cycle_threshold": 1.0,
                "range_profit_run_cycle_momentum_threshold": 0.10,
                "range_profit_run_block_confirmed_downtrend": True,
            },
        )

        self.assertEqual(result.value, 0.0)
        self.assertIn("MULTI_MOM_RANGE_BULL_PROFIT_RUN_GUARDED_CONFIRMED_DOWNTREND", result.reason_codes)

    def test_range_profit_run_cycle_fallback_blocks_bearish_state_signal(self) -> None:
        """Cycle fallback must not fight a negative state direction signal."""
        result = self._compute(
            {"multi_momentum_z": 0.25, "structure_state": "NEUTRAL", "cycle_position": 1.08},
            state={
                "regime": "RANGE",
                "transition_risk": 0.30,
                "directional_state": "TRANSITION",
                "directional_confidence": 0.50,
                "direction_signal": -0.55,
            },
            config={
                "momentum_threshold": 0.3,
                "signal_sign": 1.0,
                "use_single_fallback": True,
                "range_profit_run_enabled": True,
                "range_profit_run_threshold": 0.3,
                "range_profit_run_value": 0.75,
                "range_profit_run_confidence": 0.55,
                "range_profit_run_max_transition_risk": 0.45,
                "range_profit_run_cycle_fallback_enabled": True,
                "range_profit_run_cycle_threshold": 1.0,
                "range_profit_run_cycle_momentum_threshold": 0.10,
                "range_profit_run_min_state_direction_signal": -0.25,
            },
        )

        self.assertEqual(result.value, 0.0)
        self.assertIn("MULTI_MOM_RANGE_BULL_PROFIT_RUN_GUARDED_STATE_DIRECTION_SIGNAL", result.reason_codes)

    # ── None / missing key handling ──

    def test_none_momentum_with_fallback(self) -> None:
        """multi_momentum_z=None → falls back to momentum_roc_z."""
        result = self._compute(
            {
                "momentum_roc_z": 1.0,
                "structure_state": "UPTREND",
            }
        )
        self.assertGreater(result.value, 0.0)

    def test_none_momentum_no_fallback(self) -> None:
        """multi_momentum_z=None + use_single_fallback=False → neutral."""
        result = self._compute(
            {"momentum_roc_z": 1.0, "structure_state": "UPTREND"},
            config={"momentum_threshold": 0.3, "signal_sign": 1.0, "use_single_fallback": False},
        )
        self.assertEqual(result.value, 0.0)

    def test_both_missing_neutral(self) -> None:
        """Both momentum keys missing → neutral."""
        result = self._compute({"structure_state": "UPTREND"})
        self.assertEqual(result.value, 0.0)

    def test_missing_structure_defaults_neutral(self) -> None:
        """Missing structure_state defaults to NEUTRAL → won't match UPTREND/DOWNTREND."""
        result = self._compute({"multi_momentum_z": 1.0})
        # No structure match → only weak signal if abs > 2*threshold
        self.assertLessEqual(abs(result.value), 0.6)

    # ── Boundary tests ──

    def test_boundary_at_exact_threshold(self) -> None:
        """roc_z = 0.3 exactly → should NOT trigger (uses >)."""
        result = self._compute(
            {
                "multi_momentum_z": 0.3,
                "structure_state": "UPTREND",
            }
        )
        self.assertEqual(result.value, 0.0)

    def test_boundary_just_above_threshold(self) -> None:
        """roc_z = 0.301 + UPTREND → bullish."""
        result = self._compute(
            {
                "multi_momentum_z": 0.301,
                "structure_state": "UPTREND",
            }
        )
        self.assertGreater(result.value, 0.0)
        self.assertIn("MULTI_MOM_BULL", result.reason_codes)

    def test_boundary_negative_at_threshold(self) -> None:
        """roc_z = -0.3 exactly → should NOT trigger."""
        result = self._compute(
            {
                "multi_momentum_z": -0.3,
                "structure_state": "DOWNTREND",
            }
        )
        self.assertEqual(result.value, 0.0)

    def test_boundary_negative_just_below(self) -> None:
        """roc_z = -0.301 + DOWNTREND → bearish."""
        result = self._compute(
            {
                "multi_momentum_z": -0.301,
                "structure_state": "DOWNTREND",
            }
        )
        self.assertLess(result.value, 0.0)
        self.assertIn("MULTI_MOM_BEAR", result.reason_codes)

    # ── Happy path: strong signals ──

    def test_uptrend_bullish(self) -> None:
        """TREND + UPTREND + roc_z > threshold → strong bullish (value=1.0)."""
        result = self._compute(
            {
                "multi_momentum_z": 1.5,
                "structure_state": "UPTREND",
            }
        )
        self.assertEqual(result.value, 1.0)
        self.assertIn("MULTI_MOM_BULL", result.reason_codes)
        self.assertIn("STRUCTURE_UPTREND", result.reason_codes)

    def test_downtrend_bearish(self) -> None:
        """TREND + DOWNTREND + roc_z < -threshold → strong bearish (value=-1.0)."""
        result = self._compute(
            {
                "multi_momentum_z": -1.5,
                "structure_state": "DOWNTREND",
            }
        )
        self.assertEqual(result.value, -1.0)
        self.assertIn("MULTI_MOM_BEAR", result.reason_codes)
        self.assertIn("STRUCTURE_DOWNTREND", result.reason_codes)

    # ── Weak signal (no structure match) ──

    def test_weak_signal_strong_momentum_no_structure(self) -> None:
        """abs(roc_z) > 2*threshold but wrong structure → weak signal (0.6)."""
        result = self._compute(
            {
                "multi_momentum_z": 1.0,  # > 0.6 = 2*0.3
                "structure_state": "NEUTRAL",
            }
        )
        self.assertAlmostEqual(abs(result.value), 0.6, places=5)
        self.assertIn("MULTI_MOM_WEAK", result.reason_codes)

    def test_weak_boundary_at_2x_threshold(self) -> None:
        """abs(roc_z) = 0.6 exactly → should NOT trigger weak (uses >)."""
        result = self._compute(
            {
                "multi_momentum_z": 0.6,
                "structure_state": "NEUTRAL",
            }
        )
        self.assertEqual(result.value, 0.0)

    # ── Confidence scaling ──

    def test_confidence_scales_with_magnitude(self) -> None:
        """Higher momentum → higher confidence."""
        low = self._compute({"multi_momentum_z": 0.5, "structure_state": "UPTREND"})
        high = self._compute({"multi_momentum_z": 3.0, "structure_state": "UPTREND"})
        self.assertGreater(high.confidence, low.confidence)

    def test_confidence_capped_at_1(self) -> None:
        """Extreme momentum → confidence capped at 1.0."""
        result = self._compute({"multi_momentum_z": 100.0, "structure_state": "UPTREND"})
        self.assertLessEqual(result.confidence, 1.0)

    # ── Signal sign config ──

    def test_signal_sign_flips_direction(self) -> None:
        """signal_sign=-1.0 flips all values."""
        result = self._compute(
            {"multi_momentum_z": 1.5, "structure_state": "UPTREND"},
            config={"momentum_threshold": 0.3, "signal_sign": -1.0, "use_single_fallback": True},
        )
        self.assertEqual(result.value, -1.0)  # Flipped from +1.0


if __name__ == "__main__":
    unittest.main()
