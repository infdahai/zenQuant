#!/usr/bin/env python3
"""Download Binance aggTrades (futures) and aggregate to 4H CVD buckets.

aggTrades contain the `is_buyer_maker` flag — the ground truth for order flow
direction. This replaces the volume proxy (open>close→buy) currently used for CVD.

Downloads daily archives from Binance Vision (free, no API key).
Automatically aggregates raw tick data into 4H buckets.

Usage:
    # Download and aggregate (default: 2020-01-01 to now)
    uv run python scripts/download_aggtrades.py

    # Specific date range
    uv run python scripts/download_aggtrades.py --start 2024-01-01 --days 365

    # Skip download, just re-aggregate
    uv run python scripts/download_aggtrades.py --aggregate-only

Data flow:
    Binance Vision → daily CSV zips → raw parquet → 4H aggregated parquet
    data/raw/binance/aggtrades/{symbol}/raw/{date}.parquet
    data/raw/binance/aggtrades/{symbol}/4h/data.parquet
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

VISION_BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
ONE_HOUR_NS = 3600 * 1_000_000_000
FOUR_HOURS_NS = 4 * ONE_HOUR_NS

TIMEFRAME_NS = {
    "1h": ONE_HOUR_NS,
    "4h": FOUR_HOURS_NS,
}


def download_daily(symbol: str, date: datetime, raw_dir: Path) -> bool:
    """Download one day of aggTrades from Binance Vision."""
    date_str = date.strftime("%Y-%m-%d")
    parquet_path = raw_dir / f"{date_str}.parquet"

    # Skip if already converted
    if parquet_path.exists():
        return True

    csv_name = f"{symbol}-aggTrades-{date_str}.csv"
    csv_path = raw_dir / csv_name
    zip_path = raw_dir / f"{csv_name}.zip"

    # Skip if CSV already extracted
    if not csv_path.exists():
        url = f"{VISION_BASE}/{symbol}/{symbol}-aggTrades-{date_str}.zip"
        try:
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(raw_dir)
            zip_path.unlink(missing_ok=True)
        except Exception:
            zip_path.unlink(missing_ok=True)
            return False

    # Convert CSV to parquet
    if csv_path.exists():
        try:
            _csv_to_parquet(csv_path, parquet_path)
            csv_path.unlink()  # Remove CSV to save disk
            return True
        except Exception as e:
            print(f"  Convert error {date_str}: {e}")
            return False

    return False


def _csv_to_parquet(csv_path: Path, parquet_path: Path) -> None:
    """Convert Binance aggTrades CSV to parquet with proper schema."""
    import csv

    rows: list[dict] = []
    with csv_path.open() as f:
        reader = csv.reader(f)
        for line in reader:
            if len(line) < 7:
                continue
            # Columns: agg_trade_id, price, quantity, first_trade_id, last_trade_id, transact_time, is_buyer_maker
            try:
                transact_time_ms = int(line[5])
                rows.append(
                    {
                        "ts_ns": transact_time_ms * 1_000_000,
                        "price": float(line[1]),
                        "qty": float(line[2]),
                        "is_buyer_maker": line[6].strip().lower() == "true",
                    }
                )
            except (ValueError, IndexError):
                continue

    if rows:
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, parquet_path, compression="zstd")


def aggregate_to_4h(symbol: str, base_dir: Path) -> int:
    """Aggregate raw aggTrades to 4H buckets (backward compatible)."""
    return aggregate_to_timeframe(symbol, base_dir, "4h")


def aggregate_to_timeframe(symbol: str, base_dir: Path, timeframe: str = "4h") -> int:
    """Aggregate raw aggTrades parquet files into CVD buckets at given timeframe."""
    bucket_ns = TIMEFRAME_NS.get(timeframe, FOUR_HOURS_NS)
    raw_dir = base_dir / "raw"
    out_dir = base_dir / timeframe
    out_dir.mkdir(parents=True, exist_ok=True)

    parquet_files = sorted(raw_dir.glob("*.parquet"))
    if not parquet_files:
        print("No raw parquet files to aggregate")
        return 0

    print(f"Aggregating {len(parquet_files)} days of aggTrades into {timeframe} buckets...")

    buckets: dict[int, dict] = {}  # bucket_start_ns → aggregated data

    for i, path in enumerate(parquet_files):
        if (i + 1) % 100 == 0:
            print(f"  Processing {i + 1}/{len(parquet_files)}...")

        try:
            table = pq.read_table(path, columns=["ts_ns", "price", "qty", "is_buyer_maker"])
            ts_col = table.column("ts_ns").to_pylist()
            price_col = table.column("price").to_pylist()
            qty_col = table.column("qty").to_pylist()
            buyer_col = table.column("is_buyer_maker").to_pylist()

            for j in range(len(ts_col)):
                ts = ts_col[j]
                price = price_col[j]
                qty = qty_col[j]
                is_buyer_maker = buyer_col[j]

                # Compute bucket start based on timeframe
                bucket_start = (ts // bucket_ns) * bucket_ns

                if bucket_start not in buckets:
                    buckets[bucket_start] = {
                        "ts_ns": bucket_start,
                        "buy_volume": 0.0,
                        "sell_volume": 0.0,
                        "buy_usd": 0.0,
                        "sell_usd": 0.0,
                        "trade_count": 0,
                        "large_trade_count": 0,
                    }

                b = buckets[bucket_start]
                usd_value = price * qty
                b["trade_count"] += 1

                if usd_value > 100_000:
                    b["large_trade_count"] += 1

                if is_buyer_maker:
                    # Maker is buyer → taker is seller (sell aggressor)
                    b["sell_volume"] += qty
                    b["sell_usd"] += usd_value
                else:
                    # Maker is seller → taker is buyer (buy aggressor)
                    b["buy_volume"] += qty
                    b["buy_usd"] += usd_value

        except Exception as e:
            print(f"  Error reading {path.name}: {e}")
            continue

    if not buckets:
        return 0

    # Compute derived fields and write
    records = []
    for ts in sorted(buckets.keys()):
        b = buckets[ts]
        buy_vol = b["buy_volume"]
        sell_vol = b["sell_volume"]
        total_vol = buy_vol + sell_vol
        cvd_delta = buy_vol - sell_vol
        vpin = abs(cvd_delta) / total_vol if total_vol > 0 else 0.0

        records.append(
            {
                "ts_ns": ts,
                "buy_volume": round(buy_vol, 6),
                "sell_volume": round(sell_vol, 6),
                "cvd_delta": round(cvd_delta, 6),
                "buy_usd": round(b["buy_usd"], 2),
                "sell_usd": round(b["sell_usd"], 2),
                "trade_count": b["trade_count"],
                "large_trade_count": b["large_trade_count"],
                "vpin": round(vpin, 6),
            }
        )

    out_path = out_dir / "data.parquet"
    table = pa.Table.from_pylist(records)
    pq.write_table(table, out_path, compression="zstd")

    print(f"  Written {len(records)} {timeframe} buckets to {out_path}")
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Binance aggTrades and aggregate to 4H CVD")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", default="2020-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=None, help="Days to download (default: until today)")
    parser.add_argument("--aggregate-only", action="store_true", help="Skip download, just aggregate")
    parser.add_argument("--timeframe", default="4h", choices=["1h", "4h"], help="Aggregation timeframe")
    args = parser.parse_args()

    base_dir = Path("data/raw/binance/aggtrades") / args.symbol
    raw_dir = base_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_date = datetime.now(UTC) - timedelta(days=1)  # Yesterday (today not yet available)
    if args.days:
        end_date = start_date + timedelta(days=args.days)

    total_days = (end_date - start_date).days

    if not args.aggregate_only:
        print(f"Binance aggTrades Download — {args.symbol}")
        print(f"Range: {start_date.date()} → {end_date.date()} ({total_days} days)")
        print(f"Output: {raw_dir}")
        print("=" * 60)

        success = 0
        fail = 0
        current = start_date

        while current <= end_date:
            ok = download_daily(args.symbol, current, raw_dir)
            if ok:
                success += 1
            else:
                fail += 1

            if current.day == 1 or current.date() == end_date.date():
                print(f"  {current.date()} — {success} ok, {fail} failed")

            current += timedelta(days=1)

        print(f"\nDownload complete: {success} ok, {fail} failed")
        print()

    # Aggregate
    n_buckets = aggregate_to_timeframe(args.symbol, base_dir, args.timeframe)
    if n_buckets > 0:
        print(f"\nDone. {n_buckets} {args.timeframe} buckets ready for backtest integration.")
    else:
        print("\nNo data to aggregate.")
        sys.exit(1)


if __name__ == "__main__":
    main()
