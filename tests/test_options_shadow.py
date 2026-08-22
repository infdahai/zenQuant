"""Contracts for the observation-only OptionsState shadow sidecar."""

from __future__ import annotations

import ast
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from zenquant.app.server import OptionsStateShadowRuntimeConfig, ZenQuantWebRuntime, create_app
from zenquant.data.options_state import build_options_state, validate_options_state_identity
from zenquant.data.options_store import OptionsDataStore
from zenquant.domain import EventMeta, MarkPriceUpdate, TargetPosition, Timeframe
from zenquant.execution import ExecutionKernelConfig
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.infra import InMemoryJetStream
from zenquant.research.architecture_experiment import (
    ArchitectureObservation,
    ArchitectureRunEvidence,
    ArchitectureStageExecutionRow,
)
from zenquant.research.options_shadow import (
    OptionsShadowConfig,
    OptionsShadowObservation,
    OptionsShadowObserver,
    OptionsStateSidecarLedger,
    build_options_shadow_counterfactual,
    build_options_shadow_oos_artifact,
    execution_material_snapshot_bytes,
)
from zenquant.strategy.decision_snapshot import build_decision_snapshot

FOUR_HOURS_NS = 4 * 60 * 60 * 1_000_000_000
CUTOFF_NS = 1_725_667_200_000_000_000


def _target(*, cutoff_time_ns: int = CUTOFF_NS, target_qty: float = 0.5) -> TargetPosition:
    return TargetPosition(
        intent_id="risk-final",
        portfolio="arc",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=cutoff_time_ns,
        target_qty=target_qty,
        target_leverage=abs(target_qty),
        reduce_only=False,
        regime="TREND",
        regime_confidence=0.8,
        reason_codes=["APPROVED"],
        realized_vol=0.4,
        atr=500.0,
    )


def _snapshot(*, cutoff_time_ns: int = CUTOFF_NS, target_qty: float = 0.5):
    target = _target(cutoff_time_ns=cutoff_time_ns, target_qty=target_qty)
    return build_decision_snapshot(
        target=target,
        formula_hash="formula-v2",
        config_hash="config-v2",
        indicator_version="pack-v2",
        data_identity="prices-v1",
        alpha_direction=1.0,
        alpha_raw_score=0.4,
        alpha_confidence=0.75,
        directional_state="UPTREND",
        volatility_state="NORMAL",
        positioning_state="HEALTHY",
        quality_state="TRADABLE",
        regime_risk_multiplier=1.0,
        annualized_volatility=0.4,
        volatility_target=0.2,
        exposure_cap=1.5,
        quality_flags=(),
        options_state_id=None,
    )


def _state(*, state_id: str, cutoff_ns: int, source_ns: int, quality: str = "CONFIRMING"):
    unknown = build_options_state(
        (),
        cutoff_time_ns=cutoff_ns,
        realized_vol_30d=0.4,
        realized_vol_source_timestamp_ns=cutoff_ns,
    )
    return replace(
        unknown,
        options_state_id=state_id,
        cutoff_time_ns=cutoff_ns,
        source_timestamp_ns=source_ns,
        trend_quality=quality,
        is_stale=False,
        shadow_risk_multiplier=0.1,
        quality_flags=(),
    )


def test_shadow_is_default_off_and_emits_no_attached_snapshot():
    """Catches observational attachment becoming an implicit runtime default."""
    bus = InMemoryJetStream()
    attached = []
    bus.subscribe("research.options_shadow.snapshot.4h.>", lambda _subject, value: attached.append(value))
    observer = OptionsShadowObserver(bus=bus)

    bus.publish("strategy.decision_snapshot.4h.BTCUSDT_PERP", _snapshot())

    assert observer.config == OptionsShadowConfig(enabled=False)
    assert attached == []
    assert observer.ledger.states == ()


