"""CLI entry point for downloading historical Binance data into Parquet."""

from __future__ import annotations

import argparse
from pathlib import Path

from zenquant.historical import BinanceHistoricalDownloader, BinanceHistoricalSyncConfig


def main() -> None:
    """Sync Binance history into the repository Parquet layout.

    Example:
        zenquant-history-sync --year 2024 --month 1 --symbol BTCUSDT
    """
    parser = argparse.ArgumentParser(description="Download Binance futures history into the ZenQuant Parquet layout.")
    parser.add_argument("--root", default="data/raw/binance", help="Output root directory.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Exchange symbol, for example BTCUSDT.")
    parser.add_argument("--year", type=int, required=True, help="Calendar year, for example 2024.")
    parser.add_argument("--month", type=int, required=True, help="Calendar month, 1-12.")
    args = parser.parse_args()

    downloader = BinanceHistoricalDownloader(BinanceHistoricalSyncConfig(root_dir=Path(args.root)))
    outputs = downloader.sync_month(symbol=args.symbol, year=args.year, month=args.month)
    for key, path in sorted(outputs.items()):
        print(f"{key}={path}")


if __name__ == "__main__":
    main()
