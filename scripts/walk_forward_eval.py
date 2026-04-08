#!/usr/bin/env python3
"""Walk-forward evaluation — independent out-of-sample evaluator.

Anthropic harness principle: "Agents tend to confidently praise their own work."
This is the GAN-style evaluator that tests strategy on unseen data windows.

Rolling windows: train 2 years → test 6 months → slide 6 months.
Detects overfitting: strong in-sample but weak out-of-sample.

Usage:
    uv run python scripts/walk_forward_eval.py
    uv run python scripts/walk_forward_eval.py --train-years 1 --test-months 3
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.research.comparison import default_walk_forward_windows
from zenquant.research.splits import default_btc_split_spec


def _ts(year: int, month: int = 1) -> int:
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1_000_000_000)


def _shift_months(ts_ns: int, months: int) -> int:
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC)
    month_index = (dt.month - 1) + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1e9)


def run_walk_forward(train_years: int = 2, test_months: int = 6) -> None:
    print("Walk-Forward Evaluation — Train/Dev Selection Layer")
    print(f"Train: {train_years} years, Test: {test_months} months, Slide: {test_months} months")
    print("split_scope=train_dev_only")
    print("=" * 70)

    spec = default_btc_split_spec()
    windows: list[dict] = []
    for window in default_walk_forward_windows(split_spec=spec, comparison_split="dev"):
        train_end_ns = window.start_ns
        assert train_end_ns is not None and window.end_ns is not None
        train_start_ns = _shift_months(train_end_ns, -(train_years * 12))
        windows.append(
            {
                "train_start_ns": train_start_ns,
                "train_end_ns": train_end_ns,
                "test_start_ns": window.start_ns,
                "test_end_ns": window.end_ns,
                "label": window.label,
            }
        )

    print(f"Windows: {len(windows)}")
    print()

    results: list[dict] = []

    for i, w in enumerate(windows):
        print(f"[{i + 1}/{len(windows)}] {w['label']}")

        # Train period
        try:
            train_result = HistoricalBacktest(
                HistoricalBacktestConfig(start_ns=w["train_start_ns"], end_ns=w["train_end_ns"])
            ).run()
            train_sharpe = train_result.metrics.sharpe
            train_pnl = train_result.metrics.net_pnl
        except Exception as e:
            raise RuntimeError(f"Train ERROR in {w['label']}: {e}") from e

        # Test period (out-of-sample)
        try:
            test_result = HistoricalBacktest(
                HistoricalBacktestConfig(start_ns=w["test_start_ns"], end_ns=w["test_end_ns"])
            ).run()
            test_sharpe = test_result.metrics.sharpe
            test_pnl = test_result.metrics.net_pnl
        except Exception as e:
            raise RuntimeError(f"Test ERROR in {w['label']}: {e}") from e

        # Overfitting ratio: test/train (ideal > 0.5)
        overfit_ratio = test_sharpe / train_sharpe if abs(train_sharpe) > 0.01 else 0.0

        verdict = "OK"
        if train_sharpe > 1.0 and test_sharpe < 0:
            verdict = "OVERFIT"
        elif overfit_ratio < 0.3 and train_sharpe > 0.5:
            verdict = "DEGRADED"
        elif test_sharpe > train_sharpe:
            verdict = "ROBUST"

        results.append(
            {
                "label": w["label"],
                "train_sharpe": train_sharpe,
                "test_sharpe": test_sharpe,
                "train_pnl": train_pnl,
                "test_pnl": test_pnl,
                "overfit_ratio": overfit_ratio,
                "verdict": verdict,
            }
        )

        print(
            f"  Train Sharpe={train_sharpe:.2f}, Test Sharpe={test_sharpe:.2f}, Ratio={overfit_ratio:.2f} → {verdict}"
        )

    # Summary
    print("\n" + "=" * 70)
    print(f"{'Window':<45} {'Train':>8} {'Test':>8} {'Ratio':>8} {'Verdict':>10}")
    print("-" * 70)

    for r in results:
        print(
            f"{r['label']:<45} {r['train_sharpe']:>8.2f} {r['test_sharpe']:>8.2f} "
            f"{r['overfit_ratio']:>8.2f} {r['verdict']:>10}"
        )

    # Aggregate stats
    if results:
        avg_train = sum(r["train_sharpe"] for r in results) / len(results)
        avg_test = sum(r["test_sharpe"] for r in results) / len(results)
        avg_ratio = sum(r["overfit_ratio"] for r in results) / len(results)
        overfit_count = sum(1 for r in results if r["verdict"] == "OVERFIT")

        print("\nAggregates:")
        print(f"  Avg Train Sharpe: {avg_train:.2f}")
        print(f"  Avg Test Sharpe:  {avg_test:.2f}")
        print(f"  Avg OOS Ratio:    {avg_ratio:.2f}")
        print(f"  Overfit windows:  {overfit_count}/{len(results)}")

        if avg_ratio > 0.5:
            print(f"\nStrategy shows good train→dev stability (ratio={avg_ratio:.2f})")
        elif avg_ratio > 0.3:
            print(f"\nModerate train→dev degradation (ratio={avg_ratio:.2f})")
        else:
            print(f"\nSevere train→dev overfitting detected (ratio={avg_ratio:.2f})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-forward out-of-sample evaluation")
    parser.add_argument("--train-years", type=int, default=2)
    parser.add_argument("--test-months", type=int, default=6)
    args = parser.parse_args()

    run_walk_forward(train_years=args.train_years, test_months=args.test_months)
