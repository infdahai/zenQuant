"""Integration test: plugin path produces valid output with real plugins."""

from __future__ import annotations

import unittest
from pathlib import Path

from zenquant.domain import (
    IndicatorCoverage,
    IndicatorPack,
    MarketState,
    PlaybookSelection,
    Timeframe,
)
from zenquant.infra.event_bus import InMemoryJetStream
from zenquant.plugins.profile import build_from_profile
from zenquant.strategy.engine import ARCStrategyConfig, ARCStrategyEngine

PLUGIN_ROOT = Path(__file__).parent.parent / "zenquant" / "plugins"
PROFILE_PATH = Path(__file__).parent.parent / "config" / "profiles" / "default.toml"


def _pack(
    *,
    symbol: str = "BTCUSDT_PERP",
    timeframe: Timeframe = Timeframe.TF_4H,
    realized_vol: float = 0.6,
    atr: float = 500.0,
    cvd_slope: float = 0.05,
    ob_liq_z: float = 0.3,
    funding_oi_z: float = 0.1,
    momentum_roc_z: float = 0.8,
    structure_state: str = "UPTREND",
    last_price: float = 60000.0,
    zen_pulse: float = 65.0,
) -> IndicatorPack:
    return IndicatorPack(
        ts_ns=1,
        symbol=symbol,
        timeframe=timeframe,
        cutoff_time_ns=1,
        indicator_version="pack.v1",
        formula_hash="test",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
        values={
            "realized_vol": realized_vol,
            "atr": atr,
            "cvd_slope": cvd_slope,
            "ob_liq_z": ob_liq_z,
            "funding_oi_z": funding_oi_z,
            "momentum_roc_z": momentum_roc_z,
            "structure_state": structure_state,
            "last_price": last_price,
            "zen_pulse": zen_pulse,
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


def _state(
    *,
    regime: str = "TREND",
    directional_state: str = "UPTREND",
    volatility_state: str = "NORMAL",
    positioning_state: str = "HEALTHY",
    quality_state: str = "TRADABLE",
    transition_risk: float = 0.2,
) -> MarketState:
    return MarketState(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        directional_state=directional_state,
        directional_confidence=0.8,
        volatility_state=volatility_state,
        volatility_confidence=0.8,
        positioning_state=positioning_state,
        positioning_confidence=0.8,
        quality_state=quality_state,
        quality_confidence=0.9,
        regime=regime,
        regime_confidence=0.8,
        direction_signal=1.0,
        transition_risk=transition_risk,
        quality_flags=["OK"],
    )


def _playbook(
    *,
    playbook_id: str = "BULL_CONTINUATION",
    max_leverage: float = 2.5,
) -> PlaybookSelection:
    return PlaybookSelection(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        playbook_id=playbook_id,
        playbook_confidence=0.8,
        allowed_directions=[1.0, 0.0],
        max_leverage=max_leverage,
        trailing_stop_atr_mult=2.5,
        allow_pyramid=True,
        max_pyramid_count=2,
        time_decay_bars=None,
        selection_reason="test",
    )


class TestRegistryLoadsRealPlugins(unittest.TestCase):
    """Verify that all plugins in default.toml load successfully."""

    def test_load_default_profile(self):
        if not PROFILE_PATH.exists():
            self.skipTest("default.toml not found")

        profile, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)

        self.assertEqual(len(registry.signals), 8)  # +crash_reversal +funding_alpha +vol_mean_reversion, options disabled
        self.assertEqual(len(registry.filters), 2)
        self.assertEqual(len(registry.sizers), 4)
        self.assertEqual(len(registry.exits), 5)

        # Verify exit priority ordering
        exit_names = [p.meta.name for p in registry.exits]
        self.assertEqual(exit_names[0], "invalidation_exit")  # priority 1
        self.assertEqual(exit_names[-1], "regime_change_exit")  # priority 5

    def test_all_signal_plugins_compute(self):
        if not PROFILE_PATH.exists():
            self.skipTest("default.toml not found")

        _, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)
        indicators = dict(_pack().values)
        state_dict = {"regime": "TREND", "overall_confidence": 0.8}

        for plugin in registry.signals:
            result = plugin.compute(indicators, state_dict, plugin.config)
            self.assertGreaterEqual(result.value, -1.0)
            self.assertLessEqual(result.value, 1.0)

    def test_all_sizer_plugins_compute(self):
        if not PROFILE_PATH.exists():
            self.skipTest("default.toml not found")

        _, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)
        indicators = dict(_pack().values)
        state_dict = {
            "timeframe": "4h",
            "volatility_state": "NORMAL",
            "quality_state": "TRADABLE",
            "overall_confidence": 0.8,
            "playbook_confidence": 0.8,
        }

        for plugin in registry.sizers:
            result = plugin.compute(indicators, state_dict, plugin.config)
            self.assertGreaterEqual(result.factor, 0.0)


