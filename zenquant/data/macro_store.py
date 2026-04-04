"""Point-in-time macro indicator store for VIX, DXY, and Treasury yields.

Loads daily Parquet files produced by ``zenquant.ingestor.macro`` and provides
O(log n) lookups with deterministic point-in-time guarantees (no lookahead).
Z-scores use the same robust median/MAD algorithm as the indicator engine.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class MacroDataStoreConfig:
    """Configuration for the macro data store."""

    macro_dir: Path = field(default_factory=lambda: Path("data/raw/macro"))
    z_window: int = 60
    robust_z_min_samples: int = 15
    # 3 calendar days covers weekends + buffer
    staleness_threshold_ns: int = 3 * 86_400 * 1_000_000_000


@dataclass(slots=True, frozen=True)
class MacroSnapshot:
    """Point-in-time macro indicator values with z-scores."""

    vix: float | None
    vix_z: float
    dxy: float | None
    dxy_z: float
    yield_10y: float | None
    yield_10y_z: float
    tlt: float | None
    tlt_z: float
    qqq: float | None
    qqq_z: float
    iwm: float | None
    iwm_z: float
    ndx: float | None  # Nasdaq 100
    ndx_z: float
    gold: float | None  # Gold (GLD)
    gold_z: float
    m2: float | None  # M2 money supply
    m2_z: float
    quality_flags: list[str]


from zenquant.data._z_utils import robust_z as _robust_z

class MacroDataStore:
    """Load macro Parquet files and provide point-in-time lookups with z-scores.

    Usage::

        store = MacroDataStore()
        store.load()
        snapshot = store.lookup(cutoff_time_ns)
    """

    def __init__(self, config: MacroDataStoreConfig | None = None) -> None:
        self._config = config or MacroDataStoreConfig()
        # Each series: sorted parallel lists of (timestamps, values)
        self._series: dict[str, tuple[list[int], list[float]]] = {}

    @property
    def loaded(self) -> bool:
        return len(self._series) > 0

    def load(self) -> None:
        """Load all macro Parquet files into sorted arrays."""
        series_map = {
            "vix": self._config.macro_dir / "vix" / "data.parquet",
            "dxy": self._config.macro_dir / "dxy" / "data.parquet",
            "yield_10y": self._config.macro_dir / "treasury_10y" / "data.parquet",
            "tlt": self._config.macro_dir / "tlt" / "data.parquet",
            "qqq": self._config.macro_dir / "qqq" / "data.parquet",
            "iwm": self._config.macro_dir / "iwm" / "data.parquet",
            # New: individual macro factors for cross-sectional analysis
            "ndx": self._config.macro_dir / "ndx" / "data.parquet",  # Nasdaq 100
            "gold": self._config.macro_dir / "gold" / "data.parquet",  # Gold (GLD)
            "m2": self._config.macro_dir / "m2" / "data.parquet",  # M2 money supply
        }

        for name, path in series_map.items():
            if not path.exists():
                continue
            self._load_series(name, path)

    def _load_series(self, name: str, path: Path) -> None:
        """Load a single Parquet file into sorted parallel lists."""
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        col_names = table.column_names
        if "ts_ns" not in col_names or "value" not in col_names:
            return
        if table.num_rows == 0:
            return

        # Sort by ts_ns
        indices = table.column("ts_ns").to_pylist()
        values = table.column("value").to_pylist()
        paired = sorted(zip(indices, values), key=lambda x: x[0])
        timestamps = [int(t) for t, _ in paired]
        vals = [float(v) for _, v in paired]
        self._series[name] = (timestamps, vals)

    def lookup(self, cutoff_time_ns: int) -> MacroSnapshot:
        """Return the latest macro values at or before cutoff_time_ns.

        Guarantees point-in-time correctness: no data from after cutoff is used.
        """
        vix, vix_z = self._lookup_one("vix", cutoff_time_ns)
        dxy, dxy_z = self._lookup_one("dxy", cutoff_time_ns)
        yield_10y, yield_10y_z = self._lookup_one("yield_10y", cutoff_time_ns)
        tlt, tlt_z = self._lookup_one("tlt", cutoff_time_ns)
        qqq, qqq_z = self._lookup_one("qqq", cutoff_time_ns)
        iwm, iwm_z = self._lookup_one("iwm", cutoff_time_ns)
        ndx, ndx_z = self._lookup_one("ndx", cutoff_time_ns)
        gold, gold_z = self._lookup_one("gold", cutoff_time_ns)
        m2, m2_z = self._lookup_one("m2", cutoff_time_ns)

        quality_flags: list[str] = []
        # Check staleness across all loaded series
        if self._series:
            stale = self._check_staleness(cutoff_time_ns)
            if stale:
                quality_flags.append("MACRO_STALE")

        return MacroSnapshot(
            vix=vix,
            vix_z=vix_z,
            dxy=dxy,
            dxy_z=dxy_z,
            yield_10y=yield_10y,
            yield_10y_z=yield_10y_z,
            tlt=tlt,
            tlt_z=tlt_z,
            qqq=qqq,
            qqq_z=qqq_z,
            iwm=iwm,
            iwm_z=iwm_z,
            ndx=ndx,
            ndx_z=ndx_z,
            gold=gold,
            gold_z=gold_z,
            m2=m2,
            m2_z=m2_z,
            quality_flags=quality_flags,
        )

    def _lookup_one(
        self, name: str, cutoff_time_ns: int
    ) -> tuple[float | None, float]:
        """Lookup a single series value and compute its z-score."""
        if name not in self._series:
            return None, 0.0

        timestamps, values = self._series[name]
        # bisect_right gives the insertion point; idx-1 is the last entry <= cutoff
        idx = bisect.bisect_right(timestamps, cutoff_time_ns)
        if idx == 0:
            return None, 0.0

        current_value = values[idx - 1]

        # Gather z_window observations ending at idx (inclusive of current)
        start = max(0, idx - self._config.z_window)
        window = values[start:idx]

        z = _robust_z(window, current_value, self._config.robust_z_min_samples)
        return current_value, z

    def _check_staleness(self, cutoff_time_ns: int) -> bool:
        """Return True if any loaded series has stale data at cutoff."""
        threshold = self._config.staleness_threshold_ns
        for name in self._series:
            timestamps, _ = self._series[name]
            idx = bisect.bisect_right(timestamps, cutoff_time_ns)
            if idx == 0:
                # No data at all before cutoff — stale
                return True
            latest_ts = timestamps[idx - 1]
            if cutoff_time_ns - latest_ts > threshold:
                return True
        return False
