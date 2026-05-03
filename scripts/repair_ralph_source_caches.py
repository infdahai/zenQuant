#!/usr/bin/env python3
"""Repair free-source caches used by the Ralph research swarm.

This script writes research-only local artifacts. It never changes strategy,
risk, execution, or promotion truth.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from zenquant.data.hyperliquid_public import FOUR_HOUR_MS, build_hyperliquid_proxy_history, write_hyperliquid_raw
from zenquant.data.liquidation_pipeline import write_aggregated_liquidations
from zenquant.ingestor.binance_ws import _fetch_recent_open_interest
from zenquant.research.transfer_entropy import rolling_transfer_entropy


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair Ralph free-source research caches")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--coin", default="BTC")
    parser.add_argument("--hyperliquid-days", type=int, default=None)
    parser.add_argument("--hyperliquid-start", default="2023-01-01")
    parser.add_argument("--hyperliquid-end", default=None)
    parser.add_argument("--coinmetrics-start", default="2020-01-01")
    parser.add_argument("--coinmetrics-end", default=None)
    parser.add_argument("--allow-recent-open-interest", action="store_true")
    parser.add_argument("--binance-root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--hyperliquid-root", type=Path, default=Path("data/raw/hyperliquid/liquidations"))
    parser.add_argument("--coinmetrics-root", type=Path, default=Path("data/raw/coinmetrics/community/BTC"))
    parser.add_argument(
        "--transfer-entropy-output", type=Path, default=Path("data/research/transfer_entropy/eth_to_btc.json")
    )
    parser.add_argument(
        "--cross-asset-output",
        type=Path,
        default=Path("data/research/cross_asset/sol_bnb_btc_context.parquet"),
    )
    args = parser.parse_args()

    results = {
        "schema_version": "ralph_source_cache_repair.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "repairs": {},
    }
    results["repairs"]["binance_open_interest"] = _repair_binance_open_interest(
        symbol=args.symbol,
        binance_root=args.binance_root,
        allow_recent=args.allow_recent_open_interest,
    )
    results["repairs"]["hyperliquid_public_proxy"] = _repair_hyperliquid(
        coin=args.coin,
        output_root=args.hyperliquid_root,
        days=args.hyperliquid_days,
        start=args.hyperliquid_start,
        end=args.hyperliquid_end,
    )
    results["repairs"]["coinmetrics_community_onchain"] = _repair_coinmetrics(
        output_root=args.coinmetrics_root,
        start=args.coinmetrics_start,
        end=args.coinmetrics_end,
    )
    results["repairs"]["eth_btc_transfer_entropy"] = _repair_transfer_entropy(
        binance_root=args.binance_root,
        output_path=args.transfer_entropy_output,
    )
    results["repairs"]["sol_bnb_btc_context"] = _repair_cross_asset_context(
        binance_root=args.binance_root,
        output_path=args.cross_asset_output,
    )

    print(json.dumps(results, indent=2, sort_keys=True))


def _repair_binance_open_interest(*, symbol: str, binance_root: Path, allow_recent: bool) -> dict[str, Any]:
    output_path = binance_root / "open_interest" / symbol / "4h" / "data.parquet"
    if not allow_recent:
        existing = _read_binance_open_interest_cache(output_path.parent)
        coverage = _ns_coverage(existing, "ts_ns") if not existing.empty else None
        return {
            "status": "completed" if _has_train_dev_coverage(coverage) else "blocked",
            "blocked_by": None
            if _has_train_dev_coverage(coverage)
            else "binance_open_interest_train_dev_history_missing",
            "path": str(output_path),
            "rows": len(existing),
            "coverage": coverage,
            "recent_fetch_allowed": False,
        }

    rows = _fetch_recent_open_interest(symbol, timeframe="4h", limit=500, timeout_s=20.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["symbol"] = symbol
        frame["timeframe"] = "4h"
        frame = frame[["symbol", "timeframe", "ts_ns", "open_interest"]]
        frame.to_parquet(output_path, index=False)
    return {
        "status": "completed" if rows else "blocked",
        "blocked_by": None if rows else "binance_open_interest_unavailable",
        "path": str(output_path),
        "rows": len(rows),
        "coverage": _ns_coverage(frame, "ts_ns") if not frame.empty else None,
        "recent_fetch_allowed": True,
    }


def _read_binance_open_interest_cache(root: Path) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in sorted(root.glob("*.parquet"))]
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    if "ts_ns" not in frame.columns:
        return pd.DataFrame()
    return frame.drop_duplicates(subset=["ts_ns"]).sort_values("ts_ns")


def _repair_hyperliquid(
    *,
    coin: str,
    output_root: Path,
    days: int | None,
    start: str,
    end: str | None,
) -> dict[str, Any]:
    end_dt = _parse_utc_date(end) if end else datetime.now(UTC)
    start_dt = end_dt - timedelta(days=days) if days is not None else _parse_utc_date(start)
    candles, funding, buckets, status = build_hyperliquid_proxy_history(
        coin=coin,
        start_ms=int(start_dt.timestamp() * 1000),
        end_ms=int(end_dt.timestamp() * 1000),
    )
    raw_paths = write_hyperliquid_raw(output_root, candles=candles, funding=funding)
    aggregate_paths = write_aggregated_liquidations(output_root, buckets)
    status_path = output_root / "_backfill_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(status)
    payload["last_success_ts"] = datetime.now(UTC).isoformat()
    payload["promotion_ready"] = False
    payload["research_only_non_promotable"] = True
    payload["candle_rows"] = len(candles)
    payload["funding_rows"] = len(funding)
    payload["bucket_rows"] = len(buckets)
    coverage = _ms_coverage([candle.start_ms for candle in candles])
    coverage_quality = _time_series_coverage_quality(
        values=[candle.start_ms for candle in candles],
        expected_start_ms=int(start_dt.timestamp() * 1000),
        expected_end_ms=int(end_dt.timestamp() * 1000),
        step_ms=FOUR_HOUR_MS,
    )
    payload["coverage"] = coverage
    payload["coverage_quality"] = coverage_quality
    train_dev_ready = bool(
        coverage
        and coverage.get("start", "") < "2024-01-01"
        and coverage_quality["coverage_ratio_estimate"] >= 0.8
        and coverage_quality["max_gap_hours"] <= 48.0
    )
    payload["train_dev_history_ready"] = train_dev_ready
    if coverage and coverage.get("start", "") >= "2024-01-01":
        payload["blocked_by"] = payload.get("blocked_by") or "source_cache_outside_train_dev"
    elif coverage_quality["max_gap_hours"] > 48.0 or coverage_quality["coverage_ratio_estimate"] < 0.8:
        payload["blocked_by"] = payload.get("blocked_by") or "hyperliquid_history_gap"
    elif not train_dev_ready:
        payload["blocked_by"] = payload.get("blocked_by") or "hyperliquid_train_history_missing"
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "status": "completed",
        "blocked_by": payload.get("blocked_by"),
        "status_path": str(status_path),
        "raw_paths": [str(path) for path in raw_paths],
        "aggregate_paths": [str(path) for path in aggregate_paths],
        "candle_rows": len(candles),
        "funding_rows": len(funding),
        "bucket_rows": len(buckets),
        "coverage": coverage,
        "coverage_quality": coverage_quality,
        "train_dev_history_ready": train_dev_ready,
    }


def _repair_coinmetrics(*, output_root: Path, start: str, end: str | None) -> dict[str, Any]:
    end = end or datetime.now(UTC).date().isoformat()
    metrics = ("AdrActCnt", "TxCnt", "CapRealUSD", "TxTfrValAdjUSD", "SplyCur")
    rows, metric_status = _fetch_coinmetrics_rows(metrics=metrics, start=start, end=end)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "data.parquet"
    status_path = output_root / "_collector_status.json"
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["ts_ns"] = pd.to_datetime(frame["time"], utc=True).astype("int64")
        for metric in metrics:
            frame[metric] = pd.to_numeric(frame.get(metric), errors="coerce")
        frame.to_parquet(output_path, index=False)
    status = {
        "enabled": True,
        "last_success_ts": datetime.now(UTC).isoformat(),
        "research_ready": not frame.empty,
        "observation_ready": not frame.empty,
        "blocked_by": None if not frame.empty else "coinmetrics_community_empty",
        "rows": len(frame),
        "metrics": list(metrics),
        "metric_status": metric_status,
        "research_only_non_promotable": True,
        "coverage": _ns_coverage(frame, "ts_ns") if not frame.empty else None,
    }
    status_path.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    return {"status": "completed" if not frame.empty else "blocked", "path": str(output_path), **status}


def _repair_transfer_entropy(*, binance_root: Path, output_path: Path) -> dict[str, Any]:
    btc = _daily_returns(binance_root / "klines" / "BTCUSDT" / "1d" / "data.parquet")
    eth = _daily_returns(binance_root / "klines" / "ETHUSDT" / "1d" / "data.parquet")
    merged = btc.merge(eth, on="date", suffixes=("_btc", "_eth")).dropna()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(merged) < 130:
        payload = {
            "schema_version": "eth_btc_transfer_entropy.v1",
            "research_only_non_promotable": True,
            "blocked_by": "insufficient_cross_asset_history",
            "observations": len(merged),
        }
    else:
        te_values = rolling_transfer_entropy(
            merged["return_eth"].tolist(),
            merged["return_btc"].tolist(),
            window=120,
            lag=1,
        )
        payload = {
            "schema_version": "eth_btc_transfer_entropy.v1",
            "research_only_non_promotable": True,
            "blocked_by": None,
            "observations": len(merged),
            "latest_transfer_entropy": round(float(te_values[-1]), 8),
            "mean_transfer_entropy": round(float(sum(te_values) / len(te_values)), 8),
            "coverage": {
                "start": str(merged["date"].min().date()),
                "end": str(merged["date"].max().date()),
            },
        }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {"status": "completed" if payload["blocked_by"] is None else "blocked", "path": str(output_path), **payload}


def _repair_cross_asset_context(*, binance_root: Path, output_path: Path) -> dict[str, Any]:
    frames = []
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
        frame = _daily_returns(binance_root / "klines" / symbol / "1d" / "data.parquet")
        if frame.empty:
            continue
        frames.append(frame.rename(columns={"return": f"return_{symbol.removesuffix('USDT').lower()}"}))
    if not frames:
        return {"status": "blocked", "blocked_by": "cross_asset_history_missing", "path": str(output_path)}
    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="date", how="inner")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(output_path, index=False)
    blocked_by = None if not merged.empty else "cross_asset_history_overlap_empty"
    return {
        "status": "completed" if blocked_by is None else "blocked",
        "blocked_by": blocked_by,
        "path": str(output_path),
        "rows": len(merged),
        "columns": list(merged.columns),
        "coverage": {
            "start": str(merged["date"].min().date()) if not merged.empty else None,
            "end": str(merged["date"].max().date()) if not merged.empty else None,
        },
    }


def _daily_returns(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "return"])
    frame = pd.read_parquet(path)
    ts_column = "close_time_ns" if "close_time_ns" in frame.columns else "ts_ns"
    close_column = "close" if "close" in frame.columns else "value"
    daily = frame[[ts_column, close_column]].copy()
    daily["date"] = pd.to_datetime(daily[ts_column], unit="ns", utc=True).dt.floor("D")
    daily = daily.groupby("date", as_index=False)[close_column].last().rename(columns={close_column: "close"})
    daily["return"] = pd.to_numeric(daily["close"], errors="coerce").pct_change()
    return daily[["date", "return"]].dropna()


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "zenquant/1.0"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_coinmetrics_rows(
    *, metrics: tuple[str, ...], start: str, end: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    status: dict[str, str] = {}
    merged: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        query = urlencode(
            {
                "assets": "btc",
                "metrics": metric,
                "frequency": "1d",
                "start_time": start,
                "end_time": end,
                "page_size": "10000",
            }
        )
        url = f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?{query}"
        try:
            payload = _fetch_json(url)
        except Exception as exc:
            status[metric] = f"blocked:{type(exc).__name__}"
            continue
        rows = payload.get("data") or []
        status[metric] = "ok" if rows else "empty"
        for row in rows:
            key = str(row.get("time"))
            if not key:
                continue
            merged.setdefault(key, {"asset": row.get("asset", "btc"), "time": key})
            merged[key][metric] = row.get(metric)
    return [merged[key] for key in sorted(merged)], status


def _parse_utc_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _has_train_dev_coverage(coverage: dict[str, Any] | None) -> bool:
    if not coverage:
        return False
    start = str(coverage.get("start") or "")
    end = str(coverage.get("end") or "")
    return bool(start < "2024-01-01" and end >= "2025-12-31")


def _ns_coverage(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    return {
        "start": datetime.fromtimestamp(int(frame[column].min()) / 1_000_000_000, tz=UTC).isoformat(),
        "end": datetime.fromtimestamp(int(frame[column].max()) / 1_000_000_000, tz=UTC).isoformat(),
    }


def _ms_coverage(values: list[int]) -> dict[str, Any] | None:
    if not values:
        return None
    return {
        "start": datetime.fromtimestamp(min(values) / 1000, tz=UTC).isoformat(),
        "end": datetime.fromtimestamp(max(values) / 1000, tz=UTC).isoformat(),
    }


def _time_series_coverage_quality(
    *,
    values: list[int],
    expected_start_ms: int,
    expected_end_ms: int,
    step_ms: int,
) -> dict[str, Any]:
    unique_values = sorted(set(values))
    if not unique_values:
        return {
            "row_count": 0,
            "expected_row_count": 0,
            "coverage_ratio_estimate": 0.0,
            "max_gap_hours": None,
            "large_gap_count": 0,
        }
    expected_count = max(1, int((expected_end_ms - expected_start_ms) / step_ms))
    gaps = [
        round((current - previous) / 3_600_000, 2)
        for previous, current in pairwise(unique_values)
        if current > previous
    ]
    max_gap_hours = max(gaps) if gaps else 0.0
    return {
        "row_count": len(unique_values),
        "expected_row_count": expected_count,
        "coverage_ratio_estimate": round(min(1.0, len(unique_values) / expected_count), 4),
        "max_gap_hours": max_gap_hours,
        "large_gap_count": sum(1 for gap in gaps if gap > 48.0),
    }


if __name__ == "__main__":
    main()
