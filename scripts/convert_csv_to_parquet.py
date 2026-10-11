#!/usr/bin/env python3
"""Convert Binance CSV files to Parquet format for efficient replay."""

from pathlib import Path

import pandas as pd


def _normalize_exchange_timestamp(value: int | float) -> int:
    """Normalize exchange timestamps to milliseconds before ns conversion."""
    parsed = int(value)
    if parsed >= 1_000_000_000_000_000:
        return parsed // 1000
    return parsed


def csv_to_parquet(csv_dir: Path, output_dir: Path, data_type: str):
    """Convert all CSV files in directory to Parquet."""
    csv_files = sorted(csv_dir.glob("*.csv"))

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
            if data_type == "funding":
                df = _read_funding_csv(csv_file)
            else:
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
        combined["open_time"] = combined["open_time"].map(_normalize_exchange_timestamp)
        combined["close_time"] = combined["close_time"].map(_normalize_exchange_timestamp)
        combined["open_time_ns"] = combined["open_time"] * 1_000_000
        combined["close_time_ns"] = combined["close_time"] * 1_000_000
        combined = (
            combined.sort_values("open_time_ns")
            .drop_duplicates(subset=["open_time_ns"], keep="last")
            .reset_index(drop=True)
        )
    elif data_type == "funding":
        combined["funding_time_ns"] = combined["funding_time"] * 1_000_000
        combined = (
            combined.sort_values("funding_time_ns")
            .drop_duplicates(subset=["funding_time_ns"], keep="last")
            .reset_index(drop=True)
        )
    elif data_type == "open_interest":
        combined["ts_ns"] = combined["timestamp"] * 1_000_000
        combined = combined.sort_values("ts_ns").drop_duplicates(subset=["ts_ns"], keep="last").reset_index(drop=True)

    # Save as Parquet
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "data.parquet"
    combined.to_parquet(output_file, index=False)

    print(f"  Saved {len(combined)} rows to {output_file}")
    return len(combined)


def _read_funding_csv(csv_file: Path) -> pd.DataFrame:
    """Read both legacy and current Binance funding archive schemas."""
    frame = pd.read_csv(csv_file)
    normalized = {str(column).strip().lower(): column for column in frame.columns}
    time_col = (
        normalized.get("funding_time")
        or normalized.get("fundingtime")
        or normalized.get("calc_time")
        or normalized.get("calctime")
    )
    rate_col = (
        normalized.get("funding_rate")
        or normalized.get("fundingrate")
        or normalized.get("last_funding_rate")
        or normalized.get("lastfundingrate")
    )
    if time_col is None or rate_col is None:
        # Fall back to the old 4-column archive shape:
        # symbol,funding_time,funding_rate,mark_price
        frame = pd.read_csv(
            csv_file,
            names=["symbol", "funding_time", "funding_rate", "mark_price"],
            skiprows=1,
        )
        return frame
    mark_col = normalized.get("mark_price") or normalized.get("markprice")
    symbol_col = normalized.get("symbol")
    return pd.DataFrame(
        {
            "symbol": frame[symbol_col] if symbol_col is not None else csv_file.parent.name,
            "funding_time": frame[time_col],
            "funding_rate": frame[rate_col],
            "mark_price": frame[mark_col] if mark_col is not None else 0.0,
        }
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Convert Binance CSV to Parquet")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--source", default="binance", choices=["binance", "binance_spot"])
    args = parser.parse_args()

    base_dir = Path(f"data/raw/{args.source}")
    symbol = args.symbol
    total_rows = 0

    # Convert all available timeframes
    for tf in ["5m", "1h", "4h", "1d"]:
        csv_dir = base_dir / "klines" / symbol / tf
        if csv_dir.exists() and list(csv_dir.glob("*.csv")):
            rows = csv_to_parquet(csv_dir, csv_dir, "klines")
            total_rows += rows

    # Convert funding (futures only)
    if args.source == "binance":
        csv_dir = base_dir / "funding" / symbol
        if csv_dir.exists() and list(csv_dir.glob("*.csv")):
            rows = csv_to_parquet(csv_dir, csv_dir, "funding")
            total_rows += rows

        # Convert open interest
        csv_dir = base_dir / "open_interest" / symbol / "4h"
        if csv_dir.exists() and list(csv_dir.glob("*.csv")):
            rows = csv_to_parquet(csv_dir, csv_dir, "open_interest")
            total_rows += rows

    print()
    print("=" * 60)
    print(f"Conversion complete! Total rows: {total_rows:,}")
    print(f"Source: {args.source}, Symbol: {symbol}")


if __name__ == "__main__":
    main()
