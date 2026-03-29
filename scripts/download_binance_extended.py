#!/usr/bin/env python3
"""Download extended Binance Futures market data via REST API.

Fetches data not available in the bulk CSV archive:
  - Global long/short account ratio
  - Taker buy/sell volume ratio
  - Liquidation (force order) summaries

Data is stored as Parquet for use by the backtest and signal plugins.

Usage:
    uv run python scripts/download_binance_extended.py
    uv run python scripts/download_binance_extended.py --symbol BTCUSDT --days 90
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

BASE_URL = "https://fapi.binance.com"
REQUEST_DELAY = 0.25  # seconds between requests (rate limit: 2400 weight/min)
MAX_PAGES = 5000  # dead loop protection: max pagination iterations


def _get(
    path: str,
    params: dict[str, Any],
    retries: int = 3,
) -> list[dict[str, Any]]:
    """GET from Binance Futures API with retry and rate limit handling."""
    url = BASE_URL + path
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 429:
                print("  Rate limited, sleeping 60s...")
                time.sleep(60)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == retries - 1:
                print(f"  Failed {url}: {e}")
                return []
            time.sleep(2**attempt)
    return []


def _save_parquet(records: list[dict], output_path: Path) -> int:
    """Append or create a Parquet file."""
    if not records:
        return 0
    df_new = pd.DataFrame(records)
    if output_path.exists():
        df_existing = pd.read_parquet(output_path)
        df = pd.concat([df_existing, df_new], ignore_index=True)
        # Deduplicate by timestamp
        ts_col = next((c for c in df.columns if "time" in c.lower()), None)
        if ts_col:
            df = df.drop_duplicates(subset=[ts_col]).sort_values(ts_col)
    else:
        df = df_new
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    return len(records)


def download_long_short_ratio(
    symbol: str,
    output_dir: Path,
    days: int = 90,
    period: str = "4h",
) -> int:
    """Download global long/short account ratio (sentiment indicator).

    Returns count of records saved.
    """
    print(f"Downloading long/short ratio ({symbol}, {period}, {days} days)...")
    output_path = output_dir / "long_short_ratio" / symbol / "data.parquet"

    limit = 500
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)

    all_records: list[dict] = []
    cursor = start_ms
    pages = 0

    while cursor < end_ms and pages < MAX_PAGES:
        pages += 1
        data = _get(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": limit, "startTime": cursor},
        )
        if not data:
            break
        for row in data:
            all_records.append(
                {
                    "symbol": symbol,
                    "ts_ns": int(row["timestamp"]) * 1_000_000,
                    "long_account_ratio": float(row["longAccount"]),
                    "short_account_ratio": float(row["shortAccount"]),
                    "long_short_ratio": float(row["longShortRatio"]),
                }
            )
        last_ts = int(data[-1]["timestamp"])
        cursor = last_ts + 1
        if len(data) < limit:
            break
        time.sleep(REQUEST_DELAY)

    saved = _save_parquet(all_records, output_path)
    print(f"  Saved {saved} records to {output_path}")
    return saved


def download_taker_ratio(
    symbol: str,
    output_dir: Path,
    days: int = 90,
    period: str = "4h",
) -> int:
    """Download taker buy/sell volume ratio (order flow indicator)."""
    print(f"Downloading taker buy/sell ratio ({symbol}, {period}, {days} days)...")
    output_path = output_dir / "taker_ratio" / symbol / "data.parquet"

    limit = 500
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)

    all_records: list[dict] = []
    cursor = start_ms
    pages = 0

    while cursor < end_ms and pages < MAX_PAGES:
        pages += 1
        data = _get(
            "/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": period, "limit": limit, "startTime": cursor},
        )
        if not data:
            break
        for row in data:
            all_records.append(
                {
                    "symbol": symbol,
                    "ts_ns": int(row["timestamp"]) * 1_000_000,
                    "buy_sell_ratio": float(row["buySellRatio"]),
                    "buy_vol": float(row["buyVol"]),
                    "sell_vol": float(row["sellVol"]),
                }
            )
        last_ts = int(data[-1]["timestamp"])
        cursor = last_ts + 1
        if len(data) < limit:
            break
        time.sleep(REQUEST_DELAY)

    saved = _save_parquet(all_records, output_path)
    print(f"  Saved {saved} records to {output_path}")
    return saved


def download_liquidations(
    symbol: str,
    output_dir: Path,
    days: int = 90,
) -> int:
    """Download force order (liquidation) data.

    Note: Binance only provides last 7 days of liquidation data via API.
    For longer history, use allForceOrders which gives up to 1000 recent events.
    """
    print(f"Downloading liquidations ({symbol}, up to 7 days)...")
    output_path = output_dir / "liquidations" / symbol / "data.parquet"

    # Binance liquidation endpoint: max 1000 records, last 7 days
    data = _get(
        "/fapi/v1/allForceOrders",
        {"symbol": symbol, "limit": 1000},
    )

    all_records: list[dict] = []
    for row in data:
        all_records.append(
            {
                "symbol": row.get("symbol", symbol),
                "ts_ns": int(row["time"]) * 1_000_000,
                "side": row["side"],  # BUY=short liq, SELL=long liq
                "price": float(row["price"]),
                "qty": float(row["origQty"]),
                "notional": float(row["price"]) * float(row["origQty"]),
            }
        )

    saved = _save_parquet(all_records, output_path)
    print(f"  Saved {saved} liquidation records to {output_path}")
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Download extended Binance Futures data")
    parser.add_argument("--symbol", default="BTCUSDT", help="Futures symbol")
    parser.add_argument("--days", type=int, default=90, help="Days of history")
    parser.add_argument("--output", type=Path, default=Path("data/raw/binance"))
    args = parser.parse_args()

    print(f"Downloading extended data for {args.symbol} ({args.days} days)")
    print("=" * 60)

    total = 0
    total += download_long_short_ratio(args.symbol, args.output, days=args.days)
    total += download_taker_ratio(args.symbol, args.output, days=args.days)
    total += download_liquidations(args.symbol, args.output, days=args.days)

    print()
    print(f"Done. Total records: {total:,}")


if __name__ == "__main__":
    main()
