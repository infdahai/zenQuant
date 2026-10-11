"""Cron-friendly forward data collection and health checks."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from zenquant.data.deribit_block_flow import (
    collect_deribit_block_flow_status,
    fetch_public_block_rfq_trades,
    fetch_public_option_trades,
    save_block_flow_rows,
    write_deribit_block_flow_status,
)
from zenquant.data.deribit_options_chain import fetch_option_chain, save_option_chain
from zenquant.data.deribit_status import collect_deribit_status, write_deribit_status
from zenquant.data.hyperliquid_public import fetch_recent_trades, recent_trades_to_buckets, write_hyperliquid_raw
from zenquant.data.hyperliquid_whales import collect_and_write_hyperliquid_whales, load_whale_watchlist
from zenquant.data.liquidation_pipeline import (
    LiquidationBucket,
    merge_liquidation_buckets,
    write_aggregated_liquidations,
)
from zenquant.data.readiness import build_readiness_markdown, build_readiness_payload, load_json
from zenquant.historical import read_parquet_rows

DEFAULT_OUTPUT_DIR = Path("results/data_readiness")


@dataclass(slots=True)
class ForwardCollectionConfig:
    currency: str = "BTC"
    symbol: str = "BTCUSDT"
    deribit_root: Path = Path("data/raw/deribit_options")
    deribit_block_flow_root: Path = Path("data/raw/deribit_block_flow")
    hyperliquid_root: Path = Path("data/raw/hyperliquid/liquidations")
    hyperliquid_whale_root: Path = Path("data/raw/hyperliquid/whale_positions")
    hyperliquid_whale_watchlist: Path | None = Path("config/hyperliquid_whales.toml")
    merged_root: Path = Path("data/raw/merged_liquidations")
    binance_root: Path = Path("data/raw/binance")
    output_dir: Path = DEFAULT_OUTPUT_DIR
    min_trade_notional_usd: float = 50_000.0
    whale_min_abs_position_value_usd: float = 100_000.0
    merge_inputs: tuple[Path, ...] = (Path("data/raw/hyperliquid/liquidations"),)


@dataclass(slots=True)
class ForwardHealthConfig:
    currency: str = "BTC"
    symbol: str = "BTCUSDT"
    deribit_root: Path = Path("data/raw/deribit_options")
    deribit_block_flow_root: Path = Path("data/raw/deribit_block_flow")
    hyperliquid_root: Path = Path("data/raw/hyperliquid/liquidations")
    hyperliquid_whale_root: Path = Path("data/raw/hyperliquid/whale_positions")
    merged_root: Path = Path("data/raw/merged_liquidations")
    output_dir: Path = DEFAULT_OUTPUT_DIR
    max_staleness_hours: float = 2.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_buckets(root: Path, symbol: str) -> list[LiquidationBucket]:
    rows = read_parquet_rows(
        str(root / "aggregated" / symbol / "**" / "*.parquet"),
        order_by="bucket_start_ns ASC",
    )
    buckets: list[LiquidationBucket] = []
    for row in rows:
        buckets.append(
            LiquidationBucket(
                bucket_start_ns=int(row["bucket_start_ns"]),
                bucket_end_ns=int(row["bucket_end_ns"]),
                symbol=str(row["symbol"]),
                long_liq_count=int(row["long_liq_count"]),
                short_liq_count=int(row["short_liq_count"]),
                long_liq_usd=float(row["long_liq_usd"]),
                short_liq_usd=float(row["short_liq_usd"]),
                total_liq_usd=float(row["total_liq_usd"]),
                max_single_liq_usd=float(row["max_single_liq_usd"]),
                liq_imbalance=float(row["liq_imbalance"]),
                source=str(row.get("source", "unknown")),
            )
        )
    return buckets


def _collect_hyperliquid(config: ForwardCollectionConfig) -> dict[str, Any]:
    trades = fetch_recent_trades(config.currency)
    raw_paths = write_hyperliquid_raw(config.hyperliquid_root, trades=trades)
    buckets = recent_trades_to_buckets(trades, min_trade_notional_usd=config.min_trade_notional_usd)
    agg_paths = write_aggregated_liquidations(config.hyperliquid_root, buckets)
    status = {
        "coin": config.currency.upper(),
        "last_success_ts": _now_iso(),
        "recent_trades": len(trades),
        "proxy_buckets": len(buckets),
        "min_trade_notional_usd": config.min_trade_notional_usd,
        "blocked_by": None,
        "detail": "hyperliquid public recent-trade collector wrote liquidation-compatible proxy buckets",
    }
    _write_json(config.hyperliquid_root / "_collector_status.json", status)
    return {
        "ok": True,
        "status_path": str(config.hyperliquid_root / "_collector_status.json"),
        "recent_trades": len(trades),
        "proxy_buckets": len(buckets),
        "raw_files": [str(path) for path in raw_paths],
        "agg_files": [str(path) for path in agg_paths],
    }


def _collect_hyperliquid_whales(config: ForwardCollectionConfig) -> dict[str, Any]:
    if config.hyperliquid_whale_watchlist is None or not config.hyperliquid_whale_watchlist.exists():
        return {
            "ok": True,
            "skipped": True,
            "reason": "hyperliquid_whale_watchlist_missing",
            "watchlist_path": str(config.hyperliquid_whale_watchlist) if config.hyperliquid_whale_watchlist else None,
        }
    accounts = load_whale_watchlist(config.hyperliquid_whale_watchlist)
    status = collect_and_write_hyperliquid_whales(
        accounts=accounts,
        root=config.hyperliquid_whale_root,
        min_abs_position_value_usd=config.whale_min_abs_position_value_usd,
    )
    return {
        "ok": status.get("blocked_by") is None,
        "status_path": str(config.hyperliquid_whale_root / "_collector_status.json"),
        **status,
    }


def _collect_deribit(config: ForwardCollectionConfig) -> dict[str, Any]:
    rows = fetch_option_chain(currency=config.currency)
    if not rows:
        raise RuntimeError("deribit_collect_failed")
    _, parquet_path = save_option_chain(rows, config.deribit_root)
    status_path = write_deribit_status(config.deribit_root, config.currency)
    status = asdict(collect_deribit_status(config.deribit_root, config.currency))
    return {
        "ok": True,
        "rows": len(rows),
        "parquet_path": str(parquet_path),
        "status_path": str(status_path),
        "status": status,
    }


def _collect_deribit_block_flow(config: ForwardCollectionConfig) -> dict[str, Any]:
    now_ms = int(datetime.now(UTC).timestamp() * 1_000)
    start_ms = now_ms - 24 * 3600 * 1_000
    public_rows = fetch_public_option_trades(
        currency=config.currency,
        start_timestamp_ms=start_ms,
        end_timestamp_ms=now_ms,
        count=1000,
    )
    block_rows = fetch_public_block_rfq_trades(currency=config.currency, count=200)
    rows = public_rows + block_rows
    paths = save_block_flow_rows(rows, config.deribit_block_flow_root, config.currency)
    status_path = write_deribit_block_flow_status(config.deribit_block_flow_root, config.currency)
    status = asdict(collect_deribit_block_flow_status(config.deribit_block_flow_root, config.currency))
    return {
        "ok": True,
        "rows": len(rows),
        "public_trade_rows": len(public_rows),
        "block_rfq_rows": len(block_rows),
        "paths": [str(path) for path in paths],
        "status_path": str(status_path),
        "status": status,
    }


def _merge_liquidations(config: ForwardCollectionConfig) -> dict[str, Any]:
    buckets: list[LiquidationBucket] = []
    for root in config.merge_inputs:
        buckets.extend(_load_buckets(root, config.symbol))
    merged = merge_liquidation_buckets(buckets)
    paths = write_aggregated_liquidations(config.merged_root, merged)
    return {
        "ok": True,
        "input_roots": [str(path) for path in config.merge_inputs],
        "merged_buckets": len(merged),
        "paths": [str(path) for path in paths],
    }


def _refresh_readiness(config: ForwardCollectionConfig) -> dict[str, Any]:
    payload = build_readiness_payload(
        binance_root=config.binance_root,
        symbol=config.symbol,
        deribit_root=config.deribit_root,
        deribit_block_flow_root=config.deribit_block_flow_root,
        hyperliquid_root=config.hyperliquid_root,
        merged_root=config.merged_root,
    )
    json_path = config.output_dir / "data_readiness.json"
    md_path = config.output_dir / "data_readiness.md"
    _write_json(json_path, payload)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(build_readiness_markdown(payload), encoding="utf-8")
    return {
        "ok": True,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "overall_blockers": list(payload["overall_blockers"]),
    }


def run_collection(config: ForwardCollectionConfig) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    started_at = _now_iso()
    try:
        deribit_step = _collect_deribit(config)
        steps.append({"step": "deribit_collect", **deribit_step})

        deribit_block_step = _collect_deribit_block_flow(config)
        steps.append({"step": "deribit_block_flow_collect", **deribit_block_step})

        hyperliquid_step = _collect_hyperliquid(config)
        steps.append({"step": "hyperliquid_collect", **hyperliquid_step})

        hyperliquid_whales_step = _collect_hyperliquid_whales(config)
        steps.append({"step": "hyperliquid_whales_collect", **hyperliquid_whales_step})

        merge_step = _merge_liquidations(config)
        steps.append({"step": "merge_liquidation", **merge_step})

        readiness_step = _refresh_readiness(config)
        steps.append({"step": "readiness_refresh", **readiness_step})
    except Exception as exc:
        summary = {
            "ok": False,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "steps": steps,
            "error": str(exc),
        }
        _write_json(config.output_dir / "forward_collection_latest.json", summary)
        return summary

    summary = {
        "ok": True,
        "started_at": started_at,
        "finished_at": _now_iso(),
        "steps": steps,
    }
    _write_json(config.output_dir / "forward_collection_latest.json", summary)
    return summary


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_recent(path: Path, max_age: timedelta) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return datetime.now(UTC) - modified <= max_age


def evaluate_health(config: ForwardHealthConfig) -> dict[str, Any]:
    max_age = timedelta(hours=config.max_staleness_hours)
    failures: list[str] = []
    state_path = config.output_dir / "forward_collection_health_state.json"

    readiness_path = config.output_dir / "data_readiness.json"
    readiness = load_json(readiness_path)
    readiness_blockers: list[str] = []
    if not readiness:
        failures.append("readiness_missing")
    elif not _is_recent(readiness_path, max_age):
        failures.append("readiness_stale")
    else:
        readiness_blockers = list(readiness.get("overall_blockers") or [])

    deribit = asdict(collect_deribit_status(config.deribit_root, config.currency))
    if deribit.get("stale"):
        failures.append("deribit_stale")
    deribit_block_flow = asdict(collect_deribit_block_flow_status(config.deribit_block_flow_root, config.currency))
    if deribit_block_flow.get("stale"):
        failures.append("deribit_block_flow_stale")

    hyper_status_path = config.hyperliquid_root / "_collector_status.json"
    hyper_status = load_json(hyper_status_path)
    hyper_last = _parse_ts(hyper_status.get("last_success_ts"))
    if not hyper_status:
        failures.append("hyperliquid_status_missing")
    elif hyper_last is None or datetime.now(UTC) - hyper_last > max_age:
        failures.append("hyperliquid_stale")
    elif hyper_status.get("blocked_by"):
        failures.append("hyperliquid_blocked")

    merged_paths = list((config.merged_root / "aggregated" / config.symbol).glob("**/*.parquet"))
    if not merged_paths:
        failures.append("merged_liquidation_missing")
    elif not _is_recent(max(merged_paths, key=lambda path: path.stat().st_mtime), max_age):
        failures.append("merged_liquidation_stale")

    previous_state = load_json(state_path)
    previous_blockers = list(previous_state.get("current_blockers") or [])
    if readiness_blockers:
        consecutive_cycles = int(previous_state.get("consecutive_blocked_cycles", 0) or 0)
        if previous_blockers == readiness_blockers:
            consecutive_cycles += 1
        else:
            consecutive_cycles = 1
        if consecutive_cycles >= 2:
            failures.append("readiness_blocked_persistent")
    else:
        consecutive_cycles = 0

    state_payload = {
        "checked_at": _now_iso(),
        "current_blockers": readiness_blockers,
        "consecutive_blocked_cycles": consecutive_cycles,
    }
    _write_json(state_path, state_payload)

    payload = {
        "ok": not failures,
        "checked_at": _now_iso(),
        "failures": failures,
        "readiness_blockers": readiness_blockers,
        "readiness_blocked_cycles": consecutive_cycles,
        "deribit_status": deribit,
        "deribit_block_flow_status": deribit_block_flow,
        "hyperliquid_status_path": str(hyper_status_path),
        "readiness_path": str(readiness_path),
        "health_state_path": str(state_path),
        "max_staleness_hours": config.max_staleness_hours,
    }
    _write_json(config.output_dir / "forward_collection_health.json", payload)
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one forward data collection cycle or health check")
    parser.add_argument("--health-only", action="store_true")
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--deribit-root", type=Path, default=Path("data/raw/deribit_options"))
    parser.add_argument("--deribit-block-flow-root", type=Path, default=Path("data/raw/deribit_block_flow"))
    parser.add_argument("--hyperliquid-root", type=Path, default=Path("data/raw/hyperliquid/liquidations"))
    parser.add_argument("--hyperliquid-whale-root", type=Path, default=Path("data/raw/hyperliquid/whale_positions"))
    parser.add_argument("--hyperliquid-whale-watchlist", type=Path, default=Path("config/hyperliquid_whales.toml"))
    parser.add_argument("--merged-root", type=Path, default=Path("data/raw/merged_liquidations"))
    parser.add_argument("--binance-root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-trade-notional-usd", type=float, default=50_000.0)
    parser.add_argument("--whale-min-abs-position-value-usd", type=float, default=100_000.0)
    parser.add_argument("--merge-input", type=Path, action="append", default=None)
    parser.add_argument("--max-staleness-hours", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    merge_inputs = tuple(args.merge_input) if args.merge_input else (args.hyperliquid_root,)
    if args.health_only:
        payload = evaluate_health(
            ForwardHealthConfig(
                currency=args.currency,
                symbol=args.symbol,
                deribit_root=args.deribit_root,
                deribit_block_flow_root=args.deribit_block_flow_root,
                hyperliquid_root=args.hyperliquid_root,
                hyperliquid_whale_root=args.hyperliquid_whale_root,
                merged_root=args.merged_root,
                output_dir=args.output_dir,
                max_staleness_hours=args.max_staleness_hours,
            )
        )
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 1

    payload = run_collection(
        ForwardCollectionConfig(
            currency=args.currency,
            symbol=args.symbol,
            deribit_root=args.deribit_root,
            deribit_block_flow_root=args.deribit_block_flow_root,
            hyperliquid_root=args.hyperliquid_root,
            hyperliquid_whale_root=args.hyperliquid_whale_root,
            hyperliquid_whale_watchlist=args.hyperliquid_whale_watchlist,
            merged_root=args.merged_root,
            binance_root=args.binance_root,
            output_dir=args.output_dir,
            min_trade_notional_usd=args.min_trade_notional_usd,
            whale_min_abs_position_value_usd=args.whale_min_abs_position_value_usd,
            merge_inputs=merge_inputs,
        )
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
