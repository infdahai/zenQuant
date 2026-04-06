"""Unit tests for funding_alpha_signal plugin — asymmetric contrarian funding."""

import unittest


class FundingAlphaSignalTests(unittest.TestCase):
    """Verify contrarian funding signal with asymmetric thresholds."""

    def _compute(self, indicators, state=None, config=None):
        from zenquant.plugins.signals import funding_alpha_signal

        state = state or {"regime": "TREND"}
        config = config or {"high_funding_z": 1.8, "low_funding_z": -2.2}
        return funding_alpha_signal.compute(indicators, state, config)

    # ── Failure / neutral cases first (anti-happy-path-bias) ──

    def test_stress_regime_suppresses_signal(self) -> None:
        """STRESS regime → always neutral, regardless of extreme funding."""
        result = self._compute(
            {"funding_oi_z": 5.0},
            state={"regime": "STRESS"},
        )
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.confidence, 0.0)

    def test_neutral_zone_no_signal(self) -> None:
        """Funding within [-2.2, 1.8] → neutral."""
        result = self._compute({"funding_oi_z": 1.0})
        self.assertEqual(result.value, 0.0)

    def test_none_funding_coerces_to_zero(self) -> None:
        """None indicator value → coerces to 0.0 → neutral."""
        result = self._compute({"funding_oi_z": None})
        self.assertEqual(result.value, 0.0)

    def test_missing_key_defaults_neutral(self) -> None:
        """Missing funding_oi_z key → defaults to 0.0 → neutral."""
        result = self._compute({})
        self.assertEqual(result.value, 0.0)

    # ── Boundary tests (exact threshold behavior) ──

    def test_boundary_high_at_exact_threshold(self) -> None:
        """funding_oi_z = 1.8 exactly → should NOT trigger (uses >)."""
        result = self._compute({"funding_oi_z": 1.8})
        self.assertEqual(result.value, 0.0)

    def test_boundary_high_just_above(self) -> None:
        """funding_oi_z = 1.801 → should trigger bearish."""
        result = self._compute({"funding_oi_z": 1.801})
        self.assertLess(result.value, 0.0)
        self.assertIn("FUNDING_LONGS_CROWDED", result.reason_codes)

    def test_boundary_low_at_exact_threshold(self) -> None:
        """funding_oi_z = -2.2 exactly → should NOT trigger (uses <)."""
        result = self._compute({"funding_oi_z": -2.2})
        self.assertEqual(result.value, 0.0)

    def test_boundary_low_just_below(self) -> None:
        """funding_oi_z = -2.201 → should trigger bullish."""
        result = self._compute({"funding_oi_z": -2.201})
        self.assertGreater(result.value, 0.0)
        self.assertIn("FUNDING_SHORTS_CROWDED", result.reason_codes)

    # ── Happy path: directional signals ──

    def test_crowded_longs_bearish(self) -> None:
        """High funding (z=3.0) → bearish contrarian signal."""
        result = self._compute({"funding_oi_z": 3.0})
        self.assertLess(result.value, 0.0)
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("FUNDING_LONGS_CROWDED", result.reason_codes)

    def test_crowded_shorts_bullish(self) -> None:
        """Low funding (z=-3.0) → bullish contrarian signal."""
        result = self._compute({"funding_oi_z": -3.0})
        self.assertGreater(result.value, 0.0)
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("FUNDING_SHORTS_CROWDED", result.reason_codes)

    # ── Asymmetry verification ──

    def test_asymmetric_thresholds(self) -> None:
        """High threshold (1.8) is tighter than low (-2.2) — longs crowded sooner."""
        # 2.0 triggers high (above 1.8) but NOT low (above -2.2)
        high_result = self._compute({"funding_oi_z": 2.0})
        low_result = self._compute({"funding_oi_z": -2.0})

        self.assertLess(high_result.value, 0.0, "z=2.0 should trigger crowded longs")
        self.assertEqual(low_result.value, 0.0, "z=-2.0 should NOT trigger crowded shorts (threshold is -2.2)")

    # ── Cap / saturation tests ──

    def test_magnitude_capped_at_0_8(self) -> None:
        """Extreme funding → magnitude capped at 0.8."""
        result = self._compute({"funding_oi_z": 100.0})
        self.assertGreaterEqual(result.value, -0.8)
        self.assertLess(result.value, 0.0)

    def test_confidence_capped_at_0_8(self) -> None:
        """Extreme funding → confidence capped at 0.8."""
        result = self._compute({"funding_oi_z": 100.0})
        self.assertLessEqual(result.confidence, 0.8)

    # ── Config override ──

    def test_custom_thresholds_respected(self) -> None:
        """Custom config thresholds override defaults."""
        config = {"high_funding_z": 3.0, "low_funding_z": -3.0}
        # z=2.5 should NOT trigger with high_funding_z=3.0
        result = self._compute({"funding_oi_z": 2.5}, config=config)
        self.assertEqual(result.value, 0.0)

        # z=3.5 SHOULD trigger
        result = self._compute({"funding_oi_z": 3.5}, config=config)
        self.assertLess(result.value, 0.0)


if __name__ == "__main__":
    unittest.main()
