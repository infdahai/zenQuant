#!/usr/bin/env python3
"""Fetch a deterministic snapshot of large public Deribit option flow."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.data.deribit_block_flow import fetch_public_block_rfq_trades, fetch_public_option_trades
from zenquant.research.deribit_large_option_flow import build_large_option_flow_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect recent large Deribit option trades")
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--min-amount", type=float, default=25.0)
    parser.add_argument("--min-underlying-notional-usd", type=float)
    parser.add_argument("--require-block-trade", action="store_true")
    parser.add_argument("--skip-block-rfq", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = build_snapshot(
        currency=args.currency,
        hours=args.hours,
        count=args.count,
        min_amount=args.min_amount,
        min_underlying_notional_usd=args.min_underlying_notional_usd,
        require_block_trade=args.require_block_trade,
        include_block_rfq=not args.skip_block_rfq,
        limit=args.limit,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_snapshot(
    *,
    currency: str,
    hours: float,
    count: int,
    min_amount: float,
    min_underlying_notional_usd: float | None,
    require_block_trade: bool,
    include_block_rfq: bool,
    limit: int,
) -> dict[str, Any]:
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = end_ms - int(hours * 3600 * 1000)
    public_rows = fetch_public_option_trades(
        currency=currency,
        start_timestamp_ms=start_ms,
        end_timestamp_ms=end_ms,
        count=count,
    )
    block_rfq_rows = fetch_public_block_rfq_trades(currency=currency, count=200) if include_block_rfq else []
    rows = public_rows + block_rfq_rows
    records = build_large_option_flow_records(
        rows,
        min_amount=min_amount,
        min_underlying_notional_usd=min_underlying_notional_usd,
        require_block_trade=require_block_trade,
        limit=limit,
    )
    return {
        "schema_version": "deribit_large_option_flow_snapshot.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "currency": currency.upper(),
        "window_start": datetime.fromtimestamp(start_ms / 1000, tz=UTC).isoformat(),
        "window_end": datetime.fromtimestamp(end_ms / 1000, tz=UTC).isoformat(),
        "hours": hours,
        "api_count": count,
        "api_count_limit_reached": len(public_rows) >= count,
        "min_amount": min_amount,
        "min_underlying_notional_usd": min_underlying_notional_usd,
        "require_block_trade": require_block_trade,
        "include_block_rfq": include_block_rfq,
        "fetched_rows": len(rows),
        "public_option_trade_rows": len(public_rows),
        "public_block_rfq_rows": len(block_rfq_rows),
        "large_option_rows": len(records),
        "records": records,
    }


if __name__ == "__main__":
    main()
