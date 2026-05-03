from pathlib import Path

import pandas as pd

from zenquant.data.binance_metrics_factor_store import BinanceMetricsFactorStore, BinanceMetricsFactorStoreConfig


def test_binance_metrics_factor_store_is_point_in_time(tmp_path: Path) -> None:
    path = tmp_path / "derived_metrics_factors.parquet"
    frame = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                ["2023-01-01 03:55:00", "2023-01-01 07:55:00"],
                utc=True,
            ),
            "oi_value_growth_24h_z": [1.0, 2.0],
            "top_trader_account_lsr_z": [0.5, 0.6],
            "crowding_unwind_pressure": [-0.1, 0.2],
            "top_account_global_divergence": [0.7, 0.8],
            "oi_expansion_price_continuation": [0.9, 1.0],
            "taker_oi_exhaustion": [1.1, 1.2],
            "crowded_unwind_vol_adjusted": [1.3, 1.4],
        }
    )
    frame.to_parquet(path, index=False)
    store = BinanceMetricsFactorStore(BinanceMetricsFactorStoreConfig(data_path=path))
    store.load()

    early = store.lookup(pd.Timestamp("2023-01-01 03:54:00", tz="UTC").value)
    first = store.lookup(pd.Timestamp("2023-01-01 04:00:00", tz="UTC").value)
    second = store.lookup(pd.Timestamp("2023-01-01 08:00:00", tz="UTC").value)

    assert early.oi_value_growth_24h_z == 0.0
    assert first.oi_value_growth_24h_z == 1.0
    assert first.top_account_global_divergence == 0.7
    assert first.oi_expansion_price_continuation == 0.9
    assert first.taker_oi_exhaustion == 1.1
    assert first.crowded_unwind_vol_adjusted == 1.3
    assert second.oi_value_growth_24h_z == 2.0