def test_shadow_attaches_exact_point_in_time_state_and_keeps_target_identity():
    """Catches the sidecar linking any state other than the resolver's ASOF result."""
    bus = InMemoryJetStream()
    state = build_options_state(
        (),
        cutoff_time_ns=CUTOFF_NS,
        realized_vol_30d=0.4,
        realized_vol_source_timestamp_ns=CUTOFF_NS,
    )
    attached = []
    observer = OptionsShadowObserver(
        bus=bus,
        config=OptionsShadowConfig(enabled=True),
        state_resolver=lambda _snapshot: state,
    )
    bus.subscribe("research.options_shadow.snapshot.4h.>", lambda _subject, value: attached.append(value))
    original = _snapshot()

    bus.publish("strategy.decision_snapshot.4h.BTCUSDT_PERP", original)

    assert len(attached) == 1
    assert attached[0].options_state_id == state.options_state_id
    assert attached[0].decision_id != original.decision_id
    assert attached[0].approved_target_hash == original.approved_target_hash
    assert execution_material_snapshot_bytes(attached[0]) == execution_material_snapshot_bytes(original)
    assert observer.ledger.states == (state,)
    assert observer.ledger.links[0].risk_final_decision_id == original.decision_id
    assert observer.ledger.links[0].attached_decision_id == attached[0].decision_id


def test_future_state_is_replaced_by_deterministic_unknown_without_execution_effect():
    """Catches a future options source being attached to a current decision."""
    bus = InMemoryJetStream()
    future = _state(state_id="future", cutoff_ns=CUTOFF_NS, source_ns=CUTOFF_NS + 1)
    attached = []
    observer = OptionsShadowObserver(
        bus=bus,
        config=OptionsShadowConfig(enabled=True),
        state_resolver=lambda _snapshot: future,
    )
    bus.subscribe("research.options_shadow.snapshot.4h.>", lambda _subject, value: attached.append(value))
    original = _snapshot()

    bus.publish("strategy.decision_snapshot.4h.BTCUSDT_PERP", original)

    assert len(attached) == 1
    linked_state = observer.ledger.states[0]
    assert linked_state.trend_quality == "UNKNOWN"
    assert linked_state.shadow_risk_multiplier == 1.0
    assert linked_state.source_timestamp_ns == 0
    assert attached[0].options_state_id == linked_state.options_state_id
    assert execution_material_snapshot_bytes(attached[0]) == execution_material_snapshot_bytes(original)


def test_tampered_options_state_identity_degrades_to_canonical_unknown():
    """Catches a modified OptionsState payload being trusted under a forged identity."""
    canonical = build_options_state(
        (),
        cutoff_time_ns=CUTOFF_NS,
        realized_vol_30d=0.4,
        realized_vol_source_timestamp_ns=CUTOFF_NS,
    )
    tampered = replace(canonical, options_state_id="forged", trend_quality="CONFIRMING")
    assert validate_options_state_identity(canonical) is True
    assert validate_options_state_identity(tampered) is False
    bus = InMemoryJetStream()
    observer = OptionsShadowObserver(
        bus=bus,
        config=OptionsShadowConfig(enabled=True),
        state_resolver=lambda _snapshot: tampered,
    )
    attached = []
    bus.subscribe("research.options_shadow.snapshot.4h.>", lambda _subject, value: attached.append(value))

    bus.publish("strategy.decision_snapshot.4h.BTCUSDT_PERP", _snapshot())

    assert attached[0].options_state_id == canonical.options_state_id
    assert observer.ledger.states == (canonical,)


def test_shadow_resolver_failure_cannot_block_downstream_execution_authority():
    """Catches a sidecar calculation failure interrupting the risk-final event path."""
    bus = InMemoryJetStream()

    def broken_resolver(_snapshot):
        raise RuntimeError("malformed sidecar")

    observer = OptionsShadowObserver(
        bus=bus,
        config=OptionsShadowConfig(enabled=True),
        state_resolver=broken_resolver,
    )
    execution_authority = []
    bus.subscribe("strategy.decision_snapshot.4h.>", lambda _subject, value: execution_authority.append(value))

    original = _snapshot()
    bus.publish("strategy.decision_snapshot.4h.BTCUSDT_PERP", original)

    assert execution_authority == [original]
    assert observer.ledger.states[0].trend_quality == "UNKNOWN"


