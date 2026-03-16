#!/usr/bin/env python3
"""Convert Deribit JSON files to Parquet format for efficient replay."""

import json
import pandas as pd
from pathlib import Path
import sys


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


def main():
    """Convert all Deribit JSON files to Parquet."""
    base_dir = Path("data/raw/deribit")
    instrument = "BTC-PERPETUAL"

    total_rows = 0

    # Convert klines
    json_path = base_dir / "klines" / instrument / "klines_1h.json"
    output_path = base_dir / "klines" / instrument / "klines_1h.parquet"

    if json_path.exists():
        rows = convert_klines(json_path, output_path)
        total_rows += rows
    else:
        print(f"Klines not found: {json_path}")

    # Convert funding rates
    json_path = base_dir / "funding" / instrument / "funding_history.json"
    output_path = base_dir / "funding" / instrument / "funding_history.parquet"

    if json_path.exists():
        rows = convert_funding(json_path, output_path)
        total_rows += rows
    else:
        print(f"Funding rates not found: {json_path}")

    print()
    print("=" * 60)
    print(f"Conversion complete! Total rows: {total_rows:,}")
    print()
    print("Ready for multi-exchange training!")
    print("Run: uv run python scripts/param_sweep.py --full --exchange deribit")


if __name__ == "__main__":
    main()
