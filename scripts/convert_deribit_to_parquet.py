#!/usr/bin/env python3
"""Convert Deribit JSON files to Parquet format for efficient replay."""

import json
import pandas as pd
from pathlib import Path
import sys

from zenquant.historical import (
    HistoricalDatasetLayout,
    HistoricalFundingRow,
    HistoricalKlineRow,
    write_parquet_rows,
)


def convert_klines(json_path: Path, output_path: Path) -> int:
    """Convert Deribit klines JSON to Parquet."""
    print(f"Converting klines from {json_path}...")

    with open(json_path) as f:
        data = json.load(f)

    if not data:
        print("  No data found")
        return 0

    # Convert to DataFrame
    df = pd.DataFrame(data)

    # Convert timestamp (milliseconds) to nanoseconds
    df["timestamp_ns"] = df["timestamp"] * 1_000_000

    # Ensure correct data types
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)

    # Sort by timestamp
    df = df.sort_values("timestamp_ns")

    # Save as Parquet
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)

    print(f"  Saved {len(df)} rows to {output_path}")
    return len(df)


def convert_klines_for_replay(json_path: Path, replay_root: Path) -> int:
    """Convert 1h Deribit klines into replay-compatible 4h and 1d parquet."""
    print(f"Building replay-compatible klines from {json_path}...")

    with open(json_path) as f:
        data = json.load(f)

    if not data:
        print("  No data found")
        return 0

    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").set_index("timestamp")
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = df[column].astype(float)

    layout = HistoricalDatasetLayout(root_dir=replay_root)
    total_rows = 0
    symbol = "BTCUSDT"

    for timeframe, pandas_rule, ns_delta in (
        ("4h", "4h", 4 * 60 * 60 * 1_000_000_000),
        ("1d", "1d", 24 * 60 * 60 * 1_000_000_000),
    ):
        aggregated = (
            df.resample(pandas_rule, label="left", closed="left")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )
        aggregated["trade_count"] = 0
        aggregated = aggregated.reset_index()
        aggregated["partition"] = aggregated["timestamp"].dt.strftime("%Y-%m")

        for partition, partition_df in aggregated.groupby("partition"):
            rows = [
                HistoricalKlineRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time_ns=int(ts.value),
                    close_time_ns=int(ts.value) + ns_delta,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=float(row.volume),
                    trade_count=int(row.trade_count),
                )
                for ts, row in zip(partition_df["timestamp"], partition_df.itertuples(index=False))
            ]
            write_parquet_rows(layout.kline_path(symbol, timeframe, partition), rows)
            total_rows += len(rows)

    print(f"  Saved {total_rows} replay-compatible kline rows under {replay_root}")
    return total_rows


def convert_funding(json_path: Path, output_path: Path) -> int:
    """Convert Deribit funding rate JSON to Parquet."""
    print(f"Converting funding rates from {json_path}...")

    with open(json_path) as f:
        data = json.load(f)

    if not data:
        print("  No data found")
        return 0

    # Convert to DataFrame
    df = pd.DataFrame(data)

    # Convert timestamp (milliseconds) to nanoseconds
    df["timestamp_ns"] = df["timestamp"] * 1_000_000

    # Ensure correct data types
    df["interest_8h"] = df["interest_8h"].astype(float)

    # Sort by timestamp
    df = df.sort_values("timestamp_ns")

    # Save as Parquet
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)

    print(f"  Saved {len(df)} rows to {output_path}")
    return len(df)


def convert_funding_for_replay(json_path: Path, replay_root: Path) -> int:
    """Convert Deribit funding history into replay-compatible parquet."""
    print(f"Building replay-compatible funding from {json_path}...")

    with open(json_path) as f:
        data = json.load(f)

    if not data:
        print("  No data found")
        return 0

    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["interest_8h"] = df["interest_8h"].astype(float)
    df["partition"] = df["timestamp"].dt.strftime("%Y-%m")

    layout = HistoricalDatasetLayout(root_dir=replay_root)
    total_rows = 0
    symbol = "BTCUSDT"
    for partition, partition_df in df.groupby("partition"):
        rows = [
            HistoricalFundingRow(
                symbol=symbol,
                funding_time_ns=int(row.timestamp.value),
                funding_rate=float(row.interest_8h),
            )
            for row in partition_df.itertuples(index=False)
        ]
        write_parquet_rows(layout.funding_path(symbol, partition), rows)
        total_rows += len(rows)

    print(f"  Saved {total_rows} replay-compatible funding rows under {replay_root}")
    return total_rows


def main():
    """Convert all Deribit JSON files to Parquet."""
    base_dir = Path("data/raw/deribit")
    replay_root = Path("data/raw/deribit_replay")
    instrument = "BTC-PERPETUAL"

    total_rows = 0

    # Convert klines
    json_path = base_dir / "klines" / instrument / "klines_1h.json"
    output_path = base_dir / "klines" / instrument / "klines_1h.parquet"

    if json_path.exists():
        rows = convert_klines(json_path, output_path)
        total_rows += rows
        total_rows += convert_klines_for_replay(json_path, replay_root)
    else:
        print(f"Klines not found: {json_path}")

    # Convert funding rates
    json_path = base_dir / "funding" / instrument / "funding_history.json"
    output_path = base_dir / "funding" / instrument / "funding_history.parquet"

    if json_path.exists():
        rows = convert_funding(json_path, output_path)
        total_rows += rows
        total_rows += convert_funding_for_replay(json_path, replay_root)
    else:
        print(f"Funding rates not found: {json_path}")

    print()
    print("=" * 60)
    print(f"Conversion complete! Total rows: {total_rows:,}")
    print()
    print("Ready for multi-exchange training!")
    print("Replay-compatible root: data/raw/deribit_replay")
    print("Run: uv run python scripts/walk_forward_tune.py --quick --data-root data/raw/deribit_replay --symbol BTCUSDT")


if __name__ == "__main__":
    main()
