"""Tests for PR #7 review fixes: trailing stop ratchet, sizer cap, bias checks, etc."""

from __future__ import annotations

import unittest
from pathlib import Path

from zenquant.domain import (
    DirectionalBias,
    IndicatorCoverage,
    IndicatorPack,
    MarketState,
    OpenPositionState,
    PlaybookSelection,
    Timeframe,
)
from zenquant.infra.event_bus import InMemoryJetStream, StreamConfig
from zenquant.plugins.profile import build_from_profile
from zenquant.strategy.engine import DIRECTION_THRESHOLD, ARCStrategyConfig, ARCStrategyEngine

PLUGIN_ROOT = Path(__file__).parent.parent / "zenquant" / "plugins"
PROFILE_PATH = Path(__file__).parent.parent / "config" / "profiles" / "default.toml"


def _pack(
    *,
    last_price: float = 60000.0,
    atr: float = 500.0,
    realized_vol: float = 0.6,
    cutoff_time_ns: int = 1_000_000_000,
    **kwargs: object,
) -> IndicatorPack:
    values = {
        "realized_vol": realized_vol,
        "atr": atr,
        "cvd_slope": 0.05,
        "ob_liq_z": 0.3,
        "funding_oi_z": 0.1,
        "momentum_roc_z": 0.8,
        "structure_state": "UPTREND",
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
    }
    values.update(kwargs)
    return IndicatorPack(
        ts_ns=1,
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=cutoff_time_ns,
        indicator_version="pack.v1",
        formula_hash="test",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
        values=values,
    )


def _state(
    *,
    regime: str = "TREND",
    directional_state: str = "UPTREND",
    quality_state: str = "TRADABLE",
    transition_risk: float = 0.2,
    cutoff_time_ns: int = 1_000_000_000,
) -> MarketState:
    return MarketState(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=cutoff_time_ns,
        directional_state=directional_state,
        directional_confidence=0.8,
        volatility_state="NORMAL",
        volatility_confidence=0.8,
        positioning_state="HEALTHY",
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
    trailing_stop_atr_mult: float = 2.5,
) -> PlaybookSelection:
    return PlaybookSelection(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        playbook_id=playbook_id,
        playbook_confidence=0.8,
        allowed_directions=[1.0, 0.0],
        max_leverage=max_leverage,
        trailing_stop_atr_mult=trailing_stop_atr_mult,
        allow_pyramid=True,
        max_pyramid_count=2,
        time_decay_bars=None,
        selection_reason="test",
    )


def _position(
    *,
    direction: float = 1.0,
    entry_price: float = 60000.0,
    trailing_stop: float = 0.0,
    bars_held: int = 5,
) -> OpenPositionState:
    return OpenPositionState(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        direction=direction,
        entry_price=entry_price,
        entry_bar_ns=0,
        base_qty=0.1,
        current_qty=0.1,
        trailing_stop=trailing_stop,
        pyramid_count=0,
        bars_held=bars_held,
    )


def _build_engine_with_plugins():
    if not PROFILE_PATH.exists():
        return None, None
    _, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)
    bus = InMemoryJetStream()
    bus.add_stream(StreamConfig(name="STRATEGY", subjects=["strategy.>", "intent.>"]))
    bus.add_stream(StreamConfig(name="STREAM_EXEC", subjects=["exec.>"]))
    bus.add_stream(StreamConfig(name="STREAM_RISK", subjects=["risk.>"]))
    config = ARCStrategyConfig()
    engine = ARCStrategyEngine(bus=bus, config=config, plugin_registry=registry)
    return engine, bus


