"""Public Deribit block/combo/options trade collection helpers.

This module intentionally stays on the observation side:
- it uses only public Deribit APIs,
- it normalizes legs that are visible in public block RFQ payloads,
- it does not attempt account-level attribution.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

DERIBIT_PUBLIC_API = "https://www.deribit.com/api/v2/public"
_FLOW_STALE_HOURS = 8.1


@dataclass(slots=True)
class DeribitBlockFlowRow:
    ts_ns: int
    trade_id: str | None
    block_trade_id: str | None
    combo_id: str | None
    combo_trade_id: str | None
    instrument_name: str
    option_type: str
    expiry_ts_ms: int | None
    strike: float | None
    direction: str | None
    amount: float
    price: float | None
    index_price: float | None
    mark_price: float | None
    iv: float | None
    liquidity: str | None
    is_block_trade: bool
    is_combo_leg: bool
    leg_count: int
    source: str


@dataclass(slots=True)
class DeribitBlockFlowStatus:
    enabled: bool
    collector_running: bool
    last_success_ts: str | None
    last_error: str | None
    row_count_24h: int
    row_count_7d: int
    block_rfq_row_count_7d: int
    observation_ready: bool
    stale: bool
    blocked_by: str | None
    next_action: str


def _fetch_json(endpoint: str, query: dict[str, str]) -> Any:
    url = f"{DERIBIT_PUBLIC_API}/{endpoint}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": "zenquant/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(f"Deribit API error for {endpoint}: {payload['error']}")
    return payload.get("result")


def fetch_public_option_trades(
    *,
    currency: str = "BTC",
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    count: int = 1000,
) -> list[DeribitBlockFlowRow]:
    """Fetch public options trades in a time range."""
    result = _fetch_json(
        "get_last_trades_by_currency_and_time",
        {
            "currency": currency.upper(),
            "kind": "option",
            "start_timestamp": str(start_timestamp_ms),
            "end_timestamp": str(end_timestamp_ms),
            "count": str(count),
            "sorting": "asc",
        },
    )
    trades = result.get("trades", []) if isinstance(result, dict) else []
    rows: list[DeribitBlockFlowRow] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        instrument_name = str(trade.get("instrument_name", "") or "")
        expiry_ts_ms, strike, option_type = _parse_option_instrument(instrument_name)
        if option_type is None:
            continue
        block_trade_id = _str_or_none(trade.get("block_trade_id"))
        combo_id = _str_or_none(trade.get("combo_id"))
        combo_trade_id = _str_or_none(trade.get("combo_trade_id"))
        rows.append(
            DeribitBlockFlowRow(
                ts_ns=int(trade.get("timestamp", 0) or 0) * 1_000_000,
                trade_id=_str_or_none(trade.get("trade_id")),
                block_trade_id=block_trade_id,
                combo_id=combo_id,
                combo_trade_id=combo_trade_id,
                instrument_name=instrument_name,
                option_type=option_type,
                expiry_ts_ms=expiry_ts_ms,
                strike=strike,
                direction=_str_or_none(trade.get("direction")),
                amount=float(trade.get("amount", 0.0) or 0.0),
                price=_float_or_none(trade.get("price")),
                index_price=_float_or_none(trade.get("index_price")),
                mark_price=_float_or_none(trade.get("mark_price")),
                iv=_float_or_none(trade.get("iv")),
                liquidity=_str_or_none(trade.get("liquidity")),
                is_block_trade=block_trade_id is not None,
                is_combo_leg=combo_id is not None or combo_trade_id is not None,
                leg_count=int(trade.get("block_trade_leg_count", 1) or 1),
                source="public_option_trade",
            )
        )
    return rows


def fetch_public_block_rfq_trades(
    *,
    currency: str = "BTC",
    count: int = 200,
    continuation: str | None = None,
) -> list[DeribitBlockFlowRow]:
    """Fetch public block RFQ trades and expand them into visible legs."""
    query = {"currency": currency.upper(), "count": str(min(count, 25))}
    if continuation:
        query["continuation"] = continuation
    result = _fetch_json("get_block_rfq_trades", query)
    block_rfqs = result.get("block_rfqs", []) if isinstance(result, dict) else []
    rows: list[DeribitBlockFlowRow] = []
    for block_rfq in block_rfqs:
        if not isinstance(block_rfq, dict):
            continue
        block_rfq_id = _str_or_none(block_rfq.get("id"))
        combo_id = _str_or_none(block_rfq.get("combo_id"))
        timestamp_ms = int(block_rfq.get("timestamp", 0) or 0)
        amount = float(block_rfq.get("amount", 0.0) or 0.0)
        mark_price = _float_or_none(block_rfq.get("mark_price"))
        trade_direction = _str_or_none(block_rfq.get("direction"))
        index_prices = block_rfq.get("index_prices", {}) if isinstance(block_rfq.get("index_prices"), dict) else {}
        index_price = _first_index_price(index_prices)
        legs = block_rfq.get("legs", [])
        if not isinstance(legs, list):
            continue
        leg_count = len(legs)
        for leg_index, leg in enumerate(legs):
            if not isinstance(leg, dict):
                continue
            instrument_name = str(leg.get("instrument_name", "") or "")
            expiry_ts_ms, strike, option_type = _parse_option_instrument(instrument_name)
            if option_type is None:
                continue
            rows.append(
                DeribitBlockFlowRow(
                    ts_ns=timestamp_ms * 1_000_000,
                    trade_id=f"block_rfq:{block_rfq_id}:{leg_index}",
                    block_trade_id=block_rfq_id,
                    combo_id=combo_id,
                    combo_trade_id=None,
                    instrument_name=instrument_name,
                    option_type=option_type,
                    expiry_ts_ms=expiry_ts_ms,
                    strike=strike,
                    direction=_str_or_none(leg.get("direction")) or trade_direction,
                    amount=amount * float(leg.get("ratio", 1.0) or 1.0),
                    price=_float_or_none(leg.get("price")),
                    index_price=index_price,
                    mark_price=mark_price,
                    iv=None,
                    liquidity=None,
                    is_block_trade=True,
                    is_combo_leg=True,
                    leg_count=leg_count,
                    source="public_block_rfq",
                )
            )
    return rows


def save_block_flow_rows(rows: list[DeribitBlockFlowRow], output_root: Path, currency: str = "BTC") -> list[Path]:
    """Persist rows into monthly parquet partitions with deterministic dedupe."""
    if not rows:
        return []
    currency = currency.upper()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        ts = datetime.fromtimestamp(row.ts_ns / 1_000_000_000, tz=UTC)
        grouped.setdefault(ts.strftime("%Y-%m"), []).append(asdict(row))
    written: list[Path] = []
    for partition, payload in grouped.items():
        path = output_root / currency / partition / "data.parquet"
        frame_new = pd.DataFrame(payload)
        if path.exists():
            frame_old = pd.read_parquet(path)
            frame = pd.concat([frame_old, frame_new], ignore_index=True)
        else:
            frame = frame_new
        frame = frame.drop_duplicates(
            subset=["trade_id", "instrument_name", "ts_ns", "source"],
            keep="last",
        ).sort_values(["ts_ns", "instrument_name", "source"])
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        written.append(path)
    return written


def collect_deribit_block_flow_status(root: Path, currency: str = "BTC") -> DeribitBlockFlowStatus:
    rows = _load_block_flow_rows(root, currency)
    if rows.empty:
        return DeribitBlockFlowStatus(
            enabled=False,
            collector_running=False,
            last_success_ts=None,
            last_error="no_deribit_block_flow_rows_found",
            row_count_24h=0,
            row_count_7d=0,
            block_rfq_row_count_7d=0,
            observation_ready=False,
            stale=True,
            blocked_by="no_deribit_block_flow_rows_found",
            next_action="start_block_flow_collector",
        )
    now_ns = time.time_ns()
    last_ts_ns = int(rows["ts_ns"].max())
    age_h = (now_ns - last_ts_ns) / 1_000_000_000 / 3600.0
    recent_24h = rows[rows["ts_ns"] >= now_ns - 24 * 3600 * 1_000_000_000]
    recent_7d = rows[rows["ts_ns"] >= now_ns - 7 * 24 * 3600 * 1_000_000_000]
    block_rfq_count = int((recent_7d["source"] == "public_block_rfq").sum())
    stale = age_h > _FLOW_STALE_HOURS
    observation_ready = len(recent_24h) > 0 and not stale
    blocked_by = None
    if stale:
        blocked_by = "deribit_block_flow_stale"
    elif len(recent_24h) == 0:
        blocked_by = "deribit_block_flow_empty_24h"
    return DeribitBlockFlowStatus(
        enabled=True,
        collector_running=not stale,
        last_success_ts=datetime.fromtimestamp(last_ts_ns / 1_000_000_000, tz=UTC).isoformat(),
        last_error=None,
        row_count_24h=len(recent_24h),
        row_count_7d=len(recent_7d),
        block_rfq_row_count_7d=block_rfq_count,
        observation_ready=observation_ready,
        stale=stale,
        blocked_by=blocked_by,
        next_action="keep_block_flow_collector_running" if not stale else "restart_block_flow_collector",
    )


def write_deribit_block_flow_status(root: Path, currency: str = "BTC") -> Path:
    status = collect_deribit_block_flow_status(root, currency)
    path = root / currency.upper() / "_collector_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(status), indent=2), encoding="utf-8")
    return path


def _load_block_flow_rows(root: Path, currency: str) -> pd.DataFrame:
    currency_root = root / currency.upper()
    if not currency_root.exists():
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for path in currency_root.rglob("*.parquet"):
        if path.name != "data.parquet":
            continue
        try:
            frames.append(pd.read_parquet(path))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_index_price(index_prices: dict[str, Any]) -> float | None:
    for key in ("btc_usd", "btc_usdc", "eth_usd", "eth_usdc"):
        value = _float_or_none(index_prices.get(key))
        if value is not None:
            return value
    for value in index_prices.values():
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _parse_option_instrument(instrument_name: str) -> tuple[int | None, float | None, str | None]:
    parts = instrument_name.split("-")
    if len(parts) < 4:
        return None, None, None
    try:
        expiry_dt = datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=UTC)
        expiry_ts_ms = int(expiry_dt.timestamp() * 1000)
        strike = float(parts[2])
        option_type = parts[3].lower()
    except Exception:
        return None, None, None
    if option_type not in {"c", "p"}:
        return None, None, None
    return expiry_ts_ms, strike, option_type
