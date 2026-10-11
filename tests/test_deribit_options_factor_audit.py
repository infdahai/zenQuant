from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pandas as pd


def _load_module():
    path = Path("scripts/audit_deribit_options_factors.py")
    spec = importlib.util.spec_from_file_location("audit_deribit_options_factors", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load audit_deribit_options_factors")
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_deribit_options_factors"] = module
    spec.loader.exec_module(module)
    return module


def test_deribit_options_factor_audit_builds_pit_train_metrics() -> None:
    module = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        options_dir = root / "deribit_options"
        btc_dir = root / "btc_4h"
        options_month = options_dir / "BTC" / "2023-01"
        options_month.mkdir(parents=True)
        btc_dir.mkdir(parents=True)

        periods = 240
        bar_closes = pd.date_range("2023-01-01 04:00:00", periods=periods + 1, freq="4h", tz="UTC")
        option_times = bar_closes[:-1] - pd.Timedelta(minutes=5)
        iv_driver = pd.Series([0.4 + (idx % 48) / 120 + idx / 5000 for idx in range(periods)])

        closes = [20_000.0]
        for idx in range(periods):
            centered = iv_driver.iloc[idx] - float(iv_driver.mean())
            forward_return = centered * 0.01 + ((idx % 7) - 3) * 0.00005
            closes.append(closes[-1] * (1.0 + forward_return))
        pd.DataFrame({"close_time_ns": [ts.value for ts in bar_closes], "close": closes}).to_parquet(
            btc_dir / "2023-01.parquet",
            index=False,
        )

        rows = []
        for idx, ts in enumerate(option_times):
            snapshot_ts_ns = ts.value
            spot = 20_000.0 + idx * 3.0
            base_iv = 55.0 + iv_driver.iloc[idx] * 20.0
            for dte, tenor_bump in ((7, -3.0), (30, 0.0), (90, 4.0)):
                expiry_ts_ms = int((ts + pd.Timedelta(days=dte)).value // 1_000_000)
                for option_type, strike_mult, skew_bump in (
                    ("put", 0.9, 2.0 + (idx % 17) / 20),
                    ("call", 1.0, 0.0),
                    ("call", 1.1, -1.0),
                ):
                    rows.append(
                        {
                            "snapshot_ts_ns": snapshot_ts_ns,
                            "currency": "BTC",
                            "instrument_name": f"BTC-{idx}-{dte}-{strike_mult}-{option_type}",
                            "expiry_ts_ms": expiry_ts_ms,
                            "expiry_date": (ts + pd.Timedelta(days=dte)).strftime("%Y-%m-%d"),
                            "strike": spot * strike_mult,
                            "option_type": option_type,
                            "underlying_price": spot,
                            "open_interest": 100.0 + idx % 23 + dte + (40.0 if option_type == "put" else 0.0),
                            "volume_usd": 10_000.0 + idx * (2 if option_type == "call" else 3),
                            "mark_iv": base_iv + tenor_bump + skew_bump,
                            "bid_price": 0.1,
                            "ask_price": 0.12,
                            "mid_price": 0.11,
                            "underlying_index": "BTC",
                            "settlement_period": "day",
                        }
                    )
        pd.DataFrame(rows).to_parquet(options_month / "options_chain_202301.parquet", index=False)

        payload = module.build_deribit_options_factor_audit(
            options_dir=options_dir,
            btc_4h_dir=btc_dir,
            currency="BTC",
            train_start="2023-01-01T00:00:00+00:00",
            train_end="2023-02-28T23:59:59+00:00",
            max_feature_age_hours=1.0,
            rolling_window=30,
            corr_threshold=0.95,
            min_train_observations=20,
            min_abs_rank_ic=0.0,
            min_abs_ablation_delta=0.0,
        )

        assert payload["research_only_non_promotable"] is True
        assert payload["pit_train_only"] is True
        assert Path(payload["features_path"]).exists()
        assert payload["summary"]["snapshot_count"] == periods
        assert payload["summary"]["train_aligned_observations"] > 100
        factor_names = {factor["name"] for factor in payload["factors"]}
        assert "iv_level_30d" in factor_names
        assert "iv_term_structure_slope" in factor_names
        assert "put_call_moneyness_skew_proxy" in factor_names
        assert "oi_concentration" in factor_names


def test_deribit_options_factor_audit_marks_missing_cache_blocked() -> None:
    module = _load_module()
    payload = module.build_deribit_options_factor_audit(
        options_dir=Path("/tmp/missing_deribit_options_cache"),
        btc_4h_dir=Path("/tmp/missing_btc_4h_cache"),
        currency="BTC",
        train_start="2020-01-01T00:00:00+00:00",
        train_end="2023-12-31T23:59:59+00:00",
        max_feature_age_hours=24.0,
        rolling_window=120,
        corr_threshold=0.75,
        min_train_observations=500,
        min_abs_rank_ic=0.01,
        min_abs_ablation_delta=0.005,
    )

    assert payload["summary"]["blocked_by"] == "deribit_options_cache_or_btc_4h_missing"
    assert payload["summary"]["factor_count"] == 0
    assert payload["features_path"] is None