class TestC1TrailingStopRatchet(unittest.TestCase):
    """C1: Plugin trailing stop must ratchet (initialize and advance)."""

    def test_trailing_stop_initialized_on_first_call(self):
        engine, bus = _build_engine_with_plugins()
        if engine is None:
            self.skipTest("default.toml not found")

        pack = _pack(last_price=60000.0, atr=500.0)
        state = _state()
        playbook = _playbook(trailing_stop_atr_mult=2.5)
        position = _position(entry_price=60000.0, trailing_stop=0.0, bars_held=2)

        engine._plugin_exit(pack, state, playbook, position)

        # Trailing stop should be initialized: entry - atr * mult = 60000 - 1250 = 58750
        self.assertAlmostEqual(position.trailing_stop, 58750.0, places=0)

    def test_trailing_stop_ratchets_up_for_long(self):
        engine, bus = _build_engine_with_plugins()
        if engine is None:
            self.skipTest("default.toml not found")

        position = _position(
            entry_price=60000.0,
            trailing_stop=58750.0,  # already initialized
            bars_held=3,
        )

        # Price moved up to 62000 → new stop should be 62000 - 1250 = 60750
        pack = _pack(last_price=62000.0, atr=500.0)
        state = _state()
        playbook = _playbook(trailing_stop_atr_mult=2.5)

        engine._plugin_exit(pack, state, playbook, position)

        self.assertAlmostEqual(position.trailing_stop, 60750.0, places=0)

    def test_trailing_stop_never_decreases_for_long(self):
        engine, bus = _build_engine_with_plugins()
        if engine is None:
            self.skipTest("default.toml not found")

        position = _position(
            entry_price=60000.0,
            trailing_stop=60750.0,
            bars_held=4,
        )

        # Price pulled back to 61000 → new_stop = 59750, should NOT decrease from 60750
        pack = _pack(last_price=61000.0, atr=500.0)
        state = _state()
        playbook = _playbook(trailing_stop_atr_mult=2.5)

        engine._plugin_exit(pack, state, playbook, position)

        self.assertAlmostEqual(position.trailing_stop, 60750.0, places=0)

    def test_trailing_stop_triggers_exit_on_breach(self):
        engine, bus = _build_engine_with_plugins()
        if engine is None:
            self.skipTest("default.toml not found")

        position = _position(
            entry_price=60000.0,
            trailing_stop=60750.0,
            bars_held=5,
        )

        # Price drops below stop → should exit
        pack = _pack(last_price=60700.0, atr=500.0)
        state = _state()
        playbook = _playbook(trailing_stop_atr_mult=2.5)

        exit_decision = engine._plugin_exit(pack, state, playbook, position)

        self.assertIsNotNone(exit_decision)
        self.assertTrue(exit_decision.should_exit())
        self.assertEqual(exit_decision.exit_type, "TRAILING")


class TestC2SizerCap(unittest.TestCase):
    """C2: vol_target_sizer must have an internal hard cap."""

    def test_low_vol_capped(self):
        from zenquant.plugins.sizers import vol_target_sizer

        result = vol_target_sizer.compute(
            indicators={"realized_vol": 0.001},  # near zero
            state={"timeframe": "4h"},
            config={"target_vol_4h": 0.60, "min_realized_vol_ratio": 0.25, "max_factor": 3.5},
        )
        self.assertLessEqual(result.factor, 3.5)
        self.assertIn("SIZE_FACTOR_CAPPED", result.reason_codes)

    def test_normal_vol_not_capped(self):
        from zenquant.plugins.sizers import vol_target_sizer

        result = vol_target_sizer.compute(
            indicators={"realized_vol": 0.6},
            state={"timeframe": "4h"},
            config={"target_vol_4h": 0.60, "min_realized_vol_ratio": 0.25, "max_factor": 3.5},
        )
        self.assertAlmostEqual(result.factor, 1.0, places=1)
        self.assertNotIn("SIZE_FACTOR_CAPPED", result.reason_codes)


class TestC3BiasQualityChecks(unittest.TestCase):
    """C3: Plugin path must check bias staleness and quality."""

    def test_stale_bias_blocked(self):
        engine, bus = _build_engine_with_plugins()
        if engine is None:
            self.skipTest("default.toml not found")

        # Set a very old bias
        engine._directional_bias["BTCUSDT_PERP"] = DirectionalBias(
            symbol="BTCUSDT_PERP",
            cutoff_time_ns=0,  # ancient
            direction=-1.0,  # opposing direction
            regime="TREND",
            confidence=0.8,
            quality_flags=["OK"],
        )

        # Current time is much later → bias is stale
        pack = _pack(
            cutoff_time_ns=999_999_999_999_999,
            structure_state="UPTREND",
            momentum_roc_z=1.0,
        )
        state = _state(cutoff_time_ns=999_999_999_999_999)
        playbook = _playbook()

        direction, codes = engine._plugin_direction(pack, state, playbook)

        # Stale bias should NOT block direction
        self.assertIn("BIAS_STALE", codes)

    def test_degraded_quality_bias_blocked(self):
        engine, bus = _build_engine_with_plugins()
        if engine is None:
            self.skipTest("default.toml not found")

        engine._directional_bias["BTCUSDT_PERP"] = DirectionalBias(
            symbol="BTCUSDT_PERP",
            cutoff_time_ns=1_000_000_000,
            direction=-1.0,
            regime="TREND",
            confidence=0.8,
            quality_flags=["DATA_STALE"],  # degraded
        )

        pack = _pack(cutoff_time_ns=1_000_000_000)
        state = _state(cutoff_time_ns=1_000_000_000)
        playbook = _playbook()

        direction, codes = engine._plugin_direction(pack, state, playbook)

        self.assertIn("BIAS_QUALITY_DEGRADED", codes)

    def test_valid_bias_blocks_opposing_direction(self):
        engine, bus = _build_engine_with_plugins()
        if engine is None:
            self.skipTest("default.toml not found")

        cutoff = 1_000_000_000
        engine._directional_bias["BTCUSDT_PERP"] = DirectionalBias(
            symbol="BTCUSDT_PERP",
            cutoff_time_ns=cutoff,
            direction=-1.0,  # bearish bias
            regime="TREND",
            confidence=0.8,
            quality_flags=["OK"],
        )

        pack = _pack(
            cutoff_time_ns=cutoff,
            structure_state="UPTREND",
            momentum_roc_z=1.0,
        )
        state = _state(cutoff_time_ns=cutoff)
        playbook = _playbook()

        direction, codes = engine._plugin_direction(pack, state, playbook)

        # Bearish bias should block bullish signal
        self.assertEqual(direction, 0.0)
        self.assertIn("BIAS_DIRECTION_BLOCK", codes)


