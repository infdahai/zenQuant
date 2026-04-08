#!/usr/bin/env python3
"""Import external liquidation history into ZenQuant's aggregated format."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from zenquant.data.liquidation_pipeline import (
    LiquidationEvent,
    aggregate_liquidation_events,
    merge_liquidation_buckets,
    write_aggregated_liquidations,
    write_raw_liquidations,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import external liquidation CSV into aggregated 4H parquet")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", default=Path("data/raw/binance/liquidations"), type=Path)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--source", default="external_csv")
    return parser.parse_args()


def _load_events(path: Path, default_symbol: str, source: str = "external_csv") -> list[LiquidationEvent]:
    events: list[LiquidationEvent] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ts_ns = int(row.get("ts_ns") or row.get("timestamp_ns") or 0)
            side = str(row.get("side") or "").upper()
            price = float(row.get("price") or 0.0)
            qty = float(row.get("qty") or row.get("quantity") or 0.0)
            usd_value = float(row.get("usd_value") or row.get("notional_usd") or abs(price * qty))
            symbol = str(row.get("symbol") or default_symbol).upper()
            trade_time_ms = int(row.get("trade_time_ms") or ts_ns // 1_000_000)
            if ts_ns <= 0 or side not in {"BUY", "SELL"}:
                continue
            events.append(
                LiquidationEvent(
                    ts_ns=ts_ns,
                    symbol=symbol,
                    side=side,
                    price=price,
                    qty=qty,
                    usd_value=usd_value,
                    source=str(row.get("source") or source),
                    trade_time_ms=trade_time_ms,
                )
            )
    return events


def main() -> None:
    args = _parse_args()
    events = _load_events(args.input, args.symbol, args.source)
    if not events:
        raise SystemExit("no valid liquidation rows found in input CSV")
    raw_paths = write_raw_liquidations(args.output, events)
    buckets = merge_liquidation_buckets(aggregate_liquidation_events(events))
    paths = write_aggregated_liquidations(args.output, buckets)
    print(f"Imported {len(events)} liquidation events")
    print(f"raw_files={len(raw_paths)}")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
