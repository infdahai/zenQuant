import unittest

from zenquant.plugins.signals.macro_regime_signal import compute


class MacroRegimeSignalTests(unittest.TestCase):
    def _indicators(self, vix_z: float = 0.0, dxy_z: float = 0.0, yield_z: float = 0.0) -> dict:
        return {"vix_z": vix_z, "dxy_z": dxy_z, "yield_10y_z": yield_z}

    def _config(self) -> dict:
        return {
            "vix_extreme_threshold": 1.5,
            "dxy_strong_threshold": 1.5,
            "yield_stress_threshold": 2.0,
        }

    def test_risk_off_high_vix_strong_dollar(self) -> None:
        result = compute(
            self._indicators(vix_z=2.5, dxy_z=1.0),
            {"regime": "RANGE"},
            self._config(),
        )
        self.assertLess(result.value, -0.5)
        self.assertGreater(result.confidence, 0.3)
        self.assertIn("MACRO_RISK_OFF", result.reason_codes)

    def test_risk_on_low_vix_weak_dollar(self) -> None:
        result = compute(
            self._indicators(vix_z=-2.0, dxy_z=-1.0),
            {"regime": "RANGE"},
            self._config(),
        )
        self.assertGreater(result.value, 0.3)
        self.assertGreater(result.confidence, 0.3)
        self.assertIn("MACRO_RISK_ON", result.reason_codes)

    def test_neutral_when_conflicting(self) -> None:
        # High VIX but weak dollar — conflicting signals
        result = compute(
            self._indicators(vix_z=2.0, dxy_z=-2.0),
            {"regime": "RANGE"},
            self._config(),
        )
        # Should not fire risk-off (DXY is negative)
        self.assertNotIn("MACRO_RISK_OFF", result.reason_codes)

    def test_suppressed_in_stress_regime(self) -> None:
        result = compute(
            self._indicators(vix_z=3.0, dxy_z=2.0),
            {"regime": "STRESS"},
            self._config(),
        )
        self.assertAlmostEqual(result.value, 0.0)
        self.assertAlmostEqual(result.confidence, 0.0)

    def test_confidence_scales_with_magnitude(self) -> None:
        mild = compute(
            self._indicators(vix_z=1.6, dxy_z=0.5),
            {"regime": "RANGE"},
            self._config(),
        )
        strong = compute(
            self._indicators(vix_z=4.0, dxy_z=2.0),
            {"regime": "RANGE"},
            self._config(),
        )
        self.assertGreater(strong.confidence, mild.confidence)

    def test_neutral_when_no_macro_data(self) -> None:
        result = compute(
            self._indicators(),  # all zeros
            {"regime": "RANGE"},
            self._config(),
        )
        self.assertAlmostEqual(result.value, 0.0)
        self.assertAlmostEqual(result.confidence, 0.0)

    def test_yield_stress_amplifies_risk_off(self) -> None:
        without_yield = compute(
            self._indicators(vix_z=2.0, dxy_z=1.0, yield_z=0.0),
            {"regime": "RANGE"},
            self._config(),
        )
        with_yield = compute(
            self._indicators(vix_z=2.0, dxy_z=1.0, yield_z=3.0),
            {"regime": "RANGE"},
            self._config(),
        )
        self.assertGreaterEqual(with_yield.confidence, without_yield.confidence)

    def test_strong_dollar_alone_mild_bearish(self) -> None:
        result = compute(
            self._indicators(vix_z=0.5, dxy_z=2.0),
            {"regime": "RANGE"},
            self._config(),
        )
        self.assertLess(result.value, 0.0)
        self.assertIn("MACRO_STRONG_USD", result.reason_codes)

    def test_weak_dollar_alone_mild_bullish(self) -> None:
        result = compute(
            self._indicators(vix_z=0.5, dxy_z=-2.0),
            {"regime": "RANGE"},
            self._config(),
        )
        self.assertGreater(result.value, 0.0)
        self.assertIn("MACRO_WEAK_USD", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
