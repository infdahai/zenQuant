#!/usr/bin/env python3
"""Audit large Deribit option-flow factors against train-only BTC outcomes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.deribit_large_option_factor_audit import (
    build_deribit_large_option_flow_audit,
    build_deribit_large_option_flow_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit large Deribit option-flow factors")
    parser.add_argument("--block-flow-root", type=Path, default=Path("data/raw/deribit_block_flow"))
    parser.add_argument("--btc-4h-dir", type=Path, default=Path("data/raw/binance/klines/BTCUSDT/4h"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/research_swarm/deribit_large_option_flow_factor_audit"),
    )
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--train-start", default="2020-01-01T00:00:00+00:00")
    parser.add_argument("--train-end", default="2023-12-31T23:59:59+00:00")
    parser.add_argument("--min-amount", type=float, default=25.0)
    parser.add_argument("--min-underlying-notional-usd", type=float)
    parser.add_argument("--require-block-trade", action="store_true")
    parser.add_argument("--rolling-window", type=int, default=120)
    parser.add_argument("--corr-threshold", type=float, default=0.75)
    parser.add_argument("--min-train-observations", type=int, default=500)
    parser.add_argument("--min-abs-rank-ic", type=float, default=0.01)
    parser.add_argument("--min-abs-ablation-delta", type=float, default=0.005)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    feature_path = args.output_dir / "derived_large_option_flow_factors.parquet"
    payload = build_deribit_large_option_flow_audit(
        block_flow_root=args.block_flow_root,
        btc_4h_dir=args.btc_4h_dir,
        currency=args.currency,
        train_start=args.train_start,
        train_end=args.train_end,
        min_amount=args.min_amount,
        min_underlying_notional_usd=args.min_underlying_notional_usd,
        require_block_trade=args.require_block_trade,
        rolling_window=args.rolling_window,
        corr_threshold=args.corr_threshold,
        min_train_observations=args.min_train_observations,
        min_abs_rank_ic=args.min_abs_rank_ic,
        min_abs_ablation_delta=args.min_abs_ablation_delta,
        feature_output_path=feature_path,
    )
    json_path = args.output_dir / "deribit_large_option_flow_factor_audit.json"
    md_path = args.output_dir / "deribit_large_option_flow_factor_audit.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(build_deribit_large_option_flow_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "json_path": str(json_path),
                "md_path": str(md_path),
                "features_path": payload["features_path"],
                "summary": payload["summary"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
