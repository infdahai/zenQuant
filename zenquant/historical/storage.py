"""Small Parquet utilities for historical market datasets.

The live runtime uses websocket events. Historical mode needs the same data
captured in a queryable, versionable form so replay can feed the exact same
decision pipeline. This module keeps the storage layer deliberately small:
typed rows, predictable file paths, and DuckDB-backed reads.
"""

from __future__ import annotations

import glob
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(slots=True, frozen=True)
class HistoricalKlineRow:
    """Single historical candle row stored in Parquet."""

    symbol: str
    timeframe: str
    open_time_ns: int
    close_time_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int


@dataclass(slots=True, frozen=True)
class HistoricalFundingRow:
    """Single funding observation aligned to an exchange timestamp."""

    symbol: str
    funding_time_ns: int
    funding_rate: float


@dataclass(slots=True, frozen=True)
class HistoricalOpenInterestRow:
    """Single open-interest observation for a symbol/period pair."""

    symbol: str
    timeframe: str
    ts_ns: int
    open_interest: float


@dataclass(slots=True, frozen=True)
class HistoricalDatasetLayout:
    """Canonical on-disk layout used by README Phase 2 historical tasks."""

    root_dir: Path

    def kline_path(self, symbol: str, timeframe: str, partition: str) -> Path:
        return self.root_dir / "klines" / symbol / timeframe / f"{partition}.parquet"

    def funding_path(self, symbol: str, partition: str) -> Path:
        return self.root_dir / "funding" / symbol / f"{partition}.parquet"

    def open_interest_path(self, symbol: str, timeframe: str, partition: str) -> Path:
        return self.root_dir / "open_interest" / symbol / timeframe / f"{partition}.parquet"


def write_parquet_rows(path: Path, rows: list[object]) -> None:
    """Persist typed rows to a single Parquet file.

    Example:
        rows = [HistoricalKlineRow(...), HistoricalKlineRow(...)]
        write_parquet_rows(Path("data/raw/binance/klines/BTCUSDT/4h/2024-01.parquet"), rows)
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [_row_to_dict(row) for row in rows]
    if not normalized:
        return
    table = pa.Table.from_pylist(normalized)
    pq.write_table(table, path)


def read_parquet_rows(
    path_glob: str,
    *,
    where_sql: str = "",
    params: tuple[Any, ...] = (),
    order_by: str = "",
) -> list[dict[str, Any]]:
    """Read Parquet rows with DuckDB so replay code can filter cheaply.

    The helper accepts a glob so callers can read a month or many months
    without assembling datasets manually.

    Example:
        rows = read_parquet_rows(
            "data/raw/binance/klines/BTCUSDT/4h/*.parquet",
            where_sql="close_time_ns >= ?",
            params=(1704067200000000000,),
            order_by="close_time_ns ASC",
        )
    """

    if not _glob_exists(path_glob):
        return []

    clauses = ["SELECT * FROM read_parquet(?)"]
    if where_sql:
        clauses.append(f"WHERE {where_sql}")
    if order_by:
        clauses.append(f"ORDER BY {order_by}")
    query = "\n".join(clauses)

    with duckdb.connect(database=":memory:") as conn:
        table = conn.execute(query, (path_glob, *params)).to_arrow_table()
    return table.to_pylist()


def _row_to_dict(row: object) -> dict[str, Any]:
    """Normalize dataclass or dict rows into a Parquet-writable record."""
    if is_dataclass(row):
        return asdict(row)
    if isinstance(row, dict):
        return dict(row)
    raise TypeError(f"unsupported parquet row type: {type(row)!r}")


def _glob_exists(path_glob: str) -> bool:
    """Guard DuckDB reads so empty globs behave like an empty dataset."""
    return bool(glob.glob(path_glob))
