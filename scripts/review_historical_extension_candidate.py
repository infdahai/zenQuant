#!/usr/bin/env python3
"""Write the early-history replay-base candidate contract artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.historical_extension import (
    build_historical_extension_candidate_contract,
    write_historical_extension_candidate_contract,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review early-history replay-base candidate contract")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--spot-root", type=Path, default=Path("data/raw/binance_spot"))
    parser.add_argument("--futures-root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/data_source_review"))
    args = parser.parse_args()

    contract = build_historical_extension_candidate_contract(
        symbol=args.symbol,
        spot_root=args.spot_root,
        futures_root=args.futures_root,
    )
    payload = write_historical_extension_candidate_contract(contract, output_dir=args.output_dir)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
