#!/usr/bin/env python3
"""Analyze equity curve and show yearly breakdown.

Usage:
    uv run python scripts/analyze_equity_curve.py logs/paper_trading/equity_10k.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def analyze_equity_curve(csv_file: str) -> None:
    """Analyze equity curve with yearly breakdown."""

    # Read CSV
    rows = []
    with Path(csv_file).open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "timestamp": row["timestamp"],
                    "year": row["timestamp"][:4],
                    "equity": float(row["equity"]),
                    "dd": float(row["drawdown_pct"]),
                }
            )

    if not rows:
        print("No data in CSV")
        return

    # Overall summary
    start_equity = rows[0]["equity"]
    end_equity = rows[-1]["equity"]
    total_return = (end_equity / start_equity - 1) * 100
    max_dd = max(r["dd"] for r in rows)

    print("\n" + "=" * 70)
    print(f"{'EQUITY CURVE ANALYSIS':^70}")
    print("=" * 70)

    print("\n📊 Overall Performance:")
    print(f"  Starting Equity:  ${start_equity:,.2f}")
    print(f"  Final Equity:     ${end_equity:,.2f}")
    print(f"  Net PnL:          ${end_equity - start_equity:,.2f}")
    print(f"  Total Return:     {total_return:.1f}%")
    print(f"  Multiple:         {end_equity / start_equity:.2f}x")
    print(f"  Max Drawdown:     {max_dd:.1f}%")

    # Yearly breakdown
    yearly_data = defaultdict(lambda: {"start": None, "end": None, "max_dd": 0.0})

    for row in rows:
        year = row["year"]
        if yearly_data[year]["start"] is None:
            yearly_data[year]["start"] = row["equity"]
        yearly_data[year]["end"] = row["equity"]
        yearly_data[year]["max_dd"] = max(yearly_data[year]["max_dd"], row["dd"])

    print("\n📈 Yearly Breakdown:")
    print(f"{'Year':<6} {'Start Equity':>15} {'End Equity':>15} {'Return':>10} {'Max DD':>8}")
    print("-" * 70)

    total_positive_years = 0
    total_years = 0

    for year in sorted(yearly_data.keys()):
        data = yearly_data[year]
        if data["start"] is None or data["end"] is None:
            continue

        year_return = (data["end"] / data["start"] - 1) * 100
        total_years += 1
        if year_return > 0:
            total_positive_years += 1

        # Color indicator (text)
        indicator = "✓" if year_return > 0 else "✗"

        print(
            f"{year:<6} ${data['start']:>13,.0f} ${data['end']:>13,.0f} {year_return:>8.1f}% {data['max_dd']:>6.1f}%  {indicator}"
        )

    print("-" * 70)
    print(f"Positive Years: {total_positive_years}/{total_years} ({total_positive_years / total_years * 100:.0f}%)")

    # Key milestones
    print("\n🎯 Key Milestones:")

    milestones = [
        (20_000, "2x"),
        (50_000, "5x"),
        (100_000, "10x"),
        (200_000, "20x"),
        (500_000, "50x"),
        (1_000_000, "100x"),
    ]

    for target, label in milestones:
        for row in rows:
            if row["equity"] >= target:
                print(f"  {label:>5} (${target:>8,}): {row['timestamp'][:10]} (${row['equity']:,.0f})")
                break

    # Worst drawdown period
    worst_dd = max(rows, key=lambda r: r["dd"])
    print("\n⚠️  Worst Drawdown:")
    print(f"  Date:     {worst_dd['timestamp'][:10]}")
    print(f"  Drawdown: {worst_dd['dd']:.1f}%")
    print(f"  Equity:   ${worst_dd['equity']:,.0f}")

    print("\n" + "=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze equity curve from CSV")
    parser.add_argument("csv_file", help="Path to equity curve CSV file")
    args = parser.parse_args()

    analyze_equity_curve(args.csv_file)


if __name__ == "__main__":
    main()
