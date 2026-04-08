#!/usr/bin/env python3
"""Continuous Deribit options chain collector — snapshots every 4H.

Designed to run as a long-lived daemon or cron job. Collects ATM IV, skew,
term structure data needed to enable options_vol_signal after 30 days.

Deribit public API is completely free, no API key needed.
Rate limit: 20 req/s (we use ~3 req per snapshot).

Usage:
    # Daemon mode (runs continuously, snapshots every 4H):
    uv run python scripts/collect_deribit_options.py

    # Single snapshot (cron-friendly):
    uv run python scripts/collect_deribit_options.py --once

    # Custom interval:
    uv run python scripts/collect_deribit_options.py --interval 3600  # every 1H
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.download_deribit_options_chain import fetch_option_chain, save_option_chain

from zenquant.data.deribit_status import write_deribit_status

DEFAULT_INTERVAL = 4 * 3600  # 4 hours in seconds
DEFAULT_OUTPUT = Path("data/raw/deribit_options")

_shutdown = False


def _handle_signal(signum: int, _: object) -> None:
    global _shutdown
    print(f"\nReceived signal {signum}, shutting down after current snapshot...")
    _shutdown = True


def collect_once(currency: str, output_root: Path) -> bool:
    """Take one options chain snapshot. Returns True on success."""
    try:
        rows = fetch_option_chain(currency=currency)
        if not rows:
            print(f"  [{_ts()}] WARNING: No options rows returned")
            return False

        _, parquet_path = save_option_chain(rows, output_root)
        # Extract key metrics for logging
        spot = rows[0].underlying_price
        n_calls = sum(1 for r in rows if r.option_type == "call")
        n_puts = sum(1 for r in rows if r.option_type == "put")
        total_oi = sum(r.open_interest for r in rows)
        print(
            f"  [{_ts()}] OK: {len(rows)} options "
            f"({n_calls}C/{n_puts}P), spot=${spot:,.0f}, "
            f"OI={total_oi:,.0f}, saved={parquet_path.name}"
        )
        write_deribit_status(output_root, currency)
        return True
    except Exception as e:
        print(f"  [{_ts()}] ERROR: {e}")
        write_deribit_status(output_root, currency)
        return False


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Deribit options chain snapshots")
    parser.add_argument("--currency", default="BTC", help="BTC or ETH")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Seconds between snapshots")
    parser.add_argument("--once", action="store_true", help="Single snapshot then exit")
    args = parser.parse_args()

    print(f"Deribit Options Collector — {args.currency}")
    print(f"Output: {args.output}")
    print(f"Interval: {args.interval}s ({args.interval / 3600:.1f}h)")
    print("=" * 50)

    if args.once:
        success = collect_once(args.currency, args.output)
        sys.exit(0 if success else 1)

    # Daemon mode
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    n_snapshots = 0
    n_errors = 0

    while not _shutdown:
        success = collect_once(args.currency, args.output)
        if success:
            n_snapshots += 1
        else:
            n_errors += 1

        if _shutdown:
            break

        # Wait for next interval
        next_time = time.time() + args.interval
        while time.time() < next_time and not _shutdown:
            time.sleep(min(10, next_time - time.time()))

    print(f"\nCollector stopped. Snapshots: {n_snapshots}, Errors: {n_errors}")


if __name__ == "__main__":
    main()
