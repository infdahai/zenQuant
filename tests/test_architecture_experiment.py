"""Contract tests for the fixed-notional A0/A1 attribution experiment."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import MappingProxyType

import pytest

from zenquant.domain import (
    ArchitectureAlphaSnapshot,
    IndicatorCoverage,
    IndicatorPack,
    MarketState,
    OpenPositionState,
    Timeframe,
)
from zenquant.historical.replay import HistoricalBacktest
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.plugins.profile import build_from_profile
from zenquant.research.architecture_experiment import (
    DEFAULT_ARCHITECTURE_MANIFEST_PATH,
    ArchitectureObservation,
    ArchitectureRunEvidence,
    ArchitectureStage,
    build_architecture_attribution_report,
    load_architecture_experiment_manifest,
    stage_exposure,
)
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
    assert config.risk_config.dd_step_1_long == 0.15
    assert config.perturbation.seed == 42
    assert config.perturbation.price_noise_pct == 0.001
    assert config.profile_path == PROFILE_PATH.resolve()
    assert config_hash.startswith("sha256:")


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
    assert all(list(run["stages"]) == ["A0", "A1_SIGN", "A1_SCORE"] for run in report["runs"])
    assert report["manifest"]["sha256"] == manifest.sha256
    assert report["manifest"]["candidate_params_sha256"] == manifest.candidate_params_sha256
    assert report["manifest"]["profile_sha256"] == manifest.profile_sha256
    assert report["manifest"]["windows"][0] == {
        "label": "train_full",
        "start_ns": 1577836800000000000,
        "end_ns": 1704067200000000000,
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
    assert report["constraints"] == {
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
                "regime": "TREND",
                "raw_score": -0.4,
                "frozen_confidence": 0.6,
                "forward_return": 0.1,
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


@pytest.mark.parametrize("failure", ["missing", "duplicate"])
def test_replay_bridge_fails_closed_before_terminal_exclusion_on_alpha_gaps(failure):
    """Catches missing or duplicate same-cutoff alpha being hidden as warm-up/terminal loss."""
    runner = object.__new__(HistoricalBacktest)
    runner._confirmed_four_hour_rows = [
        {"close_time_ns": 100, "close": 100.0},
        {"close_time_ns": 200, "close": 110.0},
    ]
    alpha = ArchitectureAlphaSnapshot(
        alpha_id="alpha-100",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=100,
        formula_hash="formula-v1",
        indicator_version="pack.v1",
        alpha_config_hash="alpha-config-v1",
        raw_score=0.4,
        confidence=0.6,
        direction=1.0,
        regime="TREND",
        direction_plugin_names=("multi_momentum_signal",),
    )
    terminal = replace(alpha, alpha_id="alpha-200", cutoff_time_ns=200)
    runner._architecture_alphas = [alpha] if failure == "missing" else [alpha, alpha, terminal]

    pattern = (
        r"confirmed=2 alpha=1 missing=1 terminal_excluded=0"
        if failure == "missing"
        else r"confirmed=2 alpha=3 missing=0 terminal_excluded=0 duplicate_cutoffs=\[100\]"
    )
    with pytest.raises(ValueError, match=pattern):
        runner.build_architecture_attribution_rows(horizon=1)
