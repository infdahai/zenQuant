#!/usr/bin/env python3
"""Write the Hyperliquid data source admission review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.data_source_review import write_hyperliquid_admission_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Review Hyperliquid data source admission")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--binance-root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--merged-root", type=Path, default=Path("data/raw/merged_liquidations"))
    parser.add_argument("--hyperliquid-root", type=Path, default=Path("data/raw/hyperliquid/liquidations"))
    parser.add_argument("--deribit-root", type=Path, default=Path("data/raw/deribit_options"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/data_source_review"))
    args = parser.parse_args()

    payload = write_hyperliquid_admission_review(
        symbol=args.symbol,
        binance_root=args.binance_root,
        merged_root=args.merged_root,
        hyperliquid_root=args.hyperliquid_root,
        deribit_root=args.deribit_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
