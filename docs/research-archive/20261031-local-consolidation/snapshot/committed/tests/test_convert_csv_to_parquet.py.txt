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


CONVERTER = _load_converter()
csv_to_parquet = CONVERTER.csv_to_parquet


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


def _write_daily_range_csv(path: Path, open_times_ns: tuple[int, ...]) -> None:
    pd.DataFrame(
        {
            "open_time": [value // 1_000_000 for value in open_times_ns],
            "open": [100.0] * len(open_times_ns),
            "high": [110.0] * len(open_times_ns),
            "low": [90.0] * len(open_times_ns),
            "close": [105.0] * len(open_times_ns),
            "volume": [10.0] * len(open_times_ns),
            "close_time": [(value + 86_400_000_000_000) // 1_000_000 - 1 for value in open_times_ns],
            "quote_volume": [1000.0] * len(open_times_ns),
            "trade_count": [5] * len(open_times_ns),
            "taker_buy_base": [6.0] * len(open_times_ns),
            "taker_buy_quote": [600.0] * len(open_times_ns),
            "ignore": [0] * len(open_times_ns),
        }
    ).to_csv(path, index=False)


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


def test_spot_daily_converter_applies_explicit_open_date_bounds(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    derived_path = tmp_path / "derived" / "data.parquet"
    source_dir.mkdir()
    for index, open_time_ms in enumerate((1_777_420_800_000, 1_777_507_200_000, 1_777_593_600_000)):
        _write_kline_csv(
            source_dir / f"row-{index}.csv",
            open_time_ms=open_time_ms,
            close_time_ms=open_time_ms + 86_400_000 - 1,
        )

    rows = csv_to_parquet(
        source_dir,
        source_dir,
        "klines",
        source="binance_spot",
        timeframe="1d",
        start_open_ns=1_777_507_200_000_000_000,
        end_open_ns=1_777_593_600_000_000_000,
        spot_daily_output_path=derived_path,
    )

    assert rows == 1
    assert pd.read_parquet(derived_path)["open_time_ns"].tolist() == [1_777_507_200_000_000_000]
    assert not (source_dir / "data.parquet").exists()


def test_converter_rejects_bounds_on_legacy_paths_and_parses_utc_dates() -> None:
    with pytest.raises(ValueError, match="only valid for binance_spot 1d"):
        csv_to_parquet(
            Path("unused"),
            Path("unused"),
            "klines",
            source="binance",
            timeframe="1d",
            start_open_ns=1,
            end_open_ns=2,
        )

    assert CONVERTER.parse_utc_open_date("2025-10-08") == 1_759_881_600_000_000_000
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        CONVERTER.parse_utc_open_date("2025-10-08T04:00:00+04:00")

    with pytest.raises(ValueError, match="both"):
        csv_to_parquet(
            Path("unused"),
            Path("unused"),
            "klines",
            source="binance_spot",
            timeframe="1d",
            start_open_ns=1_759_881_600_000_000_000,
        )
    with pytest.raises(ValueError, match="non-empty"):
        csv_to_parquet(
            Path("unused"),
            Path("unused"),
            "klines",
            source="binance_spot",
            timeframe="1d",
            start_open_ns=1_787_529_600_000_000_000,
            end_open_ns=1_759_881_600_000_000_000,
        )
    with pytest.raises(ValueError, match="isolated output"):
        csv_to_parquet(
            Path("unused"),
            Path("unused"),
            "klines",
            source="binance_spot",
            timeframe="1d",
            start_open_ns=1_759_881_600_000_000_000,
            end_open_ns=1_787_529_600_000_000_000,
        )


def test_bounded_spot_converter_rejects_shared_canonical_output(tmp_path: Path) -> None:
    source_dir = tmp_path / "data/raw/binance_spot/klines/BTCUSDT/1d"
    source_dir.mkdir(parents=True)
    _write_kline_csv(
        source_dir / "daily.csv",
        open_time_ms=1_777_507_200_000,
        close_time_ms=1_777_593_599_999,
    )

    with pytest.raises(ValueError, match="canonical raw"):
        csv_to_parquet(
            source_dir,
            source_dir,
            "klines",
            source="binance_spot",
            timeframe="1d",
            start_open_ns=1_777_507_200_000_000_000,
            end_open_ns=1_777_593_600_000_000_000,
            spot_daily_output_path=source_dir / "data.parquet",
        )


def test_bounded_cli_rejects_canonical_output_before_writing(tmp_path: Path, monkeypatch) -> None:
    daily_dir = tmp_path / "data/raw/binance_spot/klines/BTCUSDT/1d"
    daily_dir.mkdir(parents=True)
    _write_kline_csv(
        daily_dir / "daily.csv",
        open_time_ms=1_777_507_200_000,
        close_time_ms=1_777_593_599_999,
    )
    canonical = daily_dir / "data.parquet"
    canonical.write_bytes(b"canonical-full-history")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        CONVERTER.main(
            [
                "--source",
                "binance_spot",
                "--timeframes",
                "1d",
                "--start-open-date",
                "2026-04-30",
                "--end-open-date-exclusive",
                "2026-05-01",
                "--spot-daily-output-path",
                "data/raw/binance_spot/klines/BTCUSDT/1d/data.parquet",
            ]
        )

    assert canonical.read_bytes() == b"canonical-full-history"


def test_bounded_cli_writes_only_isolated_1d_output(tmp_path: Path, monkeypatch) -> None:
    day_ns = 86_400_000_000_000
    start_ns = 1_759_881_600_000_000_000
    end_ns = 1_787_529_600_000_000_000
    daily_dir = tmp_path / "data/raw/binance_spot/klines/BTCUSDT/1d"
    other_dir = tmp_path / "data/raw/binance_spot/klines/BTCUSDT/4h"
    daily_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    _write_daily_range_csv(daily_dir / "monthly.csv", tuple(range(start_ns - 7 * day_ns, end_ns, day_ns)))
    _write_kline_csv(
        other_dir / "four-hour.csv",
        open_time_ms=start_ns // 1_000_000,
        close_time_ms=(start_ns + 4 * 3_600_000_000_000) // 1_000_000 - 1,
    )
    canonical = daily_dir / "data.parquet"
    other = other_dir / "data.parquet"
    canonical.write_bytes(b"canonical-full-history")
    other.write_bytes(b"other-timeframe")
    derived = tmp_path / "data/derived/trend_research/20260824/BTCUSDT/1d/data.parquet"
    monkeypatch.chdir(tmp_path)

    CONVERTER.main(
        [
            "--source",
            "binance_spot",
            "--symbol",
            "BTCUSDT",
            "--timeframes",
            "1d",
            "--start-open-date",
            "2025-10-08",
            "--end-open-date-exclusive",
            "2026-08-24",
            "--spot-daily-output-path",
            str(derived),
        ]
    )

    assert canonical.read_bytes() == b"canonical-full-history"
    assert other.read_bytes() == b"other-timeframe"
    frame = pd.read_parquet(derived)
    assert len(frame) == 320
    assert frame["open_time_ns"].min() == start_ns
    assert frame["open_time_ns"].max() == end_ns - day_ns


def test_timeframe_parser_rejects_unsupported_or_duplicate_values() -> None:
    assert CONVERTER.parse_timeframes("1d") == ("1d",)
    with pytest.raises(ValueError, match="unsupported"):
        CONVERTER.parse_timeframes("2h")
    with pytest.raises(ValueError, match="duplicate"):
        CONVERTER.parse_timeframes("1d,1d")


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
