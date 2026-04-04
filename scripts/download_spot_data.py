#!/usr/bin/env python3
"""Download Binance SPOT klines for spot-perp basis analysis.

Whales influence BTC price through spot market. The spot-perpetual basis
(premium/discount) reveals institutional flow direction.

Usage:
    uv run python scripts/download_spot_data.py
    uv run python scripts/download_spot_data.py --symbol BTCUSDT --days 2000
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path


def download_spot_data(days: int = 2000, symbol: str = "BTCUSDT") -> tuple[int, int]:
    """Download last N days of Binance spot klines."""
    base_url = "https://data.binance.vision/data/spot/daily/klines"

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    base_dir = Path("data/raw/binance_spot")
    klines_4h_dir = base_dir / "klines" / symbol / "4h"
    klines_1h_dir = base_dir / "klines" / symbol / "1h"

    for d in [klines_4h_dir, klines_1h_dir]:
        d.mkdir(parents=True, exist_ok=True)

    print(f"Downloading Binance SPOT data: {symbol}")
    print(f"Range: {start_date.date()} → {end_date.date()} ({days} days)")
    print("=" * 50)

    successful = 0
    failed = 0
    current = start_date

    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")

        for tf, out_dir in [("4h", klines_4h_dir), ("1h", klines_1h_dir)]:
            url = f"{base_url}/{symbol}/{tf}/{symbol}-{tf}-{date_str}.zip"
            zip_path = out_dir / f"{symbol}-{tf}-{date_str}.zip"
            csv_name = f"{symbol}-{tf}-{date_str}.csv"

            if (out_dir / csv_name).exists():
                successful += 1
                continue

            try:
                urllib.request.urlretrieve(url, zip_path)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(out_dir)
                zip_path.unlink()
                successful += 1
            except Exception:
                zip_path.unlink(missing_ok=True)
                failed += 1

        if current.day == 1 or current.date() == end_date.date():
            print(f"  {current.date()} — {successful} ok, {failed} failed")

        current += timedelta(days=1)

    print(f"\nDone: {successful} ok, {failed} failed")
    print(f"Spot 4H: {klines_4h_dir}")
    print(f"Spot 1H: {klines_1h_dir}")
    return successful, failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Binance spot klines")
    parser.add_argument("--days", type=int, default=2000)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()

    s, f = download_spot_data(days=args.days, symbol=args.symbol)
    sys.exit(0 if s > 0 else 1)
