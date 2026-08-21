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

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig, PerturbationConfig
from zenquant.research.architecture_experiment import (
    ArchitectureExperimentManifest,
    ArchitectureRunEvidence,
    ArchitectureWindow,
    build_architecture_attribution_report,
    load_architecture_experiment_manifest,
    observations_from_rows,
)
from zenquant.research.framework_params import (
    build_execution_config,
    build_indicator_config,
    build_plugin_config_overrides,
    build_replay_toggles,
    build_risk_config,
    build_signal_weight_overrides,
    build_strategy_config,
)

FIXED_NOTIONAL = 100_000.0


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
    if (window.label, seed) not in manifest.expected_run_keys:
        raise ValueError("window/seed is outside the frozen architecture experiment matrix")
    params = manifest.candidate_params
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
        perturbation=PerturbationConfig(seed=seed, **manifest.perturbation),
        liquidation_data_enabled=liquidation_data_enabled,
        state_liquidation_overlay_enabled=state_liquidation_overlay_enabled,
        profile_path=Path(manifest.profile_path),
    )
    identity = {
        "manifest_sha256": manifest.sha256,
        "candidate_params_sha256": manifest.candidate_params_sha256,
        "profile_sha256": manifest.profile_sha256,
        "window": {"label": window.label, "start_ns": window.start_ns, "end_ns": window.end_ns},
        "seed": seed,
        "perturbation": manifest.perturbation,
        "execution_contract": {
            "execution": manifest.a0_execution_timeframe,
            "context": manifest.a0_context_timeframe,
            "direction": manifest.a0_direction_timeframe,
            "bias": manifest.a0_bias_timeframe,
        },
        "starting_equity": FIXED_NOTIONAL,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return config, f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def main() -> None:
    args = build_parser().parse_args()

    manifest = load_architecture_experiment_manifest()
    runs: list[ArchitectureRunEvidence] = []
    for window in manifest.windows:
        for seed in manifest.seeds:
            config, config_hash = build_frozen_backtest_config(
                root=args.root,
                manifest=manifest,
                window=window,
                seed=seed,
            )
            runner = HistoricalBacktest(config)
            result = runner.run()
            batch = runner.build_architecture_attribution_rows(horizon=manifest.alpha_horizon_bars)
            runs.append(
                ArchitectureRunEvidence(
                    window=window.label,
                    start_ns=window.start_ns,
                    end_ns=window.end_ns,
                    seed=seed,
                    a0_metrics=asdict(result.metrics),
                    a0_final_equity=result.final_equity,
                    a0_config_hash=config_hash,
                    observations=tuple(observations_from_rows(batch["observations"])),
                    excluded_non_evaluable_count=int(batch["excluded_non_evaluable_count"]),
                    confirmed_4h_row_count=int(batch["confirmed_4h_row_count"]),
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
