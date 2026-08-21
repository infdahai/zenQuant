"""Integration test: plugin path produces valid output with real plugins."""

from __future__ import annotations

import unittest
from pathlib import Path

from zenquant.domain import (
    ExitDecision,
    IndicatorCoverage,
    IndicatorPack,
    MarketState,
    OpenPositionState,
    PlaybookSelection,
    RiskOverlayDecision,
    TargetPosition,
    Timeframe,
)
from zenquant.infra.event_bus import InMemoryJetStream
from zenquant.plugins.profile import build_from_profile
from zenquant.risk import RiskOverlay, RiskOverlayConfig
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
    multi_momentum_z: float | None = None,
    structure_state: str = "UPTREND",
    cycle_position: float = 1.0,
    last_price: float = 60000.0,
    zen_pulse: float = 65.0,
) -> IndicatorPack:
    if multi_momentum_z is None:
        multi_momentum_z = momentum_roc_z
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
            "multi_momentum_z": multi_momentum_z,
            "structure_state": structure_state,
            "cycle_position": cycle_position,
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
    direction_signal: float = 1.0,
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
        direction_signal=direction_signal,
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


def _target(*, qty: float, cutoff_time_ns: int = 1) -> TargetPosition:
    return TargetPosition(
        intent_id=f"intent-{cutoff_time_ns}",
        portfolio="arc",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=cutoff_time_ns,
        target_qty=qty,
        target_leverage=1.0,
        reduce_only=False,
        regime="TREND",
        regime_confidence=0.8,
        reason_codes=["TEST_INTENT"],
    )


def _position(*, direction: float) -> OpenPositionState:
    return OpenPositionState(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        direction=direction,
        entry_price=60000.0,
        entry_bar_ns=0,
        base_qty=1.0,
        current_qty=1.0,
        trailing_stop=0.0,
        pyramid_count=0,
        bars_held=1,
    )


def _strategy_bus() -> InMemoryJetStream:
    bus = InMemoryJetStream()
    bus.add_stream(
        __import__("zenquant.infra.event_bus", fromlist=["StreamConfig"]).StreamConfig(
            name="STRATEGY", subjects=["strategy.>", "intent.>"]
        )
    )
    return bus


