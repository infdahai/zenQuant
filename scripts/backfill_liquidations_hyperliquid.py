#!/usr/bin/env python3
"""Import Hyperliquid liquidation-like history from CSV into the unified pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.import_liquidation_history import _load_events

from zenquant.data.liquidation_pipeline import (
    aggregate_liquidation_events,
    merge_liquidation_buckets,
    write_aggregated_liquidations,
    write_raw_liquidations,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Hyperliquid liquidation CSV into aggregated dataset")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/raw/hyperliquid/liquidations"))
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()

    events = _load_events(args.input, args.symbol, "hyperliquid_public")
    if not events:
        raise SystemExit("no valid Hyperliquid liquidation rows found")
    raw_paths = write_raw_liquidations(args.output, events)
    agg_paths = write_aggregated_liquidations(args.output, merge_liquidation_buckets(aggregate_liquidation_events(events)))
    print(f"events={len(events)} raw_files={len(raw_paths)} agg_files={len(agg_paths)}")


if __name__ == "__main__":
    main()
