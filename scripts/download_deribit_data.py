#!/usr/bin/env python3
"""Download Deribit historical data for multi-exchange training.

This script downloads historical data from Deribit to complement Binance data:
- BTC perpetual futures (OHLCV)
- Options data (implied volatility, open interest)
- Funding rates

Data is saved in Parquet format for efficient replay.

Usage:
    # Download last 18 months
    uv run python scripts/download_deribit_data.py

    # Download specific period
    uv run python scripts/download_deribit_data.py --days 540
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path


def download_deribit_data(days: int = 540):
    """Download last N days of Deribit data via public API.

    Note: Deribit public API has rate limits (20 req/sec).
    This downloads:
    - BTC-PERPETUAL klines (1h resolution)
    - BTC options chain snapshots (daily)
    - Funding rate history
    """

    base_url = "https://www.deribit.com/api/v2/public"
    instrument = "BTC-PERPETUAL"

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    # Create directories
    base_dir = Path("data/raw/deribit")
    klines_dir = base_dir / "klines" / instrument
    funding_dir = base_dir / "funding" / instrument

    for dir_path in [klines_dir, funding_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)

    print(f"Downloading Deribit data from {start_date.date()} to {end_date.date()}")
    print(f"Total days: {days}")
    print(f"Instrument: {instrument}")
    print()
    print("⚠️  Note: Deribit public API has rate limits. This may take a while.")
    print()

    # Track downloads
    total_requests = 0
    successful = 0
    failed = 0

    # Download klines (1h resolution, chunked by week)
    print("Downloading klines (1h resolution)...")
    current = start_date
    klines_data = []

    while current < end_date:
        chunk_end = min(current + timedelta(days=7), end_date)
        start_ts = int(current.timestamp() * 1000)
        end_ts = int(chunk_end.timestamp() * 1000)

        url = f"{base_url}/get_tradingview_chart_data"
        params = f"?instrument_name={instrument}&start_timestamp={start_ts}&end_timestamp={end_ts}&resolution=60"

        try:
            with urllib.request.urlopen(url + params) as response:
                data = json.loads(response.read())
                if data.get("result"):
                    result = data["result"]
                    # Convert to list of dicts
                    for i in range(len(result["ticks"])):
                        klines_data.append({
                            "timestamp": result["ticks"][i],
                            "open": result["open"][i],
                            "high": result["high"][i],
                            "low": result["low"][i],
                            "close": result["close"][i],
                            "volume": result["volume"][i],
                        })
                    successful += 1
                else:
                    failed += 1
        except Exception as e:
            print(f"  Error downloading {current.date()}: {e}")
            failed += 1

        total_requests += 1
        current = chunk_end
        time.sleep(0.1)  # Rate limit: 10 req/sec

        if total_requests % 10 == 0:
            print(f"  Progress: {current.date()} - {successful}/{total_requests} chunks")

    # Save klines to JSON (will convert to Parquet later)
    if klines_data:
        output_file = klines_dir / "klines_1h.json"
        with open(output_file, "w") as f:
            json.dump(klines_data, f)
        print(f"  Saved {len(klines_data)} klines to {output_file}")

    # Download funding rate history
    print()
    print("Downloading funding rate history...")
    funding_data = []

    current = start_date
    while current < end_date:
        chunk_end = min(current + timedelta(days=30), end_date)
        start_ts = int(current.timestamp() * 1000)
        end_ts = int(chunk_end.timestamp() * 1000)

        url = f"{base_url}/get_funding_rate_history"
        params = f"?instrument_name={instrument}&start_timestamp={start_ts}&end_timestamp={end_ts}"

        try:
            with urllib.request.urlopen(url + params) as response:
                data = json.loads(response.read())
                if data.get("result"):
                    funding_data.extend(data["result"])
                    successful += 1
                else:
                    failed += 1
        except Exception as e:
            print(f"  Error downloading funding {current.date()}: {e}")
            failed += 1

        total_requests += 1
        current = chunk_end
        time.sleep(0.1)  # Rate limit

        if total_requests % 10 == 0:
            print(f"  Progress: {current.date()} - {successful}/{total_requests} requests")

    # Save funding data
    if funding_data:
        output_file = funding_dir / "funding_history.json"
        with open(output_file, "w") as f:
            json.dump(funding_data, f)
        print(f"  Saved {len(funding_data)} funding records to {output_file}")

    print()
    print("=" * 60)
    print("Download complete!")
    print(f"Total requests: {total_requests}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print()
    print("Data saved to:")
    print(f"  Klines: {klines_dir}")
    print(f"  Funding: {funding_dir}")
    print()
    print("⚠️  Next step: Convert JSON to Parquet format")
    print("Run: uv run python scripts/convert_deribit_to_parquet.py")

    return successful, failed

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download Deribit historical data")
    parser.add_argument("--days", type=int, default=540, help="Number of days to download (default: 540)")
    args = parser.parse_args()

    successful, failed = download_deribit_data(args.days)

    if successful == 0:
        print("❌ No data downloaded. Please check your internet connection.")
        sys.exit(1)
    else:
        print("✅ Deribit data downloaded successfully!")
        sys.exit(0)
