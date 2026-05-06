"""Point-in-time cross-asset context store for BTC research signals."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

from zenquant.data._z_utils import robust_z as _robust_z


@dataclass(slots=True)
class CrossAssetContextStoreConfig:
    """Configuration for the research-only cross-asset context cache."""

    data_path: Path = field(default_factory=lambda: Path("data/research/cross_asset/sol_bnb_btc_context.parquet"))
    z_window: int = 60
    momentum_window: int = 7
    robust_z_min_samples: int = 20


@dataclass(slots=True, frozen=True)
class CrossAssetContextSnapshot:
    """Point-in-time alt context values available at or before cutoff."""

    alt_btc_spread_z: float
    alt_momentum_7d: float
    eth_momentum_7d: float
    sol_momentum_7d: float
    bnb_momentum_7d: float
    quality_flags: list[str]


class CrossAssetContextStore:
    """Load SOL/BNB/ETH daily context and expose PIT lookup for BTC runs."""

    def __init__(self, config: CrossAssetContextStoreConfig | None = None) -> None:
        self._config = config or CrossAssetContextStoreConfig()
        self._timestamps: list[int] = []
        self._alt_spreads: list[float] = []
        self._alt_returns: list[float] = []
        self._eth_returns: list[float] = []
        self._sol_returns: list[float] = []
        self._bnb_returns: list[float] = []
        self._lookup_cache: dict[int, CrossAssetContextSnapshot] = {}

    @property
    def loaded(self) -> bool:
        return bool(self._timestamps)

    def load(self) -> None:
        self._lookup_cache.clear()
        if not self._config.data_path.exists():
            return

        import pyarrow.parquet as pq

        try:
            frame = pq.read_table(self._config.data_path).to_pandas()
        except Exception:
            return
        required = {"date", "return_btc", "return_eth", "return_sol", "return_bnb"}
        if frame.empty or not required.issubset(frame.columns):
            return

        frame = frame.sort_values("date")
        for _, row in frame.iterrows():
            date_ns = int(row["date"].value)
            btc = float(row["return_btc"] or 0.0)
            eth = float(row["return_eth"] or 0.0)
            sol = float(row["return_sol"] or 0.0)
            bnb = float(row["return_bnb"] or 0.0)
            alt_return = (sol + bnb) / 2.0
            self._timestamps.append(date_ns)
            self._alt_returns.append(alt_return)
            self._alt_spreads.append(alt_return - btc)
            self._eth_returns.append(eth)
            self._sol_returns.append(sol)
            self._bnb_returns.append(bnb)

    def lookup(self, cutoff_time_ns: int) -> CrossAssetContextSnapshot:
        empty = CrossAssetContextSnapshot(
            alt_btc_spread_z=0.0,
            alt_momentum_7d=0.0,
            eth_momentum_7d=0.0,
            sol_momentum_7d=0.0,
            bnb_momentum_7d=0.0,
            quality_flags=[],
        )
        if not self._timestamps:
            return empty

        idx = bisect.bisect_right(self._timestamps, cutoff_time_ns) - 1
        if idx < 0:
            return empty
        cached = self._lookup_cache.get(idx)
        if cached is not None:
            return cached

        end_idx = idx + 1
        z_start = max(0, end_idx - self._config.z_window)
        momentum_start = max(0, end_idx - self._config.momentum_window)
        spread_window = self._alt_spreads[z_start:end_idx]
        spread_z = _robust_z(spread_window, self._alt_spreads[idx], self._config.robust_z_min_samples)

        snapshot = CrossAssetContextSnapshot(
            alt_btc_spread_z=round(spread_z, 4),
            alt_momentum_7d=round(sum(self._alt_returns[momentum_start:end_idx]), 6),
            eth_momentum_7d=round(sum(self._eth_returns[momentum_start:end_idx]), 6),
            sol_momentum_7d=round(sum(self._sol_returns[momentum_start:end_idx]), 6),
            bnb_momentum_7d=round(sum(self._bnb_returns[momentum_start:end_idx]), 6),
            quality_flags=[],
        )
        self._lookup_cache[idx] = snapshot
        return snapshot
