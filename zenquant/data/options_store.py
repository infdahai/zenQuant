"""Point-in-time options IV store for Deribit BTC/ETH options.

Loads options chain Parquet snapshots and computes ATM IV, skew, and term structure.
Provides O(log n) lookups with deterministic point-in-time guarantees (no lookahead).
Z-scores use the same robust median/MAD algorithm as the indicator engine.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

from zenquant.data._z_utils import robust_z as _robust_z
from zenquant.domain import OptionsState


@dataclass(slots=True)
class OptionsDataStoreConfig:
    """Configuration for the options data store."""

    options_dir: Path = field(default_factory=lambda: Path("data/raw/deribit_options"))
    currency: str = "BTC"
    z_window: int = 30  # 30-day rolling window (vs 60 for macro)
    robust_z_min_samples: int = 15
    # 24 hours staleness (options update more frequently than macro)
    staleness_threshold_ns: int = 24 * 3600 * 1_000_000_000


@dataclass(slots=True, frozen=True)
class OptionsSnapshot:
    """Legacy point-in-time options metrics with z-scores.

    ``put_call_moneyness_skew_proxy`` is the 90%-put minus 110%-call IV
    proxy. The compatibility property must not be interpreted as true 25-delta
    skew; true Black-76 25-delta fields live on :class:`OptionsState`.
    """

    atm_iv_30d: float | None  # ATM implied vol at 30-day tenor
    atm_iv_z: float
    term_structure_slope: float | None  # (90d_IV - 7d_IV) / 90d_IV
    term_slope_z: float
    put_call_moneyness_skew_proxy: float | None
    skew_z: float
    underlying_price: float
    # Skew velocity (dSkew/dt per hour) — informed traders buy puts before selling
    skew_velocity: float = 0.0  # Negative = puts getting expensive faster = bearish
    # GEX metrics (gamma exposure)
    net_gex: float = 0.0  # Positive = pinning, negative = amplifying
    gamma_wall_strike: float | None = None  # Price magnet above
    flip_strike: float | None = None  # Below this = volatile
    gex_regime: str = ""  # PINNING | AMPLIFYING | NEUTRAL
    quality_flags: list[str] = field(default_factory=list)

    @property
    def put_call_skew_25d(self) -> float | None:
        """Deprecated legacy alias for the 90%/110% moneyness proxy."""
        return self.put_call_moneyness_skew_proxy


_REQUIRED_CHAIN_COLUMNS = frozenset(
    {
        "snapshot_ts_ns",
        "expiry_ts_ms",
        "strike",
        "option_type",
        "underlying_price",
        "open_interest",
        "mark_iv",
    }
)


class OptionsDataStore:
    """Load options chain Parquet snapshots and provide point-in-time IV lookups.

    Usage::

        store = OptionsDataStore()
        store.load()
        snapshot = store.lookup(cutoff_time_ns)
    """

    def __init__(self, config: OptionsDataStoreConfig | None = None) -> None:
        self._config = config or OptionsDataStoreConfig()
        # Time series: sorted lists of (timestamps, atm_iv, skew, term_slope, spot)
        self._timestamps: list[int] = []
        self._atm_iv: list[float | None] = []
        self._skew: list[float | None] = []
        self._term_slope: list[float | None] = []
        self._spot: list[float] = []
        # Skew velocity (dSkew/dt between snapshots)
        self._skew_velocity: list[float] = []
        # GEX metrics per snapshot
        self._net_gex: list[float] = []
        self._gamma_wall: list[float | None] = []
        self._flip_strike: list[float | None] = []
        self._gex_regime: list[str] = []
        self._lookup_cache: dict[tuple[int, bool], OptionsSnapshot] = {}
        self._chain_rows: list[tuple[dict[str, object], ...]] = []
        self._state_lookup_cache: dict[tuple[int, float | None, int], OptionsState] = {}

    @property
    def loaded(self) -> bool:
        return len(self._timestamps) > 0

    @property
    def snapshot_count(self) -> int:
        """Number of loaded options chain snapshots."""
        return len(self._timestamps)

    @property
    def ready_for_signal(self) -> bool:
        """True when enough snapshots exist for meaningful z-scores (≥30)."""
        return self.snapshot_count >= self._config.z_window

    def load(self) -> None:
        """Load all options chain snapshots and compute IV metrics."""
        self._lookup_cache.clear()
        self._state_lookup_cache.clear()
        self._timestamps.clear()
        self._atm_iv.clear()
        self._skew.clear()
        self._term_slope.clear()
        self._spot.clear()
        self._skew_velocity.clear()
        self._net_gex.clear()
        self._gamma_wall.clear()
        self._flip_strike.clear()
        self._gex_regime.clear()
        self._chain_rows.clear()
        currency_dir = self._config.options_dir / self._config.currency
        if not currency_dir.exists():
            return

        # Find all parquet files
        parquet_files = sorted(currency_dir.glob("**/*.parquet"))
        if not parquet_files:
            return

        import pyarrow.parquet as pq

        for path in parquet_files:
            try:
                table = pq.read_table(path)
                df = table.to_pandas()

                if df.empty:
                    continue
                if not _REQUIRED_CHAIN_COLUMNS.issubset(df.columns):
                    continue

                # Extract snapshot timestamp (same for all rows)
                snapshot_ts = int(df["snapshot_ts_ns"].iloc[0])
                spot = float(df["underlying_price"].iloc[0])

                # Compute metrics from this snapshot
                atm_iv = self._compute_atm_iv(df)
                skew = self._compute_moneyness_skew_proxy(df)
                term_slope = self._compute_term_structure(df)

                # Compute GEX from full options chain
                net_gex, gamma_wall, flip_strike, gex_regime = self._compute_gex(df, spot)

                # Append to time series
                self._timestamps.append(snapshot_ts)
                self._atm_iv.append(atm_iv)
                self._skew.append(skew)
                self._term_slope.append(term_slope)
                self._spot.append(spot)
                self._net_gex.append(net_gex)
                self._gamma_wall.append(gamma_wall)
                self._flip_strike.append(flip_strike)
                self._gex_regime.append(gex_regime)
                ordered_rows = tuple(
                    sorted(
                        df.to_dict("records"),
                        key=lambda row: (
                            int(row.get("expiry_ts_ms", 0) or 0),
                            float(row.get("strike", 0.0) or 0.0),
                            str(row.get("option_type", "")),
                            str(row.get("instrument_name", "")),
                        ),
                    )
                )
                self._chain_rows.append(ordered_rows)

            except Exception as e:
                print(f"  Warning: Failed to load {path}: {e}")
                continue

        # Sort by timestamp (should already be sorted, but enforce)
        if self._timestamps:
            indices = sorted(range(len(self._timestamps)), key=lambda i: self._timestamps[i])
            self._timestamps = [self._timestamps[i] for i in indices]
            self._atm_iv = [self._atm_iv[i] for i in indices]
            self._skew = [self._skew[i] for i in indices]
            self._term_slope = [self._term_slope[i] for i in indices]
            self._spot = [self._spot[i] for i in indices]
            self._net_gex = [self._net_gex[i] for i in indices]
            self._gamma_wall = [self._gamma_wall[i] for i in indices]
            self._flip_strike = [self._flip_strike[i] for i in indices]
            self._gex_regime = [self._gex_regime[i] for i in indices]
            self._chain_rows = [self._chain_rows[i] for i in indices]

            # Compute skew velocity (dSkew/dt) after sorting
            self._skew_velocity = [0.0]  # First snapshot has no previous
            for i in range(1, len(self._timestamps)):
                s0 = self._skew[i - 1]
                s1 = self._skew[i]
                dt_hours = (self._timestamps[i] - self._timestamps[i - 1]) / (3600 * 1e9)
                if s0 is not None and s1 is not None and dt_hours > 0:
                    self._skew_velocity.append((s1 - s0) / dt_hours)
                else:
                    self._skew_velocity.append(0.0)

    def lookup(self, cutoff_time_ns: int) -> OptionsSnapshot:
        """Return latest options snapshot before cutoff (point-in-time correct)."""
        if not self._timestamps:
            return OptionsSnapshot(
                atm_iv_30d=None,
                atm_iv_z=0.0,
                term_structure_slope=None,
                term_slope_z=0.0,
                put_call_moneyness_skew_proxy=None,
                skew_z=0.0,
                underlying_price=0.0,
                quality_flags=[],
            )

        # Bisect to find latest snapshot before cutoff
        idx = bisect.bisect_right(self._timestamps, cutoff_time_ns) - 1
        if idx < 0:
            # Before any data
            return OptionsSnapshot(
                atm_iv_30d=None,
                atm_iv_z=0.0,
                term_structure_slope=None,
                term_slope_z=0.0,
                put_call_moneyness_skew_proxy=None,
                skew_z=0.0,
                underlying_price=0.0,
                quality_flags=[],
            )

        atm_iv = self._atm_iv[idx]
        skew = self._skew[idx]
        term_slope = self._term_slope[idx]
        spot = self._spot[idx]
        snapshot_ts = self._timestamps[idx]

        # Quality flags
        quality_flags: list[str] = []

        # Staleness check
        age_ns = cutoff_time_ns - snapshot_ts
        is_stale = age_ns > self._config.staleness_threshold_ns
        cache_key = (idx, is_stale)
        cached = self._lookup_cache.get(cache_key)
        if cached is not None:
            return cached
        if is_stale:
            quality_flags.append("OPTIONS_STALE")

        # Compute z-scores using rolling window
        end_idx = idx + 1
        start_idx = max(0, end_idx - self._config.z_window)

        # ATM IV z-score
        atm_iv_z = 0.0
        if atm_iv is not None:
            atm_window = [v for v in self._atm_iv[start_idx:end_idx] if v is not None]
            if atm_window:
                atm_iv_z = _robust_z(atm_window, atm_iv, self._config.robust_z_min_samples)

        # Skew z-score
        skew_z = 0.0
        if skew is not None:
            skew_window = [v for v in self._skew[start_idx:end_idx] if v is not None]
            if skew_window:
                skew_z = _robust_z(skew_window, skew, self._config.robust_z_min_samples)

        # Term slope z-score
        term_slope_z = 0.0
        if term_slope is not None:
            term_window = [v for v in self._term_slope[start_idx:end_idx] if v is not None]
            if term_window:
                term_slope_z = _robust_z(term_window, term_slope, self._config.robust_z_min_samples)

        # Partial data flags
        if atm_iv is None:
            quality_flags.append("OPTIONS_PARTIAL_ATM")
        if skew is None:
            quality_flags.append("OPTIONS_PARTIAL_SKEW")
        if term_slope is None:
            quality_flags.append("OPTIONS_PARTIAL_TERM")

        snapshot = OptionsSnapshot(
            atm_iv_30d=atm_iv,
            atm_iv_z=atm_iv_z,
            term_structure_slope=term_slope,
            term_slope_z=term_slope_z,
            put_call_moneyness_skew_proxy=skew,
            skew_z=skew_z,
            underlying_price=spot,
            skew_velocity=self._skew_velocity[idx] if idx < len(self._skew_velocity) else 0.0,
            net_gex=self._net_gex[idx] if idx < len(self._net_gex) else 0.0,
            gamma_wall_strike=self._gamma_wall[idx] if idx < len(self._gamma_wall) else None,
            flip_strike=self._flip_strike[idx] if idx < len(self._flip_strike) else None,
            gex_regime=self._gex_regime[idx] if idx < len(self._gex_regime) else "",
            quality_flags=quality_flags,
        )
        self._lookup_cache[cache_key] = snapshot
        return snapshot

    def lookup_options_state(
        self,
        cutoff_time_ns: int,
        *,
        realized_vol_30d: float | None,
        realized_vol_source_timestamp_ns: int,
    ) -> OptionsState:
        """Return the immutable OptionsState visible at a confirmed 4h cutoff.

        The 24-hour comparison is an ASOF lookup: the latest snapshot whose
        source timestamp is no later than ``current_source - 24h``. Future
        chain files are never read into the selected state.
        """
        from zenquant.data.options_state import DAY_NS, build_options_state

        cache_key = (cutoff_time_ns, realized_vol_30d, realized_vol_source_timestamp_ns)
        cached = self._state_lookup_cache.get(cache_key)
        if cached is not None:
            return cached
        idx = bisect.bisect_right(self._timestamps, cutoff_time_ns) - 1
        if idx < 0:
            state = build_options_state(
                (),
                cutoff_time_ns=cutoff_time_ns,
                realized_vol_30d=realized_vol_30d,
                realized_vol_source_timestamp_ns=realized_vol_source_timestamp_ns,
                stale_after_ns=self._config.staleness_threshold_ns,
            )
            self._state_lookup_cache[cache_key] = state
            return state
        source_ns = self._timestamps[idx]
        history_idx = bisect.bisect_right(self._timestamps, source_ns - DAY_NS) - 1
        history_rows = self._chain_rows[history_idx] if history_idx >= 0 else ()
        state = build_options_state(
            self._chain_rows[idx],
            cutoff_time_ns=cutoff_time_ns,
            realized_vol_30d=realized_vol_30d,
            realized_vol_source_timestamp_ns=realized_vol_source_timestamp_ns,
            history_24h_rows=history_rows,
            stale_after_ns=self._config.staleness_threshold_ns,
        )
        self._state_lookup_cache[cache_key] = state
        return state

    def _compute_gex(self, df, spot: float) -> tuple[float, float | None, float | None, str]:
        """Compute GEX metrics from full options chain."""
        try:
            from zenquant.research.gex import compute_gex_profile

            rows = df.to_dict("records")
            profile = compute_gex_profile(rows, spot=spot)
            return (
                profile.net_gex,
                profile.gamma_wall_strike,
                profile.flip_strike,
                profile.regime,
            )
        except Exception:
            return 0.0, None, None, ""

    def _compute_atm_iv(self, df) -> float | None:
        """Extract ATM IV from options chain at ~30-day tenor.

        Logic:
        1. Find the expiry closest to 30 days (Deribit expiries are sparse)
        2. Find strikes closest to underlying_price (ATM)
        3. Weight call + put IV by open_interest
        4. Return weighted average
        """
        import pandas as pd

        target_dte = 30

        # Compute DTE (days to expiry)
        now_ts = df["snapshot_ts_ns"].iloc[0] / 1e9
        df = df.copy()
        df["dte"] = (df["expiry_ts_ms"] / 1000 - now_ts) / 86400

        # Find closest available expiry to 30 days (within 7-90 day range)
        eligible = df[(df["dte"] >= 7) & (df["dte"] <= 90)]
        if eligible.empty:
            return None

        # Pick the expiry closest to target
        unique_dtes = eligible["dte"].round(0).unique()
        closest_dte = min(unique_dtes, key=lambda d: abs(d - target_dte))
        valid = eligible[eligible["dte"].round(0) == closest_dte]
        if valid.empty:
            return None

        # Find ATM strikes (closest to spot)
        spot = valid["underlying_price"].iloc[0]
        valid = valid.copy()
        valid["dist_to_atm"] = abs(valid["strike"] - spot)
        atm_options = valid.nsmallest(20, "dist_to_atm")

        # Weighted average by OI
        total_iv_weighted = 0.0
        total_oi = 0.0
        for _, opt in atm_options.iterrows():
            if opt["open_interest"] > 0 and pd.notna(opt["mark_iv"]):
                total_iv_weighted += opt["mark_iv"] * opt["open_interest"]
                total_oi += opt["open_interest"]

        return (total_iv_weighted / total_oi) if total_oi > 0 else None

    def _compute_moneyness_skew_proxy(self, df) -> float | None:
        """Compute the legacy 90%-put minus 110%-call moneyness proxy.

        Use 90% strike (0.9*spot) for put, 110% strike (1.1*spot) for call.
        This is explicitly not a Black-76 25-delta risk reversal. Positive
        values mean the selected OTM put IV exceeds the selected OTM call IV.
        """
        import pandas as pd

        target_dte = 30

        # Filter by DTE
        now_ts = df["snapshot_ts_ns"].iloc[0] / 1e9
        df = df.copy()
        df["dte"] = (df["expiry_ts_ms"] / 1000 - now_ts) / 86400

        # Find closest expiry to 30 days
        eligible = df[(df["dte"] >= 7) & (df["dte"] <= 90)]
        if eligible.empty:
            return None
        unique_dtes = eligible["dte"].round(0).unique()
        closest_dte = min(unique_dtes, key=lambda d: abs(d - target_dte))
        valid = eligible[eligible["dte"].round(0) == closest_dte]
        if valid.empty:
            return None

        spot = valid["underlying_price"].iloc[0]
        put_strike = 0.9 * spot
        call_strike = 1.1 * spot

        # Find closest strikes
        puts = valid[valid["option_type"] == "put"]
        calls = valid[valid["option_type"] == "call"]

        if puts.empty or calls.empty:
            return None

        puts = puts.copy()
        puts["dist"] = abs(puts["strike"] - put_strike)
        closest_put = puts.nsmallest(1, "dist")

        calls = calls.copy()
        calls["dist"] = abs(calls["strike"] - call_strike)
        closest_call = calls.nsmallest(1, "dist")

        if closest_put.empty or closest_call.empty:
            return None

        put_iv = closest_put.iloc[0]["mark_iv"]
        call_iv = closest_call.iloc[0]["mark_iv"]

        if pd.isna(put_iv) or pd.isna(call_iv):
            return None

        return float(put_iv - call_iv)

    def _compute_term_structure(self, df) -> float | None:
        """Compute IV term structure slope: (far_IV - near_IV) / far_IV.

        Positive = contango (normal), negative = backwardation (stress).
        Uses closest available expiries to 7d (near) and 90d (far).
        """
        import pandas as pd

        # Compute DTE
        now_ts = df["snapshot_ts_ns"].iloc[0] / 1e9
        df = df.copy()
        df["dte"] = (df["expiry_ts_ms"] / 1000 - now_ts) / 86400

        # Find near-term (closest to 7d, must be 3-30d)
        near_eligible = df[(df["dte"] >= 3) & (df["dte"] <= 30)]
        # Find far-term (closest to 90d, must be 45-365d)
        far_eligible = df[(df["dte"] >= 45) & (df["dte"] <= 365)]

        if near_eligible.empty or far_eligible.empty:
            return None

        near_dtes = near_eligible["dte"].round(0).unique()
        far_dtes = far_eligible["dte"].round(0).unique()
        closest_near = min(near_dtes, key=lambda d: abs(d - 7))
        closest_far = min(far_dtes, key=lambda d: abs(d - 90))

        near = near_eligible[near_eligible["dte"].round(0) == closest_near]
        far = far_eligible[far_eligible["dte"].round(0) == closest_far]

        if near.empty or far.empty:
            return None

        # Compute ATM IV for each tenor
        spot = df["underlying_price"].iloc[0]

        def _atm_iv(options):
            options = options.copy()
            options["dist"] = abs(options["strike"] - spot)
            atm = options.nsmallest(10, "dist")
            total_iv_weighted = 0.0
            total_oi = 0.0
            for _, opt in atm.iterrows():
                if opt["open_interest"] > 0 and pd.notna(opt["mark_iv"]):
                    total_iv_weighted += opt["mark_iv"] * opt["open_interest"]
                    total_oi += opt["open_interest"]
            return (total_iv_weighted / total_oi) if total_oi > 0 else None

        near_iv = _atm_iv(near)
        far_iv = _atm_iv(far)

        if near_iv is None or far_iv is None or far_iv < 1e-9:
            return None

        return float((far_iv - near_iv) / far_iv)
