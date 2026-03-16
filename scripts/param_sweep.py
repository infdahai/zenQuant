#!/usr/bin/env python3
"""Parameter sweep tool for strategy optimization.

This script runs multiple backtests with different parameter combinations
and saves results for comparison. It's designed for rapid iteration on
strategy thresholds before paper-live deployment.

Usage:
    # Quick sweep (3 combinations)
    uv run python scripts/param_sweep.py --quick

    # Full sweep (243 combinations)
    uv run python scripts/param_sweep.py --full

    # Custom sweep with specific parameters
    uv run python scripts/param_sweep.py \
        --preserve-capital-risk 0.60,0.65,0.70 \
        --bull-leverage 2.5,3.0,3.5 \
        --output results/custom_sweep.json

Architecture Note:
    This is a V1 tool for immediate use. Future optimization pipeline will:
    - Add walk-forward validation
    - Add outcome labeling (forward returns, MAE, MFE)
    - Add feature engineering (regime persistence, volatility clustering)
    - Add ML-based parameter ranking
    - Add automated promotion workflow
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Any

from zenquant.historical.replay import (
    HistoricalBacktest,
    HistoricalBacktestConfig,
)
from zenquant.strategy import (
    ARCStrategyConfig,
    ExitEngineConfig,
    PlaybookEngineConfig,
    SizingEngineConfig,
)


@dataclass(slots=True)
class SweepConfig:
    """Configuration for parameter sweep."""

    # Data range
    data_root: Path = Path("data/raw/binance")
    symbol: str = "BTCUSDT"
    start_ns: int | None = None
    end_ns: int | None = None

    # Parameter ranges (comma-separated values)
    preserve_capital_risk: list[float] | None = None
    exit_transition_risk: list[float] | None = None
    bull_leverage: list[float] | None = None
    bear_leverage: list[float] | None = None
    range_leverage: list[float] | None = None

    # Output
    output_path: Path = Path("results/param_sweep.json")
    verbose: bool = True


@dataclass(slots=True)
class SweepResult:
    """Result from one parameter combination."""

    run_id: int
    params: dict[str, Any]
    metrics: dict[str, Any]
    diagnostics: dict[str, Any]
    runtime_s: float


def quick_sweep() -> SweepConfig:
    """Predefined quick sweep (3 combinations, ~5 minutes)."""
    return SweepConfig(
        preserve_capital_risk=[0.65],  # baseline only
        exit_transition_risk=[0.85],  # baseline only
        bull_leverage=[2.5, 3.0, 3.5],  # 3 values
        bear_leverage=[2.25],  # baseline only
        range_leverage=[1.5],  # baseline only
    )


def full_sweep() -> SweepConfig:
    """Predefined full sweep (243 combinations, ~30 minutes+)."""
    return SweepConfig(
        preserve_capital_risk=[0.60, 0.65, 0.70],  # 3 values
        exit_transition_risk=[0.80, 0.85, 0.90],  # 3 values
        bull_leverage=[2.5, 3.0, 3.5],  # 3 values
        bear_leverage=[2.0, 2.25, 2.5],  # 3 values
        range_leverage=[1.0, 1.5, 2.0],  # 3 values
    )


def run_sweep(config: SweepConfig) -> list[SweepResult]:
    """Run parameter sweep and return all results."""
    # Generate parameter combinations
    param_grid = _build_param_grid(config)
    total_runs = len(param_grid)

    if config.verbose:
        print(f"Starting parameter sweep: {total_runs} combinations")
        print(f"Data: {config.symbol} from {config.start_ns} to {config.end_ns}")
        print(f"Output: {config.output_path}")
        print("-" * 60)

    results: list[SweepResult] = []
    for run_id, params in enumerate(param_grid, start=1):
        start_time = time.time()

        # Build strategy config from params
        strategy_config = ARCStrategyConfig(
            playbook_config=PlaybookEngineConfig(
                preserve_capital_transition_risk=params["preserve_capital_risk"],
                bull_max_leverage=params["bull_leverage"],
                bear_max_leverage=params["bear_leverage"],
                range_max_leverage=params["range_leverage"],
            ),
            exit_config=ExitEngineConfig(
                transition_risk_exit_threshold=params["exit_transition_risk"],
            ),
        )

        # Run backtest
        backtest_config = HistoricalBacktestConfig(
            root_dir=config.data_root,
            symbol=config.symbol,
            start_ns=config.start_ns,
            end_ns=config.end_ns,
            strategy_config=strategy_config,
        )
        backtest = HistoricalBacktest(backtest_config)
        result = backtest.run()

        runtime_s = time.time() - start_time

        # Store result
        sweep_result = SweepResult(
            run_id=run_id,
            params=params,
            metrics=asdict(result.metrics),
            diagnostics=asdict(result.diagnostics),
            runtime_s=runtime_s,
        )
        results.append(sweep_result)

        if config.verbose:
            print(
                f"[{run_id}/{total_runs}] "
                f"Sharpe={result.metrics.sharpe:.2f} "
                f"PnL={result.metrics.net_pnl:,.0f} "
                f"MaxDD={result.metrics.max_drawdown:.2%} "
                f"({runtime_s:.1f}s)"
            )

    return results


def save_results(results: list[SweepResult], output_path: Path) -> None:
    """Save sweep results to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(
            {
                "schema_version": "param_sweep.v1",
                "sweep_timestamp": time.time(),
                "total_runs": len(results),
                "results": [asdict(r) for r in results],
            },
            f,
            indent=2,
        )


