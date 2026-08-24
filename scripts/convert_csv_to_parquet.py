#!/usr/bin/env python3
"""Convert Binance CSV files to Parquet format for efficient replay."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from zenquant.data.spot_daily import DAY_NS, normalize_spot_daily_csvs


def _normalize_exchange_timestamp(value: int | float) -> int:
    """Normalize exchange timestamps to milliseconds before ns conversion."""
    parsed = int(value)
    if parsed >= 1_000_000_000_000_000:
        return parsed // 1000
    return parsed


def csv_to_parquet(
    csv_dir: Path,
    output_dir: Path,
    data_type: str,
    *,
    source: str | None = None,
    timeframe: str | None = None,
    start_open_ns: int | None = None,
    end_open_ns: int | None = None,
):
    """Convert all CSV files in directory to Parquet."""
    has_bounds = start_open_ns is not None or end_open_ns is not None
    is_spot_daily = data_type == "klines" and source == "binance_spot" and timeframe == "1d"
    if has_bounds and not is_spot_daily:
        raise ValueError("explicit open bounds are only valid for binance_spot 1d klines")
    if is_spot_daily and (start_open_ns is None) != (end_open_ns is None):
        raise ValueError("start_open_ns and end_open_ns must both be provided")
    if start_open_ns is not None and end_open_ns is not None:
        if start_open_ns % DAY_NS or end_open_ns % DAY_NS:
            raise ValueError("explicit open bounds must be UTC daily timestamps")
        if start_open_ns >= end_open_ns:
            raise ValueError("explicit open bounds must form a non-empty half-open interval")
    csv_files = sorted(csv_dir.glob("*.csv"))

    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        return 0

    if data_type == "klines" and source == "binance_spot" and timeframe == "1d":
        result = normalize_spot_daily_csvs(
            csv_files,
            output_dir / "data.parquet",
            start_open_ns=start_open_ns,
            end_open_ns=end_open_ns,
        )
        print(f"  Saved {result.row_count} rows to {result.output_path}")
        return result.row_count

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


def parse_utc_open_date(value: str) -> int:
    """Parse one exact YYYY-MM-DD value as a UTC daily open in nanoseconds."""

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("open date must use exact YYYY-MM-DD format")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("open date must use exact YYYY-MM-DD format") from exc
    return int(parsed.timestamp() * 1_000_000_000)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Convert Binance CSV to Parquet")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--source", default="binance", choices=["binance", "binance_spot"])
    parser.add_argument("--start-open-date", default=None)
    parser.add_argument("--end-open-date-exclusive", default=None)
    args = parser.parse_args()

    if (args.start_open_date is None) != (args.end_open_date_exclusive is None):
        parser.error("--start-open-date and --end-open-date-exclusive must both be provided")
    if args.source != "binance_spot" and args.start_open_date is not None:
        parser.error("explicit open-date bounds are only valid for --source binance_spot")
    try:
        start_open_ns = parse_utc_open_date(args.start_open_date) if args.start_open_date is not None else None
        end_open_ns = (
            parse_utc_open_date(args.end_open_date_exclusive) if args.end_open_date_exclusive is not None else None
        )
    except ValueError as exc:
        parser.error(str(exc))
    if start_open_ns is not None and end_open_ns is not None and start_open_ns >= end_open_ns:
        parser.error("explicit open bounds must form a non-empty half-open interval")

    base_dir = Path(f"data/raw/{args.source}")
    symbol = args.symbol
    total_rows = 0

    # Convert all available timeframes
    for tf in ["5m", "1h", "4h", "1d"]:
        csv_dir = base_dir / "klines" / symbol / tf
        if csv_dir.exists() and list(csv_dir.glob("*.csv")):
            rows = csv_to_parquet(
                csv_dir,
                csv_dir,
                "klines",
                source=args.source,
                timeframe=tf,
                start_open_ns=start_open_ns if tf == "1d" else None,
                end_open_ns=end_open_ns if tf == "1d" else None,
            )
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
