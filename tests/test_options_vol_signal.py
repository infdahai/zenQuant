"""Unit tests for options_vol_signal plugin."""

import unittest


class OptionsVolSignalTests(unittest.TestCase):
    def test_low_iv_bullish_signal(self) -> None:
        """Test that low IV produces bullish signal."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": -1.0, "skew_z": 0.0}  # Low IV
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertGreater(result.value, 0.0)  # Bullish
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("OPTIONS_LOW_IV", result.reason_codes)

    def test_high_iv_bearish_signal(self) -> None:
        """Test that high IV produces bearish signal."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 2.0, "skew_z": 0.0}  # High IV
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertLess(result.value, 0.0)  # Bearish
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("OPTIONS_HIGH_IV", result.reason_codes)

    def test_extreme_iv_strong_bearish(self) -> None:
        """Test that extreme IV produces strong bearish signal."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 3.0, "skew_z": 0.0}  # Extreme IV
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertLessEqual(result.value, -0.8)  # Strong bearish
        self.assertIn("OPTIONS_DISTORTED", result.reason_codes)

    def test_inverted_skew_amplification(self) -> None:
        """Test that inverted skew amplifies bearish signal."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 2.0, "skew_z": -1.5}  # High IV + inverted skew
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertLess(result.value, 0.0)  # Bearish
        self.assertIn("OPTIONS_INVERTED_SKEW", result.reason_codes)

    def test_stress_regime_suppression(self) -> None:
        """Test that STRESS regime suppresses signal."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": -1.0, "skew_z": 0.0}  # Would be bullish normally
        state = {"regime": "STRESS"}  # But STRESS regime
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertEqual(result.value, 0.0)  # Neutral
        self.assertEqual(result.confidence, 0.0)

    def test_mid_range_neutral(self) -> None:
        """Test that mid-range IV produces neutral signal."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 0.5, "skew_z": 0.0}  # Mid-range IV
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertEqual(result.value, 0.0)  # Neutral
        self.assertGreater(result.confidence, 0.0)  # But some confidence

    def test_no_options_data_neutral(self) -> None:
        """Test that missing options data produces neutral signal."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 0.0, "skew_z": 0.0}  # No data (z=0)
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertEqual(result.value, 0.0)  # Neutral
        self.assertEqual(result.confidence, 0.0)  # No confidence

    def test_reason_codes(self) -> None:
        """Test that reason codes are correctly set."""
        from zenquant.plugins.signals import options_vol_signal

        # Test LOW_IV
        result1 = options_vol_signal.compute(
            {"atm_iv_z": -1.0, "skew_z": 0.0},
            {"regime": "TREND"},
            {
                "low_iv_threshold": -0.5,
                "high_iv_threshold": 1.5,
                "extreme_iv_threshold": 2.5,
                "inverted_skew_threshold": -1.0,
            },
        )
        self.assertEqual(result1.reason_codes, ("OPTIONS_LOW_IV",))

        # Test HIGH_IV
        result2 = options_vol_signal.compute(
            {"atm_iv_z": 2.0, "skew_z": 0.0},
            {"regime": "TREND"},
            {
                "low_iv_threshold": -0.5,
                "high_iv_threshold": 1.5,
                "extreme_iv_threshold": 2.5,
                "inverted_skew_threshold": -1.0,
            },
        )
        self.assertIn("OPTIONS_HIGH_IV", result2.reason_codes)

        # Test DISTORTED
        result3 = options_vol_signal.compute(
            {"atm_iv_z": 3.0, "skew_z": 0.0},
            {"regime": "TREND"},
            {
                "low_iv_threshold": -0.5,
                "high_iv_threshold": 1.5,
                "extreme_iv_threshold": 2.5,
                "inverted_skew_threshold": -1.0,
            },
        )
        self.assertIn("OPTIONS_DISTORTED", result3.reason_codes)


class OptionsVolSignalBoundaryTests(unittest.TestCase):
    """Boundary tests to eliminate happy path bias.

    Tests exact thresholds, ±epsilon, and None handling.
    Happy path tests use values safely away from boundaries (-1.0, 2.0, 3.0).
    Boundary tests verify off-by-one logic (< vs <=, > vs >=).
    """

    def test_exact_low_iv_threshold_no_trigger(self) -> None:
        """atm_iv_z at exact -0.5 should NOT trigger bullish (logic uses <)."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": -0.5, "skew_z": 0.0}
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        # At exact threshold, should NOT trigger (uses <, not <=)
        self.assertEqual(result.value, 0.0)  # Neutral (mid-range)
        self.assertNotIn("OPTIONS_LOW_IV", result.reason_codes or ())

    def test_just_below_low_iv_threshold_triggers(self) -> None:
        """atm_iv_z just below -0.5 should trigger bullish."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": -0.500001, "skew_z": 0.0}
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertGreater(result.value, 0.0)  # Bullish
        self.assertIn("OPTIONS_LOW_IV", result.reason_codes)

    def test_exact_high_iv_threshold_no_trigger(self) -> None:
        """atm_iv_z at exact 1.5 should NOT trigger bearish (logic uses >)."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 1.5, "skew_z": 0.0}
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        # At exact threshold, should NOT trigger (uses >, not >=)
        self.assertEqual(result.value, 0.0)  # Neutral (mid-range)
        self.assertNotIn("OPTIONS_HIGH_IV", result.reason_codes or ())

    def test_just_above_high_iv_threshold_triggers(self) -> None:
        """atm_iv_z just above 1.5 should trigger bearish."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 1.500001, "skew_z": 0.0}
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertLess(result.value, 0.0)  # Bearish
        self.assertIn("OPTIONS_HIGH_IV", result.reason_codes)

    def test_exact_extreme_iv_threshold_no_distorted(self) -> None:
        """atm_iv_z at exact 2.5 should NOT trigger DISTORTED (logic uses >)."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 2.5, "skew_z": 0.0}
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        # Should be bearish (HIGH_IV) but not DISTORTED
        self.assertLess(result.value, 0.0)
        self.assertIn("OPTIONS_HIGH_IV", result.reason_codes)
        self.assertNotIn("OPTIONS_DISTORTED", result.reason_codes)

    def test_just_above_extreme_iv_threshold_distorted(self) -> None:
        """atm_iv_z just above 2.5 should trigger DISTORTED."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 2.500001, "skew_z": 0.0}
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertEqual(result.value, -1.0)  # Max bearish
        self.assertIn("OPTIONS_DISTORTED", result.reason_codes)

    def test_exact_inverted_skew_threshold_no_amplification(self) -> None:
        """skew_z at exact -1.0 should NOT amplify (logic uses <)."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 2.0, "skew_z": -1.0}  # High IV + exact skew
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        # Should be bearish but NOT amplified
        self.assertLess(result.value, 0.0)
        self.assertIn("OPTIONS_HIGH_IV", result.reason_codes)
        self.assertNotIn("OPTIONS_INVERTED_SKEW", result.reason_codes)

    def test_just_below_inverted_skew_threshold_amplifies(self) -> None:
        """skew_z just below -1.0 should amplify bearish."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 2.0, "skew_z": -1.000001}
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertLess(result.value, 0.0)  # Bearish
        self.assertIn("OPTIONS_INVERTED_SKEW", result.reason_codes)

    def test_none_atm_iv_coerced_to_zero(self) -> None:
        """None atm_iv_z should be coerced to 0.0 and return neutral."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": None, "skew_z": 0.0}
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        # None → 0.0 → neutral (no data)
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.confidence, 0.0)

    def test_none_skew_coerced_to_zero(self) -> None:
        """None skew_z should be coerced to 0.0."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": 2.0, "skew_z": None}  # High IV, no skew data
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        # Should still be bearish (high IV), skew None → 0.0 → no amplification
        self.assertLess(result.value, 0.0)
        self.assertIn("OPTIONS_HIGH_IV", result.reason_codes)
        self.assertNotIn("OPTIONS_INVERTED_SKEW", result.reason_codes)

    def test_both_none_neutral(self) -> None:
        """Both atm_iv_z=None and skew_z=None should return neutral."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {"atm_iv_z": None, "skew_z": None}
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.confidence, 0.0)

    def test_missing_indicators_dict_keys(self) -> None:
        """Missing dict keys should use get() defaults (0.0)."""
        from zenquant.plugins.signals import options_vol_signal

        indicators = {}  # No keys at all
        state = {"regime": "TREND"}
        config = {
            "low_iv_threshold": -0.5,
            "high_iv_threshold": 1.5,
            "extreme_iv_threshold": 2.5,
            "inverted_skew_threshold": -1.0,
        }

        result = options_vol_signal.compute(indicators, state, config)

        # Missing keys → 0.0 → neutral
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.confidence, 0.0)


