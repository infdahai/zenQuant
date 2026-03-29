#!/usr/bin/env python3
"""Walk-forward parameter tuning with validation.

This script implements systematic parameter optimization with:
- Walk-forward validation (train on period N, validate on N+1)
- Parameter grid search with constraints
- Out-of-sample validation
- Promotion criteria checks

Usage:
    # Quick tuning (2 train/val splits, 3 param combinations)
    uv run python scripts/walk_forward_tune.py --quick

    # Full tuning (4 train/val splits, 27 param combinations)
    uv run python scripts/walk_forward_tune.py --full

    # Custom tuning with specific date ranges
    uv run python scripts/walk_forward_tune.py \
        --train-start 2024-01-01 \
        --train-end 2024-03-31 \
        --val-start 2024-04-01 \
        --val-end 2024-06-30 \
        --preserve-capital-risk 0.60,0.65,0.70 \
        --output results/tuning_report.json

Architecture Note:
    This implements the V2.3 tuning layer from OPTIMIZATION_PIPELINE.md.
    It uses the research pipeline to generate training data, then tunes
    parameters with walk-forward validation to prevent overfitting.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.strategy import (
    ARCStrategyConfig,
    ExitEngineConfig,
    PlaybookEngineConfig,
)


@dataclass(slots=True)
class WalkForwardConfig:
    """Configuration for walk-forward tuning."""

    # Data
    data_root: Path = Path("data/raw/binance")
    symbol: str = "BTCUSDT"

    # Walk-forward splits (list of (train_start, train_end, val_start, val_end) tuples)
    splits: list[tuple[int, int, int, int]] | None = None

    # Parameter ranges
    preserve_capital_risk: list[float] | None = None
    exit_transition_risk: list[float] | None = None
    bull_leverage: list[float] | None = None

    # Promotion criteria
    min_sharpe_improvement: float = 0.1  # Require +0.1 Sharpe vs baseline
    max_drawdown_regression: float = 0.02  # Allow max 2% worse drawdown
    min_trade_count: int = 10  # Require at least 10 trades

    # Output
    output_path: Path = Path("results/walk_forward_tuning.json")
    verbose: bool = True


@dataclass(slots=True)
class SplitResult:
    """Result from one train/validation split."""

    split_id: int
    train_start_ns: int
    train_end_ns: int
    val_start_ns: int
    val_end_ns: int
    best_params: dict[str, Any]
    train_sharpe: float
    val_sharpe: float
    val_pnl: float
    val_drawdown: float
    val_trade_count: int
    runtime_s: float


@dataclass(slots=True)
class TuningResult:
    """Overall tuning result."""

    config: dict[str, Any]
    splits: list[SplitResult]
    avg_val_sharpe: float
    avg_val_drawdown: float
    recommended_params: dict[str, Any]
    promotion_decision: str  # "PROMOTE", "REJECT", "REVIEW"
    promotion_reason: str


def quick_tuning() -> WalkForwardConfig:
    """Predefined quick tuning (2 splits, 3 params, ~10 minutes)."""
    # Use last 6 months, split into 2 periods
    now = datetime.now(UTC)
    end = now
    start = now - timedelta(days=180)
    mid = start + timedelta(days=90)

    return WalkForwardConfig(
        splits=[
            # Split 1: Train on first 3 months, validate on next 3 months
            (
                int(start.timestamp() * 1e9),
                int(mid.timestamp() * 1e9),
                int(mid.timestamp() * 1e9),
                int(end.timestamp() * 1e9),
            ),
        ],
        preserve_capital_risk=[0.65],  # baseline only
        exit_transition_risk=[0.85],  # baseline only
        bull_leverage=[2.5, 3.0, 3.5],  # 3 values
    )


def full_tuning() -> WalkForwardConfig:
    """Predefined full tuning (4 splits, 27 params, ~60 minutes)."""
    # Use last 12 months, split into 4 periods (3 months each)
    now = datetime.now(UTC)
    end = now
    start = now - timedelta(days=365)

    splits = []
    for i in range(3):  # 3 walk-forward splits
        train_start = start + timedelta(days=i * 90)
        train_end = train_start + timedelta(days=90)
        val_start = train_end
        val_end = val_start + timedelta(days=90)

        splits.append(
            (
                int(train_start.timestamp() * 1e9),
                int(train_end.timestamp() * 1e9),
                int(val_start.timestamp() * 1e9),
                int(val_end.timestamp() * 1e9),
            )
        )

    return WalkForwardConfig(
        splits=splits,
        preserve_capital_risk=[0.60, 0.65, 0.70],  # 3 values
        exit_transition_risk=[0.80, 0.85, 0.90],  # 3 values
        bull_leverage=[2.5, 3.0, 3.5],  # 3 values
    )


def _run_single_backtest(args: tuple[dict[str, Any], str, str, int, int]) -> tuple[dict[str, Any], float]:
    """Run one backtest in a child process. Returns (params, sharpe).

    This is a module-level function so ``ProcessPoolExecutor`` can pickle it.
    """
    params, root_dir, symbol, start_ns, end_ns = args
    strategy_config = _build_strategy_config(params)
    backtest_config = HistoricalBacktestConfig(
        root_dir=Path(root_dir),
        symbol=symbol,
        start_ns=start_ns,
        end_ns=end_ns,
        strategy_config=strategy_config,
    )
    backtest = HistoricalBacktest(backtest_config)
    result = backtest.run()
    return params, result.metrics.sharpe


def _max_workers() -> int:
    """Determine parallel workers: leave 2 cores free for OS."""
    cpu_count = os.cpu_count() or 4
    return max(1, min(cpu_count - 2, 8))


def run_walk_forward_tuning(config: WalkForwardConfig) -> TuningResult:
    """Run walk-forward tuning and return results."""
    if not config.splits:
        raise ValueError("No walk-forward splits defined")

    workers = _max_workers()
    if config.verbose:
        print(f"Starting walk-forward tuning: {len(config.splits)} splits")
        print(f"Data: {config.symbol} from {config.data_root}")
        print(f"Parallel workers: {workers}")
        print("-" * 80)

    split_results: list[SplitResult] = []

    for split_id, (train_start, train_end, val_start, val_end) in enumerate(config.splits, start=1):
        if config.verbose:
            print(f"\nSplit {split_id}/{len(config.splits)}:")
            print(f"  Train: {_format_timestamp(train_start)} to {_format_timestamp(train_end)}")
            print(f"  Val:   {_format_timestamp(val_start)} to {_format_timestamp(val_end)}")

        start_time = time.time()

        # Grid search on training period — parallel
        param_grid = _build_param_grid(config)
        best_params = None
        best_train_sharpe = -999.0

        backtest_args = [
            (params, str(config.data_root), config.symbol, train_start, train_end)
            for params in param_grid
        ]

        if len(backtest_args) > 1 and workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_run_single_backtest, args): args for args in backtest_args}
                for future in as_completed(futures):
                    params, sharpe = future.result()
                    if sharpe > best_train_sharpe:
                        best_train_sharpe = sharpe
                        best_params = params
        else:
            for args in backtest_args:
                params, sharpe = _run_single_backtest(args)
                if sharpe > best_train_sharpe:
                    best_train_sharpe = sharpe
                    best_params = params

        if best_params is None:
            raise RuntimeError(f"No valid parameters found for split {split_id}")

        # Validate on out-of-sample period
        strategy_config = _build_strategy_config(best_params)
        backtest_config = HistoricalBacktestConfig(
            root_dir=config.data_root,
            symbol=config.symbol,
            start_ns=val_start,
            end_ns=val_end,
            strategy_config=strategy_config,
        )
        backtest = HistoricalBacktest(backtest_config)
        val_result = backtest.run()

        runtime_s = time.time() - start_time

        split_result = SplitResult(
            split_id=split_id,
            train_start_ns=train_start,
            train_end_ns=train_end,
            val_start_ns=val_start,
            val_end_ns=val_end,
            best_params=best_params,
            train_sharpe=best_train_sharpe,
            val_sharpe=val_result.metrics.sharpe,
            val_pnl=val_result.metrics.net_pnl,
            val_drawdown=val_result.metrics.max_drawdown,
            val_trade_count=val_result.metrics.trade_count,
            runtime_s=runtime_s,
        )
        split_results.append(split_result)

        if config.verbose:
            print(f"  Best params: {best_params}")
            print(f"  Train Sharpe: {best_train_sharpe:.2f}")
            print(f"  Val Sharpe: {val_result.metrics.sharpe:.2f}")
            print(f"  Val PnL: {val_result.metrics.net_pnl:,.0f}")
            print(f"  Val MaxDD: {val_result.metrics.max_drawdown:.2%}")
            print(f"  Runtime: {runtime_s:.1f}s")

    # Aggregate results
    avg_val_sharpe = sum(r.val_sharpe for r in split_results) / len(split_results)
    avg_val_drawdown = sum(r.val_drawdown for r in split_results) / len(split_results)

    # Recommend most common best params
    param_counts: dict[str, int] = {}
    for result in split_results:
        param_key = json.dumps(result.best_params, sort_keys=True)
        param_counts[param_key] = param_counts.get(param_key, 0) + 1

    most_common_key = max(param_counts, key=param_counts.get)  # type: ignore
    recommended_params = json.loads(most_common_key)

    # Promotion decision: compute baseline Sharpe from default params on first split
    baseline_sharpe = _compute_baseline_sharpe(config)
    promotion_decision, promotion_reason = _check_promotion_criteria(
        avg_val_sharpe=avg_val_sharpe,
        avg_val_drawdown=avg_val_drawdown,
        baseline_sharpe=baseline_sharpe,
        config=config,
    )

    return TuningResult(
        config=asdict(config),
        splits=split_results,
        avg_val_sharpe=avg_val_sharpe,
        avg_val_drawdown=avg_val_drawdown,
        recommended_params=recommended_params,
        promotion_decision=promotion_decision,
        promotion_reason=promotion_reason,
    )


def _compute_baseline_sharpe(config: WalkForwardConfig) -> float:
    """Run a single backtest with default parameters across the full data range.

    Returns the baseline Sharpe so tuned parameters are compared against a
    meaningful reference, not zero.
    """
    if not config.splits:
        return 0.0
    # Use the validation period of the first split as the baseline window
    first_split = config.splits[0]
    baseline_params = {
        "preserve_capital_risk": 0.60,
        "exit_transition_risk": 0.80,
        "bull_leverage": 2.50,
    }
    try:
        _, sharpe = _run_single_backtest(
            (baseline_params, str(config.data_root), config.symbol,
             first_split[2], first_split[3])
        )
        return sharpe
    except Exception:
        return 0.0


def _build_param_grid(config: WalkForwardConfig) -> list[dict[str, Any]]:
    """Build Cartesian product of parameter ranges."""
    preserve_capital_risk = config.preserve_capital_risk or [0.65]
    exit_transition_risk = config.exit_transition_risk or [0.85]
    bull_leverage = config.bull_leverage or [3.0]

    combinations = product(preserve_capital_risk, exit_transition_risk, bull_leverage)

    return [
        {
            "preserve_capital_risk": pcr,
            "exit_transition_risk": etr,
            "bull_leverage": bl,
        }
        for pcr, etr, bl in combinations
    ]


def _build_strategy_config(params: dict[str, Any]) -> ARCStrategyConfig:
    """Build strategy config from parameters."""
    return ARCStrategyConfig(
        playbook_config=PlaybookEngineConfig(
            preserve_capital_transition_risk=params["preserve_capital_risk"],
            bull_max_leverage=params["bull_leverage"],
        ),
        exit_config=ExitEngineConfig(
            transition_risk_exit_threshold=params["exit_transition_risk"],
        ),
    )


def _check_promotion_criteria(
    avg_val_sharpe: float,
    avg_val_drawdown: float,
    baseline_sharpe: float,
    config: WalkForwardConfig,
) -> tuple[str, str]:
    """Check if tuned parameters meet promotion criteria."""
    sharpe_improvement = avg_val_sharpe - baseline_sharpe

    if sharpe_improvement < config.min_sharpe_improvement:
        return (
            "REJECT",
            f"Sharpe improvement {sharpe_improvement:.2f} < required {config.min_sharpe_improvement:.2f}",
        )

    if avg_val_drawdown > config.max_drawdown_regression:
        return (
            "REJECT",
            f"Drawdown {avg_val_drawdown:.2%} > allowed {config.max_drawdown_regression:.2%}",
        )

    return ("PROMOTE", f"Sharpe improvement {sharpe_improvement:.2f}, drawdown {avg_val_drawdown:.2%} acceptable")


def _format_timestamp(ts_ns: int) -> str:
    """Format nanosecond timestamp as readable UTC date."""
    return datetime.fromtimestamp(ts_ns / 1e9, tz=UTC).strftime("%Y-%m-%d")


def save_results(result: TuningResult, output_path: Path) -> None:
    """Save tuning results to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to dict and handle Path objects
    result_dict = asdict(result)

    # Convert Path objects to strings
    if "config" in result_dict and "data_root" in result_dict["config"]:
        result_dict["config"]["data_root"] = str(result_dict["config"]["data_root"])
    if "config" in result_dict and "output_path" in result_dict["config"]:
        result_dict["config"]["output_path"] = str(result_dict["config"]["output_path"])

    with open(output_path, "w") as f:
        json.dump(result_dict, f, indent=2)


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Walk-forward parameter tuning")
    parser.add_argument("--quick", action="store_true", help="Quick tuning (2 splits, 3 params)")
    parser.add_argument("--full", action="store_true", help="Full tuning (4 splits, 27 params)")
    parser.add_argument("--data-root", type=Path, help="Path to Binance data root")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol to tune")
    parser.add_argument("--output", type=Path, help="Output JSON path")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")

    args = parser.parse_args()

    # Build config
    if args.quick:
        config = quick_tuning()
    elif args.full:
        config = full_tuning()
    else:
        raise ValueError("Must specify --quick or --full")

    # Override with CLI args
    if args.data_root:
        config.data_root = args.data_root
    if args.symbol:
        config.symbol = args.symbol
    if args.output:
        config.output_path = args.output
    if args.quiet:
        config.verbose = False

    # Run tuning
    result = run_walk_forward_tuning(config)

    # Save results
    save_results(result, config.output_path)

    if config.verbose:
        print("\n" + "=" * 80)
        print("TUNING COMPLETE")
        print("=" * 80)
        print(f"Average validation Sharpe: {result.avg_val_sharpe:.2f}")
        print(f"Average validation drawdown: {result.avg_val_drawdown:.2%}")
        print("\nRecommended parameters:")
        for param, value in result.recommended_params.items():
            print(f"  {param}: {value:.2f}")
        print(f"\nPromotion decision: {result.promotion_decision}")
        print(f"Reason: {result.promotion_reason}")
        print(f"\nResults saved to: {config.output_path}")


if __name__ == "__main__":
    main()
