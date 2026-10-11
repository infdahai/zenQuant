from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd


def _load_module():
    path = Path("scripts/download_binance_metrics_history.py")
    spec = importlib.util.spec_from_file_location("download_binance_metrics_history", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load download_binance_metrics_history")
    module = importlib.util.module_from_spec(spec)
    sys.modules["download_binance_metrics_history"] = module
    spec.loader.exec_module(module)
    return module


def test_aggregate_open_interest_4h_uses_last_metric_in_bucket() -> None:
    module = _load_module()
    frame = pd.DataFrame(
        [
            {
                "create_time": "2024-01-01 00:00:00",
                "sum_open_interest": 100.0,
            },
            {
                "create_time": "2024-01-01 03:55:00",
                "sum_open_interest": 150.0,
            },
            {
                "create_time": "2024-01-01 04:00:00",
                "sum_open_interest": 200.0,
            },
        ]
    )

    rows = module._aggregate_open_interest_4h(frame, symbol="BTCUSDT")

    assert len(rows) == 2
    assert rows[0].open_interest == 150.0
    assert rows[1].open_interest == 200.0
    assert rows[0].ts_ns == pd.Timestamp("2024-01-01 03:55:00", tz="UTC").value
    assert rows[0].timeframe == "4h"


def test_merge_metrics_month_preserves_existing_dates_and_updates_overlap(tmp_path: Path) -> None:
    module = _load_module()
    path = tmp_path / "2026-05.parquet"
    pd.DataFrame(
        [
            {"create_time": "2026-05-01 03:55:00", "sum_open_interest": 100.0},
            {"create_time": "2026-05-05 03:55:00", "sum_open_interest": 150.0},
        ]
    ).to_parquet(path, index=False)
    incoming = pd.DataFrame(
        [
            {"create_time": "2026-05-05 03:55:00", "sum_open_interest": 155.0},
            {"create_time": "2026-05-06 03:55:00", "sum_open_interest": 160.0},
        ]
    )

    merged = module._merge_metrics_month(path, incoming)

    assert merged["create_time"].tolist() == [
        "2026-05-01 03:55:00",
        "2026-05-05 03:55:00",
        "2026-05-06 03:55:00",
    ]
    overlap = merged.loc[merged["create_time"] == "2026-05-05 03:55:00", "sum_open_interest"]
    assert overlap.item() == 155.0


def test_open_interest_coverage_paths_include_existing_months(tmp_path: Path) -> None:
    module = _load_module()
    oi_dir = tmp_path / "open_interest" / "BTCUSDT" / "4h"
    oi_dir.mkdir(parents=True)
    january = oi_dir / "2026-01.parquet"
    may = oi_dir / "2026-05.parquet"
    january.touch()
    may.touch()
    (oi_dir / "notes.json").touch()

    paths = module._open_interest_paths_for_coverage(tmp_path, "BTCUSDT")

    assert paths == [str(january), str(may)]
