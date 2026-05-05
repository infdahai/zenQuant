#!/usr/bin/env python3
"""Backfill historical Deribit BTC option trades from history.deribit.com."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.deribit_history_backfill import (
    backfill_history_option_trades,
    history_backfill_markdown,
    payload_for_print,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical Deribit option trades")
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--start", required=True, help="UTC start timestamp, e.g. 2023-08-01T00:00:00+00:00")
    parser.add_argument("--end", required=True, help="UTC end timestamp, e.g. 2023-08-02T00:00:00+00:00")
    parser.add_argument("--output-root", type=Path, default=Path("data/raw/deribit_block_flow"))
    parser.add_argument("--report-dir", type=Path, default=Path("results/research_swarm/deribit_history_backfill"))
    parser.add_argument("--chunk-hours", type=float, default=24.0)
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    args = parser.parse_args()

    payload = backfill_history_option_trades(
        currency=args.currency,
        start_timestamp_ms=_timestamp_ms(args.start),
        end_timestamp_ms=_timestamp_ms(args.end),
        output_root=args.output_root,
        chunk_hours=args.chunk_hours,
        count=args.count,
        max_requests=args.max_requests,
        sleep_seconds=args.sleep_seconds,
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.report_dir / "deribit_history_backfill.json"
    md_path = args.report_dir / "deribit_history_backfill.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(history_backfill_markdown(payload), encoding="utf-8")
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path), **payload_for_print(payload)}, indent=2))


def _timestamp_ms(value: str) -> int:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return int(timestamp.timestamp() * 1000)


if __name__ == "__main__":
    main()
