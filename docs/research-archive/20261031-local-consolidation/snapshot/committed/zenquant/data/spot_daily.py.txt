"""Strict, deterministic normalization for Binance BTC spot daily archives."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pandas as pd

DAY_NS: Final = 86_400_000_000_000

_KLINE_COLUMNS: Final = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
)
_PRICE_COLUMNS: Final = ("open", "high", "low", "close")
_VALUE_COLUMNS: Final = (
    "open_time_ns",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_ns",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
)


class SpotDailyError(ValueError):
    """Raised when spot-daily source data violates the frozen contract."""


@dataclass(frozen=True, slots=True)
class SpotDailyNormalizationResult:
    """Identity and bounds of one atomically published normalized corpus."""

    output_path: Path
    row_count: int
    first_open_ns: int | None
    last_close_ns: int | None
    raw_source_sha256: tuple[tuple[str, str], ...]
    normalized_sha256: str


@dataclass(frozen=True, slots=True)
class SpotDailyAudit:
    """Daily-grid coverage audit for a normalized spot corpus."""

    status: str
    row_count: int
    first_open_ns: int | None
    last_close_ns: int | None
    missing_open_ns: tuple[int, ...]
    duplicate_open_ns: tuple[int, ...]
    sha256: str


def normalize_spot_daily_csvs(
    paths: Sequence[Path],
    output_path: Path,
    *,
    start_open_ns: int | None = None,
    end_open_ns: int | None = None,
) -> SpotDailyNormalizationResult:
    """Normalize Binance millisecond/microsecond daily archives fail-closed."""
    _validate_bounds(start_open_ns, end_open_ns)
    ordered_paths = tuple(sorted((Path(path) for path in paths), key=lambda path: str(path)))
    if not ordered_paths:
        raise SpotDailyError("at least one spot daily CSV is required")

    source_digests = tuple((path.name, _sha256_file(path)) for path in ordered_paths)
    frames = tuple(_read_daily_csv(path) for path in ordered_paths)
    combined = pd.concat(frames, ignore_index=True)
    _reject_conflicting_duplicates(combined)
    normalized = (
        combined.sort_values(["open_time_ns"], kind="mergesort")
        .drop_duplicates(subset=["open_time_ns"], keep="first")
        .loc[:, _VALUE_COLUMNS]
        .reset_index(drop=True)
    )
    if start_open_ns is not None and end_open_ns is not None:
        normalized = normalized[
            (normalized["open_time_ns"] >= start_open_ns) & (normalized["open_time_ns"] < end_open_ns)
        ].reset_index(drop=True)
        if normalized.empty:
            raise SpotDailyError("explicit spot daily bounds contain no rows")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _temporary_sibling(output_path)
    try:
        normalized.to_parquet(temporary_path, index=False)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return SpotDailyNormalizationResult(
        output_path=output_path,
        row_count=len(normalized),
        first_open_ns=int(normalized["open_time_ns"].iloc[0]) if not normalized.empty else None,
        last_close_ns=int(normalized["close_time_ns"].iloc[-1]) if not normalized.empty else None,
        raw_source_sha256=source_digests,
        normalized_sha256=_sha256_file(output_path),
    )


def _validate_bounds(start_open_ns: int | None, end_open_ns: int | None) -> None:
    if (start_open_ns is None) != (end_open_ns is None):
        raise SpotDailyError("start_open_ns and end_open_ns must both be provided")
    if start_open_ns is None or end_open_ns is None:
        return
    if start_open_ns % DAY_NS or end_open_ns % DAY_NS:
        raise SpotDailyError("spot daily bounds must be UTC daily open timestamps")
    if start_open_ns >= end_open_ns:
        raise SpotDailyError("spot daily bounds must form a non-empty half-open interval")


def audit_spot_daily(path: Path, start_ns: int, end_ns: int) -> SpotDailyAudit:
    """Audit half-open UTC daily coverage without filling or hiding gaps."""
    if start_ns >= end_ns or start_ns % DAY_NS or end_ns % DAY_NS:
        raise SpotDailyError("audit window must be a non-empty UTC daily half-open interval")
    path = Path(path)
    digest = _sha256_file(path)
    frame = pd.read_parquet(path)
    required = {"open_time_ns", "close_time_ns"}
    missing_columns = sorted(required - set(frame.columns))
    if missing_columns:
        raise SpotDailyError(f"normalized parquet missing columns: {missing_columns}")

    open_times = tuple(int(value) for value in frame["open_time_ns"])
    counts = pd.Series(open_times, dtype="int64").value_counts()
    duplicate_open_ns = tuple(sorted(int(value) for value in counts[counts > 1].index))
    expected = set(range(start_ns, end_ns, DAY_NS))
    observed = set(open_times)
    missing_open_ns = tuple(sorted(expected - observed))
    invalid_grid = any(value % DAY_NS for value in open_times)
    status = (
        "READY"
        if not missing_open_ns and not duplicate_open_ns and not invalid_grid
        else "INSUFFICIENT_DATA_SPOT_DAILY_GAPS"
    )
    ordered = frame.sort_values("open_time_ns", kind="mergesort")
    return SpotDailyAudit(
        status=status,
        row_count=len(frame),
        first_open_ns=int(ordered["open_time_ns"].iloc[0]) if not ordered.empty else None,
        last_close_ns=int(ordered["close_time_ns"].iloc[-1]) if not ordered.empty else None,
        missing_open_ns=missing_open_ns,
        duplicate_open_ns=duplicate_open_ns,
        sha256=digest,
    )


def _read_daily_csv(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise SpotDailyError(f"unable to read spot daily CSV {path.name}: {exc}") from exc
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if not set(_KLINE_COLUMNS).issubset(frame.columns):
        try:
            frame = pd.read_csv(path, names=_KLINE_COLUMNS, header=None)
        except Exception as exc:
            raise SpotDailyError(f"unable to read spot daily CSV {path.name}: {exc}") from exc
    missing = sorted(set(_KLINE_COLUMNS) - set(frame.columns))
    if missing:
        raise SpotDailyError(f"spot daily CSV {path.name} missing columns: {missing}")
    if frame.empty:
        raise SpotDailyError(f"spot daily CSV {path.name} is empty")

    for column in _KLINE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[list(_PRICE_COLUMNS)].isna().any().any():
        raise SpotDailyError(f"spot daily CSV {path.name} requires positive finite OHLC")
    non_price_columns = [column for column in _KLINE_COLUMNS if column not in _PRICE_COLUMNS]
    if frame[non_price_columns].isna().any().any():
        raise SpotDailyError(f"spot daily CSV {path.name} contains non-numeric values")

    parsed_open = frame["open_time"].map(_exchange_timestamp_to_ns_and_unit)
    parsed_close = frame["close_time"].map(_exchange_timestamp_to_ns_and_unit)
    frame["open_time_ns"] = parsed_open.map(lambda item: item[0])
    open_units_ns = parsed_open.map(lambda item: item[1])
    raw_close_ns = parsed_close.map(lambda item: item[0])
    close_units_ns = parsed_close.map(lambda item: item[1])
    if not open_units_ns.equals(close_units_ns):
        raise SpotDailyError(f"spot daily CSV {path.name} contains mixed timestamp units")
    if open_units_ns.nunique(dropna=False) != 1:
        raise SpotDailyError(f"spot daily CSV {path.name} archive mixes timestamp units")
    if any(value % DAY_NS for value in frame["open_time_ns"]):
        raise SpotDailyError(f"spot daily CSV {path.name} open_time is not a UTC day")
    expected_end = frame["open_time_ns"] + DAY_NS
    expected_raw_close_ns = expected_end - open_units_ns
    if not raw_close_ns.equals(expected_raw_close_ns):
        raise SpotDailyError(f"spot daily CSV {path.name} close_time must be the exact daily close")
    frame["close_time_ns"] = expected_end - 1

    for row_index, row in frame.iterrows():
        prices = tuple(float(row[column]) for column in _PRICE_COLUMNS)
        if not all(math.isfinite(value) and value > 0 for value in prices):
            raise SpotDailyError(f"spot daily CSV {path.name} row {row_index} requires positive finite OHLC")
        open_price, high, low, close = prices
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise SpotDailyError(f"spot daily CSV {path.name} row {row_index} violates candle bounds")
    return frame.loc[:, _VALUE_COLUMNS]


def _reject_conflicting_duplicates(frame: pd.DataFrame) -> None:
    for open_time_ns, group in frame.groupby("open_time_ns", sort=True):
        if len(group) < 2:
            continue
        first = group.iloc[0]
        if not all(_rows_equal(first, group.iloc[index]) for index in range(1, len(group))):
            raise SpotDailyError(f"conflicting duplicate open_time_ns={int(open_time_ns)}")


def _rows_equal(left: pd.Series, right: pd.Series) -> bool:
    return all(left[column] == right[column] for column in _VALUE_COLUMNS)


def _exchange_timestamp_to_ns_and_unit(value: object) -> tuple[int, int]:
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise SpotDailyError("exchange timestamp must be a finite integer")
    parsed = int(numeric)
    if parsed < 1_000_000_000_000 or parsed >= 1_000_000_000_000_000_000:
        raise SpotDailyError("exchange timestamp must use milliseconds or microseconds")
    unit_ns = 1_000 if parsed >= 1_000_000_000_000_000 else 1_000_000
    return parsed * unit_ns, unit_ns


def _temporary_sibling(output_path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    os.close(descriptor)
    return Path(name)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
