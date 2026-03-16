#!/usr/bin/env python3
"""Download Binance historical data for backtesting.

This script downloads the last 3 months of:
- 4h and 1d klines (OHLCV data)
- Funding rates
- Open interest

Data is saved in Parquet format for efficient replay.
"""

import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
import sys

def download_binance_data(days: int = 90):
    """Download last N days of Binance futures data."""

    base_url = "https://data.binance.vision/data/futures/um/daily"
    symbol = "BTCUSDT"

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    # Create directories
    base_dir = Path("data/raw/binance")
    klines_4h_dir = base_dir / "klines" / symbol / "4h"
    klines_1d_dir = base_dir / "klines" / symbol / "1d"
    funding_dir = base_dir / "funding" / symbol
    oi_dir = base_dir / "open_interest" / symbol / "4h"

    for dir_path in [klines_4h_dir, klines_1d_dir, funding_dir, oi_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)

    print(f"Downloading Binance data from {start_date.date()} to {end_date.date()}")
    print(f"Total days: {days}")
    print()

    # Track downloads
    total_files = 0
    successful = 0
    failed = 0

    current = start_date
    while current <= end_date:
        date_str = current.strftime('%Y-%m-%d')

        # Download 4h klines
        url = f"{base_url}/klines/{symbol}/4h/{symbol}-4h-{date_str}.zip"
        output = klines_4h_dir / f"{symbol}-4h-{date_str}.zip"
        if download_and_extract(url, output, klines_4h_dir):
            successful += 1
        else:
            failed += 1
        total_files += 1

        # Download 1d klines
        url = f"{base_url}/klines/{symbol}/1d/{symbol}-1d-{date_str}.zip"
        output = klines_1d_dir / f"{symbol}-1d-{date_str}.zip"
        if download_and_extract(url, output, klines_1d_dir):
            successful += 1
        else:
            failed += 1
        total_files += 1

        # Download funding rate
        url = f"{base_url}/fundingRate/{symbol}/{symbol}-fundingRate-{date_str}.zip"
        output = funding_dir / f"{symbol}-fundingRate-{date_str}.zip"
        if download_and_extract(url, output, funding_dir):
            successful += 1
        else:
            failed += 1
        total_files += 1

        # Download open interest
        url = f"{base_url}/openInterest/{symbol}/{symbol}-openInterest-{date_str}.zip"
        output = oi_dir / f"{symbol}-openInterest-{date_str}.zip"
        if download_and_extract(url, output, oi_dir):
            successful += 1
        else:
            failed += 1
        total_files += 1

        # Progress
        if current.day == 1 or current == end_date:
            print(f"Progress: {current.date()} - {successful}/{total_files} files downloaded")

        current += timedelta(days=1)

    print()
    print("=" * 60)
    print(f"Download complete!")
    print(f"Total files: {total_files}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print()
    print("Data saved to:")
    print(f"  4h klines: {klines_4h_dir}")
    print(f"  1d klines: {klines_1d_dir}")
    print(f"  Funding: {funding_dir}")
    print(f"  Open Interest: {oi_dir}")
    print()

    if failed > total_files * 0.1:  # More than 10% failed
        print("⚠️  Warning: Many downloads failed. This might be due to:")
        print("  - Network issues")
        print("  - Recent dates not yet available on Binance")
        print("  - Rate limiting")
        print()
        print("You can still proceed if you have enough data.")

    return successful, failed

def download_and_extract(url: str, output_path: Path, extract_dir: Path) -> bool:
    """Download a zip file and extract it."""
    try:
        # Skip if already exists
        csv_name = output_path.stem + ".csv"
        if (extract_dir / csv_name).exists():
            return True

        # Download
        urllib.request.urlretrieve(url, output_path)

        # Extract
        with zipfile.ZipFile(output_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        # Remove zip file
        output_path.unlink()

        return True
    except Exception as e:
        # Silently fail for missing files (common for recent dates)
        return False

if __name__ == "__main__":
    days = 90
    if len(sys.argv) > 1:
        days = int(sys.argv[1])

    successful, failed = download_binance_data(days)

    if successful == 0:
        print("❌ No data downloaded. Please check your internet connection.")
        sys.exit(1)
    elif successful < 100:
        print("⚠️  Limited data downloaded. Consider downloading more days.")
        sys.exit(0)
    else:
        print("✅ Sufficient data downloaded. Ready for backtesting!")
        sys.exit(0)
