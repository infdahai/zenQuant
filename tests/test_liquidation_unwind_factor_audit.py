from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pandas as pd


def _load_module():
    path = Path("scripts/audit_liquidation_unwind_factors.py")
    spec = importlib.util.spec_from_file_location("audit_liquidation_unwind_factors", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load audit_liquidation_unwind_factors")
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_liquidation_unwind_factors"] = module
    spec.loader.exec_module(module)
    return module


def test_liquidation_unwind_audit_uses_liquidation_cache_and_writes_features() -> None:
    module = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        btc_dir, metrics_dir, funding_dir, liq_dir, output_dir = _make_dirs(
            root,
            "btc",
            "metrics",
            "funding",
            "liquidations",
            "out",
        )
        periods = 360
        close_ts = pd.date_range("2023-01-01 04:00:00", periods=periods, freq="4h", tz="UTC")
        metric_ts = close_ts - pd.Timedelta(minutes=5)
        _write_prices(btc_dir, close_ts, periods)
        trend = pd.Series(range(periods), dtype=float) / periods
        pd.DataFrame(
            {
                "create_time": [item.isoformat() for item in metric_ts],
                "sum_open_interest": 10_000 + trend * 2_000,
                "sum_open_interest_value": 100_000 + trend * 30_000,
                "count_toptrader_long_short_ratio": 1.0 + trend * 0.2,
                "sum_toptrader_long_short_ratio": 1.0 + trend * 0.4,
                "count_long_short_ratio": 1.0 + trend * 0.15,
                "sum_taker_long_short_vol_ratio": 1.0 + ((trend * 10) % 1) * 0.2,
            }
        ).to_parquet(metrics_dir / "2023-01.parquet", index=False)
        pd.DataFrame(
            {
                "funding_time_ns": [item.value for item in close_ts[::2]],
                "funding_rate": [0.0001 + (idx % 10) * 0.00001 for idx in range(len(close_ts[::2]))],
            }
        ).to_parquet(funding_dir / "data.parquet", index=False)
        pd.DataFrame(
            {
                "bucket_end_ns": [item.value for item in close_ts],
                "long_liq_usd": [float((idx % 20) * 1_000) for idx in range(periods)],
                "short_liq_usd": [float(((idx + 7) % 20) * 700) for idx in range(periods)],
                "total_liq_usd": [float((idx % 20) * 1_000 + ((idx + 7) % 20) * 700) for idx in range(periods)],
            }
        ).to_parquet(liq_dir / "liquidations.parquet", index=False)

        payload = module.build_liquidation_unwind_factor_audit(
            btc_4h_dir=btc_dir,
            metrics_dir=metrics_dir,
            open_interest_dir=root / "missing_oi",
            funding_dir=funding_dir,
            liquidation_dir=liq_dir,
            output_dir=output_dir,
            train_end="2023-03-31T00:00:00+00:00",
            corr_threshold=0.75,
            min_train_observations=20,
            min_abs_rank_ic=0.0,
            min_abs_ablation_delta=0.0,
        )

        assert Path(payload["features_path"]).exists()
        assert payload["summary"]["factor_count"] == 4
        assert payload["summary"]["liquidation_lineage"] == "liquidation_cache"
        assert payload["summary"]["liquidation_proxy_used"] is False
        assert {factor["name"] for factor in payload["factors"]} == {
            "crowded_long_unwind_risk",
            "crowded_short_squeeze",
            "oi_continuation_support",
            "taker_oi_exhaustion",
        }


def test_liquidation_unwind_audit_falls_back_to_oi_funding_proxy() -> None:
    module = _load_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        btc_dir, oi_dir, funding_dir, output_dir = _make_dirs(root, "btc", "oi", "funding", "out")
        periods = 360
        close_ts = pd.date_range("2023-01-01 04:00:00", periods=periods, freq="4h", tz="UTC")
        _write_prices(btc_dir, close_ts, periods)
        pd.DataFrame(
            {
                "ts_ns": [item.value for item in close_ts],
                "open_interest": [50_000.0 + idx * 25.0 for idx in range(periods)],
            }
        ).to_parquet(oi_dir / "data.parquet", index=False)
        pd.DataFrame(
            {
                "funding_time_ns": [item.value for item in close_ts[::2]],
                "funding_rate": [(-1) ** idx * 0.0001 for idx in range(len(close_ts[::2]))],
            }
        ).to_parquet(funding_dir / "data.parquet", index=False)

        payload = module.build_liquidation_unwind_factor_audit(
            btc_4h_dir=btc_dir,
            metrics_dir=root / "missing_metrics",
            open_interest_dir=oi_dir,
            funding_dir=funding_dir,
            liquidation_dir=root / "missing_liquidations",
            output_dir=output_dir,
            train_end="2023-03-31T00:00:00+00:00",
            corr_threshold=0.75,
            min_train_observations=20,
            min_abs_rank_ic=0.0,
            min_abs_ablation_delta=0.0,
        )

        assert payload["summary"]["factor_count"] == 4
        assert payload["summary"]["liquidation_lineage"] == "oi_funding_proxy_no_liquidation_cache"
        assert payload["summary"]["liquidation_proxy_used"] is True
        features = pd.read_parquet(payload["features_path"])
        assert set(features["liquidation_lineage"].dropna().unique()) == {"oi_funding_proxy_no_liquidation_cache"}


def test_liquidation_unwind_audit_blocks_missing_core_inputs() -> None:
    module = _load_module()
    payload = module.build_liquidation_unwind_factor_audit(
        btc_4h_dir=Path("/tmp/missing_btc_4h"),
        metrics_dir=Path("/tmp/missing_metrics"),
        open_interest_dir=Path("/tmp/missing_oi"),
        funding_dir=Path("/tmp/missing_funding"),
        liquidation_dir=Path("/tmp/missing_liq"),
        output_dir=None,
        train_end="2023-12-31T23:59:59+00:00",
        corr_threshold=0.75,
        min_train_observations=500,
        min_abs_rank_ic=0.01,
        min_abs_ablation_delta=0.005,
    )

    assert payload["summary"]["blocked_by"] == "price_funding_or_oi_missing"
    assert payload["summary"]["admissible_factor_count"] == 0
    assert payload["summary"]["liquidation_proxy_used"] is True


def _make_dirs(root: Path, *names: str) -> tuple[Path, ...]:
    paths = tuple(root / name for name in names)
    for path in paths:
        path.mkdir(parents=True)
    return paths


def _write_prices(btc_dir: Path, close_ts: pd.DatetimeIndex, periods: int) -> None:
    pd.DataFrame(
        {
            "close_time_ns": [item.value for item in close_ts],
            "close": [20_000.0 + idx * 3.0 + (idx % 24) * 2.0 for idx in range(periods)],
            "volume": [100.0 + (idx % 12) for idx in range(periods)],
            "taker_buy_base": [45.0 + (idx % 10) for idx in range(periods)],
        }
    ).to_parquet(btc_dir / "data.parquet", index=False)
