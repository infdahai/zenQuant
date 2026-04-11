"""Point-in-time macro indicator store for VIX, DXY, and Treasury yields.

Loads daily Parquet files produced by ``zenquant.ingestor.macro`` and provides
O(log n) lookups with deterministic point-in-time guarantees (no lookahead).
Z-scores use the same robust median/MAD algorithm as the indicator engine.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

from zenquant.data._z_utils import robust_z as _robust_z


@dataclass(slots=True)
class MacroDataStoreConfig:
    """Configuration for the macro data store."""

    macro_dir: Path = field(default_factory=lambda: Path("data/raw/macro"))
    cftc_path: Path = field(default_factory=lambda: Path("data/raw/cftc/cot_btc.parquet"))
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
    btc_etf_netflow: float | None
    btc_etf_netflow_z: float
    btc_etf_flow_3d: float
    btc_etf_flow_5d: float
    btc_etf_flow_accel: float
    btc_etf_positive_streak: int
    btc_etf_negative_streak: int
    cme_asset_mgr_net: float | None
    cme_asset_mgr_net_z: float
    cme_lev_money_net: float | None
    cme_lev_money_net_z: float
    cme_dealer_net: float | None
    cme_dealer_net_z: float
    cme_total_oi: float | None
    cme_total_oi_z: float
    quality_flags: list[str]

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
            "btc_etf_netflow": self._config.macro_dir / "btc_etf_netflow" / "data.parquet",
        }

        for name, path in series_map.items():
            if not path.exists():
                continue
            self._load_series(name, path)
        if self._config.cftc_path.exists():
            self._load_cme_cot(self._config.cftc_path)

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

    def _load_cme_cot(self, path: Path) -> None:
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        if table.num_rows == 0 or "report_date_ns" not in table.column_names:
            return
        timestamps = [int(ts) for ts in table.column("report_date_ns").to_pylist()]
        field_map = {
            "cme_asset_mgr_net": "asset_mgr_net",
            "cme_lev_money_net": "leveraged_net",
            "cme_dealer_net": "dealer_net",
            "cme_total_oi": "total_oi",
        }
        for target_name, source_name in field_map.items():
            if source_name not in table.column_names:
                continue
            values = [float(v or 0.0) for v in table.column(source_name).to_pylist()]
            paired = sorted(zip(timestamps, values), key=lambda x: x[0])
            self._series[target_name] = ([int(t) for t, _ in paired], [float(v) for _, v in paired])

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
        btc_etf_netflow, btc_etf_netflow_z = self._lookup_one("btc_etf_netflow", cutoff_time_ns)
        btc_etf_flow_3d = self._rolling_sum("btc_etf_netflow", cutoff_time_ns, window=3)
        btc_etf_flow_5d = self._rolling_sum("btc_etf_netflow", cutoff_time_ns, window=5)
        btc_etf_flow_accel = btc_etf_flow_3d - btc_etf_flow_5d
        btc_etf_positive_streak = self._streak("btc_etf_netflow", cutoff_time_ns, positive=True)
        btc_etf_negative_streak = self._streak("btc_etf_netflow", cutoff_time_ns, positive=False)
        cme_asset_mgr_net, cme_asset_mgr_net_z = self._lookup_one("cme_asset_mgr_net", cutoff_time_ns)
        cme_lev_money_net, cme_lev_money_net_z = self._lookup_one("cme_lev_money_net", cutoff_time_ns)
        cme_dealer_net, cme_dealer_net_z = self._lookup_one("cme_dealer_net", cutoff_time_ns)
        cme_total_oi, cme_total_oi_z = self._lookup_one("cme_total_oi", cutoff_time_ns)

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
            btc_etf_netflow=btc_etf_netflow,
            btc_etf_netflow_z=btc_etf_netflow_z,
            btc_etf_flow_3d=btc_etf_flow_3d,
            btc_etf_flow_5d=btc_etf_flow_5d,
            btc_etf_flow_accel=btc_etf_flow_accel,
            btc_etf_positive_streak=btc_etf_positive_streak,
            btc_etf_negative_streak=btc_etf_negative_streak,
            cme_asset_mgr_net=cme_asset_mgr_net,
            cme_asset_mgr_net_z=cme_asset_mgr_net_z,
            cme_lev_money_net=cme_lev_money_net,
            cme_lev_money_net_z=cme_lev_money_net_z,
            cme_dealer_net=cme_dealer_net,
            cme_dealer_net_z=cme_dealer_net_z,
            cme_total_oi=cme_total_oi,
            cme_total_oi_z=cme_total_oi_z,
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

    def _rolling_sum(self, name: str, cutoff_time_ns: int, *, window: int) -> float:
        if name not in self._series or window <= 0:
            return 0.0
        timestamps, values = self._series[name]
        idx = bisect.bisect_right(timestamps, cutoff_time_ns)
        if idx == 0:
            return 0.0
        start = max(0, idx - window)
        return round(sum(values[start:idx]), 6)

    def _streak(self, name: str, cutoff_time_ns: int, *, positive: bool) -> int:
        if name not in self._series:
            return 0
        timestamps, values = self._series[name]
        idx = bisect.bisect_right(timestamps, cutoff_time_ns)
        if idx == 0:
            return 0
        streak = 0
        for value in reversed(values[:idx]):
            if (positive and value > 0) or (not positive and value < 0):
                streak += 1
            else:
                break
        return streak

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
