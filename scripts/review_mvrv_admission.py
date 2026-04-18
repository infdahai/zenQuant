#!/usr/bin/env python3
"""Write the BTC MVRV data source admission review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.data_source_review import write_mvrv_admission_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Review BTC MVRV data source admission")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--macro-root", type=Path, default=Path("data/raw/macro"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/data_source_review"))
    args = parser.parse_args()

    payload = write_mvrv_admission_review(
        symbol=args.symbol,
        macro_root=args.macro_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
