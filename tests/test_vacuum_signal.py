"""Unit tests for vacuum_signal plugin — liquidation cascade detection."""

import unittest


class VacuumSignalTests(unittest.TestCase):
    """Verify vacuum harvesting signal scoring and direction logic."""

    def _compute(self, indicators, state=None, config=None):
        from zenquant.plugins.signals import vacuum_signal

        state = state or {"regime": "TREND"}
        config = config or {}
        return vacuum_signal.compute(indicators, state, config)

    # ── Failure / neutral cases first ──

    def test_calm_market_no_signal(self) -> None:
        """All indicators calm → score < 0.3 → neutral."""
        result = self._compute(
            {
                "funding_oi_z": 0.5,
                "vpin": 0.02,
                "realized_vol": 0.5,
            }
        )
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.confidence, 0.0)

    def test_none_indicators_neutral(self) -> None:
        """All None → coerce to 0.0 → neutral."""
        result = self._compute(
            {
                "funding_oi_z": None,
                "vpin": None,
                "realized_vol": None,
            }
        )
        self.assertEqual(result.value, 0.0)

    def test_missing_keys_neutral(self) -> None:
        """Missing keys → defaults to 0.0 → neutral."""
        result = self._compute({})
        self.assertEqual(result.value, 0.0)

    def test_vpin_alone_not_enough(self) -> None:
        """High VPIN alone (score=0.2) → below 0.3 threshold → neutral."""
        result = self._compute(
            {
                "funding_oi_z": 0.0,
                "vpin": 0.1,
                "realized_vol": 0.5,
            }
        )
        self.assertEqual(result.value, 0.0)

    # ── Boundary tests ──

    def test_boundary_funding_at_2_0_exactly(self) -> None:
        """abs(funding_oi_z) = 2.0 exactly → should NOT trigger (uses >)."""
        result = self._compute(
            {
                "funding_oi_z": 2.0,
                "vpin": 0.0,
                "realized_vol": 0.5,
            }
        )
        # funding_oi_z=2.0 doesn't trigger extreme funding (> 2.0)
        # but triggers spring loaded (abs > 1.5 and vol < 0.3 is false here)
        self.assertEqual(result.value, 0.0)

    def test_boundary_funding_above_2_0(self) -> None:
        """abs(funding_oi_z) = 2.01 → triggers extreme funding (score += 0.3)."""
        result = self._compute(
            {
                "funding_oi_z": 2.01,
                "vpin": 0.0,
                "realized_vol": 0.5,
            }
        )
        # score = 0.3 (extreme funding), exactly at threshold → triggers
        self.assertNotEqual(result.value, 0.0)

    def test_boundary_vpin_at_0_08_exactly(self) -> None:
        """vpin = 0.08 exactly → should NOT trigger (uses >)."""
        # Only vpin=0.08, no other conditions
        result = self._compute(
            {
                "funding_oi_z": 0.0,
                "vpin": 0.08,
                "realized_vol": 0.5,
            }
        )
        self.assertEqual(result.value, 0.0)

    # ── Scoring composition tests ──

    def test_extreme_funding_only_triggers(self) -> None:
        """Extreme funding alone (score=0.3) → at threshold → triggers."""
        result = self._compute(
            {
                "funding_oi_z": 3.0,
                "vpin": 0.0,
                "realized_vol": 0.5,
            }
        )
        self.assertNotEqual(result.value, 0.0)
        self.assertIn("VACUUM_SETUP", result.reason_codes)

    def test_funding_plus_vpin_stronger(self) -> None:
        """Extreme funding + high VPIN → score=0.5 → stronger signal."""
        result = self._compute(
            {
                "funding_oi_z": 3.0,
                "vpin": 0.1,
                "realized_vol": 0.5,
            }
        )
        self.assertNotEqual(result.value, 0.0)
        # Score=0.5 → confidence = min(0.6, 0.5) = 0.5
        self.assertGreater(result.confidence, 0.0)

    def test_spring_loaded_condition(self) -> None:
        """Low vol + moderate funding → spring loaded (score += 0.3)."""
        result = self._compute(
            {
                "funding_oi_z": 2.01,  # > 2.0 for extreme, > 1.5 for spring
                "vpin": 0.0,
                "realized_vol": 0.2,  # < 0.3
            }
        )
        # score = 0.3 (extreme) + 0.3 (spring) = 0.6
        self.assertNotEqual(result.value, 0.0)
        self.assertGreater(result.confidence, 0.3)

    # ── Direction tests ──

    def test_positive_funding_short_direction(self) -> None:
        """Positive crowded funding → direction = -1.0 (catch long squeeze)."""
        result = self._compute(
            {
                "funding_oi_z": 3.0,
                "vpin": 0.0,
                "realized_vol": 0.5,
            }
        )
        self.assertLess(result.value, 0.0)

    def test_negative_funding_long_direction(self) -> None:
        """Negative crowded funding → direction = +1.0 (catch short squeeze)."""
        result = self._compute(
            {
                "funding_oi_z": -3.0,
                "vpin": 0.0,
                "realized_vol": 0.5,
            }
        )
        self.assertGreater(result.value, 0.0)

    # ── Cap tests ──

    def test_value_capped_at_0_5(self) -> None:
        """Maximum signal value is ±0.5."""
        result = self._compute(
            {
                "funding_oi_z": 10.0,
                "vpin": 0.5,
                "realized_vol": 0.1,
            }
        )
        self.assertGreaterEqual(result.value, -0.5)
        self.assertLessEqual(result.value, 0.5)

    def test_confidence_capped_at_0_6(self) -> None:
        """Maximum confidence is 0.6."""
        result = self._compute(
            {
                "funding_oi_z": 10.0,
                "vpin": 0.5,
                "realized_vol": 0.1,
            }
        )
        self.assertLessEqual(result.confidence, 0.6)


if __name__ == "__main__":
    unittest.main()