def test_shadow_report_consumer_failure_cannot_block_downstream_execution_authority():
    """Catches a broken research consumer interrupting the risk-final event path."""
    bus = InMemoryJetStream()
    observer = OptionsShadowObserver(bus=bus, config=OptionsShadowConfig(enabled=True))

    def broken_report_consumer(_subject, _value):
        raise RuntimeError("broken report")

    bus.subscribe("research.options_shadow.snapshot.4h.>", broken_report_consumer)
    execution_authority = []
    bus.subscribe("strategy.decision_snapshot.4h.>", lambda _subject, value: execution_authority.append(value))

    original = _snapshot()
    bus.publish("strategy.decision_snapshot.4h.BTCUSDT_PERP", original)

    assert execution_authority == [original]
    assert observer.ledger.states[0].trend_quality == "UNKNOWN"
    assert observer.ledger.links == ()
    assert observer.ledger.failures[0].stage == "ATTACHED_SNAPSHOT_PUBLICATION"
    assert observer.ledger.failures[0].error_type == "RuntimeError"


def test_sidecar_ledger_rejects_identity_collision():
    """Catches two different calculation payloads hiding behind one options_state_id."""
    ledger = OptionsStateSidecarLedger()
    first = _state(state_id="collision", cutoff_ns=CUTOFF_NS, source_ns=CUTOFF_NS - FOUR_HOURS_NS)
    second = replace(first, source_timestamp_ns=CUTOFF_NS - 2 * FOUR_HOURS_NS)

    ledger.record_state(first)

    try:
        ledger.record_state(second)
    except ValueError as exc:
        assert "collision" in str(exc)
    else:
        raise AssertionError("sidecar identity collision was accepted")


def test_counterfactual_reports_observational_oos_buckets_with_unknown_separate():
    """Catches an options report implying target changes or hiding UNKNOWN samples."""
    observations = (
        OptionsShadowObservation(CUTOFF_NS, "s-unknown", "UNKNOWN", "HOSTILE", -0.10),
        OptionsShadowObservation(CUTOFF_NS + FOUR_HOURS_NS, "s-unknown", "UNKNOWN", "HOSTILE", 0.05),
        OptionsShadowObservation(CUTOFF_NS + 2 * FOUR_HOURS_NS, "s-stress", "STRESS", "HOSTILE", -0.05),
        OptionsShadowObservation(CUTOFF_NS + 3 * FOUR_HOURS_NS, "s-stress", "STRESS", "BULL", 0.12),
        OptionsShadowObservation(CUTOFF_NS + 4 * FOUR_HOURS_NS, "s-confirm", "CONFIRMING", "BULL", 0.08),
    )

    report = build_options_shadow_counterfactual(observations, oos_label="late_dev_2025")

    assert report.oos_label == "late_dev_2025"
    assert report.observation_only is True
    assert report.activation_recommendation is None
    assert report.status == "INSUFFICIENT_SAMPLE"
    assert report.minimum_total_samples == 30
    assert report.off.sample_count == 5
    assert report.off.hostile_sample_count == 3
    assert report.off.hostile_max_drawdown == 0.10224999999999995
    assert report.off.cvar_95 == -0.10
    assert report.off.tail_threshold == -0.10
    assert report.off.tail_loss_count == 1
    assert report.off.trend_return == 0.20
    assert report.by_trend_quality["UNKNOWN"].sample_count == 2
    assert report.by_trend_quality["UNKNOWN"].options_state_count == 1
    assert report.by_trend_quality["STRESS"].trend_return_retention == 0.6
    assert report.by_trend_quality["CONFIRMING"].trend_return_retention == pytest.approx(0.4)