class TestRegistryLoadsRealPlugins(unittest.TestCase):
    """Verify that all plugins in default.toml load successfully."""

    def test_load_default_profile(self):
        if not PROFILE_PATH.exists():
            self.skipTest("default.toml not found")

        profile, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)

        self.assertEqual(
            len(registry.signals), 11
        )  # 11 active including etf_flow_state_signal + cme_positioning_state_signal
        self.assertEqual(len(registry.filters), 2)
        self.assertEqual(len(registry.sizers), 4)  # winning_position_sizer disabled
        self.assertEqual(len(registry.exits), 5)

        # Verify exit priority ordering
        exit_names = [p.meta.name for p in registry.exits]
        self.assertEqual(exit_names[0], "invalidation_exit")  # priority 1
        self.assertEqual(exit_names[-1], "regime_change_exit")  # priority 5

    def test_profile_overrides_can_enable_research_only_plugins(self):
        if not PROFILE_PATH.exists():
            self.skipTest("default.toml not found")

        _, registry = build_from_profile(
            PROFILE_PATH,
            plugin_root=PLUGIN_ROOT,
            config_overrides={
                "signals": {"binance_metrics_state_signal": {"enabled": True, "weight": 0.18}},
                "sizers": {"binance_metrics_risk_sizer": {"enabled": True, "discount_floor": 0.60}},
            },
        )

        self.assertIn("binance_metrics_state_signal", [plugin.meta.name for plugin in registry.signals])
        self.assertIn("binance_metrics_risk_sizer", [plugin.meta.name for plugin in registry.sizers])
        self.assertEqual(registry.signal_weights["binance_metrics_state_signal"], 0.18)

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

    def _build_engine_with_plugins(
        self,
        config: ARCStrategyConfig | None = None,
    ) -> tuple[ARCStrategyEngine, InMemoryJetStream]:
        if not PROFILE_PATH.exists():
            self.skipTest("default.toml not found")

        _, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)
        bus = InMemoryJetStream()
        bus.add_stream(
            __import__("zenquant.infra.event_bus", fromlist=["StreamConfig"]).StreamConfig(
                name="STRATEGY", subjects=["strategy.>", "intent.>"]
            )
        )
        engine = ARCStrategyEngine(bus=bus, config=config or ARCStrategyConfig(), plugin_registry=registry)
        return engine, bus

    def test_v2_snapshot_publishes_only_for_explicit_4h_path(self):
        """Catches a missing V2 audit event or accidental default-path publication."""
        engine, bus = self._build_engine_with_plugins(ARCStrategyConfig(emit_decision_snapshots_v2=True))
        snapshots = []
        requested_targets = []
        bus.subscribe("strategy.decision_snapshot.4h.BTCUSDT_PERP", lambda _subject, payload: snapshots.append(payload))
        bus.subscribe(
            "intent.target_position.arc.BTCUSDT_PERP", lambda _subject, payload: requested_targets.append(payload)
        )

        engine.prime_indicator(_pack())
        engine.prime_state(_state())
        self.assertEqual(snapshots, [])
        self.assertEqual(len(requested_targets), 1)
        bus.publish("intent.target_position.final.BTCUSDT_PERP", requested_targets[0])

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].timeframe, Timeframe.TF_4H)
        self.assertEqual(snapshots[0].symbol, "BTCUSDT_PERP")
        self.assertEqual(snapshots[0].realized_vol, 0.6)
        self.assertEqual(snapshots[0].atr, 500.0)
        self.assertEqual(snapshots[0].indicator_version, "pack.v1")
        self.assertEqual(snapshots[0].quality_flags, ("OK",))
        self.assertEqual(snapshots[0].volatility_target, 1.1)
        self.assertGreater(snapshots[0].exposure_cap, 0.0)

    def test_legacy_default_does_not_publish_v2_snapshot(self):
        """Catches default strategy operation leaking into the opt-in V2 event path."""
        engine, bus = self._build_engine_with_plugins()
        snapshots = []
        bus.subscribe("strategy.decision_snapshot.>", lambda _subject, payload: snapshots.append(payload))

        engine.prime_indicator(_pack())
        engine.prime_state(_state())

        self.assertEqual(snapshots, [])
        bus.publish(
            "risk.overlay.decision",
            RiskOverlayDecision(
                symbol="BTCUSDT_PERP",
                cutoff_time_ns=1,
                original_target_qty=1.0,
                revised_target_qty=0.5,
                lev_cap=0.5,
                reduce_only=False,
                flags=["OK"],
            ),
        )
        self.assertEqual(engine._risk_final_decisions, {})

    def test_v2_snapshot_never_publishes_for_non_4h_state_path(self):
        """Catches a 1h or 5m strategy route bypassing the fixed V2 4h boundary."""
        engine, bus = self._build_engine_with_plugins(ARCStrategyConfig(emit_decision_snapshots_v2=True))
        snapshots = []
        bus.subscribe("strategy.decision_snapshot.>", lambda _subject, payload: snapshots.append(payload))
        state = _state()
        state.timeframe = Timeframe.TF_1H

        engine.prime_indicator(_pack(timeframe=Timeframe.TF_1H))
        engine.prime_state(state)

        self.assertEqual(snapshots, [])

    def test_v2_snapshot_never_publishes_for_non_btc_symbol(self):
        """Catches enabled V2 publication crossing the BTC-only experiment boundary."""
        engine, bus = self._build_engine_with_plugins(ARCStrategyConfig(emit_decision_snapshots_v2=True))
        snapshots = []
        bus.subscribe("strategy.decision_snapshot.>", lambda _subject, payload: snapshots.append(payload))
        pack = _pack(symbol="ETHUSDT_PERP")
        state = _state()
        state.symbol = "ETHUSDT_PERP"

        engine.prime_indicator(pack)
        engine.prime_state(state)

        self.assertEqual(snapshots, [])

    def test_v2_snapshot_does_not_reuse_stale_plugin_alpha_context(self):
        """Catches an exit-only bar inheriting raw alpha from an earlier cutoff."""
        engine, bus = self._build_engine_with_plugins(ARCStrategyConfig(emit_decision_snapshots_v2=True))
        snapshots = []
        bus.subscribe("strategy.decision_snapshot.>", lambda _subject, payload: snapshots.append(payload))
        pack = _pack()
        state = _state(direction_signal=1.0)
        engine._plugin_signal_context[(pack.symbol, pack.timeframe)] = {
            "cutoff_time_ns": 0,
            "effective_direction": -0.7,
            "signal_confidence": 0.9,
        }

        target = _target(qty=1.0)
        engine._publish_decision_snapshot_if_enabled(pack, state, _playbook(), target)
        bus.publish("intent.target_position.final.BTCUSDT_PERP", target)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].alpha_direction, 1.0)
        self.assertEqual(snapshots[0].alpha_raw_score, 1.0)
        self.assertEqual(snapshots[0].alpha_confidence, 0.8)

    def test_v2_snapshot_is_published_only_after_risk_and_binds_final_target(self):
        """Catches the snapshot identity approving a pre-risk strategy quantity."""
        if not PROFILE_PATH.exists():
            self.skipTest("default.toml not found")
        _, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)
        bus = _strategy_bus()
        risk = RiskOverlay(
            bus,
            RiskOverlayConfig(starting_equity=100_000.0, max_leverage=0.1),
        )
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(emit_decision_snapshots_v2=True),
            plugin_registry=registry,
        )
        pack = _pack(last_price=60_000.0)
        state = _state()
        requested = _target(qty=2.0)
        final_targets = []
        risk_decisions = []
        snapshots = []
        bus.subscribe("intent.target_position.final.>", lambda _subject, payload: final_targets.append(payload))
        bus.subscribe("risk.overlay.decision", lambda _subject, payload: risk_decisions.append(payload))
        bus.subscribe("strategy.decision_snapshot.4h.>", lambda _subject, payload: snapshots.append(payload))
        risk.prime_indicator(pack)

        engine._publish_decision_snapshot_if_enabled(pack, state, _playbook(), requested)
        self.assertEqual(snapshots, [])
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", requested)

        self.assertEqual(len(final_targets), 1)
        self.assertEqual(len(snapshots), 1)
        self.assertNotEqual(final_targets[0].target_qty, requested.target_qty)
        self.assertEqual(snapshots[0].target_qty, final_targets[0].target_qty)
        self.assertEqual(snapshots[0].target_leverage, final_targets[0].target_leverage)
        self.assertEqual(snapshots[0].reduce_only, final_targets[0].reduce_only)
        self.assertEqual(snapshots[0].exposure_cap, risk_decisions[0].lev_cap)

    def test_plugin_direction_uptrend(self):
        engine, bus = self._build_engine_with_plugins()

        pack = _pack(structure_state="UPTREND", momentum_roc_z=1.0, cvd_slope=0.1)
        state = _state(regime="TREND", directional_state="UPTREND")
        playbook = _playbook()

        direction, codes = engine._plugin_direction(pack, state, playbook)
        self.assertEqual(direction, 1.0)
        self.assertTrue(any("momentum_signal" in c for c in codes))

    def test_plugin_direction_threshold_can_neutralize_weak_signal(self):
        engine, bus = self._build_engine_with_plugins(ARCStrategyConfig(direction_signal_threshold=0.95))

        pack = _pack(structure_state="UPTREND", momentum_roc_z=1.0, cvd_slope=0.1)
        state = _state(regime="TREND", directional_state="UPTREND")
        playbook = _playbook()

        direction, _ = engine._plugin_direction(pack, state, playbook)
        self.assertEqual(direction, 0.0)

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


