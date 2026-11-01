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


@pytest.mark.parametrize(
    ("data_type", "row", "time_column", "value_column", "value"),
    [
        (
            "klines",
            "1577836800000,7189.43,7239.74,7170.15,7221.65,12055.759,1577851199999,86800000,12345,6000,43000000,0\n",
            "open_time_ns",
            "open",
            7189.43,
        ),
        ("funding", "BTCUSDT,1577836800000,0.0001,7200\n", "funding_time_ns", "funding_rate", 0.0001),
        ("open_interest", "BTCUSDT,1000,100000000,1577836800000\n", "ts_ns", "sum_open_interest", 1000),
    ],
)
def test_headerless_csv_preserves_first_record(
    tmp_path: Path, data_type: str, row: str, time_column: str, value_column: str, value: float
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.csv").write_text(row, encoding="utf-8")

    assert csv_to_parquet(source, tmp_path / "output", data_type) == 1

    frame = pd.read_parquet(tmp_path / "output/data.parquet")
    assert frame[time_column].tolist() == [1577836800000000000]
    assert frame[value_column].tolist() == [value]


@pytest.mark.parametrize(
    "bad_csv",
    [
        "",
        "open_time,open,high,low\n1577836800000,1,2,0\n",
        "1577836800000,nan,2,0,1,10,1577851199999,10,1,1,1,0\n",
        "1577836800000,inf,2,0,1,10,1577851199999,10,1,1,1,0\n",
    ],
)
def test_malformed_csv_preserves_existing_output(tmp_path: Path, bad_csv: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_kline_csv(source / "a-valid.csv", open_time_ms=1577836800000, close_time_ms=1577851199999)
    (source / "z-broken.csv").write_text(bad_csv, encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    existing = output / "data.parquet"
    existing.write_bytes(b"previous output")

    with pytest.raises(ValueError):
        csv_to_parquet(source, output, "klines")

    assert existing.read_bytes() == b"previous output"


@pytest.mark.parametrize("rate", ["-0.00012359", "broken", "nan", "inf"])
def test_current_funding_schema_validates_rates(tmp_path: Path, rate: str) -> None:
    source = tmp_path / "BTCUSDT"
    source.mkdir()
    (source / "funding.csv").write_text(
        f"calc_time,funding_interval_hours,last_funding_rate\n1577836800000,8,{rate}\n", encoding="utf-8"
    )
    output = tmp_path / "output"
    if rate != "-0.00012359":
        with pytest.raises(ValueError):
            csv_to_parquet(source, output, "funding")
        assert not (output / "data.parquet").exists()
        return

    assert csv_to_parquet(source, output, "funding") == 1
    frame = pd.read_parquet(output / "data.parquet")
    assert frame["funding_time_ns"].tolist() == [1577836800000000000]
    assert frame["funding_rate"].tolist() == [-0.00012359]


def test_binance_kline_header_aliases_are_recognized(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "klines.csv").write_text(
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
        "1577836800000,7189.43,7239.74,7170.15,7221.65,12055.759,1577851199999,86800000,12345,6000,43000000,0\n",
        encoding="utf-8",
    )

    assert csv_to_parquet(source, tmp_path / "output", "klines") == 1
    frame = pd.read_parquet(tmp_path / "output/data.parquet")
    assert frame["trade_count"].tolist() == [12345]
    assert frame["taker_buy_base"].tolist() == [6000]
