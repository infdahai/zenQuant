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
            {"low_iv_threshold": -0.5, "high_iv_threshold": 1.5, "extreme_iv_threshold": 2.5, "inverted_skew_threshold": -1.0},
        )
        self.assertEqual(result1.reason_codes, ("OPTIONS_LOW_IV",))

        # Test HIGH_IV
        result2 = options_vol_signal.compute(
            {"atm_iv_z": 2.0, "skew_z": 0.0},
            {"regime": "TREND"},
            {"low_iv_threshold": -0.5, "high_iv_threshold": 1.5, "extreme_iv_threshold": 2.5, "inverted_skew_threshold": -1.0},
        )
        self.assertIn("OPTIONS_HIGH_IV", result2.reason_codes)

        # Test DISTORTED
        result3 = options_vol_signal.compute(
            {"atm_iv_z": 3.0, "skew_z": 0.0},
            {"regime": "TREND"},
            {"low_iv_threshold": -0.5, "high_iv_threshold": 1.5, "extreme_iv_threshold": 2.5, "inverted_skew_threshold": -1.0},
        )
        self.assertIn("OPTIONS_DISTORTED", result3.reason_codes)


if __name__ == "__main__":
    unittest.main()
