#!/usr/bin/env python3
"""Download a Deribit BTC options-chain snapshot for research.

This script captures the active BTC option surface from Deribit public APIs and
stores one normalized snapshot row per option instrument. It is intentionally
observation-first: the output is meant for IV/skew/OI research, not direct
execution.
"""

from __future__ import annotations

from pathlib import Path

from zenquant.data.deribit_options_chain import fetch_option_chain, save_option_chain


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Download one Deribit BTC options-chain snapshot.")
    parser.add_argument("--currency", default="BTC", help="Underlying currency, default BTC.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/raw/deribit_options"),
        help="Snapshot output root.",
    )
    args = parser.parse_args()

    rows = fetch_option_chain(currency=args.currency)
    json_path, parquet_path = save_option_chain(rows, args.output_root)

    print("Deribit options chain snapshot complete")
    print(f"rows={len(rows)}")
    print(f"json={json_path}")
    print(f"parquet={parquet_path}")


if __name__ == "__main__":
    main()
