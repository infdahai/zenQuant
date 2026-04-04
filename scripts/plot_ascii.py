#!/usr/bin/env python3
"""Generate ASCII art equity curve from CSV.

Usage:
    uv run python scripts/plot_ascii.py logs/paper_trading/equity_10k.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def plot_ascii_equity_curve(csv_file: str, width: int = 80, height: int = 25) -> None:
    """Plot equity curve as ASCII art."""

    # Read CSV
    rows = []
    with Path(csv_file).open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                'timestamp': row['timestamp'][:10],  # Date only
                'equity': float(row['equity']),
                'dd': float(row['drawdown_pct']),
            })

    if not rows:
        print("No data in CSV")
        return

    # Sample data points to fit width
    step = max(1, len(rows) // width)
    sampled = rows[::step]

    # Get equity range
    equities = [r['equity'] for r in sampled]
    min_equity = min(equities)
    max_equity = max(equities)
    equity_range = max_equity - min_equity

    print(f"\n{'=' * width}")
    print(f"Equity Curve: ${rows[0]['equity']:,.0f} → ${rows[-1]['equity']:,.0f}")
    print(f"Return: {(rows[-1]['equity'] / rows[0]['equity'] - 1) * 100:.1f}%")
    print(f"Max DD: {max(r['dd'] for r in rows):.1f}%")
    print(f"{'=' * width}\n")

    # Create grid
    grid = [[' ' for _ in range(width)] for _ in range(height)]

    # Plot equity line
    for i, row in enumerate(sampled):
        if i >= width:
            break

        equity = row['equity']
        # Normalize to grid height
        y = int((equity - min_equity) / equity_range * (height - 1)) if equity_range > 0 else height // 2
        y = height - 1 - y  # Flip y-axis

        grid[y][i] = '●'

        # Fill column below
        for j in range(y + 1, height):
            if grid[j][i] == ' ':
                grid[j][i] = '│'

    # Print grid
    for i, row_chars in enumerate(grid):
        # Y-axis label every few rows
        if i % 5 == 0:
            equity_at_level = max_equity - (i / (height - 1)) * equity_range
            label = f"${equity_at_level:>8,.0f} │"
        else:
            label = "           │"

        print(label + ''.join(row_chars))

    # X-axis
    print("           └" + "─" * width)
    print("            " + rows[0]['timestamp'] + (" " * (width - 35)) + rows[-1]['timestamp'])

    # Legend
    print(f"\nLegend: ● = Equity point | │ = Fill")
    print(f"Samples: {len(sampled)} points (from {len(rows)} total)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot ASCII equity curve from CSV")
    parser.add_argument("csv_file", help="Path to equity curve CSV file")
    parser.add_argument("--width", type=int, default=80, help="Chart width (default: 80)")
    parser.add_argument("--height", type=int, default=25, help="Chart height (default: 25)")
    args = parser.parse_args()

    plot_ascii_equity_curve(args.csv_file, args.width, args.height)


if __name__ == "__main__":
    main()