def _stage_row(cutoff_ns: int, forward_return: float) -> ArchitectureStageExecutionRow:
    return ArchitectureStageExecutionRow(
        cutoff_time_ns=cutoff_ns,
        decision_id=f"decision-{cutoff_ns}",
        approved_exposure=0.5,
        approved_target_qty=0.5,
        filled_exposure_before=0.0,
        filled_exposure_after=0.5,
        filled_position_before=0.0,
        filled_position_after=0.5,
        target_delta_exposure=0.5,
        reference_price=60_000.0,
        actual_fill_price=60_001.0,
        filled_qty=0.5,
        fee=1.0,
        actual_slippage=0.5,
        actual_implementation_shortfall=1.5,
        funding_pnl=0.0,
        target_tracking_error=0.0,
        time_to_target_bars=0,
        reduce_only=False,
        safety_reasons=(),
        forward_return_1=forward_return,
        forward_return_3=None,
        forward_return_6=None,
    )


def _oos_evidence(*, window: str = "late_dev_2025") -> ArchitectureRunEvidence:
    observation = ArchitectureObservation(
        alpha_id="alpha-1",
        cutoff_time_ns=CUTOFF_NS,
        available_at_ns=CUTOFF_NS,
        regime="TREND",
        raw_score=0.4,
        frozen_confidence=0.75,
        forward_return=0.08,
        formula_hash="formula-v2",
        alpha_config_hash="alpha-config-v2",
        annualized_volatility=0.4,
        risk_regime="BULL",
        reference_price=60_000.0,
    )
    row = _stage_row(CUTOFF_NS, 0.08)
    return ArchitectureRunEvidence(
        window=window,
        start_ns=CUTOFF_NS,
        end_ns=CUTOFF_NS + FOUR_HOURS_NS,
        seed=42,
        a0_metrics={"net_pnl": 1.0},
        a0_final_equity=100_001.0,
        a0_config_hash="a0-config",
        donor_config_hash="donor-config",
        observations=(observation,),
        excluded_non_evaluable_count=0,
        confirmed_4h_row_count=1,
        alpha_snapshot_count=1,
        missing_alpha_count=0,
        terminal_excluded_count=0,
        stage_execution_rows_by_stage={"A4": (row,), "A5_SHADOW": (row,)},
    )


def test_real_oos_adapter_joins_options_store_and_returns_insufficient_artifact_without_overlap():
    """Catches the frozen runner reporting synthetic success when options coverage is absent."""
    artifact = build_options_shadow_oos_artifact(
        _oos_evidence(),
        options_store=OptionsDataStore(),
        manifest_identity="manifest-sha256",
    )

    assert artifact["status"] == "INSUFFICIENT_SAMPLE"
    assert artifact["window"] == "late_dev_2025"
    assert artifact["seed"] == 42
    assert artifact["stage"] == "A5_SHADOW"
    assert artifact["sample_count"] == 1
    assert artifact["known_sample_count"] == 0
    assert artifact["unknown_sample_count"] == 1
    assert artifact["label_contract"] == "forward_return_1_available_at_cutoff_plus_1_confirmed_4h_bar"
    assert artifact["rows"][0]["label_available_at_ns"] == CUTOFF_NS + FOUR_HOURS_NS
    assert artifact["identities"]["manifest"] == "manifest-sha256"
    assert artifact["identities"]["donor_config"] == "donor-config"
    assert artifact["identities"]["options_data"].startswith("sha256:")
    assert artifact["activation_recommendation"] is None


def test_oos_adapter_rejects_a5_target_drift_from_a4():
    """Catches A5 shadow changing the A4 approved target before reporting."""
    evidence = _oos_evidence()
    changed = replace(evidence.stage_execution_rows_by_stage["A5_SHADOW"][0], approved_target_qty=0.6)
    evidence = replace(
        evidence,
        stage_execution_rows_by_stage={
            **evidence.stage_execution_rows_by_stage,
            "A5_SHADOW": (changed,),
        },
    )

    with pytest.raises(ValueError, match=r"A5_SHADOW.*A4"):
        build_options_shadow_oos_artifact(
            evidence,
            options_store=OptionsDataStore(),
            manifest_identity="manifest-sha256",
        )


def test_oos_adapter_marks_train_window_as_not_oos():
    """Catches in-sample evidence being presented as an OOS options result."""
    artifact = build_options_shadow_oos_artifact(
        _oos_evidence(window="train_full"),
        options_store=OptionsDataStore(),
        manifest_identity="manifest-sha256",
    )

    assert artifact["status"] == "NOT_OOS"
    assert artifact["rows"] == []


