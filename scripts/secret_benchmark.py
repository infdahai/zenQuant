#!/usr/bin/env python3
"""Secret benchmark — quarterly OOS validation.

STRICT RULE: Do NOT tune parameters based on this output.
Run only quarterly (or via GitHub Actions cron).
Output is PASS/FAIL only — detailed metrics intentionally hidden
to prevent unconscious snooping.

Usage:
    uv run python scripts/secret_benchmark.py

    # Force detailed output (ONLY for debugging infrastructure issues)
    uv run python scripts/secret_benchmark.py --debug
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Secret OOS benchmark (quarterly)")
    parser.add_argument("--debug", action="store_true", help="Show detailed metrics (ONLY for infra debugging)")
    args = parser.parse_args()

    # Load split config
    split_path = Path("config/data_splits.toml")
    if not split_path.exists():
        print("ERROR: config/data_splits.toml not found")
        sys.exit(1)

    with split_path.open("rb") as f:
        splits = tomllib.load(f)["splits"]

    test_start = datetime.strptime(splits["test_start"], "%Y-%m-%d").replace(tzinfo=UTC)
    test_start_ns = int(test_start.timestamp() * 1_000_000_000)

    print("=" * 50)
    print("SECRET BENCHMARK — Quarterly OOS Validation")
    print(f"Test period: {splits['test_start']} → present")
    print("=" * 50)
    print()
    print("WARNING: Do NOT tune parameters based on this result.")
    print("         Any change triggered by this = OVERFIT.")
    print()

    try:
        result = HistoricalBacktest(
            HistoricalBacktestConfig(start_ns=test_start_ns)
        ).run()
    except Exception as e:
        print(f"ERROR: Benchmark failed to run: {e}")
        print("This may mean insufficient test data (2026+ not yet available).")
        sys.exit(1)

    m = result.metrics

    # Pass criteria (hard thresholds — sprint contract)
    sharpe_ok = m.sharpe > 0.5
    dd_ok = m.max_drawdown < 0.30
    trades_ok = m.trade_count > 10
    pnl_ok = m.net_pnl > 0

    passed = sharpe_ok and dd_ok and trades_ok and pnl_ok

    if args.debug:
        print(f"[DEBUG] Sharpe: {m.sharpe:.2f}")
        print(f"[DEBUG] PnL: ${m.net_pnl:,.0f}")
        print(f"[DEBUG] DD: {m.max_drawdown:.1%}")
        print(f"[DEBUG] Trades: {m.trade_count}")
    else:
        # Intentionally minimal output — prevent snooping
        print(f"Sharpe > 0.5:    {'PASS' if sharpe_ok else 'FAIL'}")
        print(f"DD < 30%:        {'PASS' if dd_ok else 'FAIL'}")
        print(f"Trades > 10:     {'PASS' if trades_ok else 'FAIL'}")
        print(f"PnL > 0:         {'PASS' if pnl_ok else 'FAIL'}")

    print()
    print(f"VERDICT: {'PASS' if passed else 'FAIL'}")

    # Log result
    log_path = Path("experiments/SNOOPING_LOG.md")
    if log_path.exists():
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        entry = f"| {date_str} | {'>.5' if sharpe_ok else '<.5'} | {'<30%' if dd_ok else '>30%'} | {'PASS' if passed else 'FAIL'} | — |\n"
        content = log_path.read_text()
        content = content.replace("| — | — | — | — | — |", entry + "| — | — | — | — | — |")
        log_path.write_text(content)
        print(f"Result logged to {log_path}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
