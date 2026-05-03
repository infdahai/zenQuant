from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pandas as pd


def _load_module():
    path = Path("scripts/audit_coinmetrics_onchain_factors.py")
    spec = importlib.util.spec_from_file_location("audit_coinmetrics_onchain_factors", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load audit_coinmetrics_onchain_factors")
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_coinmetrics_onchain_factors"] = module
    spec.loader.exec_module(module)
    return module


def test_coinmetrics_audit_writes_second_order_features_and_blocks_redundancy() -> None:
    module = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dates = pd.date_range("2020-01-01", periods=430, freq="D", tz="UTC")
        coinmetrics = pd.DataFrame(
            {
                "ts_ns": dates.astype("int64"),
                "AdrActCnt": [1000 + idx * 2 for idx in range(len(dates))],
                "TxCnt": [2000 + idx * 3 for idx in range(len(dates))],
                "SplyCur": [18_000_000 + idx for idx in range(len(dates))],
            }
        )
        btc = pd.DataFrame(
            {
                "close_time_ns": dates.astype("int64"),
                "close": [10_000 + idx * 10 for idx in range(len(dates))],
            }
        )
        coinmetrics_path = root / "coinmetrics.parquet"
        btc_path = root / "btc.parquet"
        coinmetrics.to_parquet(coinmetrics_path, index=False)
        btc.to_parquet(btc_path, index=False)

        payload = module.build_coinmetrics_factor_audit(
            coinmetrics_path=coinmetrics_path,
            btc_daily_path=btc_path,
            train_end="2021-03-01",
            corr_threshold=0.75,
            min_rank_ic=0.01,
        )

        assert Path(payload["features_path"]).exists()
        derived = pd.read_parquet(payload["features_path"])
        assert derived["date"].max() > pd.Timestamp("2021-03-01", tz="UTC")
        assert payload["summary"]["factor_count"] == 3
        assert any(factor["blocked_by"] == "high_factor_correlation" for factor in payload["factors"])


def test_coinmetrics_audit_marks_missing_inputs_blocked() -> None:
    module = _load_module()
    payload = module.build_coinmetrics_factor_audit(
        coinmetrics_path=Path("/tmp/missing_coinmetrics.parquet"),
        btc_daily_path=Path("/tmp/missing_btc.parquet"),
        train_end="2023-12-31",
        corr_threshold=0.75,
        min_rank_ic=0.01,
    )

    assert payload["summary"]["blocked_by"] == "coinmetrics_or_btc_daily_missing"
    assert payload["summary"]["admissible_factor_count"] == 0
