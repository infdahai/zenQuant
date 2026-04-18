#!/usr/bin/env python3
"""Download Binance SPOT klines for spot-perp basis analysis.

Uses monthly archives (fast) + daily for current month.

Usage:
    uv run python scripts/download_spot_data.py
    uv run python scripts/download_spot_data.py --symbol BTCUSDT --days 2000
    uv run python scripts/download_spot_data.py --symbol BTCUSDT --start-date 2016-01-01 --timeframes 1d,4h,1h
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

MONTHLY_URL = "https://data.binance.vision/data/spot/monthly/klines"
DAILY_URL = "https://data.binance.vision/data/spot/daily/klines"


def download_and_extract(url: str, output_path: Path, extract_dir: Path) -> bool:
    try:
        csv_name = output_path.stem + ".csv"
        if (extract_dir / csv_name).exists():
            return True
        urllib.request.urlretrieve(url, output_path)
        with zipfile.ZipFile(output_path, "r") as zf:
            zf.extractall(extract_dir)
        output_path.unlink()
        return True
    except Exception:
        output_path.unlink(missing_ok=True)
        return False


def download_spot_data(
    *,
    days: int = 2000,
    symbol: str = "BTCUSDT",
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    timeframes: tuple[str, ...] = ("1d", "4h", "1h"),
) -> tuple[int, int]:
    end_date = end_date or datetime.now(UTC)
    start_date = start_date or (end_date - timedelta(days=days))

    base_dir = Path("data/raw/binance_spot")

    for tf in timeframes:
        (base_dir / "klines" / symbol / tf).mkdir(parents=True, exist_ok=True)

    print(f"Downloading Binance SPOT data: {symbol}")
    print(f"Range: {start_date.date()} → {end_date.date()}")
    print("=" * 50)

    successful = 0
    failed = 0

    # Phase 1: Monthly archives
    current = start_date.replace(day=1)
    last_complete = end_date.replace(day=1) - timedelta(days=1)

    print("\n[Monthly]")
    while current <= last_complete:
        month_str = current.strftime("%Y-%m")
        for tf in timeframes:
            url = f"{MONTHLY_URL}/{symbol}/{tf}/{symbol}-{tf}-{month_str}.zip"
            out_dir = base_dir / "klines" / symbol / tf
            out = out_dir / f"{symbol}-{tf}-{month_str}.zip"
            if download_and_extract(url, out, out_dir):
                successful += 1
            else:
                failed += 1

        if current.month == 1 or current >= last_complete.replace(day=1):
            print(f"  {month_str} — {successful} ok, {failed} failed")

        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    # Phase 2: Daily (current month)
    print("\n[Daily]")
    current = end_date.replace(day=1)
    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")
        for tf in timeframes:
            url = f"{DAILY_URL}/{symbol}/{tf}/{symbol}-{tf}-{date_str}.zip"
            out_dir = base_dir / "klines" / symbol / tf
            out = out_dir / f"{symbol}-{tf}-{date_str}.zip"
            if download_and_extract(url, out, out_dir):
                successful += 1
            else:
                failed += 1
        current += timedelta(days=1)

    print(f"\nDone: {successful} ok, {failed} failed")
    return successful, failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Binance spot klines")
    parser.add_argument("--days", type=int, default=2000)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--end-date", type=str, default=None)
    parser.add_argument("--timeframes", type=str, default="1d,4h,1h")
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start_date).replace(tzinfo=UTC) if args.start_date else None
    end = datetime.fromisoformat(args.end_date).replace(tzinfo=UTC) if args.end_date else None
    timeframes = tuple(part.strip() for part in args.timeframes.split(",") if part.strip())
    s, f = download_spot_data(
        days=args.days,
        symbol=args.symbol,
        start_date=start,
        end_date=end,
        timeframes=timeframes,
    )
    sys.exit(0 if s > 0 else 1)
