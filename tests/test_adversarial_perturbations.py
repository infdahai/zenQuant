"""Test adversarial perturbation modes (GAN-style stress testing).

Validates that PerturbationConfig adversarial modes work correctly:
- regime_whipsaw: trend reversals every N bars
- liquidity_vacuum: random price gaps
- crowding_trap: extreme funding rates
"""

from __future__ import annotations

import unittest

from zenquant.historical.perturbation import (
    PerturbationConfig,
    apply_crowding_trap_funding,
    perturb_events,
)


class TestRegimeWhipsaw(unittest.TestCase):
    def test_whipsaw_reverses_direction_every_n_bars(self) -> None:
        """Regime whipsaw should reverse price direction every N bars."""
        events = [
            {"close": 42000.0, "open": 42000.0, "high": 42100.0, "low": 41900.0},
            {"close": 42000.0, "open": 42000.0, "high": 42100.0, "low": 41900.0},
            {"close": 42000.0, "open": 42000.0, "high": 42100.0, "low": 41900.0},
            {"close": 42000.0, "open": 42000.0, "high": 42100.0, "low": 41900.0},
            {"close": 42000.0, "open": 42000.0, "high": 42100.0, "low": 41900.0},
        ]

        config = PerturbationConfig(
            seed=42,
            regime_whipsaw_bars=2,  # Reverse every 2 bars
            regime_whipsaw_magnitude=50.0,  # ±50 USD
        )

        result = perturb_events(events, config)

        # Bars 0-1: direction=+1, bars 2-3: direction=-1, bar 4: direction=+1
        self.assertGreater(result[0]["close"], events[0]["close"])  # +50
        self.assertGreater(result[1]["close"], result[0]["close"])  # +50 again
        self.assertLess(result[2]["close"], result[1]["close"])  # -50 (reversal)
        self.assertLess(result[3]["close"], result[2]["close"])  # -50 again
        self.assertGreater(result[4]["close"], result[3]["close"])  # +50 (reversal)


class TestLiquidityVacuum(unittest.TestCase):
    def test_vacuum_injects_price_gaps(self) -> None:
        """Liquidity vacuum should inject K random price gaps."""
        events = [{"close": 42000.0 + i * 10, "open": 42000.0, "high": 42100.0, "low": 41900.0} for i in range(20)]

        config = PerturbationConfig(
            seed=42,
            liquidity_vacuum_count=3,  # 3 gaps
            liquidity_vacuum_pct=(-0.10, -0.05),  # -10% to -5%
        )

        result = perturb_events(events, config)

        # Should have 3 bars with significant price drops
        gaps = []
        for i, (orig, pert) in enumerate(zip(events, result)):
            change_pct = (pert["close"] - orig["close"]) / orig["close"]
            if change_pct < -0.03:  # More than 3% drop
                gaps.append((i, change_pct))

        self.assertEqual(len(gaps), 3, f"Expected 3 gaps, found {len(gaps)}: {gaps}")
        for _, change_pct in gaps:
            self.assertLessEqual(change_pct, -0.05)  # At least -5%
            self.assertGreaterEqual(change_pct, -0.10)  # At most -10%


class TestCrowdingTrap(unittest.TestCase):
    def test_crowding_trap_multiplies_funding(self) -> None:
        """Crowding trap should multiply funding rates by N."""
        funding_events = [
            {"funding_rate": 0.0001, "funding_time_ns": 1000},
            {"funding_rate": -0.0001, "funding_time_ns": 2000},
            {"funding_rate": 0.0002, "funding_time_ns": 3000},
        ]

        config = PerturbationConfig(
            seed=42,
            crowding_trap_funding_mult=10.0,  # 10x
        )

        result = apply_crowding_trap_funding(funding_events, config)

        self.assertAlmostEqual(result[0]["funding_rate"], 0.001, places=4)
        self.assertAlmostEqual(result[1]["funding_rate"], -0.001, places=4)
        self.assertAlmostEqual(result[2]["funding_rate"], 0.002, places=4)


class TestCombinedPerturbations(unittest.TestCase):
    def test_adversarial_plus_noise(self) -> None:
        """Can combine adversarial (whipsaw) + domain randomization (noise)."""
        events = [{"close": 42000.0, "open": 42000.0, "high": 42100.0, "low": 41900.0} for _ in range(10)]

        config = PerturbationConfig(
            seed=42,
            regime_whipsaw_bars=5,
            regime_whipsaw_magnitude=100.0,  # Larger magnitude to dominate noise
            price_noise_pct=0.001,  # Also add ±0.1% noise
        )

        result = perturb_events(events, config)

        # Should have whipsaw (deterministic) + noise (random per bar)
        # Bars 0-4: cumulative uptrend (+100 per bar)
        # Bar 0: +100, Bar 1: +200, Bar 2: +300, Bar 3: +400, Bar 4: +500
        # Even with ±0.1% noise (~±42), bar 4 should be higher than bar 0
        self.assertGreater(result[4]["close"], result[0]["close"],
                          "Bar 4 should be higher than bar 0 (uptrend first half)")

        # Bars 5-9: cumulative downtrend (-100 per bar from peak)
        # Bar 5: +400, Bar 6: +300, Bar 7: +200, Bar 8: +100, Bar 9: +0
        # Bar 5 should be higher than bar 9
        self.assertGreater(result[5]["close"], result[9]["close"],
                          "Bar 5 should be higher than bar 9 (downtrend second half)")


class TestNoopConfig(unittest.TestCase):
    def test_noop_returns_unmodified(self) -> None:
        """Zero config should return events unmodified."""
        events = [{"close": 42000.0, "open": 42000.0}]
        config = PerturbationConfig()  # All zeros

        result = perturb_events(events, config)

        self.assertEqual(result, events)


if __name__ == "__main__":
    unittest.main()
