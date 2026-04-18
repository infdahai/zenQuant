#!/usr/bin/env python3
"""Prepare a canonical local BTC MVRV CSV and optionally import it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.ingestor.macro import fetch_btc_mvrv
from zenquant.ingestor.mvrv_prepare import prepare_canonical_mvrv_csv
from zenquant.research.data_source_review import write_mvrv_admission_review
from zenquant.research.source_independence import (
    build_mvrv_independence_audit,
    write_source_independence_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare canonical local BTC MVRV CSV")
    parser.add_argument("--raw-csv", type=Path, default=None, help="Local raw CSV to normalize into date,mvrv")
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/raw/macro/btc_mvrv/source/btc_mvrv_local.csv"),
    )
    parser.add_argument(
        "--spot-1d-path",
        type=Path,
        default=Path("data/raw/binance_spot/klines/BTCUSDT/1d/data.parquet"),
    )
    parser.add_argument(
        "--realized-price-json-url",
        default="https://charts.bgeometrics.com/files/realized_price.json",
        help="Public realized price JSON used only when --raw-csv is not provided.",
    )
    parser.add_argument("--macro-root", type=Path, default=Path("data/raw/macro"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--skip-reviews", action="store_true")
    args = parser.parse_args()

    output_csv = prepare_canonical_mvrv_csv(
        output_csv=args.output_csv,
        raw_csv_path=args.raw_csv,
        spot_1d_path=args.spot_1d_path,
        realized_price_json_url=args.realized_price_json_url,
    )

    payload: dict[str, object] = {
        "canonical_csv": str(output_csv),
    }

    if not args.skip_import:
        payload["imported_rows"] = fetch_btc_mvrv(args.macro_root, csv_path=output_csv)

    if not args.skip_reviews:
        payload["admission_review"] = write_mvrv_admission_review(symbol=args.symbol, macro_root=args.macro_root)
        audit = build_mvrv_independence_audit(symbol=args.symbol, macro_root=args.macro_root)
        payload["independence_review"] = write_source_independence_audit(
            audit,
            Path("results/signal_combination"),
        )

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
