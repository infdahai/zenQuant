#!/usr/bin/env python3
"""Analyze signal correlation to detect redundancy.

Check if multiple signals are providing same information (correlation > 0.7).
High correlation suggests signals should be merged or one should be removed.

Usage:
    uv run python scripts/analyze_signal_correlation.py
    uv run python scripts/analyze_signal_correlation.py --signals funding_alpha,crowding,cvd,liquidity
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.risk import RiskOverlayConfig


def analyze_signal_correlation(signals: list[str] | None = None) -> None:
    """Analyze correlation between signals to detect redundancy."""

    print("=" * 70)
    print("Signal Correlation Analysis")
    print("=" * 70)
    print()

    # Run full backtest to get decision logs
    print("[1/3] Running full historical backtest...")
    config = HistoricalBacktestConfig(
        root_dir=Path("data/raw/binance"),
        symbol="BTCUSDT",
        starting_equity=100_000.0,
        risk_config=RiskOverlayConfig(starting_equity=100_000.0),
    )

    backtest = HistoricalBacktest(config)
    result = backtest.run()

    print(f"  Sharpe: {result.metrics.sharpe:.2f}")
    print(f"  Trades: {result.metrics.trade_count}")
    print(f"  Bars: {len(backtest._risk_snapshots)}")
    print()

    # Extract analysis rows
    print("[2/3] Extracting signal values...")
    analysis_rows = backtest.build_analysis_rows()

    if not analysis_rows:
        print("ERROR: No analysis rows generated!")
        return

    # Convert to DataFrame
    df = pd.DataFrame(analysis_rows)

    # Default signals to analyze (positioning + flow signals)
    if signals is None:
        signals = [
            "funding_oi_z",  # funding_alpha_signal (positioning)
            "ob_liq_z",  # liquidity_signal (order book)
            "cvd_slope",  # cvd_signal (order flow)
            # crowding signal not in analysis_rows directly (need to check)
        ]

    # Check which signals are available
    available_signals = []
    for sig in signals:
        if sig in df.columns:
            available_signals.append(sig)
        else:
            print(f"  Warning: Signal '{sig}' not found in analysis rows")

    if len(available_signals) < 2:
        print(f"ERROR: Need at least 2 signals, only found: {available_signals}")
        return

    print(f"  Found {len(available_signals)} signals: {', '.join(available_signals)}")
    print()

    # Compute correlation matrix
    print("[3/3] Computing correlation matrix...")

    # Extract signal columns and drop NaN
    signal_df = df[available_signals].dropna()

    if len(signal_df) < 100:
        print(f"WARNING: Only {len(signal_df)} valid rows (need >100 for reliable correlation)")

    # Pearson correlation
    corr_matrix = signal_df.corr()

    print()
    print("Correlation Matrix (Pearson)")
    print("-" * 70)
    print(corr_matrix.to_string())
    print()

    # Highlight high correlations (> 0.7)
    print("High Correlations (|r| > 0.7)")
    print("-" * 70)

    high_corr_found = False
    for i, sig1 in enumerate(available_signals):
        for j, sig2 in enumerate(available_signals):
            if i < j:  # Only upper triangle (avoid duplicates)
                corr = corr_matrix.loc[sig1, sig2]
                if abs(corr) > 0.7:
                    high_corr_found = True
                    direction = "positive" if corr > 0 else "negative"
                    print(f"  {sig1:<20} <-> {sig2:<20}: r={corr:>6.3f} ({direction})")

    if not high_corr_found:
        print("  None found (all correlations < 0.7)")

    print()

    # Summary statistics
    print("Signal Statistics")
    print("-" * 70)
    stats = signal_df.describe().T
    print(stats[["mean", "std", "min", "max"]].to_string())
    print()

    # Interpretation
    print("=" * 70)
    print("Interpretation")
    print("=" * 70)
    print()
    print("Correlation Thresholds:")
    print("  |r| > 0.9: VERY HIGH — signals are near-duplicates, remove one")
    print("  |r| > 0.7: HIGH     — signals provide similar info, consider merging")
    print("  |r| > 0.5: MEDIUM   — some overlap, acceptable")
    print("  |r| < 0.5: LOW      — signals are independent, good diversification")
    print()

    if high_corr_found:
        print("⚠️  HIGH CORRELATION DETECTED")
        print()
        print("Action Required:")
        print("  1. Review signal definitions to understand why they're correlated")
        print("  2. Consider merging highly correlated signals")
        print("  3. Or remove the less predictive signal (check IC)")
        print()
    else:
        print("✅ SIGNALS ARE WELL-DIVERSIFIED")
        print()
        print("All correlations < 0.7 suggests signals provide independent information.")
        print("This is good for portfolio diversification.")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze signal correlation")
    parser.add_argument(
        "--signals",
        type=str,
        help="Comma-separated list of signal column names (default: funding_oi_z,ob_liq_z,cvd_slope)",
    )
    args = parser.parse_args()

    signals = None
    if args.signals:
        signals = [s.strip() for s in args.signals.split(",")]

    analyze_signal_correlation(signals)


if __name__ == "__main__":
    main()
