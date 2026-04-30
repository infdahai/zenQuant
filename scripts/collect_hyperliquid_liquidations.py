#!/usr/bin/env python3
"""Collect recent Hyperliquid public trades and aggregate them into proxy 4H buckets."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from zenquant.data.hyperliquid_public import fetch_recent_trades, recent_trades_to_buckets, write_hyperliquid_raw
from zenquant.data.liquidation_pipeline import write_aggregated_liquidations


def _bucket_stats(buckets: list[object]) -> dict[str, object]:
    if not buckets:
        return {
            "days_covered": 0.0,
            "first_bucket_start": None,
            "last_bucket_end": None,
            "coverage_ratio_estimate": 0.0,
            "extreme_bars_estimate": 0,
        }
    first_start = min(int(bucket.bucket_start_ns) for bucket in buckets)
    last_end = max(int(bucket.bucket_end_ns) for bucket in buckets)
    span_bars = max(1, int((last_end - first_start) // (4 * 3600 * 1_000_000_000)))
    max_total = max(float(bucket.total_liq_usd) for bucket in buckets)
    extreme_threshold = max(250_000.0, max_total * 0.5) if max_total > 0 else 250_000.0
    return {
        "days_covered": round((last_end - first_start) / 1_000_000_000 / 86400.0, 2),
        "first_bucket_start": datetime.fromtimestamp(first_start / 1_000_000_000, tz=UTC).isoformat(),
        "last_bucket_end": datetime.fromtimestamp(last_end / 1_000_000_000, tz=UTC).isoformat(),
        "coverage_ratio_estimate": round(len(buckets) / span_bars, 4),
        "extreme_bars_estimate": sum(1 for bucket in buckets if float(bucket.total_liq_usd) >= extreme_threshold),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Hyperliquid recent public trades into proxy liquidation buckets"
    )
    parser.add_argument("--coin", default="BTC")
    parser.add_argument("--output", type=Path, default=Path("data/raw/hyperliquid/liquidations"))
    parser.add_argument("--min-trade-notional-usd", type=float, default=50_000.0)
    args = parser.parse_args()

    trades = fetch_recent_trades(args.coin)
    raw_paths = write_hyperliquid_raw(args.output, trades=trades)
    buckets = recent_trades_to_buckets([trade for trade in trades if trade.notional_usd >= args.min_trade_notional_usd])
    agg_paths = write_aggregated_liquidations(args.output, buckets)

    status = {
        "coin": args.coin.upper(),
        "last_success_ts": datetime.now(UTC).isoformat(),
        "recent_trades": len(trades),
        "proxy_buckets": len(buckets),
        "min_trade_notional_usd": args.min_trade_notional_usd,
        "blocked_by": None,
        "detail": "hyperliquid public recent-trade collector wrote liquidation-compatible proxy buckets",
        **_bucket_stats(buckets),
    }
    status_path = args.output / "_collector_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {"status": status, "raw_files": [str(p) for p in raw_paths], "agg_files": [str(p) for p in agg_paths]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
