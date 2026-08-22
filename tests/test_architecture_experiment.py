"""Contract tests for the fixed-notional A0/A1 attribution experiment."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType

import pytest

from zenquant.data import OptionsDataStore
from zenquant.domain import (
    ArchitectureAlphaSnapshot,
    IndicatorCoverage,
    IndicatorPack,
    MarketState,
    OpenPositionState,
    PositionEpisode,
    RiskSnapshot,
    TargetPosition,
    Timeframe,
)
from zenquant.historical.replay import HistoricalBacktest
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.plugins.profile import build_from_profile
from zenquant.research.architecture_experiment import (
    DEFAULT_ARCHITECTURE_MANIFEST_PATH,
    ArchitectureExecutionMetrics,
    ArchitectureObservation,
    ArchitectureRunEvidence,
    ArchitectureStage,
    ArchitectureStageExecutionRow,
    apply_target_no_trade_band,
    bind_stage_inputs_from_snapshot,
    build_architecture_attribution_report,
    load_architecture_experiment_manifest,
    observations_from_rows,
    observe_cost_shadow,
    stage_exposure,
)
from zenquant.research.architecture_stage_replay import (
    ArchitectureFundingSettlement,
    run_architecture_stage_replay,
)
from zenquant.strategy.decision_snapshot import build_decision_snapshot
from zenquant.strategy.engine import ARCStrategyConfig, ARCStrategyEngine

EXPECTED_AUGUST_RANK1_PARAMS = {
    "dd_step_1_long": 0.15,
    "dd_step_1_short": 0.08,
    "vpin_threshold": 0.1,
    "vol_shock_threshold": 0.95,
    "target_vol_4h": 1.1,
    "max_leverage": 4.5,
    "pyramid_initial_pct": 0.3,
    "pyramid_add_pct": 0.3,
    "pyramid_short_initial_pct": 0.2,
    "pyramid_short_add_pct": 0.1,
    "pyramid_spacing_atr": 0.5,
    "exit_unwind_pct": 0.4,
    "dca_max_adverse_atr": 2.5,
    "dca_spacing_atr": 0.75,
    "dca_size_decay": 0.75,
    "flip_min_confidence": 0.7,
}
PLUGIN_ROOT = Path(__file__).parent.parent / "zenquant" / "plugins"
PROFILE_PATH = Path(__file__).parent.parent / "config" / "profiles" / "default.toml"


def _load_cli_module():
    path = Path(__file__).parent.parent / "scripts" / "run_architecture_experiment.py"
    spec = importlib.util.spec_from_file_location("run_architecture_experiment", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_manifest_freezes_august_rank1_and_exact_matrix():
    """Catches defaults or mutable helpers replacing the approved experiment."""
    manifest = load_architecture_experiment_manifest()

    assert Path("config/research/architecture_experiment_v1.json") == DEFAULT_ARCHITECTURE_MANIFEST_PATH
    assert manifest.version == "architecture_experiment_v1"
    assert manifest.candidate_params == EXPECTED_AUGUST_RANK1_PARAMS
    assert manifest.candidate_source_sha256 == "37946e91c0fa601f688ebedfa5729192a8ac9c74de08585e23c64cc05b21bd4c"
    assert [(window.label, window.start_ns, window.end_ns) for window in manifest.windows] == [
        ("train_full", 1577836800000000000, 1704067200000000000),
        ("2020", 1577836800000000000, 1609459200000000000),
        ("2021", 1609459200000000000, 1640995200000000000),
        ("2022", 1640995200000000000, 1672531200000000000),
        ("2023", 1672531200000000000, 1704067200000000000),
        ("late_dev_2025", 1735689600000000000, 1767225600000000000),
    ]
    assert manifest.seeds == (42, 123, 456, 789, 1024)
    assert manifest.perturbation == {
        "price_noise_pct": 0.001,
        "volume_noise_pct": 0.05,
        "bar_dropout_pct": 0.02,
    }
    assert manifest.cost_stress_bps == (0.0, 5.0, 15.0)
    assert manifest.alpha_timeframe == "4h"
    assert manifest.alpha_horizon_bars == 1
    assert manifest.alpha_label == "next_retained_confirmed_4h_close"
    assert manifest.a0_execution_timeframe == "5m"
    assert manifest.a0_context_timeframe == "1h"
    assert manifest.profile_path == PROFILE_PATH.resolve()
    assert manifest.profile_path.is_absolute()

    with pytest.raises(TypeError):
        manifest.candidate_params["max_leverage"] = 99.0
    with pytest.raises(TypeError):
        manifest.perturbation["price_noise_pct"] = 99.0


def test_manifest_loader_fails_closed_when_matrix_is_altered(tmp_path):
    """Catches a changed seed/window/cost matrix retaining the frozen label."""
    payload = json.loads(DEFAULT_ARCHITECTURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["seeds"] = [42]
    altered = tmp_path / "architecture_experiment_v1.json"
    altered.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest SHA-256"):
        load_architecture_experiment_manifest(altered)


def test_cli_exposes_only_root_and_output_operational_overrides():
    """Catches experimental degrees of freedom returning to CLI arguments."""
    parser = _load_cli_module().build_parser()

    args = parser.parse_args(["--root", "/data", "--output", "/tmp/report.json"])
    assert args.root == Path("/data")
    assert args.output == Path("/tmp/report.json")
    with pytest.raises(SystemExit):
        parser.parse_args(["--seeds", "42"])


def test_cli_builds_exact_august_candidate_and_original_a0_execution_contract():
    """Catches defaults or a 4h-only proxy replacing the frozen A0 replay."""
    module = _load_cli_module()
    manifest = load_architecture_experiment_manifest()
    config, config_hash = module.build_frozen_backtest_config(
        root=Path("/data"),
        manifest=manifest,
        window=manifest.windows[0],
        seed=manifest.seeds[0],
    )

    assert config.execution_timeframe == Timeframe.TF_5M
    assert config.context_timeframe == Timeframe.TF_1H
    assert config.direction_timeframe == Timeframe.TF_4H
    assert config.bias_timeframe == Timeframe.TF_1D
    assert config.strategy_config.execution_timeframe == Timeframe.TF_5M
    assert config.strategy_config.max_leverage == 4.5
    assert config.strategy_config.target_vol_4h == 1.1
    assert config.strategy_config.emit_architecture_alpha_snapshots_v2 is True
    assert config.indicator_config.expected_symbols == ("BTCUSDT_PERP", "ETHUSDT_PERP")
    assert config.risk_config.dd_step_1_long == 0.15
    assert config.perturbation.seed == 42
    assert config.perturbation.price_noise_pct == 0.001
    assert config.profile_path == PROFILE_PATH.resolve()
    assert config_hash == "sha256:b683f79efb618c223a24ed829e52cee926089267a2cc8862cac86d44f140b1de"


def test_cli_builds_separate_zero_lag_causal_donor_without_rewriting_a0():
    module = _load_cli_module()
    manifest = load_architecture_experiment_manifest()
    window = manifest.windows[0]
    a0_config, a0_hash = module.build_frozen_backtest_config(
        root=Path("/data"), manifest=manifest, window=window, seed=42
    )
    donor_config, donor_hash = module.build_causal_donor_backtest_config(
        root=Path("/data"), manifest=manifest, window=window, seed=42
    )

    assert a0_config.indicator_config.expected_symbols == ("BTCUSDT_PERP", "ETHUSDT_PERP")
    assert donor_config.indicator_config.expected_symbols == (manifest.portfolio_symbol,)
    assert donor_config.strategy_config == a0_config.strategy_config
    assert donor_config.execution_config == a0_config.execution_config
    assert donor_config.perturbation == a0_config.perturbation
    assert donor_config.start_ns == a0_config.start_ns
    assert donor_config.end_ns == a0_config.end_ns
    assert donor_hash != a0_hash


def test_matrix_entry_sources_a0_only_from_original_runner(monkeypatch):
    module = _load_cli_module()
    manifest = load_architecture_experiment_manifest()
    window = manifest.windows[-1]

    @dataclass
    class _Metrics:
        original_a0_marker: float

    class _Result:
        metrics = _Metrics(original_a0_marker=7.0)
        final_equity = 123_456.0
        position_episodes = ()

    runner_configs = []

    class _Runner:
        def __init__(self, config):
            self.config = config
            self.options_store = OptionsDataStore()
            runner_configs.append(config)

        def run(self):
            return _Result()

        def build_architecture_attribution_rows(self, *, horizon):
            assert horizon == 1
            return {
                "excluded_non_evaluable_count": 1,
                "confirmed_4h_row_count": 2,
                "alpha_snapshot_count": 2,
                "missing_alpha_count": 0,
                "terminal_excluded_count": 1,
            }

    observation = _observation()
    monkeypatch.setattr(module, "HistoricalBacktest", _Runner)
    monkeypatch.setattr(
        module,
        "run_frozen_stage_sidecars",
        lambda runner, *, config, manifest: ((observation,), {}, {}),
    )
    monkeypatch.setattr(
        module,
        "build_options_shadow_oos_artifact",
        lambda evidence, *, options_store, manifest_identity: {"status": "INSUFFICIENT_SAMPLE"},
    )

    evidence = module.run_frozen_matrix_entry(root=Path("/data"), manifest=manifest, window=window, seed=42)

    assert runner_configs[0].indicator_config.expected_symbols == (
        "BTCUSDT_PERP",
        "ETHUSDT_PERP",
    )
    assert runner_configs[1].indicator_config.expected_symbols == (manifest.portfolio_symbol,)
    assert evidence.a0_metrics == {"original_a0_marker": 7.0}
    assert evidence.a0_final_equity == pytest.approx(123_456.0)
    assert evidence.a0_config_hash != evidence.donor_config_hash


def test_builder_revalidates_frozen_params_and_hashes_actual_constructed_configs(monkeypatch):
    """Catches post-load mutation or builder/default drift retaining A0 identity."""
    module = _load_cli_module()
    manifest = load_architecture_experiment_manifest()
    window = manifest.windows[0]
    _, baseline_hash = module.build_frozen_backtest_config(
        root=Path("/data"), manifest=manifest, window=window, seed=42
    )

    altered_manifest = replace(
        manifest,
        candidate_params=MappingProxyType({**dict(manifest.candidate_params), "max_leverage": 99.0}),
    )
    with pytest.raises(ValueError, match="candidate params SHA-256"):
        module.build_frozen_backtest_config(root=Path("/data"), manifest=altered_manifest, window=window, seed=42)

    original_builder = module.build_strategy_config
    monkeypatch.setattr(
        module,
        "build_strategy_config",
        lambda params: replace(original_builder(params), target_vol_1h=0.123456),
    )
    _, drifted_hash = module.build_frozen_backtest_config(root=Path("/data"), manifest=manifest, window=window, seed=42)
    assert drifted_hash != baseline_hash


def _alpha_pack() -> IndicatorPack:
    return IndicatorPack(
        ts_ns=100,
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=100,
        indicator_version="pack.v1",
        formula_hash="formula-v1",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=0),
        values={
            "open": 59_000.0,
            "close": 60_000.0,
            "last_price": 60_000.0,
            "multi_momentum_z": 0.8,
            "momentum_roc_z": 0.8,
            "structure_state": "UPTREND",
            "cvd_slope": 0.05,
            "funding_oi_z": 0.1,
            "cycle_position": 1.1,
        },
    )


def _alpha_state() -> MarketState:
    return MarketState(
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=100,
        directional_state="UPTREND",
        directional_confidence=0.8,
        volatility_state="NORMAL",
        volatility_confidence=0.8,
        positioning_state="HEALTHY",
        positioning_confidence=0.8,
        quality_state="TRADABLE",
        quality_confidence=0.9,
        regime="TREND",
        regime_confidence=0.8,
        direction_signal=1.0,
        transition_risk=0.2,
        quality_flags=["OK"],
    )


def _alpha_engine() -> tuple[ARCStrategyEngine, InMemoryJetStream]:
    _, registry = build_from_profile(PROFILE_PATH, plugin_root=PLUGIN_ROOT)
    bus = InMemoryJetStream()
    bus.add_stream(StreamConfig(name="STRATEGY", subjects=["strategy.>", "intent.>"]))
    engine = ARCStrategyEngine(
        bus=bus,
        config=ARCStrategyConfig(emit_architecture_alpha_snapshots_v2=True),
        plugin_registry=registry,
    )
    return engine, bus


def test_a1_alpha_is_identical_before_exit_for_different_positions():
    """Catches exit/fill/position state changing same-market A1 alpha."""
    flat_engine, flat_bus = _alpha_engine()
    positioned_engine, positioned_bus = _alpha_engine()
    flat_alphas = []
    positioned_alphas = []
    positioned_exits = []
    flat_bus.subscribe("strategy.architecture_alpha.4h.>", lambda _subject, payload: flat_alphas.append(payload))
    positioned_bus.subscribe(
        "strategy.architecture_alpha.4h.>",
        lambda _subject, payload: positioned_alphas.append(payload),
    )
    positioned_bus.subscribe("strategy.exit.>", lambda _subject, payload: positioned_exits.append(payload))
    pack = _alpha_pack()
    state = _alpha_state()

    flat_engine.prime_indicator(pack)
    flat_engine.prime_state(state)

    positioned_engine.prime_indicator(pack)
    positioned_engine._positions[(pack.symbol, pack.timeframe)] = OpenPositionState(
        symbol=pack.symbol,
        timeframe=pack.timeframe,
        direction=1.0,
        entry_price=55_000.0,
        entry_bar_ns=0,
        base_qty=1.0,
        current_qty=1.0,
        trailing_stop=61_000.0,
        pyramid_count=0,
        bars_held=5,
    )
    positioned_engine.prime_state(state)

    assert len(flat_alphas) == len(positioned_alphas) == 1
    assert asdict(flat_alphas[0]) == asdict(positioned_alphas[0])
    assert flat_alphas[0].available_at_ns == flat_alphas[0].cutoff_time_ns

    def _a1_row(snapshot):
        observation = _observation(raw_score=snapshot.raw_score, frozen_confidence=snapshot.confidence)
        return {
            "raw_score": snapshot.raw_score,
            "confidence": snapshot.confidence,
            "sign_exposure": stage_exposure(observation, ArchitectureStage.A1_SIGN),
            "score_exposure": stage_exposure(observation, ArchitectureStage.A1_SCORE),
            "forward_return": 0.01,
        }

    flat_row = _a1_row(flat_alphas[0])
    positioned_row = _a1_row(positioned_alphas[0])
    assert json.dumps(flat_row, sort_keys=True).encode() == json.dumps(positioned_row, sort_keys=True).encode()
    assert positioned_exits and positioned_exits[0].should_exit()
    assert flat_alphas[0].direction_plugin_names == (
        "crash_reversal_signal",
        "crowding_signal",
        "cvd_signal",
        "multi_momentum_signal",
    )


def _observation(
    *,
    alpha_id: str = "alpha-1",
    cutoff_time_ns: int = 100,
    regime: str = "TREND",
    raw_score: float = -0.4,
    frozen_confidence: float = 0.6,
    forward_return: float = 0.1,
) -> ArchitectureObservation:
    return ArchitectureObservation(
        alpha_id=alpha_id,
        cutoff_time_ns=cutoff_time_ns,
        available_at_ns=cutoff_time_ns,
        regime=regime,
        raw_score=raw_score,
        frozen_confidence=frozen_confidence,
        forward_return=forward_return,
        formula_hash="formula-v1",
        alpha_config_hash="alpha-config-v1",
    )


def _run_evidence(
    window_label: str,
    seed: int,
    *,
    observations: tuple[ArchitectureObservation, ...] | None = None,
) -> ArchitectureRunEvidence:
    manifest = load_architecture_experiment_manifest()
    window = next(item for item in manifest.windows if item.label == window_label)
    resolved_observations = (
        observations if observations is not None else (_observation(alpha_id=f"{window_label}-{seed}"),)
    )
    alpha_snapshot_count = len(resolved_observations) + 1
    return ArchitectureRunEvidence(
        window=window_label,
        start_ns=window.start_ns,
        end_ns=window.end_ns,
        seed=seed,
        a0_metrics={"total_return": seed / 10_000.0, "max_drawdown": 0.123},
        a0_final_equity=100_000.0 + seed,
        a0_config_hash="a0-config-v1",
        donor_config_hash="donor-config-v1",
        window_role=window.role,
        observations=resolved_observations,
        excluded_non_evaluable_count=1,
        confirmed_4h_row_count=alpha_snapshot_count,
        alpha_snapshot_count=alpha_snapshot_count,
        missing_alpha_count=0,
        terminal_excluded_count=1,
    )


def test_stage_transforms_isolate_direction_and_frozen_confidence():
    """Catches A1 consuming regime or later-stage state."""
    observation = _observation()
    unrelated_state_changed = replace(
        observation,
        regime="STRESS",
    )

    with pytest.raises(ValueError, match="actual frozen replay metrics"):
        stage_exposure(observation, ArchitectureStage.A0)
    assert stage_exposure(observation, ArchitectureStage.A1_SIGN) == pytest.approx(-1.0)
    assert stage_exposure(observation, ArchitectureStage.A1_SCORE) == pytest.approx(-0.6)
    assert stage_exposure(unrelated_state_changed, ArchitectureStage.A1_SIGN) == pytest.approx(-1.0)
    assert stage_exposure(unrelated_state_changed, ArchitectureStage.A1_SCORE) == pytest.approx(-0.6)


@pytest.mark.parametrize(
    ("annualized_volatility", "expected"),
    [
        (0.10, -1.2),
        (0.20, -0.6),
        (0.01, -1.5),
        (None, 0.0),
        (0.0, 0.0),
        (-0.1, 0.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
    ],
)
def test_a2_applies_frozen_volatility_target_and_fails_flat(
    annualized_volatility,
    expected,
):
    observation = replace(
        _observation(raw_score=-0.4, frozen_confidence=0.6),
        annualized_volatility=annualized_volatility,
    )

    assert stage_exposure(observation, ArchitectureStage.A2) == pytest.approx(expected)


def test_a2_does_not_change_a1_outputs():
    baseline = _observation(raw_score=0.4, frozen_confidence=0.75)
    changed_later_input = replace(baseline, annualized_volatility=0.01)

    assert stage_exposure(changed_later_input, ArchitectureStage.A1_SIGN) == stage_exposure(
        baseline, ArchitectureStage.A1_SIGN
    )
    assert stage_exposure(changed_later_input, ArchitectureStage.A1_SCORE) == stage_exposure(
        baseline, ArchitectureStage.A1_SCORE
    )


@pytest.mark.parametrize(
    ("risk_regime", "multiplier"),
    [
        ("normal", 1.0),
        ("TRADABLE", 1.0),
        ("transition", 0.5),
        ("NOISY", 0.5),
        ("hostile", 0.1),
        ("STRESS", 0.1),
        ("unsafe", 0.0),
        ("UNKNOWN", 0.0),
        ("new_unapproved_regime", 0.0),
    ],
)
def test_a3_uses_frozen_fail_closed_regime_mapping(risk_regime, multiplier):
    observation = replace(
        _observation(raw_score=0.4, frozen_confidence=0.6),
        annualized_volatility=0.20,
        risk_regime=risk_regime,
    )

    assert stage_exposure(observation, ArchitectureStage.A2) == pytest.approx(0.6)
    assert stage_exposure(observation, ArchitectureStage.A3) == pytest.approx(0.6 * multiplier)


def test_a3_input_does_not_change_a2_or_earlier_stages():
    baseline = replace(_observation(raw_score=0.4), annualized_volatility=0.20)
    changed_later_input = replace(baseline, risk_regime="STRESS")

    for stage in (ArchitectureStage.A1_SIGN, ArchitectureStage.A1_SCORE, ArchitectureStage.A2):
        assert stage_exposure(changed_later_input, stage) == stage_exposure(baseline, stage)


def test_later_stage_inputs_bind_to_the_same_frozen_4h_snapshot():
    observation = _observation(raw_score=0.4, frozen_confidence=0.6)
    target = TargetPosition(
        intent_id="intent-1",
        portfolio="arc",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=observation.cutoff_time_ns,
        target_qty=0.06,
        target_leverage=0.06,
        reduce_only=False,
        regime="STRESS",
        regime_confidence=0.8,
        reason_codes=["STRESS"],
        realized_vol=0.40,
    )
    snapshot = build_decision_snapshot(
        target=target,
        formula_hash=observation.formula_hash,
        config_hash="config-v2",
        indicator_version="pack.v1",
        data_identity="data-v1",
        alpha_direction=1.0,
        alpha_raw_score=observation.raw_score,
        alpha_confidence=observation.frozen_confidence,
        directional_state="UPTREND",
        volatility_state="HIGH",
        positioning_state="HEALTHY",
        quality_state="TRADABLE",
        regime_risk_multiplier=0.1,
        annualized_volatility=0.40,
        volatility_target=0.20,
        exposure_cap=1.50,
        quality_flags=("OK",),
        options_state_id=None,
    )

    bound = bind_stage_inputs_from_snapshot(
        observation,
        snapshot=snapshot,
        risk_regime="STRESS",
        exchange_min_quantity=0.001,
        reference_price=100.0,
    )

    assert bound.annualized_volatility == pytest.approx(0.40)
    assert bound.risk_regime == "STRESS"
    assert bound.frozen_regime_multiplier == pytest.approx(0.1)
    assert bound.exchange_min_quantity == pytest.approx(0.001)
    assert stage_exposure(bound, ArchitectureStage.A3) == pytest.approx(0.03)


def test_snapshot_binding_rejects_non_frozen_a2_a3_contract():
    observation = _observation(raw_score=0.4, frozen_confidence=0.6)
    target = TargetPosition(
        intent_id="intent-1",
        portfolio="arc",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=observation.cutoff_time_ns,
        target_qty=0.0,
        target_leverage=0.0,
        reduce_only=False,
        regime="STRESS",
        regime_confidence=0.8,
        reason_codes=["STRESS"],
    )
    snapshot = build_decision_snapshot(
        target=target,
        formula_hash=observation.formula_hash,
        config_hash="config-v2",
        indicator_version="pack.v1",
        data_identity="data-v1",
        alpha_direction=1.0,
        alpha_raw_score=observation.raw_score,
        alpha_confidence=observation.frozen_confidence,
        directional_state="UPTREND",
        volatility_state="HIGH",
        positioning_state="HEALTHY",
        quality_state="TRADABLE",
        regime_risk_multiplier=0.5,
        annualized_volatility=0.40,
        volatility_target=0.20,
        exposure_cap=1.50,
        quality_flags=("OK",),
        options_state_id=None,
    )

    with pytest.raises(ValueError, match="regime multiplier"):
        bind_stage_inputs_from_snapshot(
            observation,
            snapshot=snapshot,
            risk_regime="STRESS",
            exchange_min_quantity=0.001,
            reference_price=100.0,
        )


def test_a4_applies_the_only_four_percent_target_position_band():
    assert apply_target_no_trade_band(0.60, current_position=0.59, exchange_min_quantity=0.001) == pytest.approx(0.59)
    assert apply_target_no_trade_band(0.60, current_position=0.50, exchange_min_quantity=0.001) == pytest.approx(0.60)


def test_a4_band_is_floored_by_exchange_minimum_quantity():
    assert apply_target_no_trade_band(0.60, current_position=0.50, exchange_min_quantity=0.20) == pytest.approx(0.50)


@pytest.mark.parametrize("safety_reason", ["KILL", "UNSAFE", "CAP_VIOLATION"])
def test_a4_safety_corrections_bypass_band(safety_reason):
    assert apply_target_no_trade_band(
        0.0,
        current_position=0.01,
        exchange_min_quantity=0.10,
        safety_reasons=(safety_reason,),
    ) == pytest.approx(0.0)


def test_a4_reduce_only_bypasses_band():
    assert apply_target_no_trade_band(
        0.50,
        current_position=0.60,
        exchange_min_quantity=0.20,
        reduce_only=True,
    ) == pytest.approx(0.50)


def test_a4_stage_is_cumulative_and_does_not_change_a3():
    observation = replace(
        _observation(raw_score=0.4, frozen_confidence=0.6),
        annualized_volatility=0.20,
        risk_regime="NORMAL",
        exchange_min_quantity=0.001,
        reference_price=100.0,
    )

    assert stage_exposure(observation, ArchitectureStage.A3) == pytest.approx(0.60)
    assert stage_exposure(
        observation,
        ArchitectureStage.A4,
        current_position=0.59,
        fixed_notional=100_000.0,
    ) == pytest.approx(0.59)


def test_a4_report_path_converts_exchange_quantity_to_fixed_notional_exposure():
    observation = replace(
        _observation(raw_score=0.4, frozen_confidence=0.6),
        annualized_volatility=0.20,
        risk_regime="NORMAL",
        exchange_min_quantity=2.0,
        reference_price=100.0,
    )

    assert stage_exposure(
        observation,
        ArchitectureStage.A4,
        current_position=0.50,
        fixed_notional=1_000.0,
    ) == pytest.approx(0.50)


def test_stage_replay_uses_actual_filled_position_for_next_a4_cutoff():
    observations = tuple(
        replace(
            _observation(alpha_id=f"alpha-{cutoff}", cutoff_time_ns=cutoff, raw_score=0.4),
            annualized_volatility=0.10,
            risk_regime="NORMAL",
            frozen_regime_multiplier=1.0,
            reference_price=100.0,
            forward_return_3=0.02,
            forward_return_6=0.03,
        )
        for cutoff in (100, 200)
    )

    replay = run_architecture_stage_replay(
        observations,
        stage=ArchitectureStage.A4,
        fixed_notional=1_000.0,
        symbol="BTCUSDT_PERP",
        portfolio="arc",
        max_trade_qty_per_bar=1.0,
    )

    assert isinstance(replay.rows[0], ArchitectureStageExecutionRow)
    assert replay.rows[0].approved_exposure == pytest.approx(1.2)
    assert replay.rows[0].filled_exposure_before == pytest.approx(0.0)
    assert replay.rows[0].filled_exposure_after == pytest.approx(0.1)
    assert replay.rows[1].filled_exposure_before == pytest.approx(0.1)
    assert replay.rows[1].approved_exposure == pytest.approx(1.2)
    assert replay.metrics.target_tracking_error > 0.0
    assert replay.metrics.time_to_target_bars is None
    assert replay.metrics.time_to_target_status == "RIGHT_CENSORED_AT_NEXT_DECISION"
    assert replay.metrics.immediate_target_reached_rate == pytest.approx(0.0)
    assert replay.metrics.right_censored_target_rate == pytest.approx(1.0)
    assert replay.metrics.executor_created_exposure == 0
    assert all(snapshot.volatility_target == pytest.approx(0.20) for snapshot in replay.snapshots)
    assert all(snapshot.exposure_cap == pytest.approx(1.50) for snapshot in replay.snapshots)


def test_stage_snapshot_data_identity_changes_with_pit_risk_inputs():
    baseline = replace(
        _observation(raw_score=0.4),
        annualized_volatility=0.20,
        risk_regime="NORMAL",
        frozen_regime_multiplier=1.0,
        reference_price=100.0,
    )
    changed = replace(
        baseline,
        annualized_volatility=0.40,
        volatility_state="HIGH",
    )

    baseline_replay = run_architecture_stage_replay(
        (baseline,),
        stage=ArchitectureStage.A2,
        fixed_notional=1_000.0,
        symbol="BTCUSDT_PERP",
        portfolio="arc",
    )
    changed_replay = run_architecture_stage_replay(
        (changed,),
        stage=ArchitectureStage.A2,
        fixed_notional=1_000.0,
        symbol="BTCUSDT_PERP",
        portfolio="arc",
    )

    assert baseline_replay.snapshots[0].data_identity != changed_replay.snapshots[0].data_identity


def test_stage_replay_rejects_alpha_not_available_at_decision_cutoff():
    observation = replace(
        _observation(),
        available_at_ns=200,
        annualized_volatility=0.20,
        risk_regime="NORMAL",
        frozen_regime_multiplier=1.0,
        reference_price=100.0,
    )

    with pytest.raises(ValueError, match="availability must equal its decision cutoff"):
        run_architecture_stage_replay(
            (observation,),
            stage=ArchitectureStage.A2,
            fixed_notional=1_000.0,
            symbol="BTCUSDT_PERP",
            portfolio="arc",
        )


def test_stage_replay_reduce_only_never_increases_actual_filled_position():
    first = replace(
        _observation(alpha_id="alpha-100", cutoff_time_ns=100, raw_score=0.4),
        annualized_volatility=0.20,
        risk_regime="NORMAL",
        frozen_regime_multiplier=1.0,
        reference_price=100.0,
    )
    reduce_only = replace(
        first,
        alpha_id="alpha-200",
        cutoff_time_ns=200,
        available_at_ns=200,
        frozen_confidence=0.8,
        reduce_only=True,
    )

    replay = run_architecture_stage_replay(
        (first, reduce_only),
        stage=ArchitectureStage.A4,
        fixed_notional=1_000.0,
        symbol="BTCUSDT_PERP",
        portfolio="arc",
        max_trade_qty_per_bar=10.0,
    )

    assert replay.rows[0].filled_position_after == pytest.approx(6.0)
    assert replay.rows[1].approved_target_qty == pytest.approx(6.0)
    assert replay.rows[1].filled_qty == pytest.approx(0.0)
    assert replay.rows[1].filled_position_after == pytest.approx(6.0)


def test_stage_replay_kill_flattens_without_reversal():
    first = replace(
        _observation(alpha_id="alpha-100", cutoff_time_ns=100, raw_score=0.4),
        annualized_volatility=0.20,
        risk_regime="NORMAL",
        frozen_regime_multiplier=1.0,
        reference_price=100.0,
    )
    killed = replace(
        first,
        alpha_id="alpha-200",
        cutoff_time_ns=200,
        available_at_ns=200,
        raw_score=-0.4,
        safety_reasons=("KILL",),
    )

    replay = run_architecture_stage_replay(
        (first, killed),
        stage=ArchitectureStage.A4,
        fixed_notional=1_000.0,
        symbol="BTCUSDT_PERP",
        portfolio="arc",
        max_trade_qty_per_bar=10.0,
    )

    assert replay.rows[1].approved_target_qty == pytest.approx(0.0)
    assert replay.rows[1].reduce_only is True
    assert replay.rows[1].safety_reasons == ("KILL",)
    assert replay.rows[1].filled_position_after == pytest.approx(0.0)
    assert replay.metrics.unexpected_reversal == 0


def test_frozen_stage_sidecars_fail_closed_when_every_stage_is_flat():
    module = _load_cli_module()
    manifest = load_architecture_experiment_manifest()
    window = next(item for item in manifest.windows if item.label == "late_dev_2025")
    config, _config_hash = module.build_frozen_backtest_config(
        root=Path("/read-only-data"),
        manifest=manifest,
        window=window,
        seed=42,
    )
    flat_observation = replace(
        _observation(),
        annualized_volatility=None,
        risk_regime="UNKNOWN",
        reference_price=100.0,
    )

    class _FlatStageRunner:
        @staticmethod
        def build_architecture_stage_rows(**_kwargs):
            return {"observations": [asdict(flat_observation)]}

    with pytest.raises(ValueError, match="all-flat"):
        module.run_frozen_stage_sidecars(
            _FlatStageRunner(),
            config=config,
            manifest=manifest,
        )


def test_a5_stage_replay_produces_actual_fill_cost_and_forward_labels():
    observation = replace(
        _observation(raw_score=0.4, frozen_confidence=0.6),
        annualized_volatility=0.20,
        risk_regime="STRESS",
        frozen_regime_multiplier=0.1,
        reference_price=100.0,
        estimated_fee_bps=4.0,
        estimated_slippage_bps=1.5,
        forward_return_3=None,
        forward_return_6=-0.02,
    )

    replay = run_architecture_stage_replay(
        (observation,),
        stage=ArchitectureStage.A5_SHADOW,
        fixed_notional=1_000.0,
        symbol="BTCUSDT_PERP",
        portfolio="arc",
    )

    row = replay.rows[0]
    assert row.approved_exposure == pytest.approx(0.06)
    assert row.actual_fill_price == pytest.approx(100.015)
    assert row.fee > 0.0
    assert row.actual_implementation_shortfall > 0.0
    assert row.forward_return_1 == pytest.approx(observation.forward_return)
    assert row.forward_return_3 is None
    assert row.forward_return_6 == pytest.approx(-0.02)
    assert replay.metrics.slippage > 0.0
    assert replay.metrics.target_overshoot == 0


def test_stage_replay_allocates_visible_funding_to_position_episode():
    first = replace(
        _observation(alpha_id="alpha-100", cutoff_time_ns=100, raw_score=0.4),
        annualized_volatility=0.20,
        risk_regime="NORMAL",
        frozen_regime_multiplier=1.0,
        reference_price=100.0,
    )
    second = replace(
        _observation(alpha_id="alpha-200", cutoff_time_ns=200, raw_score=0.0),
        annualized_volatility=0.20,
        risk_regime="NORMAL",
        frozen_regime_multiplier=1.0,
        reference_price=110.0,
    )
    settlement = ArchitectureFundingSettlement(
        source_time_ns=150,
        available_at_ns=200,
        funding_rate=0.01,
        mark_price=110.0,
    )

    replay = run_architecture_stage_replay(
        (first, second),
        stage=ArchitectureStage.A2,
        fixed_notional=1_000.0,
        symbol="BTCUSDT_PERP",
        portfolio="arc",
        max_trade_qty_per_bar=10.0,
        funding_settlements_by_cutoff={200: (settlement,)},
    )

    assert replay.rows[1].funding_pnl == pytest.approx(-6.6)
    assert len(replay.closed_episodes) == 1
    assert replay.closed_episodes[0].funding_pnl == pytest.approx(-6.6)
    assert replay.closed_episodes[0].net_pnl == pytest.approx(
        replay.closed_episodes[0].gross_realized_pnl
        + replay.closed_episodes[0].funding_pnl
        - replay.closed_episodes[0].fees
    )


def test_cumulative_report_consumes_real_stage_rows_without_unobserved_metrics():
    observations = tuple(
        replace(
            _observation(alpha_id=f"alpha-{cutoff}", cutoff_time_ns=cutoff, raw_score=0.4),
            annualized_volatility=0.10,
            risk_regime="NORMAL",
            frozen_regime_multiplier=1.0,
            reference_price=100.0,
            estimated_fee_bps=4.0,
            estimated_slippage_bps=1.5,
        )
        for cutoff in (100, 200)
    )
    replays = {
        stage.value: run_architecture_stage_replay(
            observations,
            stage=stage,
            fixed_notional=1_000.0,
            symbol="BTCUSDT_PERP",
            portfolio="arc",
            max_trade_qty_per_bar=1.0,
        )
        for stage in (
            ArchitectureStage.A2,
            ArchitectureStage.A3,
            ArchitectureStage.A4,
            ArchitectureStage.A5_SHADOW,
        )
    }
    first = replace(
        _run_evidence("train_full", 42, observations=observations),
        execution_metrics_by_stage={stage: replay.metrics for stage, replay in replays.items()},
        stage_execution_rows_by_stage={stage: replay.rows for stage, replay in replays.items()},
    )

    report = build_architecture_attribution_report(
        _complete_matrix(replace_run=first),
        manifest=load_architecture_experiment_manifest(),
        fixed_notional=1_000.0,
    )

    stages = report["runs"][0]["stages"]
    assert all(stages[stage]["execution_metrics"]["status"] == "OBSERVED" for stage in replays)
    assert stages["A4"]["attribution_rows"][1]["execution"]["filled_exposure_before"] == pytest.approx(0.1)
    a4 = stages["A4"]
    expected_gross = sum(
        row["execution"]["filled_exposure_after"] * row["forward_return"] * 1_000.0 for row in a4["attribution_rows"]
    )
    expected_baseline_cost = sum(
        row["execution"]["fee"] + row["execution"]["actual_slippage"] for row in a4["attribution_rows"]
    )
    expected_additional_15bp = sum(
        row["execution"]["filled_qty"] * row["execution"]["actual_fill_price"] * 15.0 / 10_000.0
        for row in a4["attribution_rows"]
    )
    assert a4["pnl_basis"] == "ACTUAL_FILLED_EXPOSURE"
    assert a4["gross_pnl"] == pytest.approx(expected_gross)
    assert a4["cost_stress"]["0"]["baseline_execution_cost"] == pytest.approx(expected_baseline_cost)
    assert a4["cost_stress"]["15"]["additional_stress_cost"] == pytest.approx(expected_additional_15bp)
    assert a4["cost_stress"]["15"]["net_pnl"] == pytest.approx(
        expected_gross - expected_baseline_cost - expected_additional_15bp
    )
    assert stages["A5_SHADOW"]["cost_shadow"]["actual_shortfall_observation_count"] == 2


def test_a5_shadow_records_costs_and_forward_labels_without_blocking():
    shadow = observe_cost_shadow(
        target_delta=0.5,
        fixed_notional=1_000.0,
        estimated_fee_bps=10.0,
        estimated_slippage_bps=5.0,
        reference_price=100.0,
        actual_fill_price=100.2,
        forward_return_1=0.01,
        forward_return_3=None,
        forward_return_6=-0.02,
    )

    assert shadow.target_delta == pytest.approx(0.5)
    assert shadow.estimated_fee == pytest.approx(0.5)
    assert shadow.estimated_slippage == pytest.approx(0.25)
    assert shadow.round_trip_cost == pytest.approx(1.5)
    assert shadow.breakeven_move == pytest.approx(0.003)
    assert shadow.actual_implementation_shortfall == pytest.approx(1.0)
    assert shadow.forward_return_1 == pytest.approx(0.01)
    assert shadow.forward_return_3 is None
    assert shadow.forward_return_6 == pytest.approx(-0.02)
    assert shadow.blocks_order is False


def test_a5_shadow_preserves_a4_target_and_keeps_missing_fill_explicit():
    observation = replace(
        _observation(raw_score=0.4, frozen_confidence=0.6),
        annualized_volatility=0.20,
        risk_regime="NORMAL",
        estimated_fee_bps=10.0,
        estimated_slippage_bps=5.0,
        reference_price=100.0,
        actual_fill_price=None,
        forward_return_3=None,
        forward_return_6=None,
    )

    a4_target = stage_exposure(observation, ArchitectureStage.A4, current_position=0.0)
    a5_target = stage_exposure(observation, ArchitectureStage.A5_SHADOW, current_position=0.0)
    shadow = observe_cost_shadow(
        target_delta=a5_target,
        fixed_notional=1_000.0,
        estimated_fee_bps=observation.estimated_fee_bps,
        estimated_slippage_bps=observation.estimated_slippage_bps,
        reference_price=observation.reference_price,
        actual_fill_price=observation.actual_fill_price,
        forward_return_1=observation.forward_return,
        forward_return_3=observation.forward_return_3,
        forward_return_6=observation.forward_return_6,
    )

    assert a5_target == a4_target
    assert shadow.actual_implementation_shortfall is None
    assert shadow.forward_return_3 is None
    assert shadow.forward_return_6 is None
    assert shadow.blocks_order is False


def test_cumulative_report_carries_exact_task3_and_task4_execution_metrics():
    execution_metrics = ArchitectureExecutionMetrics(
        target_tracking_error=0.012,
        time_to_target_bars=2.0,
        time_to_target_status="OBSERVED",
        immediate_target_reached_rate=0.25,
        right_censored_target_rate=0.0,
        implementation_shortfall=14.5,
        slippage=3.25,
        target_overshoot=0,
        unexpected_reversal=0,
        executor_created_exposure=0,
        episode_profit_factor=1.42,
        episode_profit_factor_status="DEFINED",
        position_switches_per_day=0.75,
    )
    first = replace(
        _complete_matrix()[0],
        execution_metrics_by_stage={ArchitectureStage.A5_SHADOW.value: execution_metrics},
    )
    report = build_architecture_attribution_report(
        _complete_matrix(replace_run=first),
        manifest=load_architecture_experiment_manifest(),
        fixed_notional=100_000.0,
    )

    stages = report["runs"][0]["stages"]
    assert list(stages) == ["A0", "A1_SIGN", "A1_SCORE", "A2", "A3", "A4", "A5_SHADOW"]
    assert "execution_metrics" not in stages["A1_SCORE"]
    assert stages["A2"]["execution_metrics"]["status"] == "NOT_OBSERVED"
    assert stages["A5_SHADOW"]["execution_metrics"] == {
        "status": "OBSERVED",
        "target_tracking_error": 0.012,
        "time_to_target_bars": 2.0,
        "time_to_target_status": "OBSERVED",
        "immediate_target_reached_rate": 0.25,
        "right_censored_target_rate": 0.0,
        "implementation_shortfall": 14.5,
        "slippage": 3.25,
        "target_overshoot": 0,
        "unexpected_reversal": 0,
        "executor_created_exposure": 0,
        "episode_profit_factor": 1.42,
        "episode_profit_factor_status": "DEFINED",
        "position_switches_per_day": 0.75,
    }
    assert stages["A5_SHADOW"]["cost_shadow"]["blocks_order"] is False


def test_later_stage_inputs_leave_a1_report_payloads_byte_identical():
    baseline_run = _complete_matrix()[0]
    changed_observations = tuple(
        replace(
            observation,
            annualized_volatility=0.10,
            risk_regime="STRESS",
            exchange_min_quantity=0.01,
            safety_reasons=("KILL",),
            estimated_fee_bps=12.0,
            estimated_slippage_bps=8.0,
            reference_price=100.0,
            actual_fill_price=100.1,
            forward_return_3=0.03,
            forward_return_6=0.06,
        )
        for observation in baseline_run.observations
    )
    changed_run = replace(baseline_run, observations=changed_observations)
    manifest = load_architecture_experiment_manifest()
    baseline_report = build_architecture_attribution_report(
        _complete_matrix(),
        manifest=manifest,
        fixed_notional=100_000.0,
    )
    changed_report = build_architecture_attribution_report(
        _complete_matrix(replace_run=changed_run),
        manifest=manifest,
        fixed_notional=100_000.0,
    )

    for stage in ("A1_SIGN", "A1_SCORE"):
        baseline_payload = json.dumps(baseline_report["runs"][0]["stages"][stage], sort_keys=True).encode()
        changed_payload = json.dumps(changed_report["runs"][0]["stages"][stage], sort_keys=True).encode()
        assert changed_payload == baseline_payload


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_stage_transforms_reject_confidence_outside_one_x_contract(confidence):
    """Catches malformed confidence silently creating leverage above 1x."""
    with pytest.raises(ValueError, match="confidence"):
        stage_exposure(_observation(frozen_confidence=confidence), ArchitectureStage.A1_SCORE)


def test_fixed_notional_accounting_does_not_compound():
    """Catches the second bar being sized from first-bar profit."""
    observations = [
        _observation(alpha_id="d1", cutoff_time_ns=100, raw_score=0.4, forward_return=0.1),
        _observation(alpha_id="d2", cutoff_time_ns=200, raw_score=0.4, forward_return=0.1),
    ]
    run = _run_evidence("train_full", 42, observations=tuple(observations))
    report = build_architecture_attribution_report(
        _complete_matrix(replace_run=run),
        manifest=load_architecture_experiment_manifest(),
        fixed_notional=100.0,
    )
    sign = report["runs"][0]["stages"]["A1_SIGN"]

    assert sign["gross_pnl"] == pytest.approx(20.0)
    assert sign["cost_stress"]["0"]["net_pnl"] == pytest.approx(20.0)
    assert sign["cost_stress"]["0"]["return_on_fixed_capital"] == pytest.approx(0.2)
    assert sign["turnover_notional"] == pytest.approx(100.0)


def test_coverage_and_direction_hit_rate_have_explicit_denominators():
    """Catches flat signals being counted as directional wins."""
    observations = [
        _observation(alpha_id="d1", cutoff_time_ns=100, raw_score=0.4, forward_return=0.1),
        _observation(alpha_id="d2", cutoff_time_ns=200, raw_score=-0.4, forward_return=-0.1),
        _observation(alpha_id="d3", cutoff_time_ns=300, raw_score=0.0, forward_return=0.2),
    ]
    run = _run_evidence("train_full", 42, observations=tuple(observations))
    report = build_architecture_attribution_report(
        _complete_matrix(replace_run=run),
        manifest=load_architecture_experiment_manifest(),
        fixed_notional=100.0,
    )
    sign = report["runs"][0]["stages"]["A1_SIGN"]

    assert sign["observation_count"] == 3
    assert sign["evaluable_count"] == 3
    assert sign["coverage"] == pytest.approx(1.0)
    assert sign["active_evaluable_count"] == 2
    assert sign["signal_coverage"] == pytest.approx(2 / 3)
    assert sign["direction_hit_count"] == 2
    assert sign["direction_opportunity_count"] == 2
    assert sign["direction_hit_rate"] == pytest.approx(1.0)


def test_cost_stress_uses_changed_fixed_notional():
    """Catches costs being applied to returns or equity instead of turnover."""
    observations = [
        _observation(alpha_id="d1", cutoff_time_ns=100, raw_score=0.4, forward_return=0.0),
        _observation(alpha_id="d2", cutoff_time_ns=200, raw_score=-0.4, forward_return=0.0),
    ]
    run = _run_evidence("train_full", 42, observations=tuple(observations))
    report = build_architecture_attribution_report(
        _complete_matrix(replace_run=run),
        manifest=load_architecture_experiment_manifest(),
        fixed_notional=100.0,
    )
    sign = report["runs"][0]["stages"]["A1_SIGN"]

    assert sign["turnover_notional"] == pytest.approx(300.0)
    assert sign["cost_stress"]["15"]["transaction_cost"] == pytest.approx(0.45)
    assert sign["cost_stress"]["15"]["net_pnl"] == pytest.approx(-0.45)


def test_report_includes_regime_splits_without_regime_sizing():
    """Catches regime labels changing exposure instead of only grouping results."""
    observations = [
        _observation(
            alpha_id="trend",
            cutoff_time_ns=100,
            regime="TREND",
            raw_score=0.4,
            forward_return=0.1,
        ),
        _observation(
            alpha_id="stress",
            cutoff_time_ns=200,
            regime="STRESS",
            raw_score=0.4,
            forward_return=-0.05,
        ),
    ]

    run = _run_evidence("train_full", 42, observations=tuple(observations))
    report = build_architecture_attribution_report(
        _complete_matrix(replace_run=run),
        manifest=load_architecture_experiment_manifest(),
        fixed_notional=100.0,
    )
    sign = report["runs"][0]["stages"]["A1_SIGN"]

    assert set(sign["regime_splits"]) == {"STRESS", "TREND"}
    assert sign["regime_splits"]["TREND"]["gross_pnl"] == pytest.approx(10.0)
    assert sign["regime_splits"]["STRESS"]["gross_pnl"] == pytest.approx(-5.0)
    assert [row["exposure"] for row in sign["attribution_rows"]] == [1.0, 1.0]


def _complete_matrix(*, replace_run: ArchitectureRunEvidence | None = None) -> list[ArchitectureRunEvidence]:
    manifest = load_architecture_experiment_manifest()
    runs = [_run_evidence(window.label, seed) for window in manifest.windows for seed in manifest.seeds]
    if replace_run is not None:
        index = next(
            index for index, run in enumerate(runs) if (run.window, run.seed) == (replace_run.window, replace_run.seed)
        )
        runs[index] = replace_run
    return runs


def test_report_requires_exact_matrix_and_carries_frozen_identities():
    """Catches incomplete runs or mutable experiment identity entering output."""
    manifest = load_architecture_experiment_manifest()
    report = build_architecture_attribution_report(
        _complete_matrix(),
        manifest=manifest,
        fixed_notional=100_000.0,
    )

    assert [(run["window"], run["seed"]) for run in report["runs"]] == list(manifest.expected_run_keys)
    assert all(
        list(run["stages"]) == ["A0", "A1_SIGN", "A1_SCORE", "A2", "A3", "A4", "A5_SHADOW"] for run in report["runs"]
    )
    assert report["manifest"]["sha256"] == manifest.sha256
    assert report["manifest"]["candidate_params_sha256"] == manifest.candidate_params_sha256
    assert report["manifest"]["profile_sha256"] == manifest.profile_sha256
    assert report["manifest"]["windows"][0] == {
        "label": "train_full",
        "start_ns": 1577836800000000000,
        "end_ns": 1704067200000000000,
        "role": "TRAIN",
    }
    assert report["manifest"]["seeds"] == [42, 123, 456, 789, 1024]
    assert report["manifest"]["perturbation"] == manifest.perturbation
    assert report["manifest"]["alpha"] == {
        "symbol": "BTCUSDT_PERP",
        "timeframe": "4h",
        "horizon_bars": 1,
        "label": "next_retained_confirmed_4h_close",
        "signal_roles": ["direction"],
    }
    assert report["manifest"]["runner_contracts"] == {
        "a0": "ORIGINAL_AUGUST_RANK1",
        "a1_a5": "BTC_ONLY_CAUSAL_DONOR",
    }
    assert report["runs"][0]["identities"]["a0_config_hash"] == "a0-config-v1"
    assert report["runs"][0]["identities"]["donor_config_hash"] == "donor-config-v1"
    assert report["constraints"] == {
        "scope": "A0_A1_DIRECTION_ATTRIBUTION_ONLY",
        "fixed_notional": 100_000.0,
        "max_abs_exposure": 1.0,
        "compounding": False,
        "parameter_search": False,
        "risk_overlay": False,
        "volatility_sizing": False,
        "no_trade_band": False,
        "options_state": False,
        "execution_market_logic": False,
    }
    assert report["stage_constraints"]["A2"] == {
        "annualized_volatility_target": 0.2,
        "max_abs_exposure": 1.5,
        "invalid_volatility_target": 0.0,
    }
    assert report["stage_constraints"]["A3"] == {
        "regime_multipliers": {
            "NORMAL": 1.0,
            "TRADABLE": 1.0,
            "TRANSITION": 0.5,
            "NOISY": 0.5,
            "HOSTILE": 0.1,
            "STRESS": 0.1,
            "UNSAFE": 0.0,
            "UNKNOWN": 0.0,
        },
        "unrecognized_multiplier": 0.0,
    }
    assert report["stage_constraints"]["A4"] == {
        "no_trade_band_fraction": 0.04,
        "exchange_minimum_floor": True,
        "safety_bypass_reasons": ["CAP_VIOLATION", "KILL", "UNSAFE"],
    }
    assert report["stage_constraints"]["A5_SHADOW"] == {
        "observation_only": True,
        "blocks_orders": False,
        "forward_horizons_4h": [1, 3, 6],
    }


def test_report_embeds_real_options_shadow_artifact_per_oos_run():
    """Catches the frozen CLI computing options evidence that its JSON drops."""
    manifest = load_architecture_experiment_manifest()
    runs = _complete_matrix()
    late_dev_index = next(index for index, run in enumerate(runs) if run.window == "late_dev_2025")
    runs[late_dev_index] = replace(
        runs[late_dev_index],
        options_shadow_report={
            "schema_version": "options-shadow-counterfactual-v1",
            "status": "INSUFFICIENT_SAMPLE",
            "window": "late_dev_2025",
            "seed": runs[late_dev_index].seed,
        },
    )

    report = build_architecture_attribution_report(runs, manifest=manifest, fixed_notional=100_000.0)
    embedded = next(
        run["options_shadow"]
        for run in report["runs"]
        if run["window"] == "late_dev_2025" and run["seed"] == runs[late_dev_index].seed
    )

    assert embedded["status"] == "INSUFFICIENT_SAMPLE"
    assert embedded["schema_version"] == "options-shadow-counterfactual-v1"
    assert report["options_shadow_stability"] == {
        "status": "INSUFFICIENT_SAMPLE",
        "coverage_status": "INSUFFICIENT_SAMPLE",
        "stability_status": "INSUFFICIENT_SAMPLE",
        "required_sufficient_seeds_per_oos_window": 4,
        "oos_window_seed_counts": {"late_dev_2025": 1},
        "oos_window_sufficient_seed_counts": {"late_dev_2025": 0},
        "quality_stability": {},
        "activation_recommendation": None,
    }


def test_manifest_assigns_explicit_frozen_window_roles():
    """Catches train diagnostic slices entering the OOS options gate."""
    manifest = load_architecture_experiment_manifest()

    assert {window.label: window.role for window in manifest.windows} == {
        "train_full": "TRAIN",
        "2020": "TRAIN_DIAGNOSTIC",
        "2021": "TRAIN_DIAGNOSTIC",
        "2022": "TRAIN_DIAGNOSTIC",
        "2023": "TRAIN_DIAGNOSTIC",
        "late_dev_2025": "OOS",
    }


def test_options_shadow_stability_requires_cross_seed_metric_consistency():
    """Catches five contradictory sufficient reports being called stable."""
    manifest = load_architecture_experiment_manifest()
    runs = _complete_matrix()
    for index, run in enumerate(runs):
        if run.window != "late_dev_2025":
            continue
        retention = -0.8 if run.seed == 1024 else 0.8
        metrics = {
            "status": "SUFFICIENT",
            "hostile_max_drawdown": 0.10,
            "cvar_95": -0.04,
            "tail_loss_mean": -0.05,
            "trend_return_retention": retention,
        }
        runs[index] = replace(
            run,
            options_shadow_report={
                "status": "SUFFICIENT",
                "report": {"by_trend_quality": {"CONFIRMING": metrics}},
            },
        )

    report = build_architecture_attribution_report(runs, manifest=manifest, fixed_notional=100_000.0)

    stability = report["options_shadow_stability"]
    assert stability["coverage_status"] == "SUFFICIENT"
    assert stability["quality_stability"]["CONFIRMING"]["status"] == "UNSTABLE"
    assert stability["stability_status"] == "INSUFFICIENT_SAMPLE"
    assert stability["status"] == "INSUFFICIENT_SAMPLE"


def test_options_shadow_stability_ignores_buckets_from_insufficient_seed_artifacts():
    """Catches a locally populated bucket bypassing the run-level OOS sample gate."""
    manifest = load_architecture_experiment_manifest()
    runs = _complete_matrix()
    for index, run in enumerate(runs):
        if run.window != "late_dev_2025":
            continue
        runs[index] = replace(
            run,
            options_shadow_report={
                "status": "INSUFFICIENT_SAMPLE",
                "report": {
                    "by_trend_quality": {
                        "CONFIRMING": {
                            "status": "SUFFICIENT",
                            "hostile_max_drawdown": 0.10,
                            "cvar_95": -0.04,
                            "tail_loss_mean": -0.05,
                            "trend_return_retention": 0.80,
                        }
                    }
                },
            },
        )

    report = build_architecture_attribution_report(runs, manifest=manifest, fixed_notional=100_000.0)

    stability = report["options_shadow_stability"]
    assert stability["quality_stability"] == {}
    assert stability["stability_status"] == "INSUFFICIENT_SAMPLE"


def test_options_shadow_quality_stability_reports_less_than_four_seeds_as_insufficient():
    """Catches sparse cross-seed evidence being mislabeled as contradictory evidence."""
    manifest = load_architecture_experiment_manifest()
    runs = _complete_matrix()
    sufficient_seeds = set(manifest.seeds[:3])
    for index, run in enumerate(runs):
        if run.window != "late_dev_2025":
            continue
        status = "SUFFICIENT" if run.seed in sufficient_seeds else "INSUFFICIENT_SAMPLE"
        runs[index] = replace(
            run,
            options_shadow_report={
                "status": status,
                "report": {
                    "by_trend_quality": {
                        "CONFIRMING": {
                            "status": "SUFFICIENT",
                            "hostile_max_drawdown": 0.10,
                            "cvar_95": -0.04,
                            "tail_loss_mean": -0.05,
                            "trend_return_retention": 0.80,
                        }
                    }
                },
            },
        )

    report = build_architecture_attribution_report(runs, manifest=manifest, fixed_notional=100_000.0)

    stability = report["options_shadow_stability"]
    assert stability["quality_stability"]["CONFIRMING"]["status"] == "INSUFFICIENT_SAMPLE"
    assert stability["quality_stability"]["CONFIRMING"]["sufficient_seed_count"] == 3
    assert stability["stability_status"] == "INSUFFICIENT_SAMPLE"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "empty"])
def test_report_fails_closed_on_incomplete_or_altered_run_matrix(mutation):
    manifest = load_architecture_experiment_manifest()
    runs = _complete_matrix()
    if mutation == "missing":
        runs.pop()
    elif mutation == "duplicate":
        runs[-1] = runs[0]
    elif mutation == "extra":
        runs.append(replace(runs[0], window="unapproved"))
    else:
        runs[0] = replace(runs[0], observations=())

    with pytest.raises(ValueError, match=r"run matrix|observations"):
        build_architecture_attribution_report(runs, manifest=manifest, fixed_notional=100_000.0)


def test_a0_stage_preserves_actual_frozen_replay_metrics():
    """Catches target-leverage proxies replacing the baseline replay result."""
    actual_metrics = {"total_return": 0.2345, "max_drawdown": 0.091, "trade_count": 37}
    first = replace(_complete_matrix()[0], a0_metrics=actual_metrics, a0_final_equity=123_450.0)
    report = build_architecture_attribution_report(
        _complete_matrix(replace_run=first),
        manifest=load_architecture_experiment_manifest(),
        fixed_notional=100_000.0,
    )

    a0 = report["runs"][0]["stages"]["A0"]
    assert a0["metrics"] == actual_metrics
    assert a0["final_equity"] == 123_450.0
    assert "attribution_rows" not in a0


def _gate_episode(index: int, net_pnl: float) -> PositionEpisode:
    return PositionEpisode(
        episode_id=f"gate-{index}",
        symbol="BTCUSDT_PERP",
        side=1,
        opened_at_ns=index * 10,
        closed_at_ns=index * 10 + 1,
        entry_price=100.0,
        exit_price=100.0 + net_pnl,
        max_abs_qty=1.0,
        remaining_qty=0.0,
        gross_realized_pnl=net_pnl,
        fees=0.0,
        funding_pnl=0.0,
        net_pnl=net_pnl,
        mfe_pnl=max(0.0, net_pnl),
        mae_pnl=min(0.0, net_pnl),
        unrealized_pnl=0.0,
        fill_count=2,
        closed=True,
        reduce_count=1,
        holding_duration_ns=1,
    )


def test_a0_report_operationally_gates_episode_pf_not_fill_or_action_pf():
    passing = replace(
        _complete_matrix()[0],
        a0_metrics={
            "profit_factor": 0.1,
            "action_profit_factor": {"reduce": 0.2},
        },
        position_episodes=(_gate_episode(1, 5.0), _gate_episode(2, 1.0)),
    )
    failing = replace(
        passing,
        a0_metrics={
            "profit_factor": 99.0,
            "action_profit_factor": {"reduce": 99.0},
        },
        position_episodes=(_gate_episode(1, 4.0), _gate_episode(2, -5.0)),
    )

    passing_report = build_architecture_attribution_report(
        _complete_matrix(replace_run=passing),
        manifest=load_architecture_experiment_manifest(),
        fixed_notional=100_000.0,
    )
    failing_report = build_architecture_attribution_report(
        _complete_matrix(replace_run=failing),
        manifest=load_architecture_experiment_manifest(),
        fixed_notional=100_000.0,
    )
    passing_a0 = passing_report["runs"][0]["stages"]["A0"]
    failing_a0 = failing_report["runs"][0]["stages"]["A0"]

    assert passing_a0["gate_v3"]["hard_failed"] is False
    assert passing_a0["gate_v3"]["diagnostics"]["fill_profit_factor"] == pytest.approx(0.1)
    assert failing_a0["gate_v3"]["hard_failed"] is True
    assert "episode_profit_factor_below_minimum" in failing_a0["gate_v3"]["hard_failures"]
    assert passing_a0["position_episodes"][0]["reduce_count"] == 1
    json.dumps(passing_report, sort_keys=True)


def test_replay_bridge_uses_confirmed_4h_rows_and_excludes_terminal_alpha():
    """Catches 5m labels or terminal decisions entering A1 attribution/turnover."""
    runner = object.__new__(HistoricalBacktest)
    runner._execution_rows = [
        {"close_time_ns": 100, "close": 50.0},
        {"close_time_ns": 150, "close": 1.0},
        {"close_time_ns": 200, "close": 500.0},
    ]
    runner._confirmed_four_hour_rows = [
        {"close_time_ns": 100, "close": 100.0},
        {"close_time_ns": 200, "close": 110.0},
    ]
    runner._architecture_alphas = [
        ArchitectureAlphaSnapshot(
            alpha_id=f"alpha-{cutoff}",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=cutoff,
            available_at_ns=cutoff,
            formula_hash="formula-v1",
            indicator_version="pack.v1",
            alpha_config_hash="alpha-config-v1",
            raw_score=raw,
            confidence=confidence,
            direction=1.0 if raw > 0 else -1.0,
            regime="TREND",
            direction_plugin_names=("multi_momentum_signal",),
        )
        for cutoff, raw, confidence in ((100, -0.4, 0.6), (200, 0.2, 0.7))
    ]

    batch = runner.build_architecture_attribution_rows(horizon=1)

    assert batch == {
        "observations": [
            {
                "alpha_id": "alpha-100",
                "cutoff_time_ns": 100,
                "available_at_ns": 100,
                "regime": "TREND",
                "raw_score": -0.4,
                "frozen_confidence": 0.6,
                "forward_return": 0.1,
                "forward_return_available_at_ns": 200,
                "formula_hash": "formula-v1",
                "alpha_config_hash": "alpha-config-v1",
            }
        ],
        "excluded_non_evaluable_count": 1,
        "confirmed_4h_row_count": 2,
        "alpha_snapshot_count": 2,
        "missing_alpha_count": 0,
        "terminal_excluded_count": 1,
    }
    prohibited_fields = {"options_state_id", "regime_risk_multiplier", "annualized_volatility", "risk_flags"}
    assert not (prohibited_fields & set(batch["observations"][0]))


def test_replay_collector_rejects_backdated_architecture_alpha():
    runner = object.__new__(HistoricalBacktest)
    runner._current_event_cutoff_ns = 200
    runner._architecture_alphas = []
    alpha = ArchitectureAlphaSnapshot(
        alpha_id="alpha-100",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=100,
        available_at_ns=100,
        formula_hash="formula-v1",
        indicator_version="pack.v1",
        alpha_config_hash="alpha-config-v1",
        raw_score=0.4,
        confidence=0.6,
        direction=1.0,
        regime="TREND",
        direction_plugin_names=("multi_momentum_signal",),
    )

    with pytest.raises(ValueError, match="backdated architecture alpha"):
        runner._collect_architecture_alpha("strategy.architecture_alpha.4h.BTCUSDT_PERP", alpha)


def test_replay_bridge_builds_complete_pit_stage_inputs_and_true_4h_labels():
    runner = object.__new__(HistoricalBacktest)
    runner._confirmed_four_hour_rows = [
        {"close_time_ns": cutoff, "close": 100.0 + index * 10.0}
        for index, cutoff in enumerate((100, 200, 300, 400, 500, 600, 700))
    ]
    alpha = ArchitectureAlphaSnapshot(
        alpha_id="alpha-100",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=100,
        available_at_ns=100,
        formula_hash="formula-v1",
        indicator_version="pack.v1",
        alpha_config_hash="alpha-config-v1",
        raw_score=0.4,
        confidence=0.6,
        direction=1.0,
        regime="TREND",
        direction_plugin_names=("multi_momentum_signal",),
    )
    runner._architecture_alphas = [
        replace(
            alpha,
            alpha_id=f"alpha-{cutoff}",
            cutoff_time_ns=cutoff,
            available_at_ns=cutoff,
        )
        for cutoff in (100, 200, 300, 400, 500, 600, 700)
    ]
    pack = replace(
        _alpha_pack(),
        values={**_alpha_pack().values, "realized_vol": 0.40},
    )
    state = _alpha_state()
    runner._indicators = [
        replace(pack, cutoff_time_ns=cutoff, ts_ns=cutoff) for cutoff in (100, 200, 300, 400, 500, 600)
    ]
    runner._market_states = [replace(state, cutoff_time_ns=cutoff) for cutoff in (100, 200, 300, 400, 500, 600)]
    runner._risk_snapshots = [
        RiskSnapshot(
            portfolio="arc",
            ts_ns=150,
            equity=1_000.0,
            high_water_mark=1_000.0,
            drawdown=0.0,
            lev_cap=1.0,
            reduce_only_mode=True,
            flags=["PORTFOLIO_KILL"],
        )
    ]
    runner._funding_rows = [
        {"funding_time_ns": 50, "funding_rate": 0.002},
        {"funding_time_ns": 150, "funding_rate": 0.001},
    ]
    runner._execution_rows = [
        {"close_time_ns": 100, "close": 100.0},
        {"close_time_ns": 150, "close": 105.0},
        {"close_time_ns": 200, "close": 110.0},
    ]

    batch = runner.build_architecture_stage_rows(
        exchange_min_quantity=0.001,
        estimated_fee_bps=4.0,
        estimated_slippage_bps=1.5,
    )

    first = batch["observations"][0]
    typed_first = observations_from_rows(batch["observations"])[0]
    assert typed_first.available_at_ns == typed_first.cutoff_time_ns == 100
    assert first["annualized_volatility"] == pytest.approx(0.40)
    assert first["risk_regime"] == "TRADABLE"
    assert first["frozen_regime_multiplier"] == pytest.approx(1.0)
    assert first["reference_price"] == pytest.approx(100.0)
    assert first["forward_return"] == pytest.approx(0.10)
    assert first["forward_return_3"] == pytest.approx(0.30)
    assert first["forward_return_6"] == pytest.approx(0.60)
    assert first["estimated_fee_bps"] == pytest.approx(4.0)
    assert first["estimated_slippage_bps"] == pytest.approx(1.5)
    assert first["risk_snapshot_status"] == "NO_PRIOR_SNAPSHOT_NEUTRAL"
    assert first["risk_snapshot_ts_ns"] is None
    second = batch["observations"][1]
    assert second["risk_snapshot_status"] == "ASOF"
    assert second["risk_snapshot_ts_ns"] == 150
    assert second["reduce_only"] is True
    assert second["safety_reasons"] == ("KILL",)
    assert batch["funding_settlements_by_cutoff"] == {
        200: (
            ArchitectureFundingSettlement(
                source_time_ns=150,
                available_at_ns=200,
                funding_rate=0.001,
                mark_price=105.0,
            ),
        )
    }
    assert batch["missing_stage_input_count"] == 0

    runner._risk_snapshots.append(
        RiskSnapshot(
            portfolio="arc",
            ts_ns=150,
            equity=900.0,
            high_water_mark=1_000.0,
            drawdown=0.1,
            lev_cap=0.5,
            reduce_only_mode=False,
            flags=[],
        )
    )
    with pytest.raises(ValueError, match="conflicting risk snapshots"):
        runner.build_architecture_stage_rows(
            exchange_min_quantity=0.001,
            estimated_fee_bps=4.0,
            estimated_slippage_bps=1.5,
        )


def test_replay_bridge_rejects_missing_alpha_even_when_label_is_terminal():
    runner = object.__new__(HistoricalBacktest)
    runner._confirmed_four_hour_rows = [
        {"close_time_ns": 100, "close": 100.0},
        {"close_time_ns": 200, "close": 110.0},
    ]
    runner._architecture_alphas = [
        ArchitectureAlphaSnapshot(
            alpha_id="alpha-100",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=100,
            available_at_ns=100,
            formula_hash="formula-v1",
            indicator_version="pack.v1",
            alpha_config_hash="alpha-config-v1",
            raw_score=0.4,
            confidence=0.6,
            direction=1.0,
            regime="TREND",
            direction_plugin_names=("multi_momentum_signal",),
        )
    ]

    with pytest.raises(ValueError, match=r"missing_cutoffs=\[200\]"):
        runner.build_architecture_attribution_rows(horizon=1)


@pytest.mark.parametrize("failure", ["interior", "duplicate"])
def test_replay_bridge_fails_closed_before_terminal_exclusion_on_alpha_gaps(failure):
    """Catches missing or duplicate same-cutoff alpha being hidden as warm-up/terminal loss."""
    runner = object.__new__(HistoricalBacktest)
    runner._confirmed_four_hour_rows = [
        {"close_time_ns": 100, "close": 100.0},
        {"close_time_ns": 200, "close": 110.0},
        {"close_time_ns": 300, "close": 120.0},
    ]
    alpha = ArchitectureAlphaSnapshot(
        alpha_id="alpha-100",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=100,
        available_at_ns=100,
        formula_hash="formula-v1",
        indicator_version="pack.v1",
        alpha_config_hash="alpha-config-v1",
        raw_score=0.4,
        confidence=0.6,
        direction=1.0,
        regime="TREND",
        direction_plugin_names=("multi_momentum_signal",),
    )
    terminal = replace(alpha, alpha_id="alpha-300", cutoff_time_ns=300, available_at_ns=300)
    runner._architecture_alphas = [alpha, terminal] if failure == "interior" else [alpha, alpha, terminal]

    pattern = (
        r"requires one bar-close alpha.*missing_cutoffs=\[200\]"
        if failure == "interior"
        else r"confirmed=3 alpha=3.*duplicate_cutoffs=\[100\]"
    )
    with pytest.raises(ValueError, match=pattern):
        runner.build_architecture_attribution_rows(horizon=1)
