"""Spot-perpetual basis data store.

Computes the premium/discount between Binance spot and perpetual futures.
Basis = (perp_price - spot_price) / spot_price

Positive basis = perp premium (long crowding, funding will be positive)
Negative basis = perp discount (short crowding or spot demand)

Whales often move spot first → basis expansion signals institutional flow.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

from zenquant.data._z_utils import robust_z as _robust_z


@dataclass(slots=True)
class BasisStoreConfig:
    """Configuration for spot-perp basis store."""

    spot_dir: Path = field(default_factory=lambda: Path("data/raw/binance_spot/klines/BTCUSDT/4h"))
    perp_dir: Path = field(default_factory=lambda: Path("data/raw/binance/klines/BTCUSDT/4h"))
    z_window: int = 42  # ~7 days of 4H bars
    robust_z_min_samples: int = 15


@dataclass(slots=True, frozen=True)
class BasisSnapshot:
    """Point-in-time spot-perp basis metrics."""

    basis_pct: float  # (perp - spot) / spot as percentage
    basis_z: float  # z-score of basis
    spot_price: float
    perp_price: float
    quality_flags: list[str]


class BasisDataStore:
    """Load spot and perp klines, compute basis for point-in-time lookups."""

    def __init__(self, config: BasisStoreConfig | None = None) -> None:
        self._config = config or BasisStoreConfig()
        self._timestamps: list[int] = []
        self._basis_pct: list[float] = []
        self._spot_prices: list[float] = []
        self._perp_prices: list[float] = []
        self._lookup_cache: dict[int, BasisSnapshot] = {}

    @property
    def loaded(self) -> bool:
        return len(self._timestamps) > 0

    def load(self) -> None:
        """Load spot and perp parquet, compute basis at each timestamp."""
        self._lookup_cache.clear()
        spot_parquet = self._config.spot_dir / "data.parquet"
        perp_parquet = self._config.perp_dir / "data.parquet"

        if not spot_parquet.exists() or not perp_parquet.exists():
            return

        import pyarrow.parquet as pq

        try:
            spot_df = pq.read_table(spot_parquet).to_pandas()
            perp_df = pq.read_table(perp_parquet).to_pandas()
        except Exception:
            return

        if spot_df.empty or perp_df.empty:
            return

        # Use close_time_ns as join key (both 4H aligned)
        time_col = "close_time_ns" if "close_time_ns" in spot_df.columns else "close_time"
        if time_col not in spot_df.columns or time_col not in perp_df.columns:
            return

        spot_by_time = {int(row[time_col]): float(row["close"]) for _, row in spot_df.iterrows()}
        perp_by_time = {int(row[time_col]): float(row["close"]) for _, row in perp_df.iterrows()}

        # Match timestamps
        common_times = sorted(set(spot_by_time.keys()) & set(perp_by_time.keys()))

        for ts in common_times:
            spot = spot_by_time[ts]
            perp = perp_by_time[ts]
            if spot <= 0:
                continue
            basis = (perp - spot) / spot
            self._timestamps.append(ts)
            self._basis_pct.append(basis)
            self._spot_prices.append(spot)
            self._perp_prices.append(perp)

    def lookup(self, cutoff_time_ns: int) -> BasisSnapshot:
        """Return latest basis at or before cutoff."""
        empty = BasisSnapshot(basis_pct=0.0, basis_z=0.0, spot_price=0.0, perp_price=0.0, quality_flags=[])

        if not self._timestamps:
            return empty

        idx = bisect.bisect_right(self._timestamps, cutoff_time_ns) - 1
        if idx < 0:
            return empty
        cached = self._lookup_cache.get(idx)
        if cached is not None:
            return cached

        end_idx = idx + 1
        start_idx = max(0, end_idx - self._config.z_window)
        basis_val = self._basis_pct[idx]
        basis_window = self._basis_pct[start_idx:end_idx]
        basis_z = _robust_z(basis_window, basis_val, self._config.robust_z_min_samples)

        snapshot = BasisSnapshot(
            basis_pct=round(basis_val, 6),
            basis_z=basis_z,
            spot_price=self._spot_prices[idx],
            perp_price=self._perp_prices[idx],
            quality_flags=[],
        )
        self._lookup_cache[idx] = snapshot
        return snapshot
