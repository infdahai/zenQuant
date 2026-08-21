#!/usr/bin/env python3
"""Run the frozen A0/A1 causal attribution experiment without search."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig, PerturbationConfig
from zenquant.research.architecture_experiment import (
    DEFAULT_COST_STRESS_BPS,
    build_architecture_attribution_report,
    observations_from_rows,
)
from zenquant.research.comparison import default_comparison_windows
from zenquant.research.splits import default_btc_split_spec
from zenquant.strategy import ARCStrategyConfig

DEFAULT_SEEDS = (42, 123, 456, 789, 1024)


def _comma_floats(value: str) -> tuple[float, ...]:
    parsed = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    if not parsed:
        raise argparse.ArgumentTypeError("expected at least one comma-separated number")
    return parsed


def _comma_ints(value: str) -> tuple[int, ...]:
    parsed = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not parsed:
        raise argparse.ArgumentTypeError("expected at least one comma-separated integer")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed-notional A0/A1 direction attribution")
    parser.add_argument("--root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--portfolio-symbol", default="BTCUSDT_PERP")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--seeds", type=_comma_ints, default=DEFAULT_SEEDS)
    parser.add_argument("--fixed-notional", type=float, default=100_000.0)
    parser.add_argument(
        "--cost-stress-bps",
        type=_comma_floats,
        default=DEFAULT_COST_STRESS_BPS,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/architecture_experiment/architecture_attribution.json"),
    )
    args = parser.parse_args()

    split_spec = default_btc_split_spec()
    windows = default_comparison_windows(split_spec, args.split)
    observations = []
    for window in windows:
        for seed in args.seeds:
            strategy_config = replace(ARCStrategyConfig(), emit_decision_snapshots_v2=True)
            config = HistoricalBacktestConfig(
                root_dir=args.root,
                symbol=args.symbol,
                portfolio_symbol=args.portfolio_symbol,
                start_ns=window.start_ns,
                end_ns=window.end_ns,
                starting_equity=args.fixed_notional,
                strategy_config=strategy_config,
                perturbation=PerturbationConfig(
                    seed=seed,
                    price_noise_pct=0.001,
                    volume_noise_pct=0.05,
                    bar_dropout_pct=0.02,
                ),
                options_dir=args.root / "__a1_options_disabled__",
                liquidation_data_enabled=False,
                state_liquidation_overlay_enabled=False,
                plugin_config_overrides={"signals": {"options_vol_signal": {"enabled": False}}},
            )
            runner = HistoricalBacktest(config)
            runner.run()
            observations.extend(
                observations_from_rows(
                    runner.build_architecture_attribution_rows(
                        window=window.label,
                        seed=seed,
                        fixed_notional=args.fixed_notional,
                    )
                )
            )

    report = build_architecture_attribution_report(
        observations,
        fixed_notional=args.fixed_notional,
        cost_stress_bps=args.cost_stress_bps,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Saved {len(report['runs'])} fixed A0/A1 attribution runs to {args.output}")


if __name__ == "__main__":
    main()
