#!/usr/bin/env python3
"""Run the research-only 5m-to-4h TSFM-readiness probe."""

from __future__ import annotations

import argparse
from pathlib import Path

from zenquant.research.bar_tsfm_probe import BarTSFMProbeConfig, write_bar_tsfm_probe_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 5m-to-4h bar representation research artifacts.")
    parser.add_argument("--kline-path", type=Path, default=Path("data/raw/binance/klines/BTCUSDT/5m/data.parquet"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/bar_tsfm_probe"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--pca-components", type=int, default=12)
    parser.add_argument("--train-start", default="2020-01-01")
    parser.add_argument("--train-end", default="2023-12-31")
    parser.add_argument("--dev-start", default="2024-01-01")
    parser.add_argument("--dev-end", default="2025-12-31")
    parser.add_argument("--include-chronos2", action="store_true")
    parser.add_argument("--chronos2-model-id", default="amazon/chronos-2")
    parser.add_argument("--chronos2-device-map", default="cpu")
    parser.add_argument("--chronos2-batch-size", type=int, default=256)
    parser.add_argument("--chronos2-embedding-components", type=int, default=12)
    args = parser.parse_args()

    config = BarTSFMProbeConfig(
        kline_path=args.kline_path,
        output_dir=args.output_dir,
        symbol=args.symbol,
        pca_components=args.pca_components,
        train_start=args.train_start,
        train_end=args.train_end,
        dev_start=args.dev_start,
        dev_end=args.dev_end,
        include_chronos2=args.include_chronos2,
        chronos2_model_id=args.chronos2_model_id,
        chronos2_device_map=args.chronos2_device_map,
        chronos2_batch_size=args.chronos2_batch_size,
        chronos2_embedding_components=args.chronos2_embedding_components,
    )
    paths = write_bar_tsfm_probe_report(config)
    print(paths["json_path"])
    print(paths["md_path"])


if __name__ == "__main__":
    main()
