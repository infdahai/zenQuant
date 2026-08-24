from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pandas as pd
import pytest

from zenquant.data.spot_daily import (
    DAY_NS,
    SpotDailyError,
    audit_spot_daily,
    normalize_spot_daily_csvs,
)

START_NS = 1_767_225_600_000_000_000


def _write_daily_csv(
    path: Path,
    *,
    open_time: int,
    close_time: int,
    close: float = 105.0,
    high: float = 110.0,
    low: float = 90.0,
) -> None:
    path.write_text(
        "open_time,open,high,low,close,volume,close_time,quote_volume,trade_count,"
        "taker_buy_base,taker_buy_quote,ignore\n"
        f"{open_time},100,{high},{low},{close},10,{close_time},1000,5,6,600,0\n",
        encoding="utf-8",
    )


def _write_parquet(path: Path, open_times: tuple[int, ...]) -> None:
    pd.DataFrame(
        {
            "open_time_ns": open_times,
            "close_time_ns": tuple(value + DAY_NS - 1_000_000 for value in open_times),
        }
    ).to_parquet(path, index=False)


def test_exact_archive_overlap_deduplicates_and_records_ordered_source_digests(tmp_path: Path) -> None:
    first = tmp_path / "b.csv"
    second = tmp_path / "a.csv"
    _write_daily_csv(
        first,
        open_time=START_NS // 1_000_000,
        close_time=(START_NS + DAY_NS) // 1_000_000 - 1,
    )
    _write_daily_csv(
        second,
        open_time=START_NS // 1_000,
        close_time=(START_NS + DAY_NS) // 1_000 - 1,
    )

    result = normalize_spot_daily_csvs((first, second), tmp_path / "data.parquet")

    assert result.row_count == 1
    assert result.first_open_ns == START_NS
    assert result.last_close_ns == START_NS + DAY_NS - 1
    assert result.raw_source_sha256 == (
        ("a.csv", hashlib.sha256(second.read_bytes()).hexdigest()),
        ("b.csv", hashlib.sha256(first.read_bytes()).hexdigest()),
    )
    assert result.normalized_sha256 == hashlib.sha256((tmp_path / "data.parquet").read_bytes()).hexdigest()
    normalized_audit = audit_spot_daily(tmp_path / "data.parquet", START_NS, START_NS + DAY_NS)
    assert normalized_audit.last_close_ns == START_NS + DAY_NS - 1
    with pytest.raises(FrozenInstanceError):
        result.row_count = 2  # type: ignore[misc]
    assert result.__slots__


def test_conflicting_archive_overlap_fails_without_publishing_output(tmp_path: Path) -> None:
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    for path, close in ((first, 105.0), (second, 106.0)):
        _write_daily_csv(
            path,
            open_time=START_NS // 1_000_000,
            close_time=(START_NS + DAY_NS) // 1_000_000 - 1,
            close=close,
        )

    output = tmp_path / "data.parquet"
    with pytest.raises(SpotDailyError, match="conflicting duplicate"):
        normalize_spot_daily_csvs((first, second), output)

    assert not output.exists()


@pytest.mark.parametrize(
    ("high", "low", "close", "message"),
    [
        (99.0, 90.0, 105.0, "candle bounds"),
        (110.0, 101.0, 105.0, "candle bounds"),
        (110.0, 90.0, float("nan"), "positive finite OHLC"),
        (110.0, 0.0, 105.0, "positive finite OHLC"),
    ],
)
def test_normalization_rejects_invalid_ohlc(
    tmp_path: Path,
    high: float,
    low: float,
    close: float,
    message: str,
) -> None:
    source = tmp_path / "bad.csv"
    _write_daily_csv(
        source,
        open_time=START_NS // 1_000_000,
        close_time=(START_NS + DAY_NS) // 1_000_000 - 1,
        high=high,
        low=low,
        close=close,
    )

    with pytest.raises(SpotDailyError, match=message):
        normalize_spot_daily_csvs((source,), tmp_path / "data.parquet")


def test_normalization_rejects_non_utc_daily_open_and_out_of_candle_close(tmp_path: Path) -> None:
    shifted = tmp_path / "shifted.csv"
    _write_daily_csv(
        shifted,
        open_time=START_NS // 1_000_000 + 1,
        close_time=(START_NS + DAY_NS) // 1_000_000 - 1,
    )
    with pytest.raises(SpotDailyError, match="UTC day"):
        normalize_spot_daily_csvs((shifted,), tmp_path / "shifted.parquet")

    overlong = tmp_path / "overlong.csv"
    _write_daily_csv(
        overlong,
        open_time=START_NS // 1_000_000,
        close_time=(START_NS + DAY_NS) // 1_000_000,
    )
    with pytest.raises(SpotDailyError, match="close_time"):
        normalize_spot_daily_csvs((overlong,), tmp_path / "overlong.parquet")


def test_audit_reports_missing_utc_days_and_duplicate_opens(tmp_path: Path) -> None:
    gapped = tmp_path / "gapped.parquet"
    _write_parquet(gapped, (START_NS, START_NS + 2 * DAY_NS))

    gap_audit = audit_spot_daily(gapped, START_NS, START_NS + 3 * DAY_NS)

    assert gap_audit.status == "INSUFFICIENT_DATA_SPOT_DAILY_GAPS"
    assert gap_audit.missing_open_ns == (START_NS + DAY_NS,)
    assert gap_audit.duplicate_open_ns == ()
    assert gap_audit.sha256 == hashlib.sha256(gapped.read_bytes()).hexdigest()

    duplicate = tmp_path / "duplicate.parquet"
    _write_parquet(duplicate, (START_NS, START_NS))
    duplicate_audit = audit_spot_daily(duplicate, START_NS, START_NS + DAY_NS)
    assert duplicate_audit.status == "INSUFFICIENT_DATA_SPOT_DAILY_GAPS"
    assert duplicate_audit.duplicate_open_ns == (START_NS,)


def test_audit_is_ready_only_for_exact_half_open_daily_window(tmp_path: Path) -> None:
    path = tmp_path / "ready.parquet"
    _write_parquet(path, (START_NS, START_NS + DAY_NS))

    audit = audit_spot_daily(path, START_NS, START_NS + 2 * DAY_NS)

    assert audit.status == "READY"
    assert audit.row_count == 2
    assert audit.first_open_ns == START_NS
    assert audit.last_close_ns == START_NS + 2 * DAY_NS - 1_000_000
    assert audit.missing_open_ns == ()
    assert audit.duplicate_open_ns == ()
    with pytest.raises(FrozenInstanceError):
        audit.status = "changed"  # type: ignore[misc]
    assert audit.__slots__
