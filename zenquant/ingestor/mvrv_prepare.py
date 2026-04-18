"""Prepare canonical local BTC MVRV CSV inputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pandas as pd

DEFAULT_REALIZED_PRICE_JSON_URL = "https://charts.bgeometrics.com/files/realized_price.json"


def _load_spot_daily_close_map(spot_1d_path: Path) -> dict[str, float]:
    frame = pd.read_parquet(spot_1d_path)
    if frame.empty:
        return {}
    ts_column = "close_time_ns" if "close_time_ns" in frame.columns else "ts_ns"
    close_column = "close" if "close" in frame.columns else "value"
    daily = frame[[ts_column, close_column]].copy()
    daily["date"] = pd.to_datetime(daily[ts_column], unit="ns", utc=True).dt.strftime("%Y-%m-%d")
    daily = daily.groupby("date", as_index=False)[close_column].last()
    return {str(row["date"]): float(row[close_column]) for _, row in daily.iterrows()}


def _fetch_json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _coerce_realized_price_rows(payload: Any) -> list[dict[str, float | str]]:
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, float | str]] = []
    for item in payload:
        if not isinstance(item, list) or len(item) < 2:
            continue
        try:
            ts_ms = int(item[0])
            realized_price = float(item[1])
        except (TypeError, ValueError):
            continue
        date = pd.Timestamp(ts_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
        rows.append({"date": date, "realized_price": realized_price})
    return rows


def build_canonical_mvrv_rows_from_realized_price(
    *,
    spot_1d_path: Path,
    realized_price_payload: Any,
) -> list[dict[str, str]]:
    close_map = _load_spot_daily_close_map(spot_1d_path)
    realized_rows = _coerce_realized_price_rows(realized_price_payload)
    canonical: list[dict[str, str]] = []
    for row in realized_rows:
        date = str(row["date"])
        close = close_map.get(date)
        realized_price = float(row["realized_price"])
        if close is None or realized_price <= 0:
            continue
        canonical.append({"date": date, "mvrv": f"{close / realized_price:.6f}"})
    return canonical


def normalize_mvrv_csv_rows(raw_csv_path: Path) -> list[dict[str, str]]:
    rows = list(csv.DictReader(raw_csv_path.read_text(encoding="utf-8").splitlines()))
    normalized: list[dict[str, str]] = []
    for row in rows:
        lower = {str(key).strip().lower(): value for key, value in row.items()}
        date_value = lower.get("date") or lower.get("day") or lower.get("timestamp") or lower.get("time")
        mvrv_value = (
            lower.get("mvrv")
            or lower.get("mvrv_ratio")
            or lower.get("value")
            or lower.get("mv_rv")
            or next((value for key, value in lower.items() if "mvrv" in key), None)
        )
        if not date_value or mvrv_value is None:
            continue
        try:
            date = pd.Timestamp(str(date_value), tz="UTC").strftime("%Y-%m-%d")
            value = float(str(mvrv_value).replace(",", "").strip())
        except Exception:
            continue
        normalized.append({"date": date, "mvrv": f"{value:.6f}"})
    deduped: dict[str, dict[str, str]] = {}
    for row in normalized:
        deduped[row["date"]] = row
    return [deduped[key] for key in sorted(deduped)]


def write_canonical_mvrv_csv(rows: list[dict[str, str]], output_csv: Path) -> Path:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "mvrv"])
        writer.writeheader()
        writer.writerows(rows)
    return output_csv


def prepare_canonical_mvrv_csv(
    *,
    output_csv: Path,
    raw_csv_path: Path | None = None,
    spot_1d_path: Path = Path("data/raw/binance_spot/klines/BTCUSDT/1d/data.parquet"),
    realized_price_json_url: str = DEFAULT_REALIZED_PRICE_JSON_URL,
) -> Path:
    if raw_csv_path is not None:
        rows = normalize_mvrv_csv_rows(raw_csv_path)
    else:
        payload = _fetch_json(realized_price_json_url)
        rows = build_canonical_mvrv_rows_from_realized_price(
            spot_1d_path=spot_1d_path,
            realized_price_payload=payload,
        )
    return write_canonical_mvrv_csv(rows, output_csv)
