from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


def _load_converter():
    path = Path(__file__).parents[1] / "scripts" / "convert_csv_to_parquet.py"
    spec = importlib.util.spec_from_file_location("convert_csv_to_parquet", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


csv_to_parquet = _load_converter().csv_to_parquet


def _write_kline_csv(path: Path, *, open_time_ms: int, close_time_ms: int) -> None:
    path.write_text(
        "open_time,open,high,low,close,volume,close_time,quote_volume,trade_count,taker_buy_base,taker_buy_quote,ignore\n"
        f"{open_time_ms},100,110,90,105,10,{close_time_ms},1000,5,6,600,0\n",
        encoding="utf-8",
    )


def _write_funding_csv(path: Path, *, funding_time_ms: int) -> None:
    path.write_text(
        f"symbol,funding_time,funding_rate,mark_price\nBTCUSDT,{funding_time_ms},0.0001,100000\n",
        encoding="utf-8",
    )


def _write_open_interest_csv(path: Path, *, timestamp_ms: int) -> None:
    path.write_text(
        f"symbol,sum_open_interest,sum_open_interest_value,timestamp\nBTCUSDT,1000,100000000,{timestamp_ms}\n",
        encoding="utf-8",
    )


def test_csv_to_parquet_deduplicates_overlapping_monthly_and_daily_klines(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    _write_kline_csv(
        source_dir / "BTCUSDT-4h-2026-04.csv",
        open_time_ms=1_777_507_200_000,
        close_time_ms=1_777_521_599_999,
    )
    _write_kline_csv(
        source_dir / "BTCUSDT-4h-2026-04-30.csv",
        open_time_ms=1_777_507_200_000,
        close_time_ms=1_777_521_599_999,
    )

    rows = csv_to_parquet(source_dir, output_dir, "klines")

    result = pd.read_parquet(output_dir / "data.parquet")
    assert rows == 1
    assert result["close_time_ns"].tolist() == [1_777_521_599_999_000_000]


def test_csv_to_parquet_deduplicates_overlapping_funding_rows(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    _write_funding_csv(source_dir / "BTCUSDT-fundingRate-2026-04.csv", funding_time_ms=1_777_507_200_000)
    _write_funding_csv(source_dir / "BTCUSDT-fundingRate-2026-04-30.csv", funding_time_ms=1_777_507_200_000)

    rows = csv_to_parquet(source_dir, output_dir, "funding")

    result = pd.read_parquet(output_dir / "data.parquet")
    assert rows == 1
    assert result["funding_time_ns"].tolist() == [1_777_507_200_000_000_000]


def test_csv_to_parquet_deduplicates_overlapping_open_interest_rows(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    _write_open_interest_csv(source_dir / "BTCUSDT-openInterest-2026-04.csv", timestamp_ms=1_777_507_200_000)
    _write_open_interest_csv(
        source_dir / "BTCUSDT-openInterest-2026-04-30.csv",
        timestamp_ms=1_777_507_200_000,
    )

    rows = csv_to_parquet(source_dir, output_dir, "open_interest")

    result = pd.read_parquet(output_dir / "data.parquet")
    assert rows == 1
    assert result["ts_ns"].tolist() == [1_777_507_200_000_000_000]


def test_spot_daily_converter_uses_strict_conflict_detection(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    _write_kline_csv(
        source_dir / "BTCUSDT-1d-2026-04.csv",
        open_time_ms=1_777_507_200_000,
        close_time_ms=1_777_593_599_999,
    )
    conflicting = source_dir / "BTCUSDT-1d-2026-04-30.csv"
    _write_kline_csv(
        conflicting,
        open_time_ms=1_777_507_200_000,
        close_time_ms=1_777_593_599_999,
    )
    conflicting.write_text(conflicting.read_text(encoding="utf-8").replace(",105,10,", ",106,10,"), encoding="utf-8")

    with pytest.raises(ValueError, match="conflicting duplicate"):
        csv_to_parquet(
            source_dir,
            output_dir,
            "klines",
            source="binance_spot",
            timeframe="1d",
        )


@pytest.mark.parametrize(
    ("source", "timeframe"),
    [("binance", "1d"), ("binance_spot", "4h")],
)
def test_non_spot_daily_converter_preserves_legacy_last_row_wins(
    tmp_path: Path,
    source: str,
    timeframe: str,
) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    _write_kline_csv(
        source_dir / "first.csv",
        open_time_ms=1_777_507_200_000,
        close_time_ms=1_777_521_599_999,
    )
    second = source_dir / "second.csv"
    _write_kline_csv(
        second,
        open_time_ms=1_777_507_200_000,
        close_time_ms=1_777_521_599_999,
    )
    second.write_text(second.read_text(encoding="utf-8").replace(",105,10,", ",106,10,"), encoding="utf-8")

    rows = csv_to_parquet(
        source_dir,
        output_dir,
        "klines",
        source=source,
        timeframe=timeframe,
    )

    assert rows == 1
    assert pd.read_parquet(output_dir / "data.parquet")["close"].tolist() == [106]
