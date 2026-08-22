from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def _load_module():
    path = Path("scripts/download_aggtrades.py")
    spec = importlib.util.spec_from_file_location("download_aggtrades", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load download_aggtrades")
    module = importlib.util.module_from_spec(spec)
    sys.modules["download_aggtrades"] = module
    spec.loader.exec_module(module)
    return module


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_incremental_aggregation_preserves_existing_buckets_and_filters_raw_dates(tmp_path: Path) -> None:
    module = _load_module()
    base_dir = tmp_path / "BTCUSDT"
    old_bucket_ns = int(datetime(2026, 4, 2, tzinfo=UTC).timestamp() * 1_000_000_000)
    skipped_raw_ns = old_bucket_ns + module.FOUR_HOURS_NS
    new_bucket_ns = int(datetime(2026, 4, 3, tzinfo=UTC).timestamp() * 1_000_000_000)
    _write_rows(
        base_dir / "4h" / "data.parquet",
        [
            {
                "ts_ns": old_bucket_ns,
                "buy_volume": 9.0,
                "sell_volume": 3.0,
                "cvd_delta": 6.0,
                "buy_usd": 900.0,
                "sell_usd": 300.0,
                "trade_count": 2,
                "large_trade_count": 0,
                "vpin": 0.5,
            }
        ],
    )
    _write_rows(
        base_dir / "raw" / "2026-04-02.parquet",
        [{"ts_ns": skipped_raw_ns, "price": 99.0, "qty": 5.0, "is_buyer_maker": False}],
    )
    _write_rows(
        base_dir / "raw" / "2026-04-03.parquet",
        [
            {"ts_ns": new_bucket_ns + 300_000_000_000, "price": 100.0, "qty": 2.0, "is_buyer_maker": False},
            {"ts_ns": new_bucket_ns + 600_000_000_000, "price": 110.0, "qty": 1.0, "is_buyer_maker": True},
        ],
    )

    count = module.aggregate_to_timeframe(
        "BTCUSDT",
        base_dir,
        "4h",
        start_date=datetime(2026, 4, 3, tzinfo=UTC),
    )

    rows = pq.read_table(base_dir / "4h" / "data.parquet").to_pylist()
    assert count == 2
    assert [row["ts_ns"] for row in rows] == [old_bucket_ns, new_bucket_ns]
    assert rows[0]["cvd_delta"] == 6.0
    assert rows[1] == {
        "ts_ns": new_bucket_ns,
        "buy_volume": 2.0,
        "sell_volume": 1.0,
        "cvd_delta": 1.0,
        "buy_usd": 200.0,
        "sell_usd": 110.0,
        "trade_count": 2,
        "large_trade_count": 0,
        "vpin": 0.333333,
    }
