#!/usr/bin/env python3
"""Attempt Binance liquidation history backfill and fail closed when unavailable."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.data.liquidation_pipeline import (
    aggregate_liquidation_events,
    fetch_force_orders_history,
    iter_day_ranges,
    probe_binance_force_orders_support,
    write_aggregated_liquidations,
    write_raw_liquidations,
)

DEFAULT_OUTPUT = Path("data/raw/binance/liquidations")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Binance liquidation history")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.days <= 0:
        print("ERROR: --days must be > 0")
        sys.exit(1)

    probe = probe_binance_force_orders_support(args.symbol)
    if not probe.available:
        print("Binance liquidation history backfill unavailable.")
        print(f"blocked_by={probe.blocked_by}")
        print(f"detail={probe.detail}")
        sys.exit(2)

    all_events = []
    day_ranges = iter_day_ranges(args.days)
    for start_dt, end_dt in day_ranges:
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000) - 1
        events = fetch_force_orders_history(
            symbol=args.symbol,
            start_time_ms=start_ms,
            end_time_ms=end_ms,
        )
        all_events.extend(events)
        print(f"{start_dt.strftime('%Y-%m-%d')} -> {end_dt.strftime('%Y-%m-%d')} events={len(events)}")

    if not all_events:
        print("Backfill completed but returned zero events; refusing to write empty history.")
        sys.exit(3)

    raw_paths = write_raw_liquidations(args.output, all_events)
    bucket_paths = write_aggregated_liquidations(args.output, aggregate_liquidation_events(all_events))
    print(
        f"Backfill complete for {args.symbol}: events={len(all_events)} "
        f"raw_files={len(raw_paths)} agg_files={len(bucket_paths)} "
        f"until={day_ranges[-1][1].astimezone(UTC).isoformat()}"
    )


if __name__ == "__main__":
    main()
