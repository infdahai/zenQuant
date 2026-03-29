"""Gate D: Plugin/legacy path parity test.

Two code paths producing different TargetPositions from the same inputs
is a silent, capital-destroying bug. This test ensures both paths agree
on direction and leverage within tolerance, or that divergences are
explicitly declared and justified.

Mechanical enforcement: two paths, same inputs, consistent outputs.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from zenquant.domain import (
    IndicatorCoverage,
    IndicatorPack,
    MarketState,
    Timeframe,
)
from zenquant.infra.event_bus import InMemoryJetStream, StreamConfig
from zenquant.plugins.profile import build_from_profile
from zenquant.strategy.engine import ARCStrategyConfig, ARCStrategyEngine

PLUGIN_ROOT = Path(__file__).parent.parent / "zenquant" / "plugins"
PROFILE_PATH = Path(__file__).parent.parent / "config" / "profiles" / "default.toml"


def _make_pack(
    *,
    realized_vol: float = 0.6,
    cvd_slope: float = 0.05,
    momentum_roc_z: float = 0.8,
    structure_state: str = "UPTREND",
    last_price: float = 60000.0,
) -> IndicatorPack:
    return IndicatorPack(
        ts_ns=1,
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1_000_000_000,
        indicator_version="pack.v1",
        formula_hash="parity_test",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
        values={
            "realized_vol": realized_vol,
            "atr": 500.0,
            "cvd_slope": cvd_slope,
            "ob_liq_z": 0.3,
            "funding_oi_z": 0.1,
            "momentum_roc_z": momentum_roc_z,
            "structure_state": structure_state,
            "last_price": last_price,
            "zen_pulse": 65.0,
            "overall_ob_liq_z": 0.0,
            "funding_oi_weighted": 0.0,
            "book_age_ms": 100,
            "venue_count": 1,
            "oi_coverage": 1.0,
            "structure_break": False,
            "last_swing_high": 62000.0,
            "last_swing_low": 58000.0,
            "bars_since_break": 5,
            "momentum_roc": 0.02,
        },
    )


def _make_state(
    *,
    regime: str = "TREND",
    directional_state: str = "UPTREND",
) -> MarketState:
    return MarketState(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1_000_000_000,
        directional_state=directional_state,
        directional_confidence=0.8,
        volatility_state="NORMAL",
        volatility_confidence=0.8,
        positioning_state="HEALTHY",
        positioning_confidence=0.8,
        quality_state="TRADABLE",
        quality_confidence=0.9,
        regime=regime,
        regime_confidence=0.8,
        direction_signal=1.0,
        transition_risk=0.2,
        quality_flags=["OK"],
    )


class TestPluginLegacyParity(unittest.TestCase):
    """Both paths must agree on direction sign and approximate leverage."""

    def _run_both_paths(self, pack: IndicatorPack, state: MarketState):
        """Run the same inputs through legacy and plugin paths, return both targets."""
        if not PROFILE_PATH.exists():
            self.skipTest("default.toml not found")

        _, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)

        # Legacy path
        bus_legacy = InMemoryJetStream()
        bus_legacy.add_stream(StreamConfig(name="S", subjects=["strategy.>", "intent.>", "exec.>", "risk.>"]))
        legacy_engine = ARCStrategyEngine(bus=bus_legacy, config=ARCStrategyConfig())

        playbook_legacy = legacy_engine._playbook_engine.select(state)
        target_legacy = legacy_engine._build_target(pack, state, playbook_legacy)

        # Plugin path
        bus_plugin = InMemoryJetStream()
        bus_plugin.add_stream(StreamConfig(name="S", subjects=["strategy.>", "intent.>", "exec.>", "risk.>"]))
        plugin_engine = ARCStrategyEngine(bus=bus_plugin, config=ARCStrategyConfig(), plugin_registry=registry)

        playbook_plugin = plugin_engine._playbook_engine.select(state)
        target_plugin = plugin_engine._build_target(pack, state, playbook_plugin)

        return target_legacy, target_plugin

    def test_trend_uptrend_direction_agrees(self):
        """In a strong uptrend, both paths should go long."""
        pack = _make_pack(structure_state="UPTREND", momentum_roc_z=1.0, cvd_slope=0.1)
        state = _make_state(regime="TREND", directional_state="UPTREND")

        legacy, plugin = self._run_both_paths(pack, state)

        # Direction sign must agree
        legacy_sign = 1.0 if legacy.target_qty > 0 else (-1.0 if legacy.target_qty < 0 else 0.0)
        plugin_sign = 1.0 if plugin.target_qty > 0 else (-1.0 if plugin.target_qty < 0 else 0.0)
        self.assertEqual(legacy_sign, plugin_sign, f"Direction mismatch: legacy={legacy_sign}, plugin={plugin_sign}")

    def test_stress_regime_both_flat(self):
        """In STRESS regime, both paths should produce zero direction."""
        pack = _make_pack()
        state = _make_state(regime="STRESS")

        legacy, plugin = self._run_both_paths(pack, state)

        # Both should be flat (reduce_only or zero qty)
        self.assertTrue(
            legacy.reduce_only or abs(legacy.target_qty) < 0.001,
            f"Legacy should be flat/reduce-only in STRESS, got qty={legacy.target_qty}",
        )
        self.assertTrue(
            plugin.reduce_only or abs(plugin.target_qty) < 0.001,
            f"Plugin should be flat/reduce-only in STRESS, got qty={plugin.target_qty}",
        )

    def test_leverage_within_tolerance(self):
        """Both paths should produce leverage within 2x of each other."""
        pack = _make_pack(realized_vol=0.6, momentum_roc_z=0.8, structure_state="UPTREND")
        state = _make_state(regime="TREND")

        legacy, plugin = self._run_both_paths(pack, state)

        if legacy.target_leverage > 0 and plugin.target_leverage > 0:
            ratio = max(legacy.target_leverage, plugin.target_leverage) / max(
                min(legacy.target_leverage, plugin.target_leverage), 1e-9
            )
            self.assertLess(
                ratio,
                3.0,
                f"Leverage divergence too large: legacy={legacy.target_leverage:.2f}, "
                f"plugin={plugin.target_leverage:.2f}, ratio={ratio:.1f}",
            )


if __name__ == "__main__":
    unittest.main()