class SkewVelocityTests(unittest.TestCase):
    """Verify P1: IV Skew Velocity (dSkew/dt) signal behavior.

    Skew velocity is checked when IV is in mid-range (not low/high/extreme).
    To reach this path: set atm_iv_z in [-0.5, 1.5] range, non-zero.
    """

    _MID_RANGE_CONFIG: dict[str, float] = {  # noqa: RUF012
        "low_iv_threshold": -0.5,
        "high_iv_threshold": 1.5,
        "extreme_iv_threshold": 2.5,
        "inverted_skew_threshold": -1.0,
    }

    def _compute(self, indicators):
        from zenquant.plugins.signals import options_vol_signal

        state = {"regime": "TREND"}
        return options_vol_signal.compute(indicators, state, self._MID_RANGE_CONFIG)

    def test_rapid_steepening_bearish(self) -> None:
        """skew_vel > 0.5 → bearish (puts getting expensive fast)."""
        result = self._compute({"atm_iv_z": 0.5, "skew_z": 0.0, "skew_velocity": 1.0})
        self.assertLess(result.value, 0.0)
        self.assertIn("OPTIONS_SKEW_STEEPENING", result.reason_codes)

    def test_rapid_flattening_bullish(self) -> None:
        """skew_vel < -0.5 → mildly bullish (fear subsiding)."""
        result = self._compute({"atm_iv_z": 0.5, "skew_z": 0.0, "skew_velocity": -1.0})
        self.assertGreater(result.value, 0.0)
        self.assertIn("OPTIONS_SKEW_FLATTENING", result.reason_codes)

    def test_velocity_within_threshold_neutral(self) -> None:
        """skew_vel = 0.3 (within ±0.5) → no skew signal, falls to mid-range neutral."""
        result = self._compute({"atm_iv_z": 0.5, "skew_z": 0.0, "skew_velocity": 0.3})
        self.assertEqual(result.value, 0.0)

    def test_velocity_at_exact_threshold(self) -> None:
        """skew_vel = 0.5 exactly → should NOT trigger (uses >)."""
        result = self._compute({"atm_iv_z": 0.5, "skew_z": 0.0, "skew_velocity": 0.5})
        self.assertEqual(result.value, 0.0)

    def test_steepening_magnitude_capped(self) -> None:
        """Extreme steepening → magnitude capped at -0.6."""
        result = self._compute({"atm_iv_z": 0.5, "skew_z": 0.0, "skew_velocity": 100.0})
        self.assertGreaterEqual(result.value, -0.6)

    def test_flattening_magnitude_capped(self) -> None:
        """Extreme flattening → magnitude capped at +0.4."""
        result = self._compute({"atm_iv_z": 0.5, "skew_z": 0.0, "skew_velocity": -100.0})
        self.assertLessEqual(result.value, 0.4)

    def test_steepening_asymmetry(self) -> None:
        """Bearish skew (steepening) cap -0.6 > bullish cap +0.4 — asymmetric by design."""
        steep = self._compute({"atm_iv_z": 0.5, "skew_z": 0.0, "skew_velocity": 50.0})
        flat = self._compute({"atm_iv_z": 0.5, "skew_z": 0.0, "skew_velocity": -50.0})
        self.assertGreater(
            abs(steep.value), abs(flat.value), "Steepening (bearish) should be stronger than flattening (bullish)"
        )


if __name__ == "__main__":
    unittest.main()
