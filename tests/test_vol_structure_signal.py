"""Unit tests for vol_structure_signal."""

import unittest


class VolStructureSignalTests(unittest.TestCase):
    def test_expansion_penalizes_chasing(self) -> None:
        from zenquant.plugins.signals import vol_structure_signal

        result = vol_structure_signal.compute(
            {
                "rv_term_ratio": 1.55,
                "vol_of_vol": 0.18,
                "trend_persistence": 0.40,
                "multi_momentum_z": 0.80,
            },
            {"regime": "TREND"},
            {},
        )

        self.assertLess(result.value, 0.0)
        self.assertIn("VOL_STRUCTURE_EXPANSION", result.reason_codes)

    def test_compression_plus_persistence_supports_trend(self) -> None:
        from zenquant.plugins.signals import vol_structure_signal

        result = vol_structure_signal.compute(
            {
                "rv_term_ratio": 0.75,
                "vol_of_vol": 0.05,
                "trend_persistence": 0.82,
                "multi_momentum_z": 0.90,
            },
            {"regime": "TREND"},
            {},
        )

        self.assertGreater(result.value, 0.0)
        self.assertIn("VOL_STRUCTURE_TREND_SUPPORT", result.reason_codes)

