#!/usr/bin/env python3
"""Compare and analyze parameter sweep results.

This script loads sweep results and provides various analysis views:
- Ranking by different metrics (Sharpe, PnL, drawdown)
- Parameter sensitivity analysis
- Trade-off visualization (Sharpe vs drawdown)
- Best parameter recommendations

Usage:
    # Show top 10 by Sharpe
    uv run python scripts/compare_results.py results/param_sweep.json

    # Show top 10 by PnL
    uv run python scripts/compare_results.py results/param_sweep.json --metric pnl

    # Export to CSV for further analysis
    uv run python scripts/compare_results.py results/param_sweep.json --export results/analysis.csv

    # Show parameter sensitivity
    uv run python scripts/compare_results.py results/param_sweep.json --sensitivity
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ComparisonResult:
    """Parsed result for comparison."""

    run_id: int
    params: dict[str, Any]
    sharpe: float
    net_pnl: float
    max_drawdown: float
    profit_factor: float
    win_rate: float
    total_trades: int
    runtime_s: float


def load_results(path: Path) -> list[ComparisonResult]:
    """Load sweep results from JSON."""
    with open(path) as f:
        data = json.load(f)

    results: list[ComparisonResult] = []
    for item in data["results"]:
        results.append(
            ComparisonResult(
                run_id=item["run_id"],
                params=item["params"],
                sharpe=item["metrics"]["sharpe"],
                net_pnl=item["metrics"]["net_pnl"],
                max_drawdown=item["metrics"]["max_drawdown"],
                profit_factor=item["metrics"]["profit_factor"],
                win_rate=item["metrics"]["win_rate"],
                total_trades=item["metrics"]["trade_count"],
                runtime_s=item["runtime_s"],
            )
        )
    return results


def rank_by_metric(results: list[ComparisonResult], metric: str, top_n: int = 10) -> list[ComparisonResult]:
    """Rank results by specified metric."""
    metric_map = {
        "sharpe": lambda r: r.sharpe,
        "pnl": lambda r: r.net_pnl,
        "drawdown": lambda r: -r.max_drawdown,  # Lower is better
        "profit_factor": lambda r: r.profit_factor,
        "win_rate": lambda r: r.win_rate,
    }

    if metric not in metric_map:
        raise ValueError(f"Unknown metric: {metric}. Choose from {list(metric_map.keys())}")

    sorted_results = sorted(results, key=metric_map[metric], reverse=True)
    return sorted_results[:top_n]


def print_ranking(results: list[ComparisonResult], metric: str) -> None:
    """Print ranking table."""
    print(f"\nTop 10 by {metric.upper()}:")
    print("-" * 120)
    print(
        f"{'Rank':<6} {'Sharpe':<8} {'PnL':<12} {'MaxDD':<8} {'PF':<6} {'WinRate':<8} {'Trades':<8} {'Params':<50}"
    )
    print("-" * 120)

    for i, result in enumerate(results, start=1):
        params_str = ", ".join(f"{k}={v:.2f}" for k, v in result.params.items())
        print(
            f"{i:<6} "
            f"{result.sharpe:<8.2f} "
            f"{result.net_pnl:<12,.0f} "
            f"{result.max_drawdown:<8.2%} "
            f"{result.profit_factor:<6.2f} "
            f"{result.win_rate:<8.1%} "
            f"{result.total_trades:<8} "
            f"{params_str:<50}"
        )


def analyze_sensitivity(results: list[ComparisonResult]) -> None:
    """Analyze parameter sensitivity."""
    print("\nParameter Sensitivity Analysis:")
    print("-" * 80)

    # Group by each parameter
    param_names = list(results[0].params.keys())

    for param_name in param_names:
        # Group results by this parameter value
        groups: dict[float, list[ComparisonResult]] = {}
        for result in results:
            value = result.params[param_name]
            if value not in groups:
                groups[value] = []
            groups[value].append(result)

        # Calculate average Sharpe for each value
        print(f"\n{param_name}:")
        for value in sorted(groups.keys()):
            group_results = groups[value]
            avg_sharpe = sum(r.sharpe for r in group_results) / len(group_results)
            avg_pnl = sum(r.net_pnl for r in group_results) / len(group_results)
            avg_dd = sum(r.max_drawdown for r in group_results) / len(group_results)
            print(
                f"  {value:.2f}: "
                f"Sharpe={avg_sharpe:.2f} "
                f"PnL={avg_pnl:,.0f} "
                f"MaxDD={avg_dd:.2%} "
                f"(n={len(group_results)})"
            )


def find_best_tradeoff(results: list[ComparisonResult]) -> ComparisonResult:
    """Find best Sharpe/drawdown tradeoff."""
    # Score = Sharpe / max_drawdown (higher is better)
    scored = [(r, r.sharpe / max(r.max_drawdown, 0.01)) for r in results]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0]


def export_csv(results: list[ComparisonResult], output_path: Path) -> None:
    """Export results to CSV."""
    import csv

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)

        # Header
        param_names = list(results[0].params.keys())
        header = (
            ["run_id"]
            + param_names
            + ["sharpe", "net_pnl", "max_drawdown", "profit_factor", "win_rate", "total_trades", "runtime_s"]
        )
        writer.writerow(header)

        # Data
        for result in results:
            row = [result.run_id] + [result.params[p] for p in param_names] + [
                result.sharpe,
                result.net_pnl,
                result.max_drawdown,
                result.profit_factor,
                result.win_rate,
                result.total_trades,
                result.runtime_s,
            ]
            writer.writerow(row)

    print(f"\nExported to: {output_path}")


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Compare parameter sweep results")
    parser.add_argument("input", type=Path, help="Input JSON file from param_sweep.py")
    parser.add_argument(
        "--metric",
        default="sharpe",
        choices=["sharpe", "pnl", "drawdown", "profit_factor", "win_rate"],
        help="Metric to rank by",
    )
    parser.add_argument("--top", type=int, default=10, help="Number of top results to show")
    parser.add_argument("--sensitivity", action="store_true", help="Show parameter sensitivity analysis")
    parser.add_argument("--export", type=Path, help="Export results to CSV")

    args = parser.parse_args()

    # Load results
    results = load_results(args.input)
    print(f"Loaded {len(results)} sweep results from {args.input}")

    # Show ranking
    top_results = rank_by_metric(results, args.metric, args.top)
    print_ranking(top_results, args.metric)

    # Show best tradeoff
    best = find_best_tradeoff(results)
    print("\nBest Sharpe/Drawdown Tradeoff:")
    print(f"  Sharpe={best.sharpe:.2f}, MaxDD={best.max_drawdown:.2%}")
    print(f"  Params: {best.params}")

    # Sensitivity analysis
    if args.sensitivity:
        analyze_sensitivity(results)

    # Export
    if args.export:
        export_csv(results, args.export)

    # Recommendation
    print("\n" + "=" * 80)
    print("RECOMMENDATION:")
    print("=" * 80)
    print(f"Best overall (by {args.metric}): Run #{top_results[0].run_id}")
    print(f"  Sharpe: {top_results[0].sharpe:.2f}")
    print(f"  PnL: {top_results[0].net_pnl:,.0f}")
    print(f"  Max Drawdown: {top_results[0].max_drawdown:.2%}")
    print(f"  Profit Factor: {top_results[0].profit_factor:.2f}")
    print(f"  Win Rate: {top_results[0].win_rate:.1%}")
    print(f"  Total Trades: {top_results[0].total_trades}")
    print("\nParameters to use:")
    for param, value in top_results[0].params.items():
        print(f"  {param}: {value:.2f}")


if __name__ == "__main__":
    main()
