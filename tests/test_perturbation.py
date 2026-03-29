"""Tests for domain randomization perturbation module."""

from __future__ import annotations

import unittest

from zenquant.historical.perturbation import PerturbationConfig, perturb_events


def _sample_events(n: int = 10) -> list[dict]:
    return [
        {
            "open_time_ns": i * 14400_000_000_000,
            "close_time_ns": (i + 1) * 14400_000_000_000,
            "event_timeframe": "4h",
            "open": 60000.0 + i * 100,
            "high": 60500.0 + i * 100,
            "low": 59500.0 + i * 100,
            "close": 60100.0 + i * 100,
            "volume": 1000.0,
            "trade_count": 500,
        }
        for i in range(n)
    ]


class TestPerturbation(unittest.TestCase):

    def test_noop_returns_same_events(self):
        events = _sample_events()
        config = PerturbationConfig(seed=42)
        result = perturb_events(events, config)
        self.assertEqual(len(result), len(events))
        self.assertEqual(result[0]["close"], events[0]["close"])

    def test_price_noise_modifies_prices(self):
        events = _sample_events()
        config = PerturbationConfig(seed=42, price_noise_pct=0.01)
        result = perturb_events(events, config)
        self.assertEqual(len(result), len(events))
        # At least some prices should differ
        changed = sum(
            1 for a, b in zip(events, result) if abs(a["close"] - b["close"]) > 0.01
        )
        self.assertGreater(changed, 0)

    def test_price_noise_maintains_ohlc_consistency(self):
        events = _sample_events()
        config = PerturbationConfig(seed=42, price_noise_pct=0.01)
        result = perturb_events(events, config)
        for row in result:
            self.assertGreaterEqual(row["high"], max(row["open"], row["close"]))
            self.assertLessEqual(row["low"], min(row["open"], row["close"]))

    def test_bar_dropout_reduces_events(self):
        events = _sample_events(100)
        config = PerturbationConfig(seed=42, bar_dropout_pct=0.2)
        result = perturb_events(events, config)
        self.assertLess(len(result), len(events))
        self.assertGreater(len(result), 50)  # shouldn't drop more than ~30%

    def test_volume_noise(self):
        events = _sample_events()
        config = PerturbationConfig(seed=42, volume_noise_pct=0.1)
        result = perturb_events(events, config)
        changed = sum(
            1 for a, b in zip(events, result) if abs(a["volume"] - b["volume"]) > 0.01
        )
        self.assertGreater(changed, 0)
        # All volumes should remain non-negative
        for row in result:
            self.assertGreaterEqual(row["volume"], 0.0)

    def test_deterministic_with_same_seed(self):
        events = _sample_events()
        config = PerturbationConfig(seed=123, price_noise_pct=0.01, bar_dropout_pct=0.05)
        result1 = perturb_events(events, config)
        result2 = perturb_events(events, config)
        self.assertEqual(len(result1), len(result2))
        for a, b in zip(result1, result2):
            self.assertAlmostEqual(a["close"], b["close"], places=10)

    def test_different_seeds_produce_different_results(self):
        events = _sample_events()
        r1 = perturb_events(events, PerturbationConfig(seed=1, price_noise_pct=0.01))
        r2 = perturb_events(events, PerturbationConfig(seed=2, price_noise_pct=0.01))
        diffs = sum(1 for a, b in zip(r1, r2) if abs(a["close"] - b["close"]) > 0.01)
        self.assertGreater(diffs, 0)

    def test_original_events_not_modified(self):
        events = _sample_events()
        original_close = events[0]["close"]
        perturb_events(events, PerturbationConfig(seed=42, price_noise_pct=0.01))
        self.assertEqual(events[0]["close"], original_close)


if __name__ == "__main__":
    unittest.main()
