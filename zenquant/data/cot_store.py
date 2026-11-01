"""Point-in-time CFTC COT (Commitments of Traders) data store.

Loads weekly CME Bitcoin futures positioning data from CFTC reports.
Provides O(log n) lookups with point-in-time guarantees.

Key insight: institutional vs leveraged money positioning reveals
slow-moving sentiment that complements 4H fast signals.
  - Asset Managers: institutional directional bets
  - Leveraged Money: hedge fund / CTA positions
  - Extreme crowding (z > 2.0) → contrarian risk signal
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

from zenquant.data._z_utils import robust_z as _robust_z


def cot_data_available(path: Path) -> bool:
    """Check availability under the store's point-in-time admission policy."""
    store = CotDataStore(CotStoreConfig(cot_path=path))
    store.load()
    return store.loaded


def macro_cot_available(path: Path) -> bool:
    """Use the macro store's COT loader without loading other macro series."""
    from zenquant.data.macro_store import MacroDataStore

    if not path.exists():
        return False
    store = MacroDataStore()
    store._load_cme_cot(path)
    return store.loaded


@dataclass(slots=True)
class CotStoreConfig:
    """Configuration for COT data store."""

    cot_path: Path = field(default_factory=lambda: Path("data/raw/cftc/cot_btc.parquet"))
    z_window: int = 26  # ~6 months of weekly data
    robust_z_min_samples: int = 12
    # 7 days staleness (weekly report, normal lag is 3-5 days)
    staleness_threshold_ns: int = 7 * 24 * 3600 * 1_000_000_000


@dataclass(slots=True, frozen=True)
class CotSnapshot:
    """Point-in-time COT positioning metrics."""

    asset_mgr_net: int  # Institutional net position (long - short)
    leveraged_net: int  # Hedge fund/CTA net position
    dealer_net: int  # Dealer net position
    total_oi: int
    asset_mgr_z: float  # z-score of asset manager net
    leveraged_z: float  # z-score of leveraged net
    quality_flags: list[str]


class CotDataStore:
    """Load CFTC COT data for point-in-time lookups.

    Usage::

        store = CotDataStore()
        store.load()
        snap = store.lookup(cutoff_time_ns)
    """

    def __init__(self, config: CotStoreConfig | None = None) -> None:
        self._config = config or CotStoreConfig()
        self._timestamps: list[int] = []
        self._asset_mgr_net: list[int] = []
        self._leveraged_net: list[int] = []
        self._dealer_net: list[int] = []
        self._total_oi: list[int] = []
        self._lookup_cache: dict[tuple[int, bool], CotSnapshot] = {}

    @property
    def loaded(self) -> bool:
        return len(self._timestamps) > 0

    def load(self) -> None:
        """Clear cached data; date-only COT files are not usable point in time."""
        self._lookup_cache.clear()
        self._timestamps.clear()
        self._asset_mgr_net.clear()
        self._leveraged_net.clear()
        self._dealer_net.clear()
        self._total_oi.clear()
        # Position/report dates are not evidenced publication times. There is
        # no supported PIT COT ingestion contract yet, so all existing files
        # remain unavailable. Do not infer a release lag or accept a filename
        # or newly named column as proof of historical availability.

    def lookup(self, cutoff_time_ns: int) -> CotSnapshot:
        """Return latest COT data before cutoff (point-in-time correct)."""
        empty = CotSnapshot(
            asset_mgr_net=0,
            leveraged_net=0,
            dealer_net=0,
            total_oi=0,
            asset_mgr_z=0.0,
            leveraged_z=0.0,
            quality_flags=["COT_UNAVAILABLE"],
        )

        if not self._timestamps:
            return empty

        idx = bisect.bisect_right(self._timestamps, cutoff_time_ns) - 1
        if idx < 0:
            return empty

        snapshot_ts = self._timestamps[idx]
        quality_flags: list[str] = []

        age_ns = cutoff_time_ns - snapshot_ts
        is_stale = age_ns > self._config.staleness_threshold_ns
        cache_key = (idx, is_stale)
        cached = self._lookup_cache.get(cache_key)
        if cached is not None:
            return cached
        if is_stale:
            quality_flags.append("COT_STALE")

        # Z-scores over rolling window
        end_idx = idx + 1
        start_idx = max(0, end_idx - self._config.z_window)

        asset_mgr_val = float(self._asset_mgr_net[idx])
        leveraged_val = float(self._leveraged_net[idx])

        asset_mgr_window = [float(v) for v in self._asset_mgr_net[start_idx:end_idx]]
        leveraged_window = [float(v) for v in self._leveraged_net[start_idx:end_idx]]

        asset_mgr_z = _robust_z(asset_mgr_window, asset_mgr_val, self._config.robust_z_min_samples)
        leveraged_z = _robust_z(leveraged_window, leveraged_val, self._config.robust_z_min_samples)

        snapshot = CotSnapshot(
            asset_mgr_net=self._asset_mgr_net[idx],
            leveraged_net=self._leveraged_net[idx],
            dealer_net=self._dealer_net[idx],
            total_oi=self._total_oi[idx],
            asset_mgr_z=asset_mgr_z,
            leveraged_z=leveraged_z,
            quality_flags=quality_flags,
        )
        self._lookup_cache[cache_key] = snapshot
        return snapshot
