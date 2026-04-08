#!/usr/bin/env python3
"""Merge liquidation buckets from multiple source roots into one aggregated dataset."""

from __future__ import annotations

import argparse
from pathlib import Path

import pyarrow.parquet as pq

from zenquant.data.liquidation_pipeline import (
    LiquidationBucket,
    merge_liquidation_buckets,
    write_aggregated_liquidations,
)


def _load_buckets(root: Path, symbol: str) -> list[LiquidationBucket]:
    buckets: list[LiquidationBucket] = []
    for path in sorted((root / "aggregated" / symbol).glob("**/*.parquet")):
        table = pq.read_table(path)
        df = table.to_pandas()
        for _, row in df.iterrows():
            buckets.append(
                LiquidationBucket(
                    bucket_start_ns=int(row["bucket_start_ns"]),
                    bucket_end_ns=int(row["bucket_end_ns"]),
                    symbol=str(row["symbol"]),
                    long_liq_count=int(row["long_liq_count"]),
                    short_liq_count=int(row["short_liq_count"]),
                    long_liq_usd=float(row["long_liq_usd"]),
                    short_liq_usd=float(row["short_liq_usd"]),
                    total_liq_usd=float(row["total_liq_usd"]),
                    max_single_liq_usd=float(row["max_single_liq_usd"]),
                    liq_imbalance=float(row["liq_imbalance"]),
                    source=str(row.get("source", "unknown")),
                )
            )
    return buckets


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge liquidation bucket roots")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output", type=Path, default=Path("data/raw/merged_liquidations"))
    parser.add_argument("--input", type=Path, action="append", required=True, help="repeatable source roots")
    args = parser.parse_args()

    buckets: list[LiquidationBucket] = []
    for root in args.input:
        buckets.extend(_load_buckets(root, args.symbol))

    merged = merge_liquidation_buckets(buckets)
    paths = write_aggregated_liquidations(args.output, merged)
    print(f"merged_buckets={len(merged)}")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