def _build_param_grid(config: SweepConfig) -> list[dict[str, Any]]:
    """Build Cartesian product of all parameter ranges."""
    # Use defaults if not specified
    preserve_capital_risk = config.preserve_capital_risk or [0.65]
    exit_transition_risk = config.exit_transition_risk or [0.85]
    bull_leverage = config.bull_leverage or [3.0]
    bear_leverage = config.bear_leverage or [2.25]
    range_leverage = config.range_leverage or [1.5]

    # Cartesian product
    combinations = product(
        preserve_capital_risk,
        exit_transition_risk,
        bull_leverage,
        bear_leverage,
        range_leverage,
    )

    return [
        {
            "preserve_capital_risk": pcr,
            "exit_transition_risk": etr,
            "bull_leverage": bl,
            "bear_leverage": bel,
            "range_leverage": rl,
        }
        for pcr, etr, bl, bel, rl in combinations
    ]


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Run parameter sweep for strategy optimization")
    parser.add_argument("--quick", action="store_true", help="Run quick sweep (3 combinations)")
    parser.add_argument("--full", action="store_true", help="Run full sweep (243 combinations)")
    parser.add_argument("--data-root", type=Path, help="Path to Binance data root")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol to backtest")
    parser.add_argument("--start", type=int, help="Start timestamp (nanoseconds)")
    parser.add_argument("--end", type=int, help="End timestamp (nanoseconds)")
    parser.add_argument(
        "--preserve-capital-risk",
        help="Comma-separated values (e.g., 0.60,0.65,0.70)",
    )
    parser.add_argument(
        "--exit-transition-risk",
        help="Comma-separated values (e.g., 0.80,0.85,0.90)",
    )
    parser.add_argument(
        "--bull-leverage",
        help="Comma-separated values (e.g., 2.5,3.0,3.5)",
    )
    parser.add_argument(
        "--bear-leverage",
        help="Comma-separated values (e.g., 2.0,2.25,2.5)",
    )
    parser.add_argument(
        "--range-leverage",
        help="Comma-separated values (e.g., 1.0,1.5,2.0)",
    )
    parser.add_argument("--output", type=Path, help="Output JSON path")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")

    args = parser.parse_args()

    # Build config
    if args.quick:
        config = quick_sweep()
    elif args.full:
        config = full_sweep()
    else:
        config = SweepConfig()

    # Override with CLI args
    if args.data_root:
        config.data_root = args.data_root
    if args.symbol:
        config.symbol = args.symbol
    if args.start:
        config.start_ns = args.start
    if args.end:
        config.end_ns = args.end
    if args.preserve_capital_risk:
        config.preserve_capital_risk = [float(x) for x in args.preserve_capital_risk.split(",")]
    if args.exit_transition_risk:
        config.exit_transition_risk = [float(x) for x in args.exit_transition_risk.split(",")]
    if args.bull_leverage:
        config.bull_leverage = [float(x) for x in args.bull_leverage.split(",")]
    if args.bear_leverage:
        config.bear_leverage = [float(x) for x in args.bear_leverage.split(",")]
    if args.range_leverage:
        config.range_leverage = [float(x) for x in args.range_leverage.split(",")]
    if args.output:
        config.output_path = args.output
    if args.quiet:
        config.verbose = False

    # Run sweep
    results = run_sweep(config)

    # Save results
    save_results(results, config.output_path)

    if config.verbose:
        print("-" * 60)
        print(f"Sweep complete: {len(results)} runs")
        print(f"Results saved to: {config.output_path}")
        print("\nTop 3 by Sharpe:")
        sorted_results = sorted(results, key=lambda r: r.metrics["sharpe"], reverse=True)
        for i, result in enumerate(sorted_results[:3], start=1):
            print(
                f"  {i}. Sharpe={result.metrics['sharpe']:.2f} "
                f"PnL={result.metrics['net_pnl']:,.0f} "
                f"MaxDD={result.metrics['max_drawdown']:.2%}"
            )
            print(f"     Params: {result.params}")


if __name__ == "__main__":
    main()
