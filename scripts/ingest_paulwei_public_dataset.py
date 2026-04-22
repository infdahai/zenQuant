#!/usr/bin/env python3
"""Ingest the public PaulWei / BitMEX archive into a local research sidecar."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.paulwei_public import (
    ingest_paulwei_public_dataset,
    write_paulwei_dataset_contract,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest PaulWei public archive into local sidecar storage")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("data/raw/paulwei_public"))
    parser.add_argument("--results-dir", type=Path, default=Path("results/paulwei_research"))
    args = parser.parse_args()

    ingest_meta = ingest_paulwei_public_dataset(source_root=args.source_root, output_root=args.output_root)
    contract = write_paulwei_dataset_contract(data_root=args.output_root, output_dir=args.results_dir)
    print(json.dumps({"ingest": ingest_meta, "contract": contract}, indent=2))


if __name__ == "__main__":
    main()
