#!/usr/bin/env python3
"""Fetch or normalize a small Bybit BTCUSDT L2 research sample.

The output contract is intentionally narrow and matches the
``research_microstructure.py`` input expectation:

- snapshots.parquet
- trades.parquet
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _get_json(base_url: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{base_url.rstrip('/')}{path}?{query}"
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_snapshot_row(payload: dict[str, Any], *, symbol: str, depth: int) -> dict[str, Any]:
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    bids = list(result.get("b", []) or [])[:depth]
    asks = list(result.get("a", []) or [])[:depth]
    ts_ms = int(result.get("ts") or payload.get("time") or 0)
    sequence = int(result.get("u") or payload.get("time") or ts_ms)
    return {
        "ts_ns": ts_ms * 1_000_000,
        "venue": "bybit",
        "symbol": symbol,
        "sequence": sequence,
        "bid_prices": json.dumps([float(level[0]) for level in bids]),
        "bid_sizes": json.dumps([float(level[1]) for level in bids]),
        "ask_prices": json.dumps([float(level[0]) for level in asks]),
        "ask_sizes": json.dumps([float(level[1]) for level in asks]),
    }


def _trade_exec_id(row: dict[str, Any]) -> str:
    exec_id = row.get("execId") or row.get("i")
    if exec_id:
        return str(exec_id)
    return "|".join(
        [
            str(row.get("time") or row.get("T") or ""),
            str(row.get("price") or row.get("p") or ""),
            str(row.get("size") or row.get("v") or ""),
            str(row.get("side") or row.get("S") or ""),
        ]
    )


def _normalize_trade_rows(payload: dict[str, Any], *, symbol: str) -> list[dict[str, Any]]:
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    rows = result.get("list", []) or []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        ts_ms = int(row.get("time") or row.get("T") or 0)
        normalized.append(
            {
                "ts_ns": ts_ms * 1_000_000,
                "venue": "bybit",
                "symbol": symbol,
                "price": float(row.get("price") or row.get("p") or 0.0),
                "size": float(row.get("size") or row.get("v") or 0.0),
                "side": str(row.get("side") or row.get("S") or "").lower(),
                "exec_id": _trade_exec_id(row),
            }
        )
    return normalized


def _fetch_live_sample(
    *,
    base_url: str,
    symbol: str,
    category: str,
    depth: int,
    samples: int,
    poll_interval_ms: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snapshots: list[dict[str, Any]] = []
    trades_by_id: dict[str, dict[str, Any]] = {}

    for idx in range(samples):
        snapshot_payload = _get_json(
            base_url,
            "/v5/market/orderbook",
            {"category": category, "symbol": symbol, "limit": depth},
        )
        trade_payload = _get_json(
            base_url,
            "/v5/market/recent-trade",
            {"category": category, "symbol": symbol, "limit": min(1000, depth * 25)},
        )
        snapshots.append(_normalize_snapshot_row(snapshot_payload, symbol=symbol, depth=depth))
        for trade in _normalize_trade_rows(trade_payload, symbol=symbol):
            trades_by_id[trade["exec_id"]] = trade
        if idx < samples - 1:
            time.sleep(max(0.0, poll_interval_ms / 1000.0))

    trades = sorted(trades_by_id.values(), key=lambda row: (row["ts_ns"], row["exec_id"]))
    for trade in trades:
        trade.pop("exec_id", None)
    return snapshots, trades


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a normalized Bybit microstructure sample.")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--poll-interval-ms", type=int, default=250)
    parser.add_argument("--base-url", default="https://api.bybit.com")
    args = parser.parse_args()

    snapshots, trades = _fetch_live_sample(
        base_url=args.base_url,
        symbol=args.symbol,
        category=args.category,
        depth=args.depth,
        samples=args.samples,
        poll_interval_ms=args.poll_interval_ms,
    )

    args.output_root.mkdir(parents=True, exist_ok=True)
    snapshots_path = args.output_root / "snapshots.parquet"
    trades_path = args.output_root / "trades.parquet"
    metadata_path = args.output_root / "metadata.json"

    pd.DataFrame(snapshots).sort_values(["ts_ns", "sequence"], kind="stable").to_parquet(snapshots_path, index=False)
    pd.DataFrame(trades).sort_values(["ts_ns", "price"], kind="stable").to_parquet(trades_path, index=False)

    metadata = {
        "generated_at": _ts(),
        "source_type": "bybit_public_rest_microstructure_sample",
        "venue": "bybit",
        "symbol": args.symbol,
        "category": args.category,
        "depth": args.depth,
        "snapshot_rows": len(snapshots),
        "trade_rows": len(trades),
        "poll_interval_ms": args.poll_interval_ms,
        "sample_count": args.samples,
        "normalization_contract": {
            "snapshots_file": "snapshots.parquet",
            "trades_file": "trades.parquet",
            "ts_field": "ts_ns",
            "symbol_field": "symbol",
            "venue_field": "venue",
        },
    }
    _write_json(metadata_path, metadata)


if __name__ == "__main__":
    main()
