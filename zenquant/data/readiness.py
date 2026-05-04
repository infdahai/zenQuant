"""Authoritative data-readiness helpers for forward collection and research."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.data.deribit_block_flow import collect_deribit_block_flow_status
from zenquant.data.deribit_status import collect_deribit_status
from zenquant.data.liquidation_pipeline import liquidation_readiness
from zenquant.historical import read_parquet_rows


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def aggregated_span_stats(root: Path, symbol: str) -> dict[str, Any]:
    rows = read_parquet_rows(str(root / "aggregated" / symbol / "*" / "*.parquet"), order_by="bucket_start_ns ASC")
    if not rows:
        return {
            "days_covered": 0.0,
            "first_bucket_start": None,
            "last_bucket_end": None,
            "coverage_ratio_estimate": 0.0,
            "extreme_bars_estimate": 0,
        }
    first_start = int(rows[0]["bucket_start_ns"])
    last_end = int(rows[-1]["bucket_end_ns"])
    span_bars = max(1, int((last_end - first_start) // (4 * 3600 * 1_000_000_000)))
    max_total = max(float(row.get("total_liq_usd", 0.0) or 0.0) for row in rows)
    extreme_threshold = max(250_000.0, max_total * 0.5) if max_total > 0 else 250_000.0
    return {
        "days_covered": round((last_end - first_start) / 1_000_000_000 / 86400.0, 2),
        "first_bucket_start": datetime.fromtimestamp(first_start / 1_000_000_000, tz=UTC).isoformat(),
        "last_bucket_end": datetime.fromtimestamp(last_end / 1_000_000_000, tz=UTC).isoformat(),
        "coverage_ratio_estimate": round(len(rows) / span_bars, 4),
        "extreme_bars_estimate": sum(1 for row in rows if float(row.get("total_liq_usd", 0.0) or 0.0) >= extreme_threshold),
    }


def build_binance_liquidation_status(binance_root: Path, symbol: str) -> dict[str, Any]:
    readiness = liquidation_readiness(binance_root, symbol)
    return {
        "data_ready": readiness.data_ready,
        "promotion_ready": readiness.promotion_ready,
        "blocked_by": readiness.blocked_by,
        "detail": readiness.detail,
        "status": "completed" if readiness.promotion_ready else "blocked" if not readiness.data_ready else "partial",
        "next_action": "ingest_binance_liquidation_history" if not readiness.data_ready else "none",
    }


def build_hyperliquid_status(hyperliquid_root: Path, symbol: str) -> dict[str, Any]:
    collector = load_json(hyperliquid_root / "_collector_status.json")
    backfill = load_json(hyperliquid_root / "_backfill_status.json")
    agg_stats = aggregated_span_stats(hyperliquid_root, symbol)

    recent_trades = int(collector.get("recent_trades", 0) or 0)
    collector_ready = bool(collector) and collector.get("blocked_by") in (None, "", "null")
    proxy_buckets = int(backfill.get("proxy_buckets", 0) or collector.get("proxy_buckets", 0) or 0)
    blocked_by = backfill.get("blocked_by") or collector.get("blocked_by")
    backfill_ready_min = proxy_buckets > 0
    promotion_ready = bool(backfill.get("promotion_ready", False))
    detail = backfill.get("detail") or collector.get("detail") or "hyperliquid public proxy status unavailable"
    status = "completed" if promotion_ready else "partial" if (collector_ready or backfill_ready_min) else "blocked"
    next_action = (
        "continue_hyperliquid_backfill"
        if backfill_ready_min and not promotion_ready
        else "keep_recent_collector_running"
        if collector_ready
        else "repair_hyperliquid_collection"
    )
    return {
        "collector_ready": collector_ready,
        "backfill_ready_min": backfill_ready_min,
        "promotion_ready": promotion_ready,
        "recent_trades": recent_trades,
        "proxy_buckets": proxy_buckets,
        "blocked_by": blocked_by,
        "detail": detail,
        "status": status,
        "proxy_only": True,
        "next_action": next_action,
        **agg_stats,
    }


def build_merged_liquidation_status(binance_root: Path, merged_root: Path, symbol: str) -> dict[str, Any]:
    readiness = liquidation_readiness(binance_root, symbol, liquidation_dir=merged_root / "aggregated")
    span_stats = aggregated_span_stats(merged_root, symbol)
    return {
        **asdict(readiness),
        "research_ready_min": readiness.data_ready,
        "status": "completed" if readiness.promotion_ready else "partial" if readiness.data_ready else "blocked",
        "coverage_ratio": round((readiness.nonzero_bars / readiness.total_bars), 4) if readiness.total_bars else 0.0,
        "days_covered": span_stats["days_covered"],
        "next_action": "continue_liquidation_backfill_and_merge" if not readiness.promotion_ready else "none",
        **span_stats,
    }


def build_readiness_payload(
    *,
    binance_root: Path,
    symbol: str,
    deribit_root: Path,
    deribit_block_flow_root: Path,
    hyperliquid_root: Path,
    merged_root: Path,
) -> dict[str, Any]:
    deribit = asdict(collect_deribit_status(deribit_root, "BTC"))
    deribit["status"] = (
        "completed" if deribit["research_ready"] else "partial" if deribit["collector_running"] else "blocked"
    )
    deribit_block_flow = asdict(collect_deribit_block_flow_status(deribit_block_flow_root, "BTC"))
    deribit_block_flow["status"] = (
        "completed"
        if deribit_block_flow["observation_ready"]
        else "partial"
        if deribit_block_flow["collector_running"]
        else "blocked"
    )

    binance_liq = build_binance_liquidation_status(binance_root, symbol)
    hyperliquid = build_hyperliquid_status(hyperliquid_root, symbol)
    merged_liq = build_merged_liquidation_status(binance_root, merged_root, symbol)

    blockers: list[str] = []
    if deribit["blocked_by"]:
        blockers.append(str(deribit["blocked_by"]))
    if deribit_block_flow["blocked_by"]:
        blockers.append(str(deribit_block_flow["blocked_by"]))
    if binance_liq["blocked_by"]:
        blockers.append(str(binance_liq["blocked_by"]))
    if hyperliquid["blocked_by"]:
        blockers.append(str(hyperliquid["blocked_by"]))
    if merged_liq["blocked_by"]:
        blockers.append(str(merged_liq["blocked_by"]))
    if not merged_liq["promotion_ready"] and merged_liq["data_ready"]:
        blockers.append("merged_liquidation_not_promotion_ready")

    return {
        "deribit_status": deribit,
        "deribit_block_flow_status": deribit_block_flow,
        "binance_liquidation_status": binance_liq,
        "hyperliquid_status": hyperliquid,
        "merged_liquidation_status": merged_liq,
        "overall_blockers": sorted(dict.fromkeys(blockers)),
    }


def build_readiness_markdown(payload: dict[str, Any]) -> str:
    deribit = payload["deribit_status"]
    deribit_block_flow = payload["deribit_block_flow_status"]
    binance_liq = payload["binance_liquidation_status"]
    hyperliquid = payload["hyperliquid_status"]
    merged = payload["merged_liquidation_status"]
    blockers = payload["overall_blockers"]
    lines = [
        "# Data Readiness",
        "",
        "## Deribit",
        "",
        f"- status: `{deribit['status']}`",
        f"- collector_running: `{deribit['collector_running']}`",
        f"- observation_ready: `{deribit['observation_ready']}`",
        f"- research_ready: `{deribit['research_ready']}`",
        f"- blocked_by: `{deribit['blocked_by']}`",
        f"- next_action: `{deribit['next_action']}`",
        "",
        "## Deribit Block Flow",
        "",
        f"- status: `{deribit_block_flow['status']}`",
        f"- collector_running: `{deribit_block_flow['collector_running']}`",
        f"- observation_ready: `{deribit_block_flow['observation_ready']}`",
        f"- row_count_24h: `{deribit_block_flow['row_count_24h']}`",
        f"- block_rfq_row_count_7d: `{deribit_block_flow['block_rfq_row_count_7d']}`",
        f"- blocked_by: `{deribit_block_flow['blocked_by']}`",
        f"- next_action: `{deribit_block_flow['next_action']}`",
        "",
        "## Binance Liquidation",
        "",
        f"- status: `{binance_liq['status']}`",
        f"- data_ready: `{binance_liq['data_ready']}`",
        f"- promotion_ready: `{binance_liq['promotion_ready']}`",
        f"- blocked_by: `{binance_liq['blocked_by']}`",
        f"- next_action: `{binance_liq['next_action']}`",
        "",
        "## Hyperliquid Public",
        "",
        f"- status: `{hyperliquid['status']}`",
        f"- collector_ready: `{hyperliquid['collector_ready']}`",
        f"- backfill_ready_min: `{hyperliquid['backfill_ready_min']}`",
        f"- promotion_ready: `{hyperliquid['promotion_ready']}`",
        f"- blocked_by: `{hyperliquid['blocked_by']}`",
        f"- days_covered: `{hyperliquid['days_covered']}`",
        f"- coverage_ratio_estimate: `{hyperliquid['coverage_ratio_estimate']}`",
        f"- extreme_bars_estimate: `{hyperliquid['extreme_bars_estimate']}`",
        f"- next_action: `{hyperliquid['next_action']}`",
        f"- detail: `{hyperliquid['detail']}`",
        "",
        "## Merged Liquidation",
        "",
        f"- status: `{merged['status']}`",
        f"- research_ready_min: `{merged['research_ready_min']}`",
        f"- promotion_ready: `{merged['promotion_ready']}`",
        f"- blocked_by: `{merged['blocked_by']}`",
        f"- coverage_ratio: `{merged['coverage_ratio']}`",
        f"- extreme_bars: `{merged['extreme_bars']}`",
        f"- days_covered: `{merged['days_covered']}`",
        f"- next_action: `{merged['next_action']}`",
        f"- detail: `{merged['detail']}`",
        "",
        "## Overall Blockers",
        "",
    ]
    if blockers:
        lines.extend(f"- `{item}`" for item in blockers)
    else:
        lines.append("- `none`")
    return "\n".join(lines)
