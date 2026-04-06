"""Unit tests for crash_reversal_signal plugin — extreme drop mean-reversion."""

import unittest


class CrashReversalSignalTests(unittest.TestCase):
    """Verify crash detection and bullish reversal signal."""

    def _compute(self, indicators, state=None, config=None):
        from zenquant.plugins.signals import crash_reversal_signal

        state = state or {"regime": "TREND"}
        config = config or {"crash_threshold": -0.05, "deep_crash_threshold": -0.08}
        return crash_reversal_signal.compute(indicators, state, config)

    def _bar(self, open_price, close, volume_ratio=1.0):
        return {"open": open_price, "close": close, "volume_ratio": volume_ratio}

    # ── Failure / neutral cases ──

    def test_no_crash_neutral(self) -> None:
        """Normal bar (small move) → neutral."""
        result = self._compute(self._bar(100, 98))  # -2%
        self.assertEqual(result.value, 0.0)

    def test_positive_bar_neutral(self) -> None:
        """Up bar → neutral (only detects crashes)."""
        result = self._compute(self._bar(100, 110))  # +10%
        self.assertEqual(result.value, 0.0)

    def test_zero_close_neutral(self) -> None:
        """close=0 → neutral (invalid price)."""
        result = self._compute(self._bar(100, 0))
        self.assertEqual(result.value, 0.0)

    def test_zero_open_neutral(self) -> None:
        """open=0 → neutral (invalid price)."""
        result = self._compute(self._bar(0, 100))
        self.assertEqual(result.value, 0.0)

    def test_missing_prices_neutral(self) -> None:
        """Missing price keys → neutral."""
        result = self._compute({})
        self.assertEqual(result.value, 0.0)

    def test_none_prices_neutral(self) -> None:
        """None prices → neutral."""
        result = self._compute({"open": None, "close": None})
        self.assertEqual(result.value, 0.0)

    def test_fires_in_stress_regime(self) -> None:
        """Crash reversal fires in ALL regimes including STRESS (no regime filter)."""
        result = self._compute(
            self._bar(100, 90),  # -10% deep crash
            state={"regime": "STRESS"},
        )
        self.assertGreater(result.value, 0.0)

    # ── Boundary tests ──

    def test_boundary_at_crash_threshold(self) -> None:
        """-5% exactly → should NOT trigger (uses <, not <=)."""
        result = self._compute(self._bar(100, 95))  # exactly -5%
        self.assertEqual(result.value, 0.0)

    def test_boundary_just_below_crash(self) -> None:
        """-5.1% → triggers standard crash."""
        result = self._compute(self._bar(1000, 949))  # -5.1%
        self.assertAlmostEqual(result.value, 0.6)
        self.assertIn("CRASH_REVERSAL", result.reason_codes)

    def test_boundary_at_deep_crash_threshold(self) -> None:
        """-8% exactly → should NOT trigger deep (uses <)."""
        result = self._compute(self._bar(100, 92))  # exactly -8%
        # Falls to standard crash (< -0.05 = True)
        self.assertAlmostEqual(result.value, 0.6)
        self.assertIn("CRASH_REVERSAL", result.reason_codes)

    def test_boundary_just_below_deep_crash(self) -> None:
        """-8.1% → triggers deep crash."""
        result = self._compute(self._bar(1000, 919))  # -8.1%
        self.assertAlmostEqual(result.value, 0.8)
        self.assertIn("CRASH_DEEP_REVERSAL", result.reason_codes)

    # ── Happy path ──

    def test_standard_crash_bullish(self) -> None:
        """6% drop → standard crash reversal (value=0.6)."""
        result = self._compute(self._bar(100, 94))  # -6%
        self.assertAlmostEqual(result.value, 0.6)
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("CRASH_REVERSAL", result.reason_codes)

    def test_deep_crash_strong_bullish(self) -> None:
        """12% drop → deep crash reversal (value=0.8)."""
        result = self._compute(self._bar(100, 88))  # -12%
        self.assertAlmostEqual(result.value, 0.8)
        self.assertIn("CRASH_DEEP_REVERSAL", result.reason_codes)

    def test_always_bullish_never_bearish(self) -> None:
        """Crash reversal is always bullish — never returns negative value."""
        for close in [90, 85, 80, 70, 50]:
            result = self._compute(self._bar(100, close))
            self.assertGreaterEqual(result.value, 0.0,
                                    f"close={close} produced bearish signal")

    # ── Volume confirmation ──

    def test_volume_boosts_confidence(self) -> None:
        """volume_ratio > 2.0 → confidence +0.1."""
        no_vol = self._compute(self._bar(100, 94, volume_ratio=1.0))
        with_vol = self._compute(self._bar(100, 94, volume_ratio=3.0))
        self.assertGreater(with_vol.confidence, no_vol.confidence)

    def test_volume_at_2x_no_boost(self) -> None:
        """volume_ratio = 2.0 exactly → should NOT boost (uses >)."""
        no_vol = self._compute(self._bar(100, 94, volume_ratio=1.0))
        at_2x = self._compute(self._bar(100, 94, volume_ratio=2.0))
        self.assertAlmostEqual(no_vol.confidence, at_2x.confidence)

    # ── Confidence caps ──

    def test_confidence_capped_at_1(self) -> None:
        """Extreme crash → confidence capped at 1.0."""
        result = self._compute(self._bar(100, 10, volume_ratio=5.0))  # -90% crash
        self.assertLessEqual(result.confidence, 1.0)

    # ── last_price fallback ──

    def test_last_price_fallback(self) -> None:
        """If close missing, falls back to last_price."""
        result = self._compute({"open": 100, "last_price": 88, "volume_ratio": 1.0})
        self.assertAlmostEqual(result.value, 0.8)


if __name__ == "__main__":
    unittest.main()