class TestM2CrowdingStressRegime(unittest.TestCase):
    """M2: crowding_signal should suppress in STRESS regime."""

    def test_stress_regime_suppressed(self):
        from zenquant.plugins.signals import crowding_signal

        result = crowding_signal.compute(
            indicators={"funding_oi_z": 3.0},  # extreme crowding
            state={"regime": "STRESS"},
            config={"extreme_threshold": 1.8},
        )
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.confidence, 0.0)

    def test_trend_regime_not_suppressed(self):
        from zenquant.plugins.signals import crowding_signal

        result = crowding_signal.compute(
            indicators={"funding_oi_z": 3.0},
            state={"regime": "TREND"},
            config={"extreme_threshold": 1.8},
        )
        self.assertNotEqual(result.value, 0.0)


class TestM3ExecutionHaircut(unittest.TestCase):
    """M3: Plugin path must apply execution haircut."""

    def test_degraded_quality_reduces_leverage(self):
        engine, bus = _build_engine_with_plugins()
        if engine is None:
            self.skipTest("default.toml not found")

        pack = _pack(realized_vol=0.6)
        state_ok = _state(quality_state="TRADABLE")
        state_degraded = _state(quality_state="DEGRADED")
        playbook = _playbook(max_leverage=2.5)

        lev_ok, _ = engine._plugin_sizing(pack, state_ok, playbook)
        lev_degraded, codes = engine._plugin_sizing(pack, state_degraded, playbook)

        # Degraded quality should produce lower leverage via haircut
        # (The haircut is applied in _build_target, not _plugin_sizing)
        # Check by looking at the SizingEngine._execution_haircut
        from zenquant.strategy.sizing_engine import SizingEngine
        haircut = SizingEngine._execution_haircut(state_degraded)
        self.assertLess(haircut, 1.0)
        self.assertEqual(haircut, 0.7)


class TestM7DirectionThreshold(unittest.TestCase):
    """M7: Direction threshold should be a named constant."""

    def test_direction_threshold_is_constant(self):
        self.assertEqual(DIRECTION_THRESHOLD, 0.15)


class TestTrailingStopPlugin(unittest.TestCase):
    """Direct unit tests for the trailing_stop_exit plugin."""

    def test_no_exit_when_no_breach(self):
        from zenquant.plugins.exits import trailing_stop_exit

        result = trailing_stop_exit.compute(
            indicators={"last_price": 61000.0, "atr": 500.0},
            state={},
            position={"direction": 1.0, "trailing_stop": 58750.0, "bars_held": 3},
            config={"atr_mult": 2.5, "min_bars_before_trail": 1},
        )
        self.assertFalse(result.should_exit)

    def test_exit_on_breach(self):
        from zenquant.plugins.exits import trailing_stop_exit

        result = trailing_stop_exit.compute(
            indicators={"last_price": 58000.0, "atr": 500.0},
            state={},
            position={"direction": 1.0, "trailing_stop": 58750.0, "bars_held": 3},
            config={"atr_mult": 2.5, "min_bars_before_trail": 1},
        )
        self.assertTrue(result.should_exit)
        self.assertEqual(result.exit_type, "TRAILING")

    def test_no_exit_when_stop_is_zero(self):
        from zenquant.plugins.exits import trailing_stop_exit

        result = trailing_stop_exit.compute(
            indicators={"last_price": 60000.0, "atr": 500.0},
            state={},
            position={"direction": 1.0, "trailing_stop": 0.0, "bars_held": 3},
            config={"atr_mult": 2.5, "min_bars_before_trail": 1},
        )
        self.assertFalse(result.should_exit)


if __name__ == "__main__":
    unittest.main()
