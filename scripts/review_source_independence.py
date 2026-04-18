#!/usr/bin/env python3
"""Write source-level independence audits for research-only feature candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.source_independence import (
    build_deribit_block_flow_independence_audit,
    build_mvrv_independence_audit,
    write_source_independence_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review source-level signal independence")
    parser.add_argument("--source", choices=["mvrv", "deribit_block_flow"], required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--macro-root", type=Path, default=Path("data/raw/macro"))
    parser.add_argument("--data-root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--deribit-block-flow-root", type=Path, default=Path("data/raw/deribit_block_flow"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/signal_combination"))
    args = parser.parse_args()

    if args.source == "mvrv":
        audit = build_mvrv_independence_audit(
            symbol=args.symbol,
            macro_root=args.macro_root,
            data_root=args.data_root,
        )
    else:
        audit = build_deribit_block_flow_independence_audit(
            symbol=args.symbol,
            deribit_block_flow_root=args.deribit_block_flow_root,
            macro_root=args.macro_root,
            data_root=args.data_root,
        )
    payload = write_source_independence_audit(audit, args.output_dir)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
