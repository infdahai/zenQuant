#!/usr/bin/env python3
"""Download macro indicator data via yfinance.

Each indicator is stored independently for cross-sectional analysis.
Separating VIX, DXY, 10Y yield, Nasdaq, Gold allows treating each
as an independent factor rather than a composite signal.

Usage:
    uv run python scripts/download_macro_data.py
    uv run python scripts/download_macro_data.py --years 7
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# yfinance tickers for each macro series
SERIES = {
    "vix": "^VIX",           # CBOE Volatility Index
    "dxy": "DX-Y.NYB",       # US Dollar Index
    "treasury_10y": "^TNX",  # 10-Year Treasury Yield
    "tlt": "TLT",            # 20+ Year Treasury Bond ETF (liquidity proxy)
    "qqq": "QQQ",            # Nasdaq 100 ETF
    "iwm": "IWM",            # Russell 2000 ETF
    "ndx": "^NDX",           # Nasdaq 100 Index (direct)
    "gold": "GLD",           # Gold ETF
}


def download_yfinance(output_dir: Path, years: int = 7) -> None:
    """Download all yfinance-based macro series."""
    try:
        import yfinance as yf
    except ImportError:
        print("ERROR: yfinance required. Install with: uv add yfinance")
        sys.exit(1)

    end = datetime.now(UTC)
    start = end - timedelta(days=years * 365)

    for name, ticker in SERIES.items():
        print(f"  Downloading {name} ({ticker})...")
        try:
            data = yf.download(ticker, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), progress=False)
            if data.empty:
                print(f"    No data for {ticker}")
                continue

            # Flatten MultiIndex columns if present
            if hasattr(data.columns, "levels"):
                data.columns = [col[0] if isinstance(col, tuple) else col for col in data.columns]

            records: list[dict] = []
            for idx, row in data.iterrows():
                ts = idx
                if hasattr(ts, "timestamp"):
                    ts_ns = int(ts.timestamp() * 1_000_000_000)
                else:
                    continue

                close = row.get("Close", row.get("Adj Close"))
                if close is None or (hasattr(close, "__iter__") and not isinstance(close, str)):
                    # Handle Series/array case
                    close = float(next(iter(close))) if hasattr(close, "__iter__") else float(close)
                close = float(close)
                if close <= 0:
                    continue

                records.append({"ts_ns": ts_ns, "value": round(close, 6)})

            if records:
                out_path = output_dir / name / "data.parquet"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                table = pa.Table.from_pylist(records)
                pq.write_table(table, out_path)
                print(f"    {len(records)} records → {out_path}")
            else:
                print(f"    No valid records for {ticker}")

        except Exception as e:
            print(f"    Error: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download macro indicator data")
    parser.add_argument("--output", type=Path, default=Path("data/raw/macro"))
    parser.add_argument("--years", type=int, default=7)
    args = parser.parse_args()

    print("Macro Data Download")
    print(f"Output: {args.output}")
    print(f"Years: {args.years}")
    print("=" * 50)

    download_yfinance(args.output, years=args.years)

    print("\nDone.")


if __name__ == "__main__":
    main()
