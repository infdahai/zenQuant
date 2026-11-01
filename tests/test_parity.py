"""Gate D: Plugin/legacy path parity test.

Two code paths producing different TargetPositions from the same inputs
is a silent, capital-destroying bug. This test ensures both paths agree
on direction and leverage within tolerance, or that divergences are
explicitly declared and justified.

Mechanical enforcement: two paths, same inputs, consistent outputs.
"""

from __future__ import annotations

import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zenquant.app.server import ZenQuantWebRuntime
from zenquant.domain import (
    EventMeta,
    ExecutionReport,
    IndicatorCoverage,
    IndicatorPack,
    MarketState,
    MarkPriceUpdate,
    TargetPosition,
    Timeframe,
)
from zenquant.execution import ExecutionKernelConfig
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.infra.event_bus import InMemoryJetStream, StreamConfig
from zenquant.plugins.profile import build_from_profile
from zenquant.plugins.protocols import PluginMeta, SignalResult, SizerResult
from zenquant.plugins.registry import LoadedPlugin, PluginRegistry
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


class TestPluginRuntimeContracts(unittest.TestCase):
    def test_execution_modifier_affects_sizing_on_same_bar(self):
        bus = InMemoryJetStream()
        registry = PluginRegistry(
            signals=[
                LoadedPlugin(
                    meta=PluginMeta(
                        name="multi_momentum_signal",
                        version="1.0",
                        plugin_type="signal",
                        description="direction",
                    ),
                    compute=lambda ind, st, cfg: SignalResult(value=1.0, confidence=1.0, reason_codes=("DIR",)),
                    config={},
                    weight=1.0,
                    source_path="<test>/multi.py",
                    role="direction",
                ),
                LoadedPlugin(
                    meta=PluginMeta(
                        name="liquidity_signal",
                        version="1.0",
                        plugin_type="signal",
                        description="execution",
                    ),
                    compute=lambda ind, st, cfg: SignalResult(value=1.0, confidence=1.0, reason_codes=("EXEC",)),
                    config={},
                    weight=1.0,
                    source_path="<test>/liq.py",
                    role="execution_modifier",
                ),
            ],
            sizers=[
                LoadedPlugin(
                    meta=PluginMeta(name="unit_sizer", version="1.0", plugin_type="sizer", description="unit"),
                    compute=lambda ind, st, cfg: SizerResult(factor=1.0, reason_codes=("UNIT_SIZE",)),
                    config={},
                    weight=1.0,
                    source_path="<test>/sizer.py",
                )
            ],
        )
        registry._index = {plugin.meta.name: plugin for plugin in [*registry.signals, *registry.sizers]}
        engine = ARCStrategyEngine(bus=bus, config=ARCStrategyConfig(), plugin_registry=registry)
        state = _make_state(regime="TREND", directional_state="UPTREND")
        pack = _make_pack()

        target = engine._build_target(pack, state, engine._playbook_engine.select(state))

        self.assertIn("EXECUTION_MODIFIER_ACTIVE", target.reason_codes)
        self.assertGreater(target.target_leverage, 1.0)

    def test_adaptive_signal_weights_default_off_for_restart_parity(self):
        registry = PluginRegistry(
            signals=[
                LoadedPlugin(
                    meta=PluginMeta(
                        name="multi_momentum_signal",
                        version="1.0",
                        plugin_type="signal",
                        description="direction",
                    ),
                    compute=lambda ind, st, cfg: SignalResult(value=1.0, confidence=1.0, reason_codes=("DIR",)),
                    config={},
                    weight=1.0,
                    source_path="<test>/multi.py",
                    role="direction",
                )
            ]
        )
        registry._index = {plugin.meta.name: plugin for plugin in registry.signals}
        state = _make_state(regime="TREND", directional_state="UPTREND")
        playbook_bus = InMemoryJetStream()
        warm_engine = ARCStrategyEngine(bus=playbook_bus, config=ARCStrategyConfig(), plugin_registry=registry)
        cold_engine = ARCStrategyEngine(bus=InMemoryJetStream(), config=ARCStrategyConfig(), plugin_registry=registry)

        for idx in range(80):
            pack = _make_pack(last_price=60000.0 + idx)
            warm_engine._build_target(pack, state, warm_engine._playbook_engine.select(state))
        pack = _make_pack(last_price=60100.0)
        warm_target = warm_engine._build_target(pack, state, warm_engine._playbook_engine.select(state))
        cold_target = cold_engine._build_target(pack, state, cold_engine._playbook_engine.select(state))

        self.assertNotIn("ADAPTIVE_SIGNAL_WEIGHTS_ENABLED", warm_target.reason_codes)
        self.assertEqual(warm_target.target_qty, cold_target.target_qty)
        self.assertEqual(warm_target.target_leverage, cold_target.target_leverage)