def test_strategy_risk_and_execution_cannot_import_or_read_shadow_multiplier():
    """Catches observation metadata gaining formula authority through a direct dependency."""
    project_root = Path(__file__).parent.parent
    protected = (
        project_root / "zenquant" / "strategy",
        project_root / "zenquant" / "risk",
        project_root / "zenquant" / "execution",
    )
    violations: list[str] = []
    for directory in protected:
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "shadow_risk_multiplier":
                    violations.append(f"{path.name}: reads shadow_risk_multiplier")
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "zenquant.data.options_state",
                    "zenquant.research.options_shadow",
                }:
                    violations.append(f"{path.name}: imports {node.module}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"zenquant.data.options_state", "zenquant.research.options_shadow"}:
                            violations.append(f"{path.name}: imports {alias.name}")

    assert violations == []


def test_execution_material_serialization_excludes_only_observation_identity():
    """Catches the OFF/ON comparison accidentally ignoring an execution-material field."""
    original = _snapshot()
    observed = replace(original, options_state_id="state", decision_id="observed-decision")

    assert execution_material_snapshot_bytes(observed) == execution_material_snapshot_bytes(original)
    assert execution_material_snapshot_bytes(replace(observed, target_qty=0.6)) != execution_material_snapshot_bytes(
        original
    )
    assert asdict(observed)["options_state_id"] == "state"


def _replay_runtime(*, shadow_enabled: bool) -> HistoricalBacktest:
    empty_store = SimpleNamespace(loaded=False)
    replay_context = SimpleNamespace(
        funding_rows=[],
        open_interest_rows=[],
        aggtrades={},
        events=[],
        macro_store=empty_store,
        options_store=OptionsDataStore(),
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
    with patch("zenquant.historical.replay._build_replay_context", return_value=replay_context):
        return HistoricalBacktest(
            HistoricalBacktestConfig(
                profile_path=None,
                execution_config=ExecutionKernelConfig(execution_authority_mode="target_tracking_v2"),
                options_state_shadow_enabled=shadow_enabled,
            )
        )


def _live_runtime(*, shadow_enabled: bool) -> ZenQuantWebRuntime:
    with patch.dict("os.environ", {"ZENQUANT_MICROSTRUCTURE_SIDECAR_ENABLED": "0"}):
        runtime = ZenQuantWebRuntime(
            runtime_mode="live",
            execution_config=ExecutionKernelConfig(execution_authority_mode="target_tracking_v2"),
            options_state_shadow_enabled=shadow_enabled,
        )
        runtime._initialize_trading_chain()
    return runtime


def _run_execution_trace(runtime) -> dict[str, object]:
    bus = runtime.bus
    orders: list[dict[str, object]] = []
    reports: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    risk_decisions: list[dict[str, object]] = []
    final_targets: list[dict[str, object]] = []
    approved_snapshots: list[bytes] = []
    target_hashes: list[str] = []
    position_curve: list[dict[str, float]] = []
    equity_curve: list[float] = []
    bus.subscribe("exec.order.submit", lambda _subject, value: orders.append(asdict(value)))
    bus.subscribe("exec.order.report", lambda _subject, value: reports.append(asdict(value)))
    bus.subscribe("exec.order.audit", lambda _subject, value: audits.append(asdict(value)))
    bus.subscribe("risk.overlay.decision", lambda _subject, value: risk_decisions.append(asdict(value)))
    bus.subscribe("intent.target_position.final.>", lambda _subject, value: final_targets.append(asdict(value)))
    kernel = runtime.execution_kernel
    risk = runtime.risk_overlay
    assert kernel is not None
    assert risk is not None
    for index, (target_qty, mark_price) in enumerate(((0.5, 60_000.0), (-0.25, 61_000.0), (0.0, 59_000.0))):
        cutoff = CUTOFF_NS + index * FOUR_HOURS_NS
        snapshot = _snapshot(cutoff_time_ns=cutoff, target_qty=target_qty)
        target = _target(cutoff_time_ns=cutoff, target_qty=target_qty)
        approved_snapshots.append(execution_material_snapshot_bytes(snapshot))
        target_hashes.append(snapshot.approved_target_hash)
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(
                    event_time_ns=cutoff,
                    ingest_time_ns=cutoff,
                    venue="BINANCE",
                    symbol="BTCUSDT_PERP",
                    stream_seq=index + 1,
                ),
                mark_price=mark_price,
                index_price=mark_price,
            ),
        )
        bus.publish("strategy.decision_snapshot.4h.BTCUSDT_PERP", snapshot)
        bus.publish(f"intent.target_position.{target.portfolio}.{target.symbol}", target)
        position_curve.append(dict(kernel.positions))
        equity_curve.append(risk.equity)
    return {
        "snapshot_targets": approved_snapshots,
        "approved_target_hashes": target_hashes,
        "final_targets": final_targets,
        "orders": orders,
        "reports": reports,
        "audits": audits,
        "risk_decisions": risk_decisions,
        "positions": dict(kernel.positions),
        "equity": risk.equity,
        "position_curve": position_curve,
        "equity_curve": equity_curve,
        "purity": kernel.purity_counters,
    }


