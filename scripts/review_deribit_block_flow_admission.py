#!/usr/bin/env python3
"""Write the Deribit block-flow data source admission review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.data_source_review import write_deribit_block_flow_admission_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Review Deribit block-flow data source admission")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--deribit-block-flow-root", type=Path, default=Path("data/raw/deribit_block_flow"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/data_source_review"))
    args = parser.parse_args()

    payload = write_deribit_block_flow_admission_review(
        symbol=args.symbol,
        deribit_block_flow_root=args.deribit_block_flow_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
