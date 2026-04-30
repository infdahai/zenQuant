from pathlib import Path

from zenquant.domain import IndicatorCoverage, IndicatorPack, MarketState, PlaybookSelection, Timeframe
from zenquant.infra.event_bus import InMemoryJetStream, StreamConfig
from zenquant.plugins.filters import bear_defense_override_filter
from zenquant.plugins.protocols import PluginMeta, SignalResult
from zenquant.plugins.registry import LoadedPlugin, PluginRegistry
from zenquant.strategy.engine import ARCStrategyConfig, ARCStrategyEngine


def _zero_signal_plugin() -> LoadedPlugin:
    return LoadedPlugin(
        meta=PluginMeta(name="zero_signal", version="1.0", plugin_type="signal", description="neutral"),
        compute=lambda indicators, state, config: SignalResult(value=0.0, confidence=1.0, reason_codes=("ZERO",)),
        config={},
        weight=1.0,
        source_path="<test>/zero_signal.py",
    )


def _bear_override_filter_plugin() -> LoadedPlugin:
    return LoadedPlugin(
        meta=bear_defense_override_filter.meta,
        compute=bear_defense_override_filter.compute,
        config={
            "max_directional_confidence": 0.58,
            "min_transition_risk": 0.40,
        },
        weight=1.0,
        source_path=str(Path("zenquant/plugins/filters/bear_defense_override_filter.py")),
    )


def test_bearish_direction_override_is_filtered_after_plugin_direction() -> None:
    registry = PluginRegistry(
        signals=[_zero_signal_plugin()],
        filters=[_bear_override_filter_plugin()],
    )
    bus = InMemoryJetStream()
    bus.add_stream(StreamConfig(name="S", subjects=["strategy.>", "intent.>", "exec.>", "risk.>"]))
    engine = ARCStrategyEngine(bus=bus, config=ARCStrategyConfig(), plugin_registry=registry)
    pack = IndicatorPack(
        ts_ns=1,
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        indicator_version="pack.v1",
        formula_hash="test",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
        values={
            "last_price": 60_000.0,
            "atr": 500.0,
            "realized_vol": 0.2,
            "cvd_slope": 0.0,
            "funding_oi_z": 0.0,
            "ob_liq_z": 0.0,
            "momentum_roc_z": -0.8,
            "structure_state": "TRANSITION",
            "zen_pulse": 50.0,
        },
    )
    state = MarketState(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        directional_state="TRANSITION",
        directional_confidence=0.52,
        volatility_state="LOW",
        volatility_confidence=0.8,
        positioning_state="HEALTHY",
        positioning_confidence=0.8,
        quality_state="NOISY",
        quality_confidence=0.8,
        regime="RANGE",
        regime_confidence=0.4,
        direction_signal=-0.8,
        transition_risk=0.48,
        quality_flags=["OK"],
    )
    playbook = PlaybookSelection(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        playbook_id="BEAR_DEFENSE",
        playbook_confidence=0.7,
        allowed_directions=[-1.0, 0.0],
        max_leverage=2.25,
        trailing_stop_atr_mult=2.5,
        allow_pyramid=False,
        max_pyramid_count=0,
        time_decay_bars=None,
        selection_reason="test",
    )

    target = engine._build_target(pack, state, playbook)

    assert target.target_qty == 0.0
    assert "STATE_BEARISH_DIRECTION_OVERRIDE_FILTERED" in target.reason_codes
    assert any("BEAR_DEFENSE_NOISY_TRANSITION_OVERRIDE_BLOCK" in code for code in target.reason_codes)
