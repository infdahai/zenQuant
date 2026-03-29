"""Historical Binance data sync helpers.

This module intentionally targets the first README use case:
- fetch BTC futures klines into Parquet,
- fetch funding rate history,
- fetch open-interest history,
- keep the files in a stable layout for replay/backtest consumers.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .storage import (
    HistoricalDatasetLayout,
    HistoricalFundingRow,
    HistoricalKlineRow,
    HistoricalOpenInterestRow,
    write_parquet_rows,
)

BINANCE_DATA_BASE = "https://data.binance.vision/data/futures/um/monthly"
BINANCE_REST_BASE = "https://fapi.binance.com"


@dataclass(slots=True)
class BinanceHistoricalSyncConfig:
    """Configuration for historical sync jobs.

    Example:
        cfg = BinanceHistoricalSyncConfig(root_dir=Path("data/raw/binance"))
        BinanceHistoricalDownloader(cfg).sync_month(symbol="BTCUSDT", year=2024, month=1)
    """

    root_dir: Path = Path("data/raw/binance")
    request_timeout_s: float = 12.0
    open_interest_period: str = "4h"


class BinanceHistoricalDownloader:
    """Download Binance public futures history into the repo's Parquet layout."""

    def __init__(self, config: BinanceHistoricalSyncConfig | None = None) -> None:
        self._config = config or BinanceHistoricalSyncConfig()
        self._layout = HistoricalDatasetLayout(root_dir=self._config.root_dir)

    def sync_month(
        self,
        *,
        symbol: str,
        year: int,
        month: int,
        timeframes: tuple[str, ...] = ("1h", "4h", "1d"),
    ) -> dict[str, Path]:
        """Download a single month of history for the requested symbol.

        Example:
            outputs = downloader.sync_month(symbol="BTCUSDT", year=2024, month=1)
            print(outputs["kline:4h"])
        """

        partition = f"{year:04d}-{month:02d}"
        outputs: dict[str, Path] = {}
        for timeframe in timeframes:
            rows = self._download_kline_month(symbol=symbol, timeframe=timeframe, year=year, month=month)
            path = self._layout.kline_path(symbol=symbol, timeframe=timeframe, partition=partition)
            write_parquet_rows(path, rows)
            outputs[f"kline:{timeframe}"] = path

        month_start = datetime(year, month, 1, tzinfo=UTC)
        if month == 12:
            month_end = datetime(year + 1, 1, 1, tzinfo=UTC)
        else:
            month_end = datetime(year, month + 1, 1, tzinfo=UTC)

        funding_rows = self._download_funding(symbol=symbol, start_dt=month_start, end_dt=month_end)
        funding_path = self._layout.funding_path(symbol=symbol, partition=partition)
        write_parquet_rows(funding_path, funding_rows)
        outputs["funding"] = funding_path

        oi_rows = self._download_open_interest(
            symbol=symbol,
            timeframe=self._config.open_interest_period,
            start_dt=month_start,
            end_dt=month_end,
        )
        oi_path = self._layout.open_interest_path(
            symbol=symbol,
            timeframe=self._config.open_interest_period,
            partition=partition,
        )
        write_parquet_rows(oi_path, oi_rows)
        outputs["open_interest"] = oi_path
        return outputs

    def _download_kline_month(
        self,
        *,
        symbol: str,
        timeframe: str,
        year: int,
        month: int,
    ) -> list[HistoricalKlineRow]:
        """Fetch one monthly kline archive and normalize it into typed rows."""
        partition = f"{year:04d}-{month:02d}"
        filename = f"{symbol}-{timeframe}-{partition}.zip"
        url = f"{BINANCE_DATA_BASE}/klines/{symbol}/{timeframe}/{filename}"
        payload = _fetch_bytes(url, timeout_s=self._config.request_timeout_s)
        if payload is None:
            raise RuntimeError(f"failed to download Binance kline archive: {url}")

        rows: list[HistoricalKlineRow] = []
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            csv_name = archive.namelist()[0]
            with archive.open(csv_name) as raw:
                decoded = io.TextIOWrapper(raw, encoding="utf-8")
                reader = csv.reader(decoded)
                for line in reader:
                    if len(line) < 9:
                        continue
                    open_ms = int(line[0])
                    close_ms = int(line[6])
                    rows.append(
                        HistoricalKlineRow(
                            symbol=symbol,
                            timeframe=timeframe,
                            open_time_ns=open_ms * 1_000_000,
                            close_time_ns=(close_ms + 1) * 1_000_000,
                            open=float(line[1]),
                            high=float(line[2]),
                            low=float(line[3]),
                            close=float(line[4]),
                            volume=float(line[5]),
                            trade_count=int(line[8]),
                        )
                    )
        return rows

    def _download_funding(
        self,
        *,
        symbol: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[HistoricalFundingRow]:
        """Page through Binance funding history within the requested window."""
        start_ms = int(start_dt.timestamp() * 1_000)
        end_ms = int(end_dt.timestamp() * 1_000)
        rows: list[HistoricalFundingRow] = []
        cursor_ms = start_ms
        while cursor_ms < end_ms:
            payload = _fetch_json(
                f"{BINANCE_REST_BASE}/fapi/v1/fundingRate",
                timeout_s=self._config.request_timeout_s,
                query={
                    "symbol": symbol,
                    "startTime": str(cursor_ms),
                    "endTime": str(end_ms),
                    "limit": "1000",
                },
            )
            if not isinstance(payload, list) or not payload:
                break
            for item in payload:
                if not isinstance(item, dict):
                    continue
                funding_time_ms = int(item.get("fundingTime", 0) or 0)
                if funding_time_ms <= 0:
                    continue
                rows.append(
                    HistoricalFundingRow(
                        symbol=symbol,
                        funding_time_ns=funding_time_ms * 1_000_000,
                        funding_rate=float(item.get("fundingRate", 0.0) or 0.0),
                    )
                )
                cursor_ms = max(cursor_ms, funding_time_ms + 1)
            if len(payload) < 1000:
                break
        return rows

    def _download_open_interest(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[HistoricalOpenInterestRow]:
        """Page through Binance open-interest history within the requested window."""
        start_ms = int(start_dt.timestamp() * 1_000)
        end_ms = int(end_dt.timestamp() * 1_000)
        rows: list[HistoricalOpenInterestRow] = []
        cursor_ms = start_ms
        while cursor_ms < end_ms:
            payload = _fetch_json(
                f"{BINANCE_REST_BASE}/futures/data/openInterestHist",
                timeout_s=self._config.request_timeout_s,
                query={
                    "symbol": symbol,
                    "period": timeframe,
                    "startTime": str(cursor_ms),
                    "endTime": str(end_ms),
                    "limit": "500",
                },
            )
            if not isinstance(payload, list) or not payload:
                break
            for item in payload:
                if not isinstance(item, dict):
                    continue
                ts_ms = int(item.get("timestamp", 0) or 0)
                if ts_ms <= 0:
                    continue
                rows.append(
                    HistoricalOpenInterestRow(
                        symbol=symbol,
                        timeframe=timeframe,
                        ts_ns=ts_ms * 1_000_000,
                        open_interest=float(item.get("sumOpenInterest", 0.0) or 0.0),
                    )
                )
                cursor_ms = max(cursor_ms, ts_ms + 1)
            if len(payload) < 500:
                break
        return rows


def _fetch_bytes(url: str, *, timeout_s: float) -> bytes | None:
    """Download raw bytes with a stable user agent and soft failure semantics."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "zenquant/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _fetch_json(url: str, *, timeout_s: float, query: dict[str, str]) -> object | None:
    """Download JSON payloads for historical REST pagination."""
    encoded = urllib.parse.urlencode(query)
    payload = _fetch_bytes(f"{url}?{encoded}", timeout_s=timeout_s)
    if payload is None:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None
