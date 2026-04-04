#!/usr/bin/env python3
"""Export equity curve data from backtest (no matplotlib required).

Outputs CSV file with timestamps, equity, drawdown for easy plotting.

Usage:
    uv run python scripts/export_equity_curve.py --starting-equity 10000
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.risk import RiskOverlayConfig


def export_equity_curve(
    starting_equity: float = 10_000.0,
    output_file: str = "equity_curve.csv",
) -> None:
    """Run backtest and export equity curve to CSV."""

    print(f"Running backtest with starting equity: ${starting_equity:,.0f}")
    print("=" * 70)

    # Run backtest
    config = HistoricalBacktestConfig(
        root_dir=Path("data/raw/binance"),
        symbol="BTCUSDT",
        starting_equity=starting_equity,
        risk_config=RiskOverlayConfig(starting_equity=starting_equity),
    )

    backtest = HistoricalBacktest(config)
    result = backtest.run()

    # Print summary
    print(f"\nBacktest Results:")
    print(f"  Starting Equity: ${starting_equity:,.2f}")
    print(f"  Final Equity:    ${result.final_equity:,.2f}")
    print(f"  Net PnL:         ${result.metrics.net_pnl:,.2f}")
    print(f"  Return:          {(result.final_equity / starting_equity - 1) * 100:.1f}%")
    print(f"  Multiple:        {result.final_equity / starting_equity:.2f}x")
    print(f"  Sharpe:          {result.metrics.sharpe:.2f}")
    print(f"  Max Drawdown:    {result.metrics.max_drawdown:.1%}")
    print(f"  Trades:          {result.metrics.trade_count}")
    print(f"  Win Rate:        {result.metrics.win_rate:.1%}")

    # Extract equity curve
    risk_snapshots = backtest._risk_snapshots

    if not risk_snapshots:
        print("\nNo risk snapshots available")
        return

    # Calculate high water mark and drawdown
    peak = starting_equity
    rows = []

    for snapshot in risk_snapshots:
        timestamp = datetime.fromtimestamp(snapshot.ts_ns / 1e9, tz=UTC)
        equity = snapshot.equity
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0

        rows.append({
            'timestamp': timestamp.isoformat(),
            'equity': round(equity, 2),
            'high_water_mark': round(peak, 2),
            'drawdown_pct': round(dd * 100, 2),
            'return_pct': round((equity / starting_equity - 1) * 100, 2),
        })

    # Write CSV
    output_path = Path(output_file)
    with output_path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['timestamp', 'equity', 'high_water_mark', 'drawdown_pct', 'return_pct'])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✓ Exported {len(rows)} data points to: {output_path}")
    print(f"\nTo plot in Python:")
    print(f"  import pandas as pd")
    print(f"  import matplotlib.pyplot as plt")
    print(f"  df = pd.read_csv('{output_path}')")
    print(f"  df['timestamp'] = pd.to_datetime(df['timestamp'])")
    print(f"  df.plot(x='timestamp', y='equity', figsize=(12,6))")
    print(f"  plt.show()")

    # Print ASCII summary
    print(f"\nEquity Curve Summary (every 100th point):")
    print(f"{'Date':<12} {'Equity':>12} {'Return':>10} {'DD':>8}")
    print("-" * 45)

    step = max(1, len(rows) // 20)  # Show ~20 points
    for i in range(0, len(rows), step):
        row = rows[i]
        date = row['timestamp'][:10]
        equity = row['equity']
        ret = row['return_pct']
        dd = row['drawdown_pct']
        print(f"{date:<12} ${equity:>10,.0f} {ret:>8.1f}% {dd:>6.1f}%")

    # Final row
    if len(rows) > 0:
        row = rows[-1]
        date = row['timestamp'][:10]
        equity = row['equity']
        ret = row['return_pct']
        dd = row['drawdown_pct']
        print(f"{date:<12} ${equity:>10,.0f} {ret:>8.1f}% {dd:>6.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export backtest equity curve to CSV")
    parser.add_argument("--starting-equity", type=float, default=10_000.0,
                       help="Starting equity in USD (default: 10000)")
    parser.add_argument("--output", type=str, default="equity_curve.csv",
                       help="Output CSV file name (default: equity_curve.csv)")
    args = parser.parse_args()

    export_equity_curve(
        starting_equity=args.starting_equity,
        output_file=args.output,
    )


if __name__ == "__main__":
    main()