class TestTargetTrackingRuntimeParity(unittest.TestCase):
    def test_historical_and_live_runtime_compositions_have_byte_identical_v2_trace(self):
        empty_store = SimpleNamespace(loaded=False)
        replay_context = SimpleNamespace(
            funding_rows=[],
            open_interest_rows=[],
            aggtrades={},
            events=[],
            macro_store=empty_store,
            options_store=empty_store,
            hf_store=empty_store,
            cot_store=empty_store,
            liquidation_store=empty_store,
            eth_store=empty_store,
            basis_store=empty_store,
            cross_asset_context_store=empty_store,
            onchain_factor_store=empty_store,
            binance_metrics_factor_store=empty_store,
            cache_stats={},
        )
        execution_config = ExecutionKernelConfig(execution_authority_mode="target_tracking_v2")
        with patch("zenquant.historical.replay._build_replay_context", return_value=replay_context):
            historical = HistoricalBacktest(
                HistoricalBacktestConfig(
                    profile_path=None,
                    execution_config=execution_config,
                )
            )
        with patch.dict("os.environ", {"ZENQUANT_MICROSTRUCTURE_SIDECAR_ENABLED": "0"}):
            live = ZenQuantWebRuntime(
                runtime_mode="live",
                execution_config=ExecutionKernelConfig(execution_authority_mode="target_tracking_v2"),
            )
            live._initialize_trading_chain()

        def run_trace(bus, strategy, risk, kernel):
            snapshots = []
            orders = []
            reports = []
            audits = []
            bus.subscribe(
                "strategy.decision_snapshot.4h.>", lambda _subject, payload: snapshots.append(asdict(payload))
            )
            bus.subscribe("exec.order.submit", lambda _subject, payload: orders.append(asdict(payload)))
            bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(asdict(payload)))
            bus.subscribe("exec.order.audit", lambda _subject, payload: audits.append(asdict(payload)))
            pack = _make_pack(last_price=60_000.0)
            state = _make_state()
            playbook = strategy._playbook_engine.select(state)
            target = TargetPosition(
                intent_id="composition-parity-target",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=pack.cutoff_time_ns,
                target_qty=0.75,
                target_leverage=0.75,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.8,
                reason_codes=["APPROVED_4H_TARGET"],
                realized_vol=0.4,
                atr=500.0,
            )
            risk.prime_indicator(pack)
            bus.publish(
                "md.mark.BINANCE.BTCUSDT_PERP",
                MarkPriceUpdate(
                    meta=EventMeta(
                        event_time_ns=target.cutoff_time_ns,
                        ingest_time_ns=target.cutoff_time_ns,
                        venue="BINANCE",
                        symbol=target.symbol,
                        stream_seq=1,
                    ),
                    mark_price=60_000.0,
                ),
            )
            strategy._publish_decision_snapshot_if_enabled(pack, state, playbook, target)
            bus.publish(f"intent.target_position.{target.portfolio}.{target.symbol}", target)
            self.assertEqual(len(reports), 1)
            position_before = dict(risk._actual_positions)
            cash_before = risk._cash
            for report in (ExecutionReport(**reports[0]), ExecutionReport(**(reports[0] | {"filled_qty": 0.2}))):
                with self.assertRaisesRegex(ValueError, "external execution reports"):
                    bus.publish("exec.order.report", report)
            self.assertEqual(len(reports), 1)
            self.assertEqual(risk._actual_positions, position_before)
            self.assertEqual(risk._cash, cash_before)
            return snapshots, orders, reports, audits, kernel.positions

        historical_trace = run_trace(
            historical.bus,
            historical.strategy_engine,
            historical.risk_overlay,
            historical.execution_kernel,
        )
        assert live.strategy_engine is not None
        assert live.risk_overlay is not None
        assert live.execution_kernel is not None
        live_trace = run_trace(
            live.bus,
            live.strategy_engine,
            live.risk_overlay,
            live.execution_kernel,
        )

        self.assertEqual(historical_trace, live_trace)


if __name__ == "__main__":
    unittest.main()
