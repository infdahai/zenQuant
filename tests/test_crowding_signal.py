"""Unit tests for crowding_signal plugin — contrarian positioning signal."""

import unittest


class CrowdingSignalTests(unittest.TestCase):
    """Verify crowding detection with contrarian direction."""

    def _compute(self, indicators, state=None, config=None):
        from zenquant.plugins.signals import crowding_signal

        state = state or {"regime": "TREND"}
        config = config or {"extreme_threshold": 1.8}
        return crowding_signal.compute(indicators, state, config)

    # ── Regime filtering ──

    def test_stress_regime_neutral(self) -> None:
        """STRESS → neutral."""
        result = self._compute({"funding_oi_z": 3.0}, state={"regime": "STRESS"})
        self.assertEqual(result.value, 0.0)

    def test_range_regime_active(self) -> None:
        """RANGE → signal fires (not STRESS-only filter)."""
        result = self._compute({"funding_oi_z": 3.0}, state={"regime": "RANGE"})
        self.assertNotEqual(result.value, 0.0)

    # ── None / missing key handling ──

    def test_none_funding_neutral(self) -> None:
        """funding_oi_z=None → 0.0 → below threshold → neutral."""
        result = self._compute({"funding_oi_z": None})
        self.assertEqual(result.value, 0.0)

    def test_missing_key_neutral(self) -> None:
        """Missing key → neutral."""
        result = self._compute({})
        self.assertEqual(result.value, 0.0)

    # ── Boundary tests ──

    def test_boundary_below_threshold(self) -> None:
        """abs(funding_oi_z) = 1.7 → below 1.8 → neutral."""
        result = self._compute({"funding_oi_z": 1.7})
        self.assertEqual(result.value, 0.0)

    def test_boundary_at_exact_threshold(self) -> None:
        """abs(funding_oi_z) = 1.8 exactly → NOT crowded (uses <, so 1.8 is NOT < 1.8)."""
        result = self._compute({"funding_oi_z": 1.8})
        self.assertNotEqual(result.value, 0.0)

    def test_boundary_negative_at_threshold(self) -> None:
        """abs(funding_oi_z) = -1.8 → abs is 1.8 → triggers."""
        result = self._compute({"funding_oi_z": -1.8})
        self.assertNotEqual(result.value, 0.0)

    # ── Direction tests (contrarian) ──

    def test_crowded_longs_bearish(self) -> None:
        """Positive crowding → bearish contrarian."""
        result = self._compute({"funding_oi_z": 3.0})
        self.assertEqual(result.value, -1.0)
        self.assertIn("CROWDING_LONG", result.reason_codes)

    def test_crowded_shorts_bullish(self) -> None:
        """Negative crowding → bullish contrarian."""
        result = self._compute({"funding_oi_z": -3.0})
        self.assertEqual(result.value, 1.0)
        self.assertIn("CROWDING_SHORT", result.reason_codes)

    # ── Confidence scaling ──

    def test_confidence_scales_with_magnitude(self) -> None:
        """Higher abs(funding_oi_z) → higher confidence."""
        small = self._compute({"funding_oi_z": 2.0})
        large = self._compute({"funding_oi_z": 3.5})
        self.assertGreater(large.confidence, small.confidence)

    def test_confidence_capped_at_1(self) -> None:
        """Extreme crowding → confidence capped at 1.0."""
        result = self._compute({"funding_oi_z": 100.0})
        self.assertLessEqual(result.confidence, 1.0)

    # ── Config override ──

    def test_custom_threshold(self) -> None:
        """Custom threshold respected."""
        config = {"extreme_threshold": 3.0}
        # 2.5 < 3.0 → not crowded
        result = self._compute({"funding_oi_z": 2.5}, config=config)
        self.assertEqual(result.value, 0.0)

        # 3.5 ≥ 3.0 → crowded
        result = self._compute({"funding_oi_z": 3.5}, config=config)
        self.assertNotEqual(result.value, 0.0)

    # ── Difference from funding_alpha_signal ──

    def test_symmetric_threshold(self) -> None:
        """Unlike funding_alpha (1.8/-2.2), crowding uses SYMMETRIC threshold."""
        pos = self._compute({"funding_oi_z": 2.0})
        neg = self._compute({"funding_oi_z": -2.0})
        # Both should trigger (symmetric at 1.8)
        self.assertNotEqual(pos.value, 0.0)
        self.assertNotEqual(neg.value, 0.0)


if __name__ == "__main__":
    unittest.main()
