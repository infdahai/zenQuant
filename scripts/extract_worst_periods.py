#!/usr/bin/env python3
"""Extract worst DD periods from backtest for historical adversarial replay.

Implements historical replay enhancement from GAN discriminator evaluation (2026-04-05).
Instead of synthetic scenarios, replay actual periods where strategy performed worst.

Usage:
    uv run python scripts/extract_worst_periods.py --top 5

Output:
    - JSON file with worst period metadata (start_ns, end_ns, max_dd, description)
    - Used by adversarial_stress.py for historical replay scenarios
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


def extract_worst_dd_windows(
    risk_snapshots: list,
    top_n: int = 5,
    min_dd_threshold: float = 0.05,
    context_bars: int = 100,
) -> list[dict]:
    """Extract top N worst drawdown windows from risk snapshots.

    Args:
        risk_snapshots: List of RiskSnapshot from backtest
        top_n: Number of worst periods to extract
        min_dd_threshold: Minimum DD to consider (e.g., 0.05 = 5%)
        context_bars: Bars before DD peak to include for context

    Returns:
        List of dicts with: start_idx, end_idx, max_dd, peak_equity, trough_equity
    """
    if not risk_snapshots:
        return []

    # Find all local DD peaks (points where DD is at local maximum)
    dd_peaks: list[dict] = []
    peak_equity = risk_snapshots[0].equity
    in_drawdown = False
    dd_start_idx = 0
    max_dd_in_window = 0.0
    max_dd_idx = 0

    for i, snapshot in enumerate(risk_snapshots):
        equity = snapshot.equity

        # Update peak
        if equity >= peak_equity:
            # End of drawdown period
            if in_drawdown and max_dd_in_window >= min_dd_threshold:
                dd_peaks.append({
                    'start_idx': max(0, dd_start_idx - context_bars),
                    'peak_idx': dd_start_idx,
                    'trough_idx': max_dd_idx,
                    'end_idx': i,
                    'max_dd': max_dd_in_window,
                    'peak_equity': peak_equity,
                    'trough_equity': risk_snapshots[max_dd_idx].equity,
                    'timestamp': snapshot.ts_ns,
                })
            peak_equity = equity
            in_drawdown = False
            max_dd_in_window = 0.0
        else:
            # In drawdown
            if not in_drawdown:
                dd_start_idx = i
                in_drawdown = True

            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            if dd > max_dd_in_window:
                max_dd_in_window = dd
                max_dd_idx = i

    # Sort by worst DD first
    dd_peaks.sort(key=lambda x: x['max_dd'], reverse=True)

    # Take top N non-overlapping periods
    selected: list[dict] = []
    for peak in dd_peaks:
        if len(selected) >= top_n:
            break

        # Check if overlaps with already selected periods
        overlaps = False
        for s in selected:
            if not (peak['end_idx'] < s['start_idx'] or peak['start_idx'] > s['end_idx']):
                overlaps = True
                break

        if not overlaps:
            selected.append(peak)

    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract worst DD periods from backtest")
    parser.add_argument("--top", type=int, default=5, help="Number of worst periods to extract")
    parser.add_argument("--min-dd", type=float, default=0.05, help="Minimum DD threshold (e.g., 0.05 = 5%)")
    parser.add_argument("--context-bars", type=int, default=50, help="Context bars before DD peak")
    parser.add_argument("--output", type=str, default="tests/adversarial/worst_periods.json", help="Output JSON file")
    args = parser.parse_args()

    print("Extracting Worst DD Periods from Full Backtest")
    print("=" * 65)

    # Run full backtest (2020-2025 or available data)
    print("\n[1/3] Running full historical backtest...")
    config = HistoricalBacktestConfig(
        root_dir=Path("data/raw/binance"),
        symbol="BTCUSDT",
    )
    backtest = HistoricalBacktest(config)
    result = backtest.run()

    print(f"  Backtest complete:")
    print(f"    Sharpe: {result.metrics.sharpe:.2f}")
    print(f"    Max DD: {result.metrics.max_drawdown:.1%}")
    print(f"    Trades: {result.metrics.trade_count}")
    print(f"    Risk snapshots: {len(backtest._risk_snapshots)}")

    # Extract worst periods
    print(f"\n[2/3] Extracting top {args.top} worst DD periods...")
    worst_periods = extract_worst_dd_windows(
        backtest._risk_snapshots,
        top_n=args.top,
        min_dd_threshold=args.min_dd,
        context_bars=args.context_bars,
    )

    if not worst_periods:
        print("  No significant DD periods found (all DD < threshold)")
        sys.exit(0)

    # Convert to serializable format
    output_periods = []
    for i, period in enumerate(worst_periods, 1):
        timestamp_ns = period['timestamp']
        dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=UTC)

        # Calculate window timestamps from risk snapshots
        start_snapshot = backtest._risk_snapshots[period['start_idx']]
        end_snapshot = backtest._risk_snapshots[period['end_idx']]

        output_periods.append({
            'rank': i,
            'max_dd': round(period['max_dd'], 4),
            'start_ns': start_snapshot.ts_ns,
            'end_ns': end_snapshot.ts_ns,
            'start_date': datetime.fromtimestamp(start_snapshot.ts_ns / 1e9, tz=UTC).isoformat(),
            'end_date': datetime.fromtimestamp(end_snapshot.ts_ns / 1e9, tz=UTC).isoformat(),
            'peak_equity': round(period['peak_equity'], 2),
            'trough_equity': round(period['trough_equity'], 2),
            'bars': period['end_idx'] - period['start_idx'],
            'description': f"{dt.strftime('%Y-%m')} DD",
        })

        print(f"  #{i}: {output_periods[-1]['max_dd']:.1%} DD "
              f"({output_periods[-1]['start_date'][:10]} to {output_periods[-1]['end_date'][:10]}, "
              f"{output_periods[-1]['bars']} bars)")

    # Save to JSON
    print(f"\n[3/3] Saving to {args.output}...")
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_data = {
        'extracted_at': datetime.now(UTC).isoformat(),
        'full_backtest': {
            'sharpe': round(result.metrics.sharpe, 4),
            'max_dd': round(result.metrics.max_drawdown, 4),
            'total_bars': result.metrics.total_bars,
        },
        'worst_periods': output_periods,
    }

    output_path.write_text(json.dumps(output_data, indent=2))
    print(f"  ✓ Saved {len(output_periods)} worst periods")

    print(f"\n{'=' * 65}")
    print("Next step: Run adversarial_stress.py --historical")
    print("This will replay these worst periods with strict pass criteria.")


if __name__ == "__main__":
    main()
