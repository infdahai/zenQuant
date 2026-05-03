from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pandas as pd


def _load_module():
    path = Path("scripts/audit_binance_metrics_factors.py")
    spec = importlib.util.spec_from_file_location("audit_binance_metrics_factors", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load audit_binance_metrics_factors")
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_binance_metrics_factors"] = module
    spec.loader.exec_module(module)
    return module


def test_binance_metrics_audit_writes_features_and_blocks_redundant_factor() -> None:
    module = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        metrics_dir = root / "metrics"
        btc_dir = root / "btc"
        metrics_dir.mkdir(parents=True)
        btc_dir.mkdir(parents=True)
        periods = 900
        ts = pd.date_range("2023-01-01 03:55:00", periods=periods, freq="4h", tz="UTC")
        trend = [idx / periods for idx in range(periods)]
        metrics = pd.DataFrame(
            {
                "create_time": [item.strftime("%Y-%m-%d %H:%M:%S") for item in ts],
                "symbol": "BTCUSDT",
                "sum_open_interest": [1000 + idx * 3 for idx in range(periods)],
                "sum_open_interest_value": [10_000 + idx * 30 for idx in range(periods)],
                "count_toptrader_long_short_ratio": [1.0 + value * 0.1 for value in trend],
                "sum_toptrader_long_short_ratio": [1.0 + value * 0.2 for value in trend],
                "count_long_short_ratio": [1.0 + value * 0.15 for value in trend],
                "sum_taker_long_short_vol_ratio": [1.0 + value * 0.05 for value in trend],
            }
        )
        metrics.to_parquet(metrics_dir / "2023-01.parquet", index=False)
        closes = [10_000 + idx * 2 + (idx % 12) for idx in range(periods + 2)]
        btc = pd.DataFrame(
            {
                "close_time_ns": [
                    item.value
                    for item in pd.date_range("2023-01-01 04:00:00", periods=periods + 2, freq="4h", tz="UTC")
                ],
                "close": closes,
            }
        )
        btc.to_parquet(btc_dir / "2023-01.parquet", index=False)

        payload = module.build_binance_metrics_factor_audit(
            metrics_dir=metrics_dir,
            btc_4h_dir=btc_dir,
            train_end="2023-06-01T00:00:00+00:00",
            corr_threshold=0.75,
            min_abs_rank_ic=0.0,
            min_abs_ablation_delta=0.0,
        )

        assert Path(payload["features_path"]).exists()
        assert payload["summary"]["factor_count"] > 0
        assert payload["summary"]["high_correlation_blocked_count"] >= 1


def test_binance_metrics_audit_marks_missing_inputs_blocked() -> None:
    module = _load_module()
    payload = module.build_binance_metrics_factor_audit(
        metrics_dir=Path("/tmp/missing_metrics"),
        btc_4h_dir=Path("/tmp/missing_btc_4h"),
        train_end="2023-12-31T23:59:59+00:00",
        corr_threshold=0.75,
        min_abs_rank_ic=0.01,
        min_abs_ablation_delta=0.005,
    )

    assert payload["summary"]["blocked_by"] == "binance_metrics_or_btc_4h_missing"
    assert payload["summary"]["admissible_factor_count"] == 0
