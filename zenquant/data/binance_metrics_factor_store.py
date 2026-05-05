"""Point-in-time Binance metrics/OI factor store for BTC research signals."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class BinanceMetricsFactorStoreConfig:
    data_path: Path = field(
        default_factory=lambda: Path("data/raw/binance/metrics/BTCUSDT/derived_metrics_factors.parquet")
    )


@dataclass(slots=True, frozen=True)
class BinanceMetricsFactorSnapshot:
    oi_value_growth_24h_z: float
    top_trader_account_lsr_z: float
    crowding_unwind_pressure: float
    top_account_global_divergence: float
    oi_expansion_price_continuation: float
    taker_oi_exhaustion: float
    crowded_unwind_vol_adjusted: float
    quality_flags: list[str]


class BinanceMetricsFactorStore:
    """Load audited Binance-derived metrics factors and expose PIT lookup."""

    def __init__(self, config: BinanceMetricsFactorStoreConfig | None = None) -> None:
        self._config = config or BinanceMetricsFactorStoreConfig()
        self._timestamps: list[int] = []
        self._oi_value_growth_24h_z: list[float] = []
        self._top_trader_account_lsr_z: list[float] = []
        self._crowding_unwind_pressure: list[float] = []
        self._top_account_global_divergence: list[float] = []
        self._oi_expansion_price_continuation: list[float] = []
        self._taker_oi_exhaustion: list[float] = []
        self._crowded_unwind_vol_adjusted: list[float] = []

    @property
    def loaded(self) -> bool:
        return bool(self._timestamps)

    def load(self) -> None:
        if not self._config.data_path.exists():
            return
        import pyarrow.parquet as pq

        try:
            frame = pq.read_table(self._config.data_path).to_pandas()
        except Exception:
            return
        required = {"ts", "oi_value_growth_24h_z", "top_trader_account_lsr_z", "crowding_unwind_pressure"}
        if frame.empty or not required.issubset(frame.columns):
            return
        frame = frame.sort_values("ts")
        for _, row in frame.iterrows():
            self._timestamps.append(int(row["ts"].value))
            self._oi_value_growth_24h_z.append(_safe_float(row.get("oi_value_growth_24h_z")))
            self._top_trader_account_lsr_z.append(_safe_float(row.get("top_trader_account_lsr_z")))
            self._crowding_unwind_pressure.append(_safe_float(row.get("crowding_unwind_pressure")))
            self._top_account_global_divergence.append(_safe_float(row.get("top_account_global_divergence")))
            self._oi_expansion_price_continuation.append(_safe_float(row.get("oi_expansion_price_continuation")))
            self._taker_oi_exhaustion.append(_safe_float(row.get("taker_oi_exhaustion")))
            self._crowded_unwind_vol_adjusted.append(_safe_float(row.get("crowded_unwind_vol_adjusted")))

    def lookup(self, cutoff_time_ns: int) -> BinanceMetricsFactorSnapshot:
        empty = BinanceMetricsFactorSnapshot(
            oi_value_growth_24h_z=0.0,
            top_trader_account_lsr_z=0.0,
            crowding_unwind_pressure=0.0,
            top_account_global_divergence=0.0,
            oi_expansion_price_continuation=0.0,
            taker_oi_exhaustion=0.0,
            crowded_unwind_vol_adjusted=0.0,
            quality_flags=[],
        )
        if not self._timestamps:
            return empty
        idx = bisect.bisect_right(self._timestamps, cutoff_time_ns) - 1
        if idx < 0:
            return empty
        return BinanceMetricsFactorSnapshot(
            oi_value_growth_24h_z=round(self._oi_value_growth_24h_z[idx], 6),
            top_trader_account_lsr_z=round(self._top_trader_account_lsr_z[idx], 6),
            crowding_unwind_pressure=round(self._crowding_unwind_pressure[idx], 6),
            top_account_global_divergence=round(self._top_account_global_divergence[idx], 6),
            oi_expansion_price_continuation=round(self._oi_expansion_price_continuation[idx], 6),
            taker_oi_exhaustion=round(self._taker_oi_exhaustion[idx], 6),
            crowded_unwind_vol_adjusted=round(self._crowded_unwind_vol_adjusted[idx], 6),
            quality_flags=[],
        )


def _safe_float(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0
