#!/usr/bin/env python3
"""Write a mechanical audit of local data coverage and training integration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.data_coverage_audit import build_data_coverage_audit, write_data_coverage_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit local parquet/csv coverage and training integration status")
    parser.add_argument("--data-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", type=Path, default=Path("results/data_source_review"))
    args = parser.parse_args()

    payload = build_data_coverage_audit(data_root=args.data_root, symbol=args.symbol)
    result = write_data_coverage_audit(payload, output_dir=args.output_dir)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
