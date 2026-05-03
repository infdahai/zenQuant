#!/usr/bin/env python3
"""Download Binance futures daily metrics and derive 4h open-interest history."""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from zenquant.historical.storage import HistoricalOpenInterestRow, write_parquet_rows

BINANCE_DAILY_BASE = "https://data.binance.vision/data/futures/um/daily"


@dataclass(slots=True, frozen=True)
class BinanceMetricsRow:
    create_time: str
    symbol: str
    sum_open_interest: float
    sum_open_interest_value: float
    count_toptrader_long_short_ratio: float
    sum_toptrader_long_short_ratio: float
    count_long_short_ratio: float
    sum_taker_long_short_vol_ratio: float


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Binance futures daily metrics history")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-date", default="2023-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    payload = download_binance_metrics_history(
        symbol=args.symbol,
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        root=args.root,
        workers=args.workers,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


def download_binance_metrics_history(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    root: Path,
    workers: int = 8,
) -> dict[str, Any]:
    days = list(_date_range(start_date, end_date))
    raw_rows_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    end_ts = pd.Timestamp(end_date, tz=UTC) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    fetched = 0
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(_download_metrics_day, symbol, day): day for day in days}
        for future in as_completed(futures):
            day = futures[future]
            rows = future.result()
            if not rows:
                failed.append(day.isoformat())
                continue
            fetched += 1
            for row in rows:
                if pd.Timestamp(row.create_time, tz=UTC) > end_ts:
                    continue
                raw_rows_by_month[row.create_time[:7]].append(_metrics_row_to_dict(row))

    raw_paths: list[str] = []
    oi_paths: list[str] = []
    oi_rows_total = 0
    for month, rows in sorted(raw_rows_by_month.items()):
        raw_frame = pd.DataFrame(rows).sort_values("create_time")
        raw_path = root / "metrics" / symbol / f"{month}.parquet"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_frame.to_parquet(raw_path, index=False)
        raw_paths.append(str(raw_path))

        oi_rows = _aggregate_open_interest_4h(raw_frame, symbol=symbol)
        oi_path = root / "open_interest" / symbol / "4h" / f"{month}.parquet"
        write_parquet_rows(oi_path, oi_rows)
        if oi_rows:
            oi_paths.append(str(oi_path))
            oi_rows_total += len(oi_rows)

    coverage = _coverage_from_oi_paths(oi_paths)
    return {
        "schema_version": "binance_metrics_history_download.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "symbol": symbol,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily_files_requested": len(days),
        "daily_files_downloaded": fetched,
        "daily_files_failed": len(failed),
        "failed_dates_sample": failed[:20],
        "raw_metrics_paths": raw_paths,
        "open_interest_paths": oi_paths,
        "open_interest_rows": oi_rows_total,
        "coverage": coverage,
        "blocked_by": None
        if coverage and coverage["start"] < "2024-01-01" and coverage["end"] >= "2025-12-31"
        else "binance_metrics_oi_history_incomplete",
    }


def _download_metrics_day(symbol: str, day: date) -> list[BinanceMetricsRow]:
    date_str = day.isoformat()
    filename = f"{symbol}-metrics-{date_str}.zip"
    url = f"{BINANCE_DAILY_BASE}/metrics/{symbol}/{filename}"
    request = urllib.request.Request(url, headers={"User-Agent": "zenquant/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return []
    rows: list[BinanceMetricsRow] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive, archive.open(archive.namelist()[0]) as raw:
        decoded = io.TextIOWrapper(raw, encoding="utf-8")
        for row in csv.DictReader(decoded):
            try:
                rows.append(
                    BinanceMetricsRow(
                        create_time=str(row["create_time"]),
                        symbol=str(row["symbol"]),
                        sum_open_interest=float(row["sum_open_interest"]),
                        sum_open_interest_value=float(row["sum_open_interest_value"]),
                        count_toptrader_long_short_ratio=float(row["count_toptrader_long_short_ratio"]),
                        sum_toptrader_long_short_ratio=float(row["sum_toptrader_long_short_ratio"]),
                        count_long_short_ratio=float(row["count_long_short_ratio"]),
                        sum_taker_long_short_vol_ratio=float(row["sum_taker_long_short_vol_ratio"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def _aggregate_open_interest_4h(frame: pd.DataFrame, *, symbol: str) -> list[HistoricalOpenInterestRow]:
    if frame.empty:
        return []
    working = frame.copy()
    working["ts"] = pd.to_datetime(working["create_time"], utc=True)
    working["bucket"] = working["ts"].dt.floor("4h")
    grouped = working.sort_values("ts").groupby("bucket", as_index=False).tail(1)
    return [
        HistoricalOpenInterestRow(
            symbol=symbol,
            timeframe="4h",
            ts_ns=int(row.ts.value),
            open_interest=float(row.sum_open_interest),
        )
        for row in grouped.itertuples(index=False)
    ]


def _coverage_from_oi_paths(paths: list[str]) -> dict[str, str] | None:
    if not paths:
        return None
    frame = pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)
    if frame.empty:
        return None
    ts = pd.to_datetime(frame["ts_ns"], unit="ns", utc=True)
    return {"start": ts.min().isoformat(), "end": ts.max().isoformat()}


def _metrics_row_to_dict(row: BinanceMetricsRow) -> dict[str, Any]:
    return {
        "create_time": row.create_time,
        "symbol": row.symbol,
        "sum_open_interest": row.sum_open_interest,
        "sum_open_interest_value": row.sum_open_interest_value,
        "count_toptrader_long_short_ratio": row.count_toptrader_long_short_ratio,
        "sum_toptrader_long_short_ratio": row.sum_toptrader_long_short_ratio,
        "count_long_short_ratio": row.count_long_short_ratio,
        "sum_taker_long_short_vol_ratio": row.sum_taker_long_short_vol_ratio,
    }


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


if __name__ == "__main__":
    main()
