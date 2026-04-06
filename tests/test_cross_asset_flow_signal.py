"""Unit tests for cross_asset_flow_signal plugin — ETH→BTC Transfer Entropy."""

import unittest


class CrossAssetFlowSignalTests(unittest.TestCase):
    """Verify ETH→BTC transfer entropy signal behavior."""

    def _compute(self, indicators, state=None, config=None):
        from zenquant.plugins.signals import cross_asset_flow_signal

        state = state or {"regime": "TREND"}
        config = config or {"te_threshold": 0.1}
        return cross_asset_flow_signal.compute(indicators, state, config)

    # ── Failure / neutral cases first ──

    def test_no_eth_data_neutral(self) -> None:
        """te_score=0.0 → auto-neutral (no ETH data available)."""
        result = self._compute({"te_eth_to_btc": 0.0, "eth_momentum": 0.05})
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.confidence, 0.0)

    def test_none_te_coerces_to_zero(self) -> None:
        """None indicator → coerces to 0.0 → neutral."""
        result = self._compute({"te_eth_to_btc": None, "eth_momentum": None})
        self.assertEqual(result.value, 0.0)

    def test_missing_keys_defaults_neutral(self) -> None:
        """Missing keys → defaults to 0.0 → neutral."""
        result = self._compute({})
        self.assertEqual(result.value, 0.0)

    def test_low_te_below_threshold_neutral(self) -> None:
        """te_score=0.05 (below 0.1 threshold) → neutral."""
        result = self._compute({"te_eth_to_btc": 0.05, "eth_momentum": 0.1})
        self.assertEqual(result.value, 0.0)

    def test_high_te_flat_eth_neutral(self) -> None:
        """High TE but ETH momentum within ±0.01 → neutral (no direction)."""
        result = self._compute({"te_eth_to_btc": 0.5, "eth_momentum": 0.005})
        self.assertEqual(result.value, 0.0)

    def test_high_te_eth_exactly_at_threshold(self) -> None:
        """eth_momentum=0.01 exactly → should NOT trigger (uses >)."""
        result = self._compute({"te_eth_to_btc": 0.5, "eth_momentum": 0.01})
        self.assertEqual(result.value, 0.0)

    # ── Boundary tests ──

    def test_boundary_te_at_exact_threshold(self) -> None:
        """te_score=0.1 exactly → should NOT trigger (uses >)."""
        result = self._compute({"te_eth_to_btc": 0.1, "eth_momentum": 0.05})
        self.assertEqual(result.value, 0.0)

    def test_boundary_te_just_above_threshold(self) -> None:
        """te_score=0.101 → should trigger."""
        result = self._compute({"te_eth_to_btc": 0.101, "eth_momentum": 0.05})
        self.assertGreater(result.value, 0.0)

    # ── Happy path: directional signals ──

    def test_high_te_bullish_eth(self) -> None:
        """High TE + positive ETH momentum → bullish (ETH leading up)."""
        result = self._compute({"te_eth_to_btc": 0.5, "eth_momentum": 0.05})
        self.assertGreater(result.value, 0.0)
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("ETH_LEADING_BULLISH", result.reason_codes)

    def test_high_te_bearish_eth(self) -> None:
        """High TE + negative ETH momentum → bearish (ETH leading down)."""
        result = self._compute({"te_eth_to_btc": 0.5, "eth_momentum": -0.05})
        self.assertLess(result.value, 0.0)
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("ETH_LEADING_BEARISH", result.reason_codes)

    # ── Cap / saturation tests ──

    def test_magnitude_capped_at_0_6(self) -> None:
        """Extreme TE → value magnitude capped at 0.6."""
        result = self._compute({"te_eth_to_btc": 10.0, "eth_momentum": 0.1})
        self.assertLessEqual(abs(result.value), 0.6)

    def test_confidence_capped_at_0_7(self) -> None:
        """Extreme TE → confidence capped at 0.7."""
        result = self._compute({"te_eth_to_btc": 10.0, "eth_momentum": 0.1})
        self.assertLessEqual(result.confidence, 0.7)

    # ── Config override ──

    def test_custom_te_threshold_respected(self) -> None:
        """Higher custom threshold → tighter activation."""
        config = {"te_threshold": 0.5}
        # te=0.3 should NOT trigger with threshold=0.5
        result = self._compute({"te_eth_to_btc": 0.3, "eth_momentum": 0.05}, config=config)
        self.assertEqual(result.value, 0.0)

        # te=0.6 SHOULD trigger
        result = self._compute({"te_eth_to_btc": 0.6, "eth_momentum": 0.05}, config=config)
        self.assertGreater(result.value, 0.0)


if __name__ == "__main__":
    unittest.main()
