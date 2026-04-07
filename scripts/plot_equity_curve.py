#!/usr/bin/env python3
"""Plot equity curve from backtest with custom starting equity.

Usage:
    uv run python scripts/plot_equity_curve.py --starting-equity 10000
    uv run python scripts/plot_equity_curve.py --starting-equity 100000
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.risk import RiskOverlayConfig


def plot_equity_curve(
    starting_equity: float = 10_000.0,
    output_file: str = "equity_curve.png",
    show: bool = True,
) -> None:
    """Run backtest and plot equity curve."""

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
    print("\nBacktest Results:")
    print(f"  Starting Equity: ${starting_equity:,.2f}")
    print(f"  Final Equity:    ${result.final_equity:,.2f}")
    print(f"  Net PnL:         ${result.metrics.net_pnl:,.2f}")
    print(f"  Return:          {(result.final_equity / starting_equity - 1) * 100:.1f}%")
    print(f"  Sharpe:          {result.metrics.sharpe:.2f}")
    print(f"  Max Drawdown:    {result.metrics.max_drawdown:.1%}")
    print(f"  Trades:          {result.metrics.trade_count}")
    print(f"  Win Rate:        {result.metrics.win_rate:.1%}")

    # Extract equity curve
    risk_snapshots = backtest._risk_snapshots

    if not risk_snapshots:
        print("\nNo risk snapshots available for plotting")
        return

    # Convert to timestamps and equity values
    timestamps = [datetime.fromtimestamp(s.ts_ns / 1e9, tz=UTC) for s in risk_snapshots]
    equity_values = [s.equity for s in risk_snapshots]

    # Calculate high water mark and drawdown
    hwm_values = []
    dd_values = []
    peak = starting_equity

    for equity in equity_values:
        peak = max(peak, equity)
        hwm_values.append(peak)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        dd_values.append(dd * 100)  # Convert to percentage

    # Create figure with 2 subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    # Plot 1: Equity Curve
    ax1.plot(timestamps, equity_values, label="Equity", linewidth=2, color="#2E86AB")
    ax1.plot(timestamps, hwm_values, label="High Water Mark", linewidth=1, linestyle="--", color="#A23B72", alpha=0.7)
    ax1.axhline(y=starting_equity, color="gray", linestyle=":", label=f"Starting (${starting_equity:,.0f})", alpha=0.5)

    ax1.set_ylabel("Equity ($)", fontsize=12, fontweight="bold")
    ax1.set_title(f"ZenQuant Equity Curve (Starting: ${starting_equity:,.0f})", fontsize=14, fontweight="bold", pad=20)
    ax1.legend(loc="upper left", fontsize=10)
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"${x:,.0f}"))

    # Add metrics text box
    metrics_text = (
        f"Final: ${result.final_equity:,.0f}\n"
        f"Return: {(result.final_equity / starting_equity - 1) * 100:.1f}%\n"
        f"Sharpe: {result.metrics.sharpe:.2f}\n"
        f"Max DD: {result.metrics.max_drawdown:.1%}"
    )
    ax1.text(
        0.02,
        0.98,
        metrics_text,
        transform=ax1.transAxes,
        fontsize=10,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
    )

    # Plot 2: Drawdown
    ax2.fill_between(timestamps, dd_values, 0, alpha=0.3, color="#F18F01", label="Drawdown")
    ax2.plot(timestamps, dd_values, linewidth=1.5, color="#F18F01")
    ax2.set_ylabel("Drawdown (%)", fontsize=12, fontweight="bold")
    ax2.set_xlabel("Date", fontsize=12, fontweight="bold")
    ax2.set_title("Drawdown Over Time", fontsize=12, fontweight="bold", pad=10)
    ax2.grid(True, alpha=0.3)
    ax2.invert_yaxis()  # Drawdown goes down

    # Format x-axis
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

    # Adjust layout
    plt.tight_layout()

    # Save figure
    output_path = Path(output_file)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\n✓ Saved plot to: {output_path}")

    # Show plot
    if show:
        plt.show()
    else:
        plt.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot backtest equity curve")
    parser.add_argument(
        "--starting-equity", type=float, default=10_000.0, help="Starting equity in USD (default: 10000)"
    )
    parser.add_argument(
        "--output", type=str, default="equity_curve.png", help="Output file name (default: equity_curve.png)"
    )
    parser.add_argument("--no-show", action="store_true", help="Don't show plot window, only save to file")
    args = parser.parse_args()

    plot_equity_curve(
        starting_equity=args.starting_equity,
        output_file=args.output,
        show=not args.no_show,
    )


if __name__ == "__main__":
    main()
