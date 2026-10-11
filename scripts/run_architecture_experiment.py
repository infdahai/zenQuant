#!/usr/bin/env python3
"""Run the frozen A0/A1 causal attribution experiment without search."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.domain import PositionEpisode, Timeframe
from zenquant.domain.serde import to_jsonable
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig, PerturbationConfig
from zenquant.research.architecture_experiment import (
    ArchitectureExecutionMetrics,
    ArchitectureExperimentManifest,
    ArchitectureObservation,
    ArchitectureRunEvidence,
    ArchitectureStage,
    ArchitectureStageExecutionRow,
    ArchitectureWindow,
    build_architecture_attribution_report,
    load_architecture_experiment_manifest,
    observations_from_rows,
)
from zenquant.research.architecture_stage_replay import run_architecture_stage_replay
from zenquant.research.framework_params import (
    build_execution_config,
    build_indicator_config,
    build_plugin_config_overrides,
    build_replay_toggles,
    build_risk_config,
    build_signal_weight_overrides,
    build_strategy_config,
)
from zenquant.research.options_shadow import build_options_shadow_oos_artifact

FIXED_NOTIONAL = 100_000.0
FROZEN_TAKER_FEE_BPS = 4.0
FROZEN_FALLBACK_SLIPPAGE_BPS = 1.5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fixed-notional A0/A1 direction attribution")
    parser.add_argument("--root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/architecture_experiment/architecture_attribution.json"),
    )
    return parser


def build_frozen_backtest_config(
    *,
    root: Path,
    manifest: ArchitectureExperimentManifest,
    window: ArchitectureWindow,
    seed: int,
) -> tuple[HistoricalBacktestConfig, str]:
    """Build one exact August-candidate replay; no experimental inputs are accepted."""
    _revalidate_manifest(manifest)
    if (window.label, seed) not in manifest.expected_run_keys:
        raise ValueError("window/seed is outside the frozen architecture experiment matrix")
    params = dict(manifest.candidate_params)
    strategy_config = replace(
        build_strategy_config(params),
        execution_timeframe=Timeframe(manifest.a0_execution_timeframe),
        emit_architecture_alpha_snapshots_v2=True,
    )
    liquidation_data_enabled, state_liquidation_overlay_enabled = build_replay_toggles(params)
    config = HistoricalBacktestConfig(
        root_dir=root,
        symbol=manifest.symbol,
        portfolio_symbol=manifest.portfolio_symbol,
        start_ns=window.start_ns,
        end_ns=window.end_ns,
        starting_equity=FIXED_NOTIONAL,
        execution_timeframe=Timeframe(manifest.a0_execution_timeframe),
        context_timeframe=Timeframe(manifest.a0_context_timeframe),
        direction_timeframe=Timeframe(manifest.a0_direction_timeframe),
        bias_timeframe=Timeframe(manifest.a0_bias_timeframe),
        strategy_config=strategy_config,
        risk_config=build_risk_config(params),
        execution_config=build_execution_config(params),
        indicator_config=build_indicator_config(params),
        signal_weight_overrides=build_signal_weight_overrides(params),
        plugin_config_overrides=build_plugin_config_overrides(params),
        perturbation=PerturbationConfig(seed=seed, **dict(manifest.perturbation)),
        liquidation_data_enabled=liquidation_data_enabled,
        state_liquidation_overlay_enabled=state_liquidation_overlay_enabled,
        profile_path=manifest.profile_path,
    )
    return config, _frozen_config_hash(config, manifest=manifest)


def build_causal_donor_backtest_config(
    *,
    root: Path,
    manifest: ArchitectureExperimentManifest,
    window: ArchitectureWindow,
    seed: int,
) -> tuple[HistoricalBacktestConfig, str]:
    """Build the isolated zero-lag A1-A5 donor without rewriting A0."""
    a0_config, _a0_hash = build_frozen_backtest_config(
        root=root,
        manifest=manifest,
        window=window,
        seed=seed,
    )
    donor_config = replace(
        a0_config,
        indicator_config=replace(
            a0_config.indicator_config,
            expected_symbols=(manifest.portfolio_symbol,),
        ),
    )
    return donor_config, _frozen_config_hash(donor_config, manifest=manifest)


def _frozen_config_hash(
    config: HistoricalBacktestConfig,
    *,
    manifest: ArchitectureExperimentManifest,
) -> str:
    identity = {
        "strategy_config": to_jsonable(config.strategy_config),
        "risk_config": to_jsonable(config.risk_config),
        "execution_config": to_jsonable(config.execution_config),
        "indicator_config": to_jsonable(config.indicator_config),
        "signal_weight_overrides": to_jsonable(config.signal_weight_overrides),
        "plugin_config_overrides": to_jsonable(config.plugin_config_overrides),
        "replay": {
            "symbol": config.symbol,
            "portfolio_symbol": config.portfolio_symbol,
            "start_ns": config.start_ns,
            "end_ns": config.end_ns,
            "starting_equity": config.starting_equity,
            "execution_timeframe": config.execution_timeframe.value,
            "context_timeframe": config.context_timeframe.value if config.context_timeframe else None,
            "direction_timeframe": config.direction_timeframe.value,
            "bias_timeframe": config.bias_timeframe.value,
            "perturbation": to_jsonable(config.perturbation),
            "liquidation_data_enabled": config.liquidation_data_enabled,
            "state_liquidation_overlay_enabled": config.state_liquidation_overlay_enabled,
            "research_compute_mode": config.research_compute_mode,
        },
        "profile": {
            "sha256": hashlib.sha256(manifest.profile_path.read_bytes()).hexdigest(),
        },
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _revalidate_manifest(manifest: ArchitectureExperimentManifest) -> None:
    params_digest = hashlib.sha256(
        json.dumps(dict(manifest.candidate_params), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if params_digest != manifest.candidate_params_sha256:
        raise ValueError("architecture experiment candidate params SHA-256 mismatch before build")
    if not manifest.profile_path.is_absolute():
        raise ValueError("architecture experiment profile path must be resolved and absolute")
    profile_digest = hashlib.sha256(manifest.profile_path.read_bytes()).hexdigest()
    if profile_digest != manifest.profile_sha256:
        raise ValueError("architecture experiment profile SHA-256 mismatch before build")
    if manifest != load_architecture_experiment_manifest(manifest.manifest_path):
        raise ValueError("architecture experiment in-memory manifest payload diverged from validated bytes")


def run_frozen_stage_sidecars(
    runner: HistoricalBacktest,
    *,
    config: HistoricalBacktestConfig,
    manifest: ArchitectureExperimentManifest,
) -> tuple[
    tuple[ArchitectureObservation, ...],
    dict[str, ArchitectureExecutionMetrics],
    dict[str, tuple[ArchitectureStageExecutionRow, ...]],
    dict[str, tuple[PositionEpisode, ...]],
]:
    """Run A2-A5 from complete PIT 4h inputs through target_tracking_v2."""
    execution_config = config.execution_config or build_execution_config(dict(manifest.candidate_params))
    batch = runner.build_architecture_stage_rows(
        exchange_min_quantity=execution_config.min_trade_qty,
        estimated_fee_bps=FROZEN_TAKER_FEE_BPS,
        estimated_slippage_bps=FROZEN_FALLBACK_SLIPPAGE_BPS,
    )
    observations = tuple(observations_from_rows(batch["observations"]))
    metrics_by_stage: dict[str, ArchitectureExecutionMetrics] = {}
    rows_by_stage: dict[str, tuple[ArchitectureStageExecutionRow, ...]] = {}
    episodes_by_stage: dict[str, tuple[PositionEpisode, ...]] = {}
    for stage in (
        ArchitectureStage.A2,
        ArchitectureStage.A3,
        ArchitectureStage.A4,
        ArchitectureStage.A5_SHADOW,
    ):
        replay = run_architecture_stage_replay(
            observations,
            stage=stage,
            fixed_notional=FIXED_NOTIONAL,
            symbol=manifest.portfolio_symbol,
            portfolio="architecture-v2",
            min_trade_qty=execution_config.min_trade_qty,
            max_trade_qty_per_bar=execution_config.max_trade_qty_per_bar,
            quantity_precision=execution_config.quantity_precision,
            taker_fee_bps=FROZEN_TAKER_FEE_BPS,
            fallback_slippage_bps=FROZEN_FALLBACK_SLIPPAGE_BPS,
            funding_settlements_by_cutoff=batch.get("funding_settlements_by_cutoff", {}),
        )
        if not any(abs(row.approved_exposure) > 0.0 for row in replay.rows):
            raise ValueError(
                f"frozen architecture stage {stage.value} produced an all-flat replay; "
                "point-in-time stage inputs are incomplete or non-evaluable"
            )
        metrics_by_stage[stage.value] = replay.metrics
        rows_by_stage[stage.value] = replay.rows
        episodes_by_stage[stage.value] = replay.position_episodes
    return observations, metrics_by_stage, rows_by_stage, episodes_by_stage


def run_frozen_matrix_entry(
    *,
    root: Path,
    manifest: ArchitectureExperimentManifest,
    window: ArchitectureWindow,
    seed: int,
) -> ArchitectureRunEvidence:
    """Combine original A0 evidence with an isolated causal A1-A5 donor."""
    a0_config, a0_config_hash = build_frozen_backtest_config(
        root=root,
        manifest=manifest,
        window=window,
        seed=seed,
    )
    a0_runner = HistoricalBacktest(a0_config)
    a0_result = a0_runner.run()
    donor_config, donor_config_hash = build_causal_donor_backtest_config(
        root=root,
        manifest=manifest,
        window=window,
        seed=seed,
    )
    donor_runner = HistoricalBacktest(donor_config)
    donor_runner.run()
    batch = donor_runner.build_architecture_attribution_rows(horizon=manifest.alpha_horizon_bars)
    stage_observations, stage_metrics, stage_rows, stage_episodes = run_frozen_stage_sidecars(
        donor_runner,
        config=donor_config,
        manifest=manifest,
    )
    evidence = ArchitectureRunEvidence(
        window=window.label,
        start_ns=window.start_ns,
        end_ns=window.end_ns,
        seed=seed,
        a0_metrics=asdict(a0_result.metrics),
        a0_final_equity=a0_result.final_equity,
        a0_config_hash=a0_config_hash,
        donor_config_hash=donor_config_hash,
        observations=stage_observations,
        excluded_non_evaluable_count=int(batch["excluded_non_evaluable_count"]),
        confirmed_4h_row_count=int(batch["confirmed_4h_row_count"]),
        alpha_snapshot_count=int(batch["alpha_snapshot_count"]),
        missing_alpha_count=int(batch["missing_alpha_count"]),
        terminal_excluded_count=int(batch["terminal_excluded_count"]),
        position_episodes=a0_result.position_episodes,
        execution_metrics_by_stage=stage_metrics,
        stage_execution_rows_by_stage=stage_rows,
        stage_position_episodes_by_stage=stage_episodes,
        window_role=window.role,
    )
    options_shadow_report = build_options_shadow_oos_artifact(
        evidence,
        options_store=donor_runner.options_store,
        manifest_identity=manifest.sha256,
    )
    return replace(evidence, options_shadow_report=options_shadow_report)


def main() -> None:
    args = build_parser().parse_args()

    manifest = load_architecture_experiment_manifest()
    runs: list[ArchitectureRunEvidence] = []
    for window in manifest.windows:
        for seed in manifest.seeds:
            runs.append(
                run_frozen_matrix_entry(
                    root=args.root,
                    manifest=manifest,
                    window=window,
                    seed=seed,
                )
            )

    report = build_architecture_attribution_report(
        runs,
        manifest=manifest,
        fixed_notional=FIXED_NOTIONAL,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Saved {len(report['runs'])} fixed A0/A1 attribution runs to {args.output}")


if __name__ == "__main__":
    main()
