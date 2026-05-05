#!/usr/bin/env python3
"""Generate one authoritative data-readiness report for research delivery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from zenquant.data.readiness import (
    build_readiness_markdown as render_readiness_markdown,
)
from zenquant.data.readiness import (
    build_readiness_payload as render_readiness_payload,
)


def build_readiness_payload(
    *,
    binance_root: Path,
    symbol: str,
    deribit_root: Path,
    deribit_block_flow_root: Path,
    hyperliquid_root: Path,
    merged_root: Path,
) -> dict[str, Any]:
    return render_readiness_payload(
        binance_root=binance_root,
        symbol=symbol,
        deribit_root=deribit_root,
        deribit_block_flow_root=deribit_block_flow_root,
        hyperliquid_root=hyperliquid_root,
        merged_root=merged_root,
    )


def build_markdown(payload: dict[str, Any]) -> str:
    return render_readiness_markdown(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize current research data readiness")
    parser.add_argument("--binance-root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--deribit-root", type=Path, default=Path("data/raw/deribit_options"))
    parser.add_argument("--deribit-block-flow-root", type=Path, default=Path("data/raw/deribit_block_flow"))
    parser.add_argument("--hyperliquid-root", type=Path, default=Path("data/raw/hyperliquid/liquidations"))
    parser.add_argument("--merged-root", type=Path, default=Path("data/raw/merged_liquidations"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/data_readiness"))
    args = parser.parse_args()

    payload = build_readiness_payload(
        binance_root=args.binance_root,
        symbol=args.symbol,
        deribit_root=args.deribit_root,
        deribit_block_flow_root=args.deribit_block_flow_root,
        hyperliquid_root=args.hyperliquid_root,
        merged_root=args.merged_root,
    )
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "data_readiness.json"
    md_path = output_dir / "data_readiness.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(build_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {"json_path": str(json_path), "md_path": str(md_path), "overall_blockers": payload["overall_blockers"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
