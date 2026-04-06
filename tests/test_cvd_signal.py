"""Unit tests for cvd_signal plugin — Cumulative Volume Delta direction."""

import unittest


class CVDSignalTests(unittest.TestCase):
    """Verify CVD slope direction signal (TREND-only, binary)."""

    def _compute(self, indicators, state=None, config=None):
        from zenquant.plugins.signals import cvd_signal

        state = state or {"regime": "TREND"}
        config = config or {}
        return cvd_signal.compute(indicators, state, config)

    # ── Regime filtering ──

    def test_stress_regime_neutral(self) -> None:
        """STRESS → neutral."""
        result = self._compute({"cvd_slope": 0.5}, state={"regime": "STRESS"})
        self.assertEqual(result.value, 0.0)

    def test_range_regime_neutral(self) -> None:
        """RANGE → neutral (TREND-only signal)."""
        result = self._compute({"cvd_slope": 0.5}, state={"regime": "RANGE"})
        self.assertEqual(result.value, 0.0)

    # ── None / missing key handling ──

    def test_none_cvd_slope_neutral(self) -> None:
        """cvd_slope=None → coerces to 0.0 → neutral."""
        result = self._compute({"cvd_slope": None})
        self.assertEqual(result.value, 0.0)

    def test_missing_key_neutral(self) -> None:
        """Missing cvd_slope → defaults to 0.0 → neutral."""
        result = self._compute({})
        self.assertEqual(result.value, 0.0)

    # ── Boundary tests ──

    def test_boundary_exactly_zero(self) -> None:
        """cvd_slope = 0.0 exactly → neutral (neither > nor <)."""
        result = self._compute({"cvd_slope": 0.0})
        self.assertEqual(result.value, 0.0)
        self.assertAlmostEqual(result.confidence, 0.2)

    def test_boundary_tiny_positive(self) -> None:
        """cvd_slope = 0.0001 → bullish (any positive triggers)."""
        result = self._compute({"cvd_slope": 0.0001})
        self.assertEqual(result.value, 1.0)
        self.assertIn("CVD_POSITIVE", result.reason_codes)

    def test_boundary_tiny_negative(self) -> None:
        """cvd_slope = -0.0001 → bearish (any negative triggers)."""
        result = self._compute({"cvd_slope": -0.0001})
        self.assertEqual(result.value, -1.0)
        self.assertIn("CVD_NEGATIVE", result.reason_codes)

    # ── Happy path ──

    def test_positive_slope_bullish(self) -> None:
        """Positive CVD slope → bullish with fixed confidence 0.5."""
        result = self._compute({"cvd_slope": 0.5})
        self.assertEqual(result.value, 1.0)
        self.assertEqual(result.confidence, 0.5)
        self.assertIn("CVD_POSITIVE", result.reason_codes)

    def test_negative_slope_bearish(self) -> None:
        """Negative CVD slope → bearish with fixed confidence 0.5."""
        result = self._compute({"cvd_slope": -0.5})
        self.assertEqual(result.value, -1.0)
        self.assertEqual(result.confidence, 0.5)
        self.assertIn("CVD_NEGATIVE", result.reason_codes)

    # ── No magnitude scaling ──

    def test_confidence_fixed_regardless_of_magnitude(self) -> None:
        """Confidence is always 0.5 regardless of slope magnitude."""
        small = self._compute({"cvd_slope": 0.001})
        large = self._compute({"cvd_slope": 100.0})
        self.assertEqual(small.confidence, large.confidence)
        self.assertEqual(small.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