def test_real_replay_and_live_compositions_are_byte_identical_with_shadow_off_or_on():
    """Catches the sidecar changing any real replay/live execution boundary."""
    replay_off = _replay_runtime(shadow_enabled=False)
    replay_on = _replay_runtime(shadow_enabled=True)
    live_off = _live_runtime(shadow_enabled=False)
    live_on = _live_runtime(shadow_enabled=True)

    replay_off_trace = _run_execution_trace(replay_off)
    replay_on_trace = _run_execution_trace(replay_on)
    live_off_trace = _run_execution_trace(live_off)
    live_on_trace = _run_execution_trace(live_on)

    assert replay_off_trace == replay_on_trace
    assert live_off_trace == live_on_trace
    assert replay_off_trace == live_off_trace
    assert replay_on.options_shadow_observer.ledger.states[0].trend_quality == "UNKNOWN"
    assert live_on.options_shadow_observer.ledger.states[0].trend_quality == "UNKNOWN"
    assert replay_off.options_shadow_observer.ledger.states == ()
    assert live_off.options_shadow_observer.ledger.states == ()
    assert replay_on._decision_snapshots[0].options_state_id is not None
    assert replay_off._decision_snapshots[0].options_state_id is None


def test_runtime_shadow_switch_defaults_off():
    """Catches replay or live runtime silently enabling observational attachment."""
    assert HistoricalBacktestConfig().options_state_shadow_enabled is False
    replay = _replay_runtime(shadow_enabled=False)
    live = _live_runtime(shadow_enabled=False)

    assert replay.options_shadow_observer.config.enabled is False
    assert live.options_shadow_observer.config.enabled is False


def test_live_runtime_and_app_use_explicit_local_options_store_seam(tmp_path):
    """Catches the app factory enabling shadow without a real PIT store resolver."""
    config = OptionsStateShadowRuntimeConfig(enabled=True, options_dir=tmp_path / "deribit")
    runtime = ZenQuantWebRuntime(runtime_mode="live", options_state_shadow_config=config)

    assert isinstance(runtime.options_shadow_store, OptionsDataStore)
    assert runtime.options_shadow_observer.config.enabled is True
    bus_attached = []
    runtime.bus.subscribe("research.options_shadow.snapshot.4h.>", lambda _subject, value: bus_attached.append(value))
    runtime.bus.publish("strategy.decision_snapshot.4h.BTCUSDT_PERP", _snapshot())
    assert bus_attached[0].options_state_id == runtime.options_shadow_observer.ledger.states[0].options_state_id

    app = create_app(runtime_mode="live", options_state_shadow_config=config)
    assert app.state.zenquant_runtime.options_shadow_observer.config.enabled is True
    assert isinstance(app.state.zenquant_runtime.options_shadow_store, OptionsDataStore)
