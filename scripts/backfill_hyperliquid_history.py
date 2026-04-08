#!/usr/bin/env python3
"""Backfill recent Hyperliquid public history and build proxy liquidation buckets."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from zenquant.data.hyperliquid_public import build_hyperliquid_proxy_history, write_hyperliquid_raw
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
    parser = argparse.ArgumentParser(description="Backfill Hyperliquid public candle/funding history")
    parser.add_argument("--coin", default="BTC")
    parser.add_argument("--output", type=Path, default=Path("data/raw/hyperliquid/liquidations"))
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(days=args.days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    candles, funding, buckets, status = build_hyperliquid_proxy_history(
        coin=args.coin,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    raw_paths = write_hyperliquid_raw(args.output, candles=candles, funding=funding)
    agg_paths = write_aggregated_liquidations(args.output, buckets)

    status_path = args.output / "_backfill_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(status)
    payload["last_success_ts"] = datetime.now(UTC).isoformat()
    payload["promotion_ready"] = False
    payload.update(_bucket_stats(buckets))
    status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": asdict(status),
                "raw_files": [str(path) for path in raw_paths],
                "agg_files": [str(path) for path in agg_paths],
            },
            indent=2,
        )
    )
    if status.blocked_by:
        print(f"blocked_by={status.blocked_by}")


if __name__ == "__main__":
    main()