class TestStrategyIntentConfirmation(unittest.TestCase):
    def test_entry_intent_requires_configured_persistence(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe("strategy.intent_confirmation.>", lambda _subject, payload: audit_events.append(payload))
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_entry_confirmation_bars=2),
        )

        self.assertIsNone(engine._confirm_directional_intent(_target(qty=1.0, cutoff_time_ns=1)))
        confirmed = engine._confirm_directional_intent(_target(qty=1.0, cutoff_time_ns=2))

        self.assertIsNotNone(confirmed)
        self.assertIn("INTENT_CONFIRMED_2_BARS", confirmed.reason_codes)
        self.assertEqual(audit_events[0]["reason"], "INTENT_CONFIRMATION_PENDING")
        self.assertEqual(audit_events[-1]["reason"], "INTENT_CONFIRMED")

    def test_flip_intent_requires_configured_persistence(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_flip_confirmation_bars=2),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)

        self.assertIsNone(engine._confirm_directional_intent(_target(qty=-1.0, cutoff_time_ns=1)))
        confirmed = engine._confirm_directional_intent(_target(qty=-1.0, cutoff_time_ns=2))

        self.assertIsNotNone(confirmed)
        self.assertLess(confirmed.target_qty, 0.0)
        self.assertIn("INTENT_CONFIRMED_2_BARS", confirmed.reason_codes)

    def test_unconfirmed_flip_can_emit_reduce_only_flatten_intent(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_flip_confirmation_bars=2,
                intent_unconfirmed_flip_reduce_only=True,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)

        flatten = engine._confirm_directional_intent(_target(qty=-1.0, cutoff_time_ns=1))
        confirmed = engine._confirm_directional_intent(_target(qty=-1.0, cutoff_time_ns=2))

        self.assertIsNotNone(flatten)
        self.assertEqual(flatten.target_qty, 0.0)
        self.assertEqual(flatten.target_leverage, 0.0)
        self.assertTrue(flatten.reduce_only)
        self.assertIn("INTENT_FLIP_FLATTEN_FIRST", flatten.reason_codes)
        self.assertIsNotNone(confirmed)
        self.assertLess(confirmed.target_qty, 0.0)

    def test_bearish_override_intent_uses_slower_confirmation(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_entry_confirmation_bars=2,
                intent_bearish_override_confirmation_bars=4,
            ),
        )

        for cutoff_time_ns in (1, 2, 3):
            target = _target(qty=-1.0, cutoff_time_ns=cutoff_time_ns)
            target.reason_codes.append("STATE_BEARISH_DIRECTION_OVERRIDE")
            self.assertIsNone(engine._confirm_directional_intent(target))
        confirmed = _target(qty=-1.0, cutoff_time_ns=4)
        confirmed.reason_codes.append("STATE_BEARISH_DIRECTION_OVERRIDE")
        confirmed = engine._confirm_directional_intent(confirmed)

        self.assertIsNotNone(confirmed)
        assert confirmed is not None
        self.assertIn("INTENT_CONFIRMED_4_BARS", confirmed.reason_codes)

    def test_bearish_override_chop_guard_blocks_noisy_transition_pending_entry(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe(
            "strategy.intent_bearish_override_chop_guard.>",
            lambda _subject, payload: audit_events.append(payload),
        )
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_bearish_override_chop_guard_enabled=True),
        )
        pending = _target(qty=-1.0)
        pending.reason_codes.append("STATE_BEARISH_DIRECTION_OVERRIDE")
        state = _state(directional_state="TRANSITION", quality_state="NOISY", transition_risk=0.55)
        state.directional_confidence = 0.45

        blocked = engine._bearish_override_chop_guard_ready(
            pending=pending,
            state=state,
            timeframe=Timeframe.TF_5M,
            bars_waited=2,
        )
        state.directional_state = "DOWNTREND"
        ready = engine._bearish_override_chop_guard_ready(
            pending=pending,
            state=state,
            timeframe=Timeframe.TF_5M,
            bars_waited=3,
        )

        self.assertFalse(blocked)
        self.assertTrue(ready)
        self.assertEqual(audit_events[-1]["reason"], "INTENT_BEARISH_OVERRIDE_CHOP_GUARD_PENDING")
        self.assertEqual(audit_events[-1]["bars_waited"], 3)

    def test_flat_intent_requires_configured_persistence(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe("strategy.intent_confirmation.>", lambda _subject, payload: audit_events.append(payload))
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_flat_confirmation_bars=2),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)

        held = engine._confirm_directional_intent(_target(qty=0.0, cutoff_time_ns=1), price=60000.0)
        confirmed = engine._confirm_directional_intent(_target(qty=0.0, cutoff_time_ns=2), price=60000.0)

        self.assertIsNotNone(held)
        self.assertGreater(held.target_qty, 0.0)
        self.assertFalse(held.reduce_only)
        self.assertIn("INTENT_FLAT_HELD", held.reason_codes)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed.target_qty, 0.0)
        self.assertIn("INTENT_FLAT_CONFIRMED_2_BARS", confirmed.reason_codes)
        self.assertEqual(audit_events[0]["reason"], "INTENT_FLAT_CONFIRMATION_PENDING")
        self.assertEqual(audit_events[-1]["reason"], "INTENT_FLAT_CONFIRMED")

    def test_flat_step_reduce_throttles_repeated_non_risk_flat_targets(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe("strategy.intent_flat_step.>", lambda _subject, payload: audit_events.append(payload))
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_flat_confirmation_bars=2,
                intent_flat_reduce_step_pct=0.50,
                intent_flat_step_cadence_bars=2,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)

        held = engine._confirm_directional_intent(_target(qty=0.0, cutoff_time_ns=1), price=60000.0)
        stepped = engine._confirm_directional_intent(_target(qty=0.0, cutoff_time_ns=2), price=60000.0)

        self.assertIsNotNone(held)
        self.assertIn("INTENT_FLAT_HELD", held.reason_codes)
        self.assertIsNotNone(stepped)
        self.assertEqual(stepped.target_qty, 0.5)
        self.assertAlmostEqual(stepped.target_leverage, 0.3)
        self.assertIn("INTENT_FLAT_CONFIRMED_2_BARS", stepped.reason_codes)
        self.assertIn("INTENT_FLAT_STEP_REDUCE", stepped.reason_codes)
        self.assertEqual(audit_events[-1]["reason"], "INTENT_FLAT_STEP_REDUCE")

        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)].current_qty = 0.5
        cooldown_hold = engine._confirm_directional_intent(_target(qty=0.0, cutoff_time_ns=3), price=60000.0)

        self.assertIsNotNone(cooldown_hold)
        self.assertEqual(cooldown_hold.target_qty, 0.5)
        self.assertAlmostEqual(cooldown_hold.target_leverage, 0.3)
        self.assertIn("INTENT_FLAT_STEP_CADENCE", cooldown_hold.reason_codes)
        self.assertEqual(audit_events[-1]["reason"], "INTENT_FLAT_STEP_CADENCE")

    def test_range_light_pending_entry_waits_for_lower_timeframe_confirmation(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe(
            "strategy.intent_range_entry_confirmation.>",
            lambda _subject, payload: audit_events.append(payload),
        )
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                execution_timeframe=Timeframe.TF_1H,
                intent_range_entry_confirmation_bars=6,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_1H)] = _position(direction=1.0)
        pending = _target(qty=-1.0, cutoff_time_ns=0)
        pending.reason_codes[:] = ["REGIME_RANGE", "PLAYBOOK_RANGE_LIGHT", "TREND_SHORT"]
        engine._pending_4h_target["BTCUSDT_PERP"] = pending
        engine._pending_4h_bars["BTCUSDT_PERP"] = 4
        engine.prime_indicator(_pack(timeframe=Timeframe.TF_1H))
        state = _state(regime="RANGE", directional_state="DOWNTREND")
        state.timeframe = Timeframe.TF_1H

        self.assertIsNone(engine._try_1h_entry(state))

        self.assertEqual(engine._pending_4h_bars["BTCUSDT_PERP"], 5)
        self.assertEqual(audit_events[-1]["reason"], "INTENT_RANGE_ENTRY_CONFIRMATION_PENDING")
        self.assertEqual(audit_events[-1]["bars"], 5)
        self.assertEqual(audit_events[-1]["required_bars"], 6)

    def test_range_light_pending_entry_executes_after_lower_timeframe_confirmation(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                execution_timeframe=Timeframe.TF_1H,
                intent_range_entry_confirmation_bars=6,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_1H)] = _position(direction=1.0)
        pending = _target(qty=-1.0, cutoff_time_ns=0)
        pending.reason_codes[:] = ["REGIME_RANGE", "PLAYBOOK_RANGE_LIGHT", "TREND_SHORT"]
        engine._pending_4h_target["BTCUSDT_PERP"] = pending
        engine._pending_4h_bars["BTCUSDT_PERP"] = 5
        engine.prime_indicator(_pack(timeframe=Timeframe.TF_1H))
        state = _state(regime="RANGE", directional_state="DOWNTREND")
        state.timeframe = Timeframe.TF_1H

        target = engine._try_1h_entry(state)

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.timeframe, Timeframe.TF_1H)
        self.assertLess(target.target_qty, 0.0)
        self.assertIn("INTENT_RANGE_ENTRY_CONFIRMED_6_BARS", target.reason_codes)
        self.assertIn("1H_ENTRY_OPTIMIZED", target.reason_codes)

    def test_range_trailing_exit_arms_reentry_cooldown(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe(
            "strategy.intent_range_reentry_cooldown.>",
            lambda _subject, payload: audit_events.append(payload),
        )
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_range_reentry_cooldown_bars=3),
        )
        engine._arm_range_reentry_cooldown_if_needed(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_5M,
            cutoff_time_ns=10,
            playbook=_playbook(playbook_id="RANGE_LIGHT"),
            exit_decision=ExitDecision(
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_5M,
                cutoff_time_ns=10,
                exit_type="TRAILING",
                exit_urgency="IMMEDIATE",
                target_reduction_pct=1.0,
                stop_price=60000.0,
                reason_code="TRAILING_STOP_BREACH",
                reason_detail="test",
                reason_codes=["EXIT_TRAILING_STOP"],
            ),
        )

        cooldown_until_ns = 10 + (3 * Timeframe.TF_5M.seconds * 1_000_000_000)
        self.assertEqual(engine._range_reentry_cooldown_until_ns[("BTCUSDT_PERP", Timeframe.TF_5M)], cooldown_until_ns)
        self.assertEqual(audit_events[-1]["reason"], "INTENT_RANGE_REENTRY_COOLDOWN_ARMED")
        self.assertEqual(audit_events[-1]["cooldown_bars"], 3)

    def test_range_reentry_cooldown_blocks_pending_entry_until_expiry(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe(
            "strategy.intent_range_reentry_cooldown.>",
            lambda _subject, payload: audit_events.append(payload),
        )
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_range_reentry_cooldown_bars=3),
        )
        bar_ns = Timeframe.TF_5M.seconds * 1_000_000_000
        cooldown_until_ns = 10 + (3 * bar_ns)
        engine._range_reentry_cooldown_until_ns[("BTCUSDT_PERP", Timeframe.TF_5M)] = cooldown_until_ns
        pending = _target(qty=1.0, cutoff_time_ns=10)
        pending.reason_codes[:] = ["REGIME_RANGE", "PLAYBOOK_RANGE_LIGHT", "TREND_LONG"]

        blocked = engine._range_reentry_cooldown_ready(
            pending=pending,
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_5M,
            cutoff_time_ns=10,
        )
        ready = engine._range_reentry_cooldown_ready(
            pending=pending,
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_5M,
            cutoff_time_ns=cooldown_until_ns,
        )

        self.assertFalse(blocked)
        self.assertTrue(ready)
        self.assertNotIn(("BTCUSDT_PERP", Timeframe.TF_5M), engine._range_reentry_cooldown_until_ns)
        self.assertEqual(audit_events[-1]["reason"], "INTENT_RANGE_REENTRY_COOLDOWN_PENDING")
        self.assertEqual(audit_events[-1]["remaining_bars"], 3)

    def test_pending_lower_timeframe_target_is_cleared_when_confirmation_waits(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                execution_timeframe=Timeframe.TF_1H,
                intent_entry_confirmation_bars=2,
            ),
        )
        engine._pending_4h_target["BTCUSDT_PERP"] = _target(qty=-1.0, cutoff_time_ns=0)
        engine._pending_4h_bars["BTCUSDT_PERP"] = 1
        engine._pending_1h_target["BTCUSDT_PERP"] = _target(qty=-1.0, cutoff_time_ns=0)
        engine._pending_1h_bars["BTCUSDT_PERP"] = 1

        engine.prime_indicator(_pack())
        engine.prime_state(_state())

        self.assertNotIn("BTCUSDT_PERP", engine._pending_4h_target)
        self.assertNotIn("BTCUSDT_PERP", engine._pending_1h_target)

    def test_add_signal_gate_holds_same_direction_add_when_signal_is_weak(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_add_signal_threshold=0.40),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        engine._plugin_signal_context[("BTCUSDT_PERP", Timeframe.TF_4H)] = {"effective_direction": 0.25}
        reason_codes: list[str] = []

        qty, leverage = engine._apply_same_direction_add_gate(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=1.5,
            target_leverage=0.9,
            price=60000.0,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 1.0)
        self.assertAlmostEqual(leverage, 0.6)
        self.assertIn("INTENT_ADD_SIGNAL_GATE", reason_codes)

    def test_add_signal_gate_allows_same_direction_add_when_signal_is_strong(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_add_signal_threshold=0.40),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        engine._plugin_signal_context[("BTCUSDT_PERP", Timeframe.TF_4H)] = {"effective_direction": 0.45}
        reason_codes: list[str] = []

        qty, leverage = engine._apply_same_direction_add_gate(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=1.5,
            target_leverage=0.9,
            price=60000.0,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 1.5)
        self.assertEqual(leverage, 0.9)
        self.assertNotIn("INTENT_ADD_SIGNAL_GATE", reason_codes)

    def test_add_confirmation_holds_same_direction_add_until_persistent(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe("strategy.intent_add_confirmation.>", lambda _subject, payload: audit_events.append(payload))
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_add_signal_threshold=0.40,
                intent_add_confirmation_bars=2,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        engine._plugin_signal_context[("BTCUSDT_PERP", Timeframe.TF_4H)] = {"effective_direction": 0.45}

        first_reasons: list[str] = []
        first_qty, first_leverage = engine._apply_same_direction_add_gate(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=1.5,
            target_leverage=0.9,
            price=60000.0,
            reason_codes=first_reasons,
        )
        second_reasons: list[str] = []
        second_qty, second_leverage = engine._apply_same_direction_add_gate(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=1.5,
            target_leverage=0.9,
            price=60000.0,
            reason_codes=second_reasons,
        )

        self.assertEqual(first_qty, 1.0)
        self.assertAlmostEqual(first_leverage, 0.6)
        self.assertIn("INTENT_ADD_CONFIRMATION_PENDING", first_reasons)
        self.assertIn("INTENT_ADD_HELD", first_reasons)
        self.assertEqual(second_qty, 1.5)
        self.assertEqual(second_leverage, 0.9)
        self.assertIn("INTENT_ADD_CONFIRMED_2_BARS", second_reasons)
        self.assertEqual(audit_events[0]["reason"], "INTENT_ADD_CONFIRMATION_PENDING")
        self.assertEqual(audit_events[-1]["reason"], "INTENT_ADD_CONFIRMED")

    def test_time_leverage_guard_holds_existing_position_before_flip_is_confirmed(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe("strategy.time_leverage_hold.>", lambda _subject, payload: audit_events.append(payload))
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_time_leverage_hold_enabled=True,
                intent_time_leverage_min_hold_bars=12,
                intent_time_leverage_flip_signal_threshold=0.60,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        engine._plugin_signal_context[("BTCUSDT_PERP", Timeframe.TF_4H)] = {"effective_direction": -0.35}
        reason_codes: list[str] = []

        qty, leverage = engine._apply_time_leverage_hold_guard(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=-1.5,
            target_leverage=0.9,
            price=60000.0,
            state=_state(directional_state="UPTREND", transition_risk=0.2),
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 1.0)
        self.assertAlmostEqual(leverage, 0.6)
        self.assertIn("TIME_LEVERAGE_FLIP_HELD", reason_codes)
        self.assertIn("TIME_LEVERAGE_MIN_HOLD", reason_codes)
        self.assertEqual(audit_events[-1]["reason"], "TIME_LEVERAGE_FLIP_HELD")

    def test_time_leverage_guard_allows_confirmed_opposite_trend_after_hold_window(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_time_leverage_hold_enabled=True,
                intent_time_leverage_min_hold_bars=12,
                intent_time_leverage_flip_signal_threshold=0.60,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)].bars_held = 15
        engine._plugin_signal_context[("BTCUSDT_PERP", Timeframe.TF_4H)] = {"effective_direction": -0.75}
        reason_codes: list[str] = []

        qty, leverage = engine._apply_time_leverage_hold_guard(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=-1.5,
            target_leverage=0.9,
            price=60000.0,
            state=_state(directional_state="DOWNTREND", transition_risk=0.6),
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, -1.5)
        self.assertEqual(leverage, 0.9)
        self.assertIn("TIME_LEVERAGE_FLIP_CONFIRMED", reason_codes)

    def test_time_leverage_trailing_exit_hold_keeps_long_before_confirmed_reversal(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe(
            "strategy.time_leverage_trailing_exit_hold.>",
            lambda _subject, payload: audit_events.append(payload),
        )
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                execution_timeframe=Timeframe.TF_5M,
                intent_time_leverage_hold_enabled=True,
                intent_time_leverage_min_hold_bars=12,
                intent_time_leverage_trailing_exit_hold_enabled=True,
                intent_time_leverage_trailing_exit_max_adverse_atr=2.5,
            ),
        )
        position = _position(direction=1.0)
        position.timeframe = Timeframe.TF_5M
        position.bars_held = 4
        exit_decision = ExitDecision(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_5M,
            cutoff_time_ns=1,
            exit_type="TRAILING",
            exit_urgency="IMMEDIATE",
            target_reduction_pct=1.0,
            stop_price=59000.0,
            reason_code="TRAILING",
            reason_detail="test",
            reason_codes=["EXIT_TRAILING_STOP"],
        )

        target = engine._time_leverage_trailing_exit_hold_target(
            pack=_pack(timeframe=Timeframe.TF_5M, last_price=59000.0),
            state=_state(directional_state="TRANSITION", transition_risk=0.3),
            position=position,
            exit_decision=exit_decision,
            price=59000.0,
            atr=500.0,
        )

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.target_qty, 1.0)
        self.assertIn("TIME_LEVERAGE_TRAILING_EXIT_HELD", target.reason_codes)
        self.assertIn("TIME_LEVERAGE_MIN_HOLD", target.reason_codes)
        self.assertEqual(audit_events[-1]["reason"], "TIME_LEVERAGE_TRAILING_EXIT_HELD")
        self.assertEqual(audit_events[-1]["adverse_atr"], 2.0)

    def test_time_leverage_trailing_exit_hold_allows_confirmed_adverse_trend(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                execution_timeframe=Timeframe.TF_5M,
                intent_time_leverage_hold_enabled=True,
                intent_time_leverage_min_hold_bars=12,
                intent_time_leverage_flip_signal_threshold=0.6,
                intent_time_leverage_confidence_floor=0.65,
                intent_time_leverage_transition_risk_floor=0.45,
                intent_time_leverage_trailing_exit_hold_enabled=True,
            ),
        )
        position = _position(direction=1.0)
        position.timeframe = Timeframe.TF_5M
        position.bars_held = 20
        engine._plugin_signal_context[("BTCUSDT_PERP", Timeframe.TF_5M)] = {"effective_direction": -0.8}
        exit_decision = ExitDecision(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_5M,
            cutoff_time_ns=1,
            exit_type="TRAILING",
            exit_urgency="IMMEDIATE",
            target_reduction_pct=1.0,
            stop_price=59000.0,
            reason_code="TRAILING",
            reason_detail="test",
            reason_codes=["EXIT_TRAILING_STOP"],
        )

        target = engine._time_leverage_trailing_exit_hold_target(
            pack=_pack(timeframe=Timeframe.TF_5M, last_price=59000.0),
            state=_state(directional_state="DOWNTREND", transition_risk=0.6),
            position=position,
            exit_decision=exit_decision,
            price=59000.0,
            atr=500.0,
        )

        self.assertIsNone(target)

    def test_time_leverage_trailing_stop_widens_for_long_bias(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe("strategy.time_leverage_trailing_stop.>", lambda _subject, payload: audit_events.append(payload))
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_time_leverage_hold_enabled=True,
                intent_time_leverage_long_bias_enabled=True,
                intent_time_leverage_long_bias_confidence_floor=0.65,
                intent_time_leverage_trailing_stop_atr_mult=9.0,
            ),
        )

        atr_mult = engine._time_leverage_trailing_stop_atr_mult(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_5M,
            state=_state(directional_state="UPTREND", transition_risk=0.3),
            position=_position(direction=1.0),
            default_mult=4.5,
        )

        self.assertEqual(atr_mult, 9.0)
        self.assertEqual(audit_events[-1]["reason"], "TIME_LEVERAGE_TRAILING_STOP_WIDENED")

    def test_time_leverage_trailing_stop_keeps_default_for_short(self):
        engine = ARCStrategyEngine(
            bus=_strategy_bus(),
            config=ARCStrategyConfig(
                intent_time_leverage_hold_enabled=True,
                intent_time_leverage_long_bias_enabled=True,
                intent_time_leverage_trailing_stop_atr_mult=9.0,
            ),
        )

        atr_mult = engine._time_leverage_trailing_stop_atr_mult(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_5M,
            state=_state(directional_state="UPTREND", transition_risk=0.3),
            position=_position(direction=-1.0),
            default_mult=4.5,
        )

        self.assertEqual(atr_mult, 4.5)

    def test_min_hours_before_trail_normalizes_across_timeframes(self):
        engine = ARCStrategyEngine(
            bus=_strategy_bus(),
            config=ARCStrategyConfig(min_bars_before_trail=1, min_hours_before_trail=20.0),
        )

        self.assertEqual(engine._min_bars_before_trail(Timeframe.TF_1H), 20)
        self.assertEqual(engine._min_bars_before_trail(Timeframe.TF_5M), 240)

    def test_time_leverage_size_hysteresis_holds_small_aligned_reduce(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe(
            "strategy.time_leverage_size_hysteresis.>", lambda _subject, payload: audit_events.append(payload)
        )
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_time_leverage_size_hysteresis_enabled=True,
                intent_time_leverage_reduce_deadband_pct=0.20,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        reason_codes: list[str] = []

        qty, leverage = engine._apply_time_leverage_size_hysteresis(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=0.85,
            target_leverage=0.51,
            price=60000.0,
            state=_state(directional_state="UPTREND"),
            reduce_only=False,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 1.0)
        self.assertAlmostEqual(leverage, 0.6)
        self.assertIn("TIME_LEVERAGE_SIZE_HELD", reason_codes)
        self.assertIn("TIME_LEVERAGE_REDUCE_DEADBAND", reason_codes)
        self.assertEqual(audit_events[-1]["resize"], "reduce")

    def test_time_leverage_size_hysteresis_allows_reduce_when_trend_breaks(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_time_leverage_size_hysteresis_enabled=True,
                intent_time_leverage_reduce_deadband_pct=0.20,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        reason_codes: list[str] = []

        qty, leverage = engine._apply_time_leverage_size_hysteresis(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=0.85,
            target_leverage=0.51,
            price=60000.0,
            state=_state(directional_state="DOWNTREND"),
            reduce_only=False,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 0.85)
        self.assertEqual(leverage, 0.51)
        self.assertNotIn("TIME_LEVERAGE_SIZE_HELD", reason_codes)

    def test_same_direction_reduce_confirmation_holds_and_aggregates_reduce(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe("strategy.intent_reduce_confirmation.>", lambda _subject, payload: audit_events.append(payload))
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_same_direction_reduce_confirmation_bars=3),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)

        reason_codes: list[str] = []
        qty, leverage = engine._apply_same_direction_reduce_confirmation(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=0.8,
            target_leverage=0.48,
            price=60000.0,
            state=_state(directional_state="UPTREND"),
            reduce_only=False,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 1.0)
        self.assertAlmostEqual(leverage, 0.6)
        self.assertIn("INTENT_REDUCE_CONFIRMATION_PENDING", reason_codes)
        self.assertEqual(audit_events[-1]["pending_target_qty"], 0.8)

        reason_codes = []
        qty, leverage = engine._apply_same_direction_reduce_confirmation(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=0.6,
            target_leverage=0.36,
            price=60000.0,
            state=_state(directional_state="UPTREND"),
            reduce_only=False,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 1.0)
        self.assertAlmostEqual(leverage, 0.6)
        self.assertEqual(audit_events[-1]["pending_target_qty"], 0.6)

        reason_codes = []
        qty, leverage = engine._apply_same_direction_reduce_confirmation(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=0.7,
            target_leverage=0.42,
            price=60000.0,
            state=_state(directional_state="UPTREND"),
            reduce_only=False,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 0.6)
        self.assertAlmostEqual(leverage, 0.36)
        self.assertIn("INTENT_REDUCE_CONFIRMED_3_BARS", reason_codes)
        self.assertIn("INTENT_REDUCE_AGGREGATED", reason_codes)
        self.assertEqual(audit_events[-1]["reason"], "INTENT_REDUCE_CONFIRMED")
        self.assertEqual(audit_events[-1]["confirmed_target_qty"], 0.6)

    def test_same_direction_reduce_confirmation_bypasses_urgent_trend_break(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe("strategy.intent_reduce_confirmation.>", lambda _subject, payload: audit_events.append(payload))
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_same_direction_reduce_confirmation_bars=4),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        reason_codes: list[str] = []

        qty, leverage = engine._apply_same_direction_reduce_confirmation(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=0.5,
            target_leverage=0.3,
            price=60000.0,
            state=_state(directional_state="DOWNTREND"),
            reduce_only=False,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 0.5)
        self.assertEqual(leverage, 0.3)
        self.assertIn("INTENT_REDUCE_CONFIRMATION_BYPASS", reason_codes)
        self.assertEqual(audit_events, [])

    def test_time_leverage_long_bias_blocks_short_and_accumulates_dip(self):
        bus = _strategy_bus()
        audit_events = []
        bus.subscribe("strategy.time_leverage_long_bias.>", lambda _subject, payload: audit_events.append(payload))
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_time_leverage_long_bias_enabled=True,
                intent_time_leverage_long_bias_confidence_floor=0.65,
                intent_time_leverage_long_bias_transition_ceiling=0.45,
                intent_time_leverage_dip_accumulation_enabled=True,
                intent_time_leverage_dip_min_adverse_atr=1.0,
                intent_time_leverage_dip_max_adverse_atr=3.0,
                intent_time_leverage_dip_add_pct=0.20,
                intent_time_leverage_dip_max_leverage=3.0,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        reason_codes: list[str] = []

        qty, leverage = engine._apply_time_leverage_long_bias_guard(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=-1.5,
            target_leverage=0.9,
            price=59000.0,
            atr=500.0,
            state=_state(directional_state="UPTREND", transition_risk=0.2),
            reduce_only=False,
            reason_codes=reason_codes,
        )

        self.assertAlmostEqual(qty, 1.2)
        self.assertAlmostEqual(leverage, 0.708)
        self.assertIn("TIME_LEVERAGE_LONG_BIAS_ACTIVE", reason_codes)
        self.assertIn("TIME_LEVERAGE_SHORT_BLOCKED", reason_codes)
        self.assertIn("TIME_LEVERAGE_DIP_ACCUMULATION", reason_codes)
        self.assertEqual(audit_events[-1]["action"], "dip_accumulation")

    def test_time_leverage_long_bias_holds_aligned_reduction(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(
                intent_time_leverage_long_bias_enabled=True,
                intent_time_leverage_long_bias_hold_reductions=True,
            ),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        reason_codes: list[str] = []

        qty, leverage = engine._apply_time_leverage_long_bias_guard(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=0.5,
            target_leverage=0.3,
            price=60000.0,
            atr=500.0,
            state=_state(directional_state="UPTREND", transition_risk=0.2),
            reduce_only=False,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 1.0)
        self.assertAlmostEqual(leverage, 0.6)
        self.assertIn("TIME_LEVERAGE_REDUCTION_HELD", reason_codes)

    def test_time_leverage_long_bias_does_not_block_reduce_only(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_time_leverage_long_bias_enabled=True),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=1.0)
        reason_codes: list[str] = []

        qty, leverage = engine._apply_time_leverage_long_bias_guard(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=0.5,
            target_leverage=0.3,
            price=60000.0,
            atr=500.0,
            state=_state(directional_state="UPTREND", transition_risk=0.2),
            reduce_only=True,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 0.5)
        self.assertEqual(leverage, 0.3)
        self.assertEqual(reason_codes, [])

    def test_time_leverage_long_bias_flattens_continued_short(self):
        bus = _strategy_bus()
        engine = ARCStrategyEngine(
            bus=bus,
            config=ARCStrategyConfig(intent_time_leverage_long_bias_enabled=True),
        )
        engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)] = _position(direction=-1.0)
        reason_codes: list[str] = []

        qty, leverage = engine._apply_time_leverage_long_bias_guard(
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            target_qty=-1.5,
            target_leverage=0.9,
            price=60000.0,
            atr=500.0,
            state=_state(directional_state="UPTREND", transition_risk=0.2),
            reduce_only=False,
            reason_codes=reason_codes,
        )

        self.assertEqual(qty, 0.0)
        self.assertEqual(leverage, 0.0)
        self.assertIn("TIME_LEVERAGE_SHORT_BLOCKED", reason_codes)

    def test_range_bear_profit_run_override_requires_bear_context(self):
        engine = ARCStrategyEngine(
            bus=_strategy_bus(),
            config=ARCStrategyConfig(
                intent_range_bear_profit_run_override_enabled=True,
                intent_range_bear_profit_run_direction_signal=-0.25,
                intent_range_bear_profit_run_momentum_threshold=-0.20,
                intent_range_bear_profit_run_confidence_floor=0.50,
                intent_range_bear_profit_run_max_transition_risk=0.55,
            ),
        )

        self.assertTrue(
            engine._range_bear_profit_run_override_ready(
                pack=_pack(
                    momentum_roc_z=-0.35,
                    multi_momentum_z=-0.40,
                    structure_state="DOWNTREND",
                    cycle_position=0.92,
                ),
                state=_state(
                    regime="RANGE",
                    directional_state="DOWNTREND",
                    transition_risk=0.35,
                    direction_signal=-0.30,
                ),
                playbook=_playbook(playbook_id="RANGE_LIGHT"),
            )
        )
        self.assertTrue(
            engine._range_bear_profit_run_override_ready(
                pack=_pack(
                    momentum_roc_z=-0.35,
                    multi_momentum_z=-0.40,
                    structure_state="DOWNTREND",
                    cycle_position=0.92,
                ),
                state=_state(
                    regime="RANGE",
                    directional_state="DOWNTREND",
                    transition_risk=0.35,
                    direction_signal=-0.30,
                ),
                playbook=_playbook(playbook_id="BEAR_DEFENSE"),
            )
        )
        self.assertFalse(
            engine._range_bear_profit_run_override_ready(
                pack=_pack(
                    momentum_roc_z=-0.35,
                    multi_momentum_z=-0.40,
                    structure_state="DOWNTREND",
                    cycle_position=0.92,
                ),
                state=_state(
                    regime="RANGE",
                    directional_state="TRANSITION",
                    transition_risk=0.35,
                    direction_signal=0.35,
                ),
                playbook=_playbook(playbook_id="RANGE_LIGHT"),
            )
        )
        self.assertTrue(
            engine._range_bear_profit_run_override_ready(
                pack=_pack(
                    momentum_roc_z=-0.35,
                    multi_momentum_z=-0.40,
                    structure_state="NEUTRAL",
                    cycle_position=0.90,
                ),
                state=_state(
                    regime="RANGE",
                    directional_state="TRANSITION",
                    transition_risk=0.35,
                    direction_signal=0.0,
                ),
                playbook=_playbook(playbook_id="RANGE_LIGHT"),
            )
        )


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
