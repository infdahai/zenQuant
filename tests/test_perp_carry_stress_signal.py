"""Unit tests for perp_carry_stress_signal."""

import unittest


class PerpCarryStressSignalTests(unittest.TestCase):
    def test_crowded_longs_emit_bearish_modifier(self) -> None:
        from zenquant.plugins.signals import perp_carry_stress_signal

        result = perp_carry_stress_signal.compute(
            {
                "carry_stress_score": 0.65,
                "leverage_pressure_score": 0.70,
                "trend_persistence": 0.55,
            },
            {"regime": "TREND"},
            {},
        )

        self.assertLess(result.value, 0.0)
        self.assertIn("PERP_CARRY_LONG_STRESS", result.reason_codes)

    def test_crowded_shorts_emit_bullish_modifier(self) -> None:
        from zenquant.plugins.signals import perp_carry_stress_signal

        result = perp_carry_stress_signal.compute(
            {
                "carry_stress_score": -0.70,
                "leverage_pressure_score": 0.72,
                "trend_persistence": 0.50,
            },
            {"regime": "TREND"},
            {},
        )

        self.assertGreater(result.value, 0.0)
        self.assertIn("PERP_CARRY_SHORT_STRESS", result.reason_codes)

    def test_low_pressure_stays_neutral(self) -> None:
        from zenquant.plugins.signals import perp_carry_stress_signal

        result = perp_carry_stress_signal.compute(
            {"carry_stress_score": 0.70, "leverage_pressure_score": 0.30},
            {"regime": "TREND"},
            {},
        )

        self.assertEqual(result.value, 0.0)

