"""Point-in-time liquidation data store.

Loads aggregated liquidation Parquet files from Binance forceOrder stream
and provides O(log n) lookups with deterministic point-in-time guarantees.

Key metrics:
  - total_liq_usd: Total liquidation volume in USD (4H bucket)
  - liq_imbalance: (long_liq - short_liq) / total, [-1, 1]
  - liq_intensity_z: z-score of total liquidation volume
  - Large liq cascade → crash_reversal enhancement signal
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

from zenquant.data._z_utils import robust_z as _robust_z


@dataclass(slots=True)
class LiquidationStoreConfig:
    """Configuration for the liquidation data store."""

    liquidation_dir: Path = field(default_factory=lambda: Path("data/raw/binance/liquidations/aggregated"))
    z_window: int = 42  # ~7 days of 4H bars
    robust_z_min_samples: int = 15
    staleness_threshold_ns: int = 8 * 3600 * 1_000_000_000  # 8 hours


@dataclass(slots=True, frozen=True)
class LiquidationSnapshot:
    """Point-in-time liquidation metrics."""

    total_liq_usd: float
    long_liq_usd: float
    short_liq_usd: float
    liq_imbalance: float  # [-1, 1]: positive = more long liquidations
    max_single_liq_usd: float
    liq_intensity_z: float  # z-score of total_liq_usd
    liq_imbalance_z: float  # z-score of imbalance
    quality_flags: list[str]


class LiquidationDataStore:
    """Load aggregated liquidation Parquet files for point-in-time lookups.

    Usage::

        store = LiquidationDataStore()
        store.load()
        snap = store.lookup(cutoff_time_ns)
    """

    def __init__(self, config: LiquidationStoreConfig | None = None) -> None:
        self._config = config or LiquidationStoreConfig()
        self._timestamps: list[int] = []
        self._total_liq: list[float] = []
        self._long_liq: list[float] = []
        self._short_liq: list[float] = []
        self._imbalance: list[float] = []
        self._max_single: list[float] = []
        self._lookup_cache: dict[tuple[int, bool], LiquidationSnapshot] = {}

    @property
    def loaded(self) -> bool:
        return len(self._timestamps) > 0

    def load(self) -> None:
        """Load all aggregated liquidation Parquet files."""
        self._lookup_cache.clear()
        liq_dir = self._config.liquidation_dir
        if not liq_dir.exists():
            return

        parquet_files = sorted(liq_dir.glob("**/*.parquet"))
        if not parquet_files:
            return

        import pyarrow.parquet as pq

        for path in parquet_files:
            try:
                table = pq.read_table(path)
                df = table.to_pandas()

                for _, row in df.iterrows():
                    ts = int(row.get("bucket_start_ns", 0))
                    if ts <= 0:
                        continue

                    self._timestamps.append(ts)
                    self._total_liq.append(float(row.get("total_liq_usd", 0)))
                    self._long_liq.append(float(row.get("long_liq_usd", 0)))
                    self._short_liq.append(float(row.get("short_liq_usd", 0)))
                    self._imbalance.append(float(row.get("liq_imbalance", 0)))
                    self._max_single.append(float(row.get("max_single_liq_usd", 0)))
            except Exception:
                continue

        # Sort by timestamp
        if self._timestamps:
            indices = sorted(range(len(self._timestamps)), key=lambda i: self._timestamps[i])
            self._timestamps = [self._timestamps[i] for i in indices]
            self._total_liq = [self._total_liq[i] for i in indices]
            self._long_liq = [self._long_liq[i] for i in indices]
            self._short_liq = [self._short_liq[i] for i in indices]
            self._imbalance = [self._imbalance[i] for i in indices]
            self._max_single = [self._max_single[i] for i in indices]

    def lookup(self, cutoff_time_ns: int) -> LiquidationSnapshot:
        """Return latest liquidation data before cutoff (point-in-time correct)."""
        empty = LiquidationSnapshot(
            total_liq_usd=0.0,
            long_liq_usd=0.0,
            short_liq_usd=0.0,
            liq_imbalance=0.0,
            max_single_liq_usd=0.0,
            liq_intensity_z=0.0,
            liq_imbalance_z=0.0,
            quality_flags=[],
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
            quality_flags.append("LIQ_STALE")

        # Z-scores over rolling window
        end_idx = idx + 1
        start_idx = max(0, end_idx - self._config.z_window)

        total_val = self._total_liq[idx]
        imb_val = self._imbalance[idx]

        total_window = self._total_liq[start_idx:end_idx]
        imb_window = self._imbalance[start_idx:end_idx]

        liq_intensity_z = _robust_z(total_window, total_val, self._config.robust_z_min_samples)
        liq_imbalance_z = _robust_z(imb_window, imb_val, self._config.robust_z_min_samples)

        snapshot = LiquidationSnapshot(
            total_liq_usd=total_val,
            long_liq_usd=self._long_liq[idx],
            short_liq_usd=self._short_liq[idx],
            liq_imbalance=imb_val,
            max_single_liq_usd=self._max_single[idx],
            liq_intensity_z=liq_intensity_z,
            liq_imbalance_z=liq_imbalance_z,
            quality_flags=quality_flags,
        )
        self._lookup_cache[cache_key] = snapshot
        return snapshot
