#!/usr/bin/env python3
"""Update real data baseline after approved parameter changes.

This script:
1. Runs full backtest on real data (2020-2025)
2. Shows before/after comparison
3. Updates tests/golden/real_data_baseline.json
4. Appends entry to experiments/SNOOPING_LOG.md

Usage:
    uv run python scripts/update_golden.py --reason "DCA on loss: DD -47%, Sharpe -21%"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig

BASELINE_PATH = Path("tests/golden/real_data_baseline.json")
SNOOPING_LOG = Path("experiments/SNOOPING_LOG.md")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update real data baseline")
    parser.add_argument("--reason", required=True, help="Why this baseline update is needed")
    parser.add_argument("--dry-run", action="store_true", help="Show comparison without updating")
    args = parser.parse_args()

    # Load old baseline
    old = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else None

    # Run backtest
    print("Running full backtest...")
    result = HistoricalBacktest(
        HistoricalBacktestConfig(
            profile_path=Path("config/profiles/default.toml"),
        )
    ).run()

    new_metrics = {
        "sharpe": round(result.metrics.sharpe, 4),
        "max_drawdown": round(result.metrics.max_drawdown, 4),
        "profit_factor": round(result.metrics.profit_factor, 4),
        "trade_count": result.metrics.trade_count,
    }

    # Print comparison
    print()
    print("=" * 60)
    print("BASELINE UPDATE COMPARISON")
    print("=" * 60)
    if old:
        om = old["metrics"]
        print(f"{'Metric':<20} {'Before':>12} {'After':>12} {'Change':>12}")
        print("-" * 60)
        for key in ["sharpe", "max_drawdown", "profit_factor", "trade_count"]:
            before = om[key]
            after = new_metrics[key]
            if isinstance(before, float):
                delta = (after - before) / max(abs(before), 0.001) * 100
                print(f"{key:<20} {before:>12.4f} {after:>12.4f} {delta:>+11.1f}%")
            else:
                print(f"{key:<20} {before:>12} {after:>12}")
    else:
        print("No previous baseline found.")
        for key, val in new_metrics.items():
            print(f"  {key}: {val}")

    print()
    print(f"Reason: {args.reason}")

    if args.dry_run:
        print("\n[DRY RUN] No files updated.")
        return

    # Update baseline file
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    new_baseline = {
        "description": "Real data baseline (2020-2025) — updated only via scripts/update_golden.py",
        "updated_at": date_str,
        "reason": args.reason,
        "metrics": new_metrics,
        "tolerances": {
            "sharpe_max_decrease_pct": 30,
            "dd_max_increase_pct": 50,
            "comment": "If Sharpe drops >30% or DD increases >50%, the change is too large for auto-merge",
        },
    }
    BASELINE_PATH.write_text(json.dumps(new_baseline, indent=2) + "\n")
    print(f"\n✅ Baseline updated: {BASELINE_PATH}")

    # Append to SNOOPING_LOG
    if SNOOPING_LOG.exists():
        sharpe_delta = f"{new_metrics['sharpe'] - om['sharpe']:+.2f}" if old else "N/A"
        entry = f"| {date_str} | Baseline update: {args.reason} | Sharpe {sharpe_delta} | Updated real_data_baseline.json | LOW — 1 run |\n"
        content = SNOOPING_LOG.read_text()
        # Insert before Budget Assessment section
        if "## Budget Assessment" in content:
            content = content.replace("## Budget Assessment", entry + "\n## Budget Assessment")
            SNOOPING_LOG.write_text(content)
            print("✅ SNOOPING_LOG updated")


if __name__ == "__main__":
    main()