class TestPluginPathProducesValidOutput(unittest.TestCase):
    """Verify the plugin path in ARCStrategyEngine produces valid targets."""

    def _build_engine_with_plugins(self) -> tuple[ARCStrategyEngine, InMemoryJetStream]:
        if not PROFILE_PATH.exists():
            self.skipTest("default.toml not found")

        _, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)
        bus = InMemoryJetStream()
        bus.add_stream(
            __import__("zenquant.infra.event_bus", fromlist=["StreamConfig"]).StreamConfig(
                name="STRATEGY", subjects=["strategy.>", "intent.>"]
            )
        )
        config = ARCStrategyConfig()
        engine = ARCStrategyEngine(bus=bus, config=config, plugin_registry=registry)
        return engine, bus

    def test_plugin_direction_uptrend(self):
        engine, bus = self._build_engine_with_plugins()

        pack = _pack(structure_state="UPTREND", momentum_roc_z=1.0, cvd_slope=0.1)
        state = _state(regime="TREND", directional_state="UPTREND")
        playbook = _playbook()

        direction, codes = engine._plugin_direction(pack, state, playbook)
        self.assertEqual(direction, 1.0)
        self.assertTrue(any("momentum_signal" in c for c in codes))

    def test_plugin_direction_stress_zero(self):
        engine, bus = self._build_engine_with_plugins()

        pack = _pack()
        state = _state(regime="STRESS")
        playbook = _playbook()

        direction, codes = engine._plugin_direction(pack, state, playbook)
        # In STRESS, momentum and cvd both return 0 → direction should be 0
        self.assertEqual(direction, 0.0)

    def test_plugin_sizing_produces_positive_leverage(self):
        engine, bus = self._build_engine_with_plugins()

        pack = _pack(realized_vol=0.6)
        state = _state()
        playbook = _playbook(max_leverage=2.5)

        leverage, codes = engine._plugin_sizing(pack, state, playbook)
        self.assertGreater(leverage, 0.0)
        self.assertLessEqual(leverage, 2.5)

    def test_plugin_exit_invalidation(self):
        engine, bus = self._build_engine_with_plugins()

        pack = _pack()
        state = _state(directional_state="DOWNTREND")
        playbook = _playbook(playbook_id="BULL_CONTINUATION")

        from zenquant.domain import OpenPositionState

        position = OpenPositionState(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            direction=1.0,
            entry_price=60000.0,
            entry_bar_ns=0,
            base_qty=0.1,
            current_qty=0.1,
            trailing_stop=0.0,
            pyramid_count=0,
            bars_held=5,
        )

        exit_decision = engine._plugin_exit(pack, state, playbook, position)
        self.assertIsNotNone(exit_decision)
        self.assertTrue(exit_decision.should_exit())
        self.assertEqual(exit_decision.exit_type, "INVALIDATION")

    def test_quality_filter_blocks_unsafe(self):
        engine, bus = self._build_engine_with_plugins()

        pack = _pack(structure_state="UPTREND", momentum_roc_z=1.0)
        state = _state(regime="TREND", quality_state="UNSAFE")
        playbook = _playbook()

        direction, codes = engine._plugin_direction(pack, state, playbook)
        # Quality filter should block
        self.assertEqual(direction, 0.0)
        self.assertTrue(any("QUALITY" in c for c in codes))


class TestLegacyPathUnchanged(unittest.TestCase):
    """Verify that engine without plugins uses legacy path."""

    def test_no_plugins_uses_legacy(self):
        bus = InMemoryJetStream()
        bus.add_stream(
            __import__("zenquant.infra.event_bus", fromlist=["StreamConfig"]).StreamConfig(
                name="STRATEGY", subjects=["strategy.>", "intent.>"]
            )
        )
        # No plugin_registry → legacy path
        engine = ARCStrategyEngine(bus=bus)
        self.assertIsNone(engine._plugins)


if __name__ == "__main__":
    unittest.main()
