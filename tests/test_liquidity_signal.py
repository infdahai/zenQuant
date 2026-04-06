"""Unit tests for liquidity_signal plugin — order book mean-reversion."""

import unittest


class LiquiditySignalTests(unittest.TestCase):
    """Verify liquidity mean-reversion with CVD calm + OB extreme conditions."""

    def _compute(self, indicators, state=None, config=None):
        from zenquant.plugins.signals import liquidity_signal

        state = state or {"regime": "RANGE"}
        config = config or {"cvd_calm_threshold": 0.03, "ob_extreme_threshold": 1.2}
        return liquidity_signal.compute(indicators, state, config)

    # ── Regime filtering ──

    def test_stress_regime_neutral(self) -> None:
        """STRESS → neutral."""
        result = self._compute(
            {"cvd_slope": 0.0, "ob_liq_z": 2.0},
            state={"regime": "STRESS"},
        )
        self.assertEqual(result.value, 0.0)

    def test_trend_regime_reduced_confidence(self) -> None:
        """TREND → signal fires but confidence * 0.4."""
        range_result = self._compute(
            {"cvd_slope": 0.0, "ob_liq_z": 2.0},
            state={"regime": "RANGE"},
        )
        trend_result = self._compute(
            {"cvd_slope": 0.0, "ob_liq_z": 2.0},
            state={"regime": "TREND"},
        )
        self.assertAlmostEqual(trend_result.confidence, range_result.confidence * 0.4, places=5)

    # ── Condition failures ──

    def test_cvd_not_calm_neutral(self) -> None:
        """CVD slope too large (>0.03) → neutral even with extreme OB."""
        result = self._compute({"cvd_slope": 0.05, "ob_liq_z": 2.0})
        self.assertEqual(result.value, 0.0)

    def test_ob_not_extreme_neutral(self) -> None:
        """OB z-score too small (<1.2) → neutral even with calm CVD."""
        result = self._compute({"cvd_slope": 0.0, "ob_liq_z": 1.0})
        self.assertEqual(result.value, 0.0)

    def test_both_conditions_fail_neutral(self) -> None:
        """Both conditions fail → neutral."""
        result = self._compute({"cvd_slope": 0.1, "ob_liq_z": 0.5})
        self.assertEqual(result.value, 0.0)

    # ── None / missing key handling ──

    def test_none_indicators_neutral(self) -> None:
        """None values → coerce to 0.0 → neutral."""
        result = self._compute({"cvd_slope": None, "ob_liq_z": None})
        self.assertEqual(result.value, 0.0)

    def test_missing_keys_neutral(self) -> None:
        """Missing keys → neutral."""
        result = self._compute({})
        self.assertEqual(result.value, 0.0)

    # ── Boundary tests ──

    def test_boundary_cvd_at_threshold(self) -> None:
        """abs(cvd_slope) = 0.03 exactly → NOT calm (uses <, not <=)."""
        result = self._compute({"cvd_slope": 0.03, "ob_liq_z": 2.0})
        self.assertEqual(result.value, 0.0)

    def test_boundary_cvd_just_below(self) -> None:
        """abs(cvd_slope) = 0.029 → calm."""
        result = self._compute({"cvd_slope": 0.029, "ob_liq_z": 2.0})
        self.assertNotEqual(result.value, 0.0)

    def test_boundary_ob_at_threshold(self) -> None:
        """abs(ob_liq_z) = 1.2 exactly → NOT extreme (uses >, not >=)."""
        result = self._compute({"cvd_slope": 0.0, "ob_liq_z": 1.2})
        self.assertEqual(result.value, 0.0)

    def test_boundary_ob_just_above(self) -> None:
        """abs(ob_liq_z) = 1.201 → extreme."""
        result = self._compute({"cvd_slope": 0.0, "ob_liq_z": 1.201})
        self.assertNotEqual(result.value, 0.0)

    # ── Direction tests (contrarian) ──

    def test_positive_ob_bearish(self) -> None:
        """ob_liq_z > 0 (bullish pressure) → bearish mean-reversion (-1.0)."""
        result = self._compute({"cvd_slope": 0.0, "ob_liq_z": 2.0})
        self.assertEqual(result.value, -1.0)
        self.assertIn("LIQUIDITY_MEAN_REVERSION", result.reason_codes)

    def test_negative_ob_bullish(self) -> None:
        """ob_liq_z < 0 (bearish pressure) → bullish mean-reversion (+1.0)."""
        result = self._compute({"cvd_slope": 0.0, "ob_liq_z": -2.0})
        self.assertEqual(result.value, 1.0)
        self.assertIn("LIQUIDITY_MEAN_REVERSION", result.reason_codes)

    # ── Confidence scaling ──

    def test_confidence_scales_with_ob(self) -> None:
        """Larger ob_liq_z → higher confidence."""
        small = self._compute({"cvd_slope": 0.0, "ob_liq_z": 1.5})
        large = self._compute({"cvd_slope": 0.0, "ob_liq_z": 3.0})
        self.assertGreater(large.confidence, small.confidence)

    def test_confidence_capped_at_1(self) -> None:
        """Extreme ob → confidence capped at 1.0."""
        result = self._compute({"cvd_slope": 0.0, "ob_liq_z": 100.0})
        self.assertLessEqual(result.confidence, 1.0)

    # ── Config override ──

    def test_custom_thresholds(self) -> None:
        """Custom thresholds override defaults."""
        config = {"cvd_calm_threshold": 0.01, "ob_extreme_threshold": 2.0}
        # cvd=0.02 > 0.01 → not calm
        result = self._compute({"cvd_slope": 0.02, "ob_liq_z": 3.0}, config=config)
        self.assertEqual(result.value, 0.0)


if __name__ == "__main__":
    unittest.main()
