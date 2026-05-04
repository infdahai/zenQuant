"""Historical Deribit option trade backfill helpers."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.data.deribit_block_flow import DeribitBlockFlowRow, save_block_flow_rows

DERIBIT_HISTORY_PUBLIC_API = "https://history.deribit.com/api/v2/public"


def backfill_history_option_trades(
    *,
    currency: str,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    output_root: Path,
    chunk_hours: float = 24.0,
    count: int = 10_000,
    max_requests: int | None = None,
    sleep_seconds: float = 0.0,
) -> dict[str, Any]:
    """Backfill historical Deribit option trades into the block-flow parquet layout."""
    chunk_ms = max(1, int(chunk_hours * 3600 * 1000))
    cursor = start_timestamp_ms
    requests_used = 0
    rows_written = 0
    paths: set[str] = set()
    while cursor < end_timestamp_ms:
        chunk_end = min(end_timestamp_ms, cursor + chunk_ms)
        rows, page_requests = fetch_history_option_trades_paginated(
            currency=currency,
            start_timestamp_ms=cursor,
            end_timestamp_ms=chunk_end,
            count=count,
            max_requests=None if max_requests is None else max_requests - requests_used,
        )
        requests_used += page_requests
        if rows:
            written = save_block_flow_rows(rows, output_root, currency)
            paths.update(str(path) for path in written)
            rows_written += len(rows)
        if max_requests is not None and requests_used >= max_requests:
            break
        cursor = chunk_end
        if sleep_seconds > 0.0 and cursor < end_timestamp_ms:
            time.sleep(sleep_seconds)
    completed = cursor >= end_timestamp_ms
    return {
        "schema_version": "deribit_history_option_trade_backfill.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "currency": currency.upper(),
        "start_timestamp_ms": start_timestamp_ms,
        "end_timestamp_ms": end_timestamp_ms,
        "completed_until_timestamp_ms": min(cursor, end_timestamp_ms),
        "completed": completed,
        "output_root": str(output_root),
        "chunk_hours": chunk_hours,
        "count": count,
        "max_requests": max_requests,
        "requests_used": requests_used,
        "rows_written": rows_written,
        "paths": sorted(paths),
    }


def fetch_history_option_trades_paginated(
    *,
    currency: str,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    count: int = 10_000,
    max_requests: int | None = None,
) -> tuple[list[DeribitBlockFlowRow], int]:
    rows: list[DeribitBlockFlowRow] = []
    cursor = start_timestamp_ms
    requests_used = 0
    while cursor < end_timestamp_ms:
        if max_requests is not None and requests_used >= max_requests:
            break
        result = _fetch_history_json(
            "get_last_trades_by_currency",
            {
                "currency": currency.upper(),
                "kind": "option",
                "start_timestamp": str(cursor),
                "end_timestamp": str(end_timestamp_ms),
                "count": str(count),
                "sorting": "asc",
            },
        )
        requests_used += 1
        trades = result.get("trades", []) if isinstance(result, dict) else []
        page_rows = [_normalize_history_trade(trade) for trade in trades if isinstance(trade, dict)]
        page_rows = [row for row in page_rows if row is not None]
        if not page_rows:
            break
        rows.extend(page_rows)
        last_ts_ms = max(row.ts_ns // 1_000_000 for row in page_rows)
        if not result.get("has_more"):
            break
        if last_ts_ms < cursor:
            raise RuntimeError("deribit_history_pagination_stalled")
        cursor = last_ts_ms + 1
    return rows, requests_used


def _fetch_history_json(endpoint: str, query: dict[str, str]) -> Any:
    url = f"{DERIBIT_HISTORY_PUBLIC_API}/{endpoint}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": "zenquant/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(f"Deribit history API error for {endpoint}: {payload['error']}")
    return payload.get("result")


def _normalize_history_trade(trade: dict[str, Any]) -> DeribitBlockFlowRow | None:
    instrument_name = str(trade.get("instrument_name", "") or "")
    expiry_ts_ms, strike, option_type = _parse_option_instrument(instrument_name)
    if option_type is None:
        return None
    block_trade_id = _str_or_none(trade.get("block_trade_id"))
    combo_id = _str_or_none(trade.get("combo_id"))
    combo_trade_id = _str_or_none(trade.get("combo_trade_id"))
    return DeribitBlockFlowRow(
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


def history_backfill_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Deribit History Option Trade Backfill",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- currency: `{payload['currency']}`",
        f"- start_timestamp_ms: `{payload['start_timestamp_ms']}`",
        f"- end_timestamp_ms: `{payload['end_timestamp_ms']}`",
        f"- completed_until_timestamp_ms: `{payload['completed_until_timestamp_ms']}`",
        f"- completed: `{payload['completed']}`",
        f"- output_root: `{payload['output_root']}`",
        f"- requests_used: `{payload['requests_used']}`",
        f"- rows_written: `{payload['rows_written']}`",
        "",
        "## Paths",
        "",
    ]
    lines.extend(f"- `{path}`" for path in payload["paths"])
    return "\n".join(lines) + "\n"


def payload_for_print(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "currency": payload["currency"],
        "completed": payload["completed"],
        "completed_until_timestamp_ms": payload["completed_until_timestamp_ms"],
        "requests_used": payload["requests_used"],
        "rows_written": payload["rows_written"],
        "paths": payload["paths"],
    }


def row_to_dict(row: DeribitBlockFlowRow) -> dict[str, Any]:
    return asdict(row)


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
