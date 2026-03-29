#!/usr/bin/env python3
"""Convert Binance CSV files to Parquet format for efficient replay."""

from pathlib import Path

import pandas as pd


def csv_to_parquet(csv_dir: Path, output_dir: Path, data_type: str):
    """Convert all CSV files in directory to Parquet."""
    csv_files = list(csv_dir.glob("*.csv"))

    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        return 0

    print(f"Converting {len(csv_files)} {data_type} files...")

    # Define column names based on data type
    if data_type == "klines":
        columns = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trade_count",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ]
    elif data_type == "funding":
        columns = ["symbol", "funding_time", "funding_rate", "mark_price"]
    elif data_type == "open_interest":
        columns = ["symbol", "sum_open_interest", "sum_open_interest_value", "timestamp"]
    else:
        raise ValueError(f"Unknown data type: {data_type}")

    all_data = []
    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file, names=columns, skiprows=1)  # Skip header row
            all_data.append(df)
        except Exception as e:
            print(f"Error reading {csv_file.name}: {e}")

    if not all_data:
        return 0

    # Combine all data
    combined = pd.concat(all_data, ignore_index=True)

    # Convert timestamps to nanoseconds
    if data_type == "klines":
        combined["open_time_ns"] = combined["open_time"] * 1_000_000
        combined["close_time_ns"] = combined["close_time"] * 1_000_000
    elif data_type == "funding":
        combined["funding_time_ns"] = combined["funding_time"] * 1_000_000
    elif data_type == "open_interest":
        combined["ts_ns"] = combined["timestamp"] * 1_000_000

    # Save as Parquet
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "data.parquet"
    combined.to_parquet(output_file, index=False)

    print(f"  Saved {len(combined)} rows to {output_file}")
    return len(combined)


def main():
    base_dir = Path("data/raw/binance")
    symbol = "BTCUSDT"

    total_rows = 0

    # Convert 4h klines
    csv_dir = base_dir / "klines" / symbol / "4h"
    output_dir = csv_dir
    rows = csv_to_parquet(csv_dir, output_dir, "klines")
    total_rows += rows

    # Convert 1d klines
    csv_dir = base_dir / "klines" / symbol / "1d"
    output_dir = csv_dir
    rows = csv_to_parquet(csv_dir, output_dir, "klines")
    total_rows += rows

    # Convert funding
    csv_dir = base_dir / "funding" / symbol
    output_dir = csv_dir
    rows = csv_to_parquet(csv_dir, output_dir, "funding")
    total_rows += rows

    # Convert open interest
    csv_dir = base_dir / "open_interest" / symbol / "4h"
    output_dir = csv_dir
    rows = csv_to_parquet(csv_dir, output_dir, "open_interest")
    total_rows += rows

    print()
    print("=" * 60)
    print(f"Conversion complete! Total rows: {total_rows:,}")
    print()
    print("Ready for backtesting!")
    print("Run: uv run python scripts/param_sweep.py --quick")


if __name__ == "__main__":
    main()
