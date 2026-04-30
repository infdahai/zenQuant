"""Deribit options-chain download helpers shared by scripts and apps."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

DERIBIT_PUBLIC_API = "https://www.deribit.com/api/v2/public"


@dataclass(slots=True)
class OptionChainRow:
    snapshot_ts_ns: int
    currency: str
    instrument_name: str
    expiry_ts_ms: int
    expiry_date: str
    strike: float
    option_type: str
    underlying_price: float
    open_interest: float
    volume_usd: float
    mark_iv: float
    bid_price: float | None
    ask_price: float | None
    mid_price: float | None
    underlying_index: str
    settlement_period: str


def _fetch_json(endpoint: str, query: dict[str, str]) -> Any:
    url = f"{DERIBIT_PUBLIC_API}/{endpoint}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": "zenquant/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(f"Deribit API error for {endpoint}: {payload['error']}")
    return payload.get("result")


def fetch_option_chain(currency: str = "BTC") -> list[OptionChainRow]:
    """Fetch the active options chain and normalize it into flat rows."""
    currency = currency.upper()
    instruments = _fetch_json(
        "get_instruments",
        {"currency": currency, "kind": "option", "expired": "false"},
    )
    summaries = _fetch_json(
        "get_book_summary_by_currency",
        {"currency": currency, "kind": "option"},
    )
    snapshot_ts_ns = time.time_ns()
    if not isinstance(instruments, list) or not isinstance(summaries, list):
        raise RuntimeError("Unexpected Deribit options payload shape")

    instrument_by_name = {
        str(item.get("instrument_name", "") or ""): item
        for item in instruments
        if isinstance(item, dict) and item.get("instrument_name")
    }

    rows: list[OptionChainRow] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        instrument_name = str(summary.get("instrument_name", "") or "")
        instrument = instrument_by_name.get(instrument_name)
        if instrument is None:
            continue
        expiry_ts_ms = int(instrument.get("expiration_timestamp", 0) or 0)
        if expiry_ts_ms <= 0:
            continue
        expiry_date = datetime.fromtimestamp(expiry_ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
        bid_price = _float_or_none(summary.get("bid_price"))
        ask_price = _float_or_none(summary.get("ask_price"))
        rows.append(
            OptionChainRow(
                snapshot_ts_ns=snapshot_ts_ns,
                currency=currency,
                instrument_name=instrument_name,
                expiry_ts_ms=expiry_ts_ms,
                expiry_date=expiry_date,
                strike=float(instrument.get("strike", 0.0) or 0.0),
                option_type=str(instrument.get("option_type", "") or "").lower(),
                underlying_price=float(summary.get("underlying_price", 0.0) or 0.0),
                open_interest=float(summary.get("open_interest", 0.0) or 0.0),
                volume_usd=float(summary.get("volume_usd", 0.0) or 0.0),
                mark_iv=float(summary.get("mark_iv", 0.0) or 0.0),
                bid_price=bid_price,
                ask_price=ask_price,
                mid_price=_mid_price(bid_price, ask_price),
                underlying_index=str(instrument.get("base_currency", currency) or currency),
                settlement_period=str(instrument.get("settlement_period", "") or ""),
            )
        )

    rows.sort(key=lambda row: (row.expiry_ts_ms, row.strike, row.option_type))
    return rows


def save_option_chain(rows: list[OptionChainRow], output_root: Path) -> tuple[Path, Path]:
    """Save one snapshot as both JSON and Parquet."""
    if not rows:
        raise RuntimeError("No Deribit option rows to save")
    snapshot_ts_ns = rows[0].snapshot_ts_ns
    stamp = datetime.fromtimestamp(snapshot_ts_ns / 1_000_000_000, tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    partition = datetime.fromtimestamp(snapshot_ts_ns / 1_000_000_000, tz=UTC).strftime("%Y-%m")
    partition_dir = output_root / rows[0].currency / partition
    partition_dir.mkdir(parents=True, exist_ok=True)

    json_path = partition_dir / f"options_chain_{stamp}.json"
    parquet_path = partition_dir / f"options_chain_{stamp}.parquet"

    payload = [asdict(row) for row in rows]
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    table = pa.Table.from_pylist(payload)
    pq.write_table(table, parquet_path)
    return json_path, parquet_path


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    parsed = float(value or 0.0)
    return parsed if parsed > 0 else None


def _mid_price(bid_price: float | None, ask_price: float | None) -> float | None:
    if bid_price is None or ask_price is None:
        return None
    return (bid_price + ask_price) / 2.0
