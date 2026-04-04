#!/usr/bin/env python3
"""Download Binance historical data for backtesting.

Uses MONTHLY archives for bulk history (30x faster than daily),
then DAILY archives for the current incomplete month.

Usage:
    uv run python scripts/download_binance_data.py --days 2500
    uv run python scripts/download_binance_data.py --symbol ETHUSDT --days 2000
    uv run python scripts/download_binance_data.py --days 90  # Quick update
"""

import argparse
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

MONTHLY_URL = "https://data.binance.vision/data/futures/um/monthly"
DAILY_URL = "https://data.binance.vision/data/futures/um/daily"


def download_and_extract(url: str, output_path: Path, extract_dir: Path) -> bool:
    """Download a zip file and extract it."""
    try:
        csv_name = output_path.stem + ".csv"
        if (extract_dir / csv_name).exists():
            return True
        urllib.request.urlretrieve(url, output_path)
        with zipfile.ZipFile(output_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)
        output_path.unlink()
        return True
    except Exception:
        output_path.unlink(missing_ok=True)
        return False


def download_binance_data(days: int = 2500, symbol: str = "BTCUSDT") -> tuple[int, int]:
    """Download Binance futures data using monthly + daily archives."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    base_dir = Path("data/raw/binance")
    timeframes = ["1h", "4h", "1d"]
    data_types = {
        "klines": timeframes,
        "fundingRate": [None],
        "openInterest": ["4h"],
    }

    # Create directories
    for tf in timeframes:
        (base_dir / "klines" / symbol / tf).mkdir(parents=True, exist_ok=True)
    (base_dir / "funding" / symbol).mkdir(parents=True, exist_ok=True)
    (base_dir / "open_interest" / symbol / "4h").mkdir(parents=True, exist_ok=True)

    print(f"Downloading Binance data: {symbol}")
    print(f"Range: {start_date.date()} → {end_date.date()} ({days} days)")
    print("Strategy: monthly archives (bulk) + daily (recent)")
    print("=" * 60)

    successful = 0
    failed = 0

    # Phase 1: Monthly archives (fast bulk download)
    current = start_date.replace(day=1)
    last_complete_month = end_date.replace(day=1) - timedelta(days=1)

    print("\n[Phase 1] Monthly archives...")
    while current <= last_complete_month:
        month_str = current.strftime("%Y-%m")

        for tf in timeframes:
            url = f"{MONTHLY_URL}/klines/{symbol}/{tf}/{symbol}-{tf}-{month_str}.zip"
            out_dir = base_dir / "klines" / symbol / tf
            out = out_dir / f"{symbol}-{tf}-{month_str}.zip"
            if download_and_extract(url, out, out_dir):
                successful += 1
            else:
                failed += 1

        # Funding
        url = f"{MONTHLY_URL}/fundingRate/{symbol}/{symbol}-fundingRate-{month_str}.zip"
        out_dir = base_dir / "funding" / symbol
        out = out_dir / f"{symbol}-fundingRate-{month_str}.zip"
        if download_and_extract(url, out, out_dir):
            successful += 1
        else:
            failed += 1

        if current.month == 1 or current >= last_complete_month.replace(day=1):
            print(f"  {month_str} — {successful} ok, {failed} failed")

        # Next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    # Phase 2: Daily archives (current incomplete month)
    print("\n[Phase 2] Daily archives (current month)...")
    current = end_date.replace(day=1)
    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")

        for tf in timeframes:
            url = f"{DAILY_URL}/klines/{symbol}/{tf}/{symbol}-{tf}-{date_str}.zip"
            out_dir = base_dir / "klines" / symbol / tf
            out = out_dir / f"{symbol}-{tf}-{date_str}.zip"
            if download_and_extract(url, out, out_dir):
                successful += 1
            else:
                failed += 1

        url = f"{DAILY_URL}/fundingRate/{symbol}/{symbol}-fundingRate-{date_str}.zip"
        out_dir = base_dir / "funding" / symbol
        out = out_dir / f"{symbol}-fundingRate-{date_str}.zip"
        if download_and_extract(url, out, out_dir):
            successful += 1
        else:
            failed += 1

        url = f"{DAILY_URL}/openInterest/{symbol}/{symbol}-openInterest-{date_str}.zip"
        out_dir = base_dir / "open_interest" / symbol / "4h"
        out = out_dir / f"{symbol}-openInterest-{date_str}.zip"
        if download_and_extract(url, out, out_dir):
            successful += 1
        else:
            failed += 1

        current += timedelta(days=1)

    print(f"\nDone: {successful} ok, {failed} failed")
    return successful, failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Binance futures historical data")
    parser.add_argument("--days", type=int, default=2500)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()

    s, f = download_binance_data(days=args.days, symbol=args.symbol)
    sys.exit(0 if s > 0 else 1)
