"""Point-in-time on-chain factor store for BTC research signals."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class OnchainFactorStoreConfig:
    data_path: Path = field(
        default_factory=lambda: Path("data/raw/coinmetrics/community/BTC/derived_onchain_factors.parquet")
    )


@dataclass(slots=True, frozen=True)
class OnchainFactorSnapshot:
    active_address_momentum_14d: float
    supply_activity_regime: float
    quality_flags: list[str]


class OnchainFactorStore:
    """Load audited CoinMetrics-derived factors and expose PIT lookup."""

    def __init__(self, config: OnchainFactorStoreConfig | None = None) -> None:
        self._config = config or OnchainFactorStoreConfig()
        self._timestamps: list[int] = []
        self._active_address_momentum_14d: list[float] = []
        self._supply_activity_regime: list[float] = []

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
        required = {"date", "active_address_momentum_14d", "supply_activity_regime"}
        if frame.empty or not required.issubset(frame.columns):
            return
        frame = frame.sort_values("date")
        for _, row in frame.iterrows():
            self._timestamps.append(int(row["date"].value))
            self._active_address_momentum_14d.append(float(row["active_address_momentum_14d"] or 0.0))
            self._supply_activity_regime.append(float(row["supply_activity_regime"] or 0.0))

    def lookup(self, cutoff_time_ns: int) -> OnchainFactorSnapshot:
        empty = OnchainFactorSnapshot(
            active_address_momentum_14d=0.0,
            supply_activity_regime=0.0,
            quality_flags=[],
        )
        if not self._timestamps:
            return empty
        idx = bisect.bisect_right(self._timestamps, cutoff_time_ns) - 1
        if idx < 0:
            return empty
        return OnchainFactorSnapshot(
            active_address_momentum_14d=round(self._active_address_momentum_14d[idx], 6),
            supply_activity_regime=round(self._supply_activity_regime[idx], 6),
            quality_flags=[],
        )
