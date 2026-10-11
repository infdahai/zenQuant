#!/usr/bin/env python3
"""Write the BitMEX 2017-2020 replay-base candidate contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.bitmex_replay_candidate import (
    build_bitmex_replay_candidate_contract,
    write_bitmex_replay_candidate_contract,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review BitMEX XBTUSD as a 2017-2020 replay-base candidate")
    parser.add_argument("--data-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--symbol", default="XBTUSD")
    parser.add_argument("--normalized-symbol", default="BTCUSD")
    parser.add_argument("--output-dir", type=Path, default=Path("results/data_source_review"))
    args = parser.parse_args()

    contract = build_bitmex_replay_candidate_contract(
        data_root=args.data_root,
        symbol=args.symbol,
        normalized_symbol=args.normalized_symbol,
    )
    result = write_bitmex_replay_candidate_contract(contract, output_dir=args.output_dir)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
