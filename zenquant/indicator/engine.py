"""Indicator engine that converts bar-close market state into indicator packs.

The engine only emits after it has both the finalized candle and the matching
market snapshot for the same cutoff time. That guard is a core determinism
invariant for live, replay, and backtest parity.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict, deque
from dataclasses import dataclass

from zenquant.domain import (
    Candle,
    IndicatorCoverage,
    IndicatorPack,
    MarketSnapshot,
    SwingPoint,
    SwingStructure,
    Timeframe,
)
from zenquant.infra import EventBus


@dataclass(slots=True)
class IndicatorStoreConfig:
    """History depth for retained indicator packs."""

    max_points: int = 1_500


class IndicatorStore:
    """In-memory store used by the API, dashboard, and tests."""

    def __init__(self, config: IndicatorStoreConfig | None = None) -> None:
        self._config = config or IndicatorStoreConfig()
        self._history: dict[tuple[str, Timeframe], deque[IndicatorPack]] = defaultdict(
            lambda: deque(maxlen=self._config.max_points)
        )

    def append(self, pack: IndicatorPack) -> None:
        """Append one new indicator pack to the retained history."""
        self._history[(pack.symbol, pack.timeframe)].append(pack)

    def latest(self, symbol: str, timeframe: Timeframe) -> IndicatorPack | None:
        """Return the latest indicator pack for a symbol/timeframe."""
        history = self._history.get((symbol, timeframe))
        if not history:
            return None
        return history[-1]

    def history(
        self,
        symbol: str,
        timeframe: Timeframe,
        from_ns: int | None = None,
        to_ns: int | None = None,
    ) -> list[IndicatorPack]:
        """Return retained history, optionally filtered by cutoff timestamp."""
        history = list(self._history.get((symbol, timeframe), []))
        if from_ns is not None:
            history = [item for item in history if item.cutoff_time_ns >= from_ns]
        if to_ns is not None:
            history = [item for item in history if item.cutoff_time_ns <= to_ns]
        return history


@dataclass(slots=True)
class IndicatorEngineConfig:
    """Feature windows and cross-symbol settings for indicator generation."""

    indicator_version: str = "pack.v1"
    vol_window: int = 20
    atr_window: int = 14
    cvd_window: int = 8
    z_window: int = 60
    robust_z_min_samples: int = 15
    pending_ttl_bars: int = 3
    expected_symbols: tuple[str, ...] = ("BTCUSDT_PERP", "ETHUSDT_PERP")
    # NOTE: target_vol values are ANNUALIZED to match realized_vol output
    # which is scaled by sqrt(365 * bars_per_day).  BTC ~60-80% ann, ETH ~70-90%.
    target_vol_1h: float = 0.55
    target_vol_5m: float = 0.45
    target_vol_4h: float = 0.60
    target_vol_1d: float = 0.50
    # Swing structure: N-bar pivot lookback (confirmed after N bars on each side)
    pivot_lookback: int = 5
    # Max swing points retained for structure classification
    max_swing_points: int = 50
    # Momentum ROC: lookback period in bars and z-score window
    roc_lookback: int = 12
    roc_z_window: int = 40
    # Multi-horizon momentum: horizons in bars (4H bars → 1d,2d,4d,8d)
    roc_horizons: tuple[int, ...] = (6, 12, 24, 48)


@dataclass(slots=True)
class _PendingPoint:
    candle: Candle | None = None
    snapshot: MarketSnapshot | None = None


class IndicatorEngine:
    """Build the canonical indicator pack consumed by strategy and dashboard.

    Example:
        engine = IndicatorEngine(bus=bus, store=IndicatorStore())
        # after matching candle + snapshot events, ``indicator.pack.v1...`` is published.
    """

    def __init__(
        self,
        bus: EventBus,
        store: IndicatorStore,
        config: IndicatorEngineConfig | None = None,
        macro_store: object | None = None,
        options_store: object | None = None,
        hf_store: object | None = None,
        cot_store: object | None = None,
        basis_store: object | None = None,
        eth_store: object | None = None,
        liquidation_store: object | None = None,
    ) -> None:
        self._bus = bus
        self._store = store
        self._config = config or IndicatorEngineConfig()
        self._macro_store = macro_store  # MacroDataStore (optional, avoids circular import)
        self._options_store = options_store  # OptionsDataStore (optional, avoids circular import)
        self._hf_store = hf_store  # HFFeatureStore (optional, 5min features)
        self._cot_store = cot_store  # CotDataStore (optional, weekly institutional positioning)
        self._basis_store = basis_store  # BasisDataStore (optional, spot-perp premium)
        self._eth_store = eth_store  # EthDataStore (optional, cross-asset TE)
        self._liquidation_store = liquidation_store  # LiquidationDataStore (optional, aggregated force-order data)
        self._btc_returns_buffer: list[float] = []  # Rolling BTC returns for TE
        self._formula_hash = _formula_hash(
            self._config,
            has_macro=macro_store is not None,
            has_options=options_store is not None,
            has_liquidation=liquidation_store is not None,
        )

        self._pending: dict[tuple[str, Timeframe, int], _PendingPoint] = {}
        # Buffer must hold enough bars for vol, atr, pivot lookback, and ROC
        _buf = (
            max(
                self._config.vol_window,
                self._config.atr_window,
                max(self._config.roc_horizons) + self._config.roc_z_window,
                self._config.roc_lookback + self._config.roc_z_window,
                2 * self._config.pivot_lookback + 4,
            )
            + 8
        )
        self._candles: dict[tuple[str, Timeframe], deque[Candle]] = defaultdict(lambda: deque(maxlen=_buf))
        # Phase 2: swing point history per (symbol, timeframe)
        self._swing_points: dict[tuple[str, Timeframe], deque[SwingPoint]] = defaultdict(
            lambda: deque(maxlen=self._config.max_swing_points)
        )
        self._last_swing_structure: dict[tuple[str, Timeframe], SwingStructure] = {}
        # Phase 2: momentum ROC z-score history
        self._roc_values: dict[tuple[str, Timeframe], deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.roc_z_window)
        )
        # Multi-horizon ROC history (one deque per horizon)
        self._roc_horizon_values: dict[tuple[str, Timeframe, int], deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.roc_z_window)
        )
        self._cvd_values: dict[tuple[str, Timeframe], deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.cvd_window)
        )
        self._ob_values: dict[tuple[str, Timeframe], deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.z_window)
        )
        self._funding_values: dict[tuple[str, Timeframe], deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.z_window)
        )
        self._ob_index_by_cutoff: dict[tuple[Timeframe, int], dict[str, float]] = defaultdict(dict)
        self._pending_overall: dict[tuple[Timeframe, int], list[IndicatorPack]] = defaultdict(list)
        self._overall_by_cutoff: dict[tuple[Timeframe, int], float] = {}
        # Daily close prices for 200MA cycle position (per symbol)
        self._daily_closes: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=250))
        # Structure state history for directional consistency
        self._structure_states: dict[tuple[str, Timeframe], deque[str]] = defaultdict(
            lambda: deque(maxlen=120)  # ~20 days of 4H bars
        )

        self._bus.subscribe("md.candle.>", self._on_candle)
        self._bus.subscribe("md.snapshot.>", self._on_snapshot)

    def _on_candle(self, _: str, payload: object) -> None:
        if not isinstance(payload, Candle):
            return
        key = (payload.symbol, payload.timeframe, payload.bar_end_ns)
        pending = self._pending.setdefault(key, _PendingPoint())
        pending.candle = payload
        self._try_emit(key)

    def _on_snapshot(self, _: str, payload: object) -> None:
        if not isinstance(payload, MarketSnapshot):
            return
        key = (payload.symbol, payload.timeframe, payload.cutoff_time_ns)
        pending = self._pending.setdefault(key, _PendingPoint())
        pending.snapshot = payload
        self._try_emit(key)

    def _try_emit(self, key: tuple[str, Timeframe, int]) -> None:
        """Emit an indicator pack only when candle and snapshot are both present."""
        pending = self._pending.get(key)
        if pending is None or pending.candle is None or pending.snapshot is None:
            self._gc_pending(key[1], key[2])
            return

        candle = pending.candle
        snapshot = pending.snapshot
        history_key = (candle.symbol, candle.timeframe)
        self._candles[history_key].append(candle)

        ob_raw = _ob_imbalance(snapshot.bid_depth, snapshot.ask_depth)
        ob_series = self._ob_values[history_key]
        ob_series.append(ob_raw)
        ob_liq_z = _robust_z(list(ob_series), ob_raw, min_samples=self._config.robust_z_min_samples)

        funding_value = snapshot.funding_oi_weighted if snapshot.funding_oi_weighted is not None else 0.0
        funding_series = self._funding_values[history_key]
        funding_series.append(funding_value)
        funding_oi_z = _robust_z(
            list(funding_series),
            funding_value,
            min_samples=self._config.robust_z_min_samples,
        )

        cvd_series = self._cvd_values[history_key]
        cvd_series.append(snapshot.cvd_delta)
        cvd_slope = _slope(list(cvd_series))

        candles = list(self._candles[history_key])
        realized_vol = _realized_vol(candles, window=self._config.vol_window, timeframe=candle.timeframe)
        atr = _atr(candles, window=self._config.atr_window)

        # --- Phase 2: Swing structure detection ---
        new_pivots = _detect_pivots(candles, lookback=self._config.pivot_lookback)
        swing_points = self._swing_points[history_key]
        for sp in new_pivots:
            # Avoid duplicates (same bar_end_ns + is_high)
            if not swing_points or (
                sp.bar_end_ns != swing_points[-1].bar_end_ns or sp.is_high != swing_points[-1].is_high
            ):
                swing_points.append(sp)
        prev_structure = self._last_swing_structure.get(history_key)
        swing_struct = _classify_structure(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            cutoff_time_ns=candle.bar_end_ns,
            swing_points=list(swing_points),
            prev_structure=prev_structure,
            pivot_lookback=self._config.pivot_lookback,
        )
        self._last_swing_structure[history_key] = swing_struct

        # Directional consistency: ratio of |UPTREND - DOWNTREND| / total
        self._structure_states[history_key].append(swing_struct.structure_state)
        _sh = self._structure_states[history_key]
        _up = sum(1 for s in _sh if s == "UPTREND")
        _dn = sum(1 for s in _sh if s == "DOWNTREND")
        directional_consistency = abs(_up - _dn) / max(_up + _dn, 1)

        # --- Phase 2: Momentum ROC z-score ---
        roc_raw = _rate_of_change(candles, lookback=self._config.roc_lookback)
        roc_series = self._roc_values[history_key]
        roc_series.append(roc_raw)
        momentum_roc_z = _robust_z(list(roc_series), roc_raw, min_samples=self._config.robust_z_min_samples)

        # --- Multi-horizon momentum z-score ---
        horizon_zscores: list[float] = []
        for h in self._config.roc_horizons:
            h_roc = _rate_of_change(candles, lookback=h)
            h_key = (history_key[0], history_key[1], h)
            h_series = self._roc_horizon_values[h_key]
            h_series.append(h_roc)
            h_z = _robust_z(list(h_series), h_roc, min_samples=self._config.robust_z_min_samples)
            horizon_zscores.append(h_z)
        multi_momentum_z = sum(horizon_zscores) / len(horizon_zscores) if horizon_zscores else 0.0

        index_key = (candle.timeframe, candle.bar_end_ns)
        self._ob_index_by_cutoff[index_key][candle.symbol] = ob_liq_z

        target_vol = {
            Timeframe.TF_5M: self._config.target_vol_5m,
            Timeframe.TF_1H: self._config.target_vol_1h,
            Timeframe.TF_4H: self._config.target_vol_4h,
            Timeframe.TF_1D: self._config.target_vol_1d,
        }.get(candle.timeframe, self._config.target_vol_4h)
        zen_pulse, components, weights = _zen_pulse(
            ob_liq_z=ob_liq_z,
            cvd_slope=cvd_slope,
            funding_oi_z=funding_oi_z,
            realized_vol=realized_vol,
            target_vol=target_vol,
            quality_flags=snapshot.quality_flags,
        )

        quality_flags = sorted(set(snapshot.quality_flags)) if snapshot.quality_flags else ["OK"]

        # Inject macro indicator values if store is available
        macro_values: dict[str, object] = {}
        if self._macro_store is not None and hasattr(self._macro_store, "lookup"):
            macro_snap = self._macro_store.lookup(candle.bar_end_ns)
            macro_values = {
                "vix": macro_snap.vix,
                "vix_z": macro_snap.vix_z,
                "dxy": macro_snap.dxy,
                "dxy_z": macro_snap.dxy_z,
                "yield_10y": macro_snap.yield_10y,
                "yield_10y_z": macro_snap.yield_10y_z,
                "tlt": macro_snap.tlt,
                "tlt_z": macro_snap.tlt_z,
                "qqq_z": macro_snap.qqq_z,
                "iwm_z": macro_snap.iwm_z,
                # Cross-sectional macro factors (individual, not composite)
                "ndx_z": macro_snap.ndx_z,
                "gold_z": macro_snap.gold_z,
                "btc_mvrv": macro_snap.btc_mvrv,
                "btc_mvrv_z": macro_snap.btc_mvrv_z,
                "btc_etf_netflow": macro_snap.btc_etf_netflow,
                "btc_etf_netflow_z": macro_snap.btc_etf_netflow_z,
                "btc_etf_flow_3d": macro_snap.btc_etf_flow_3d,
                "btc_etf_flow_5d": macro_snap.btc_etf_flow_5d,
                "btc_etf_flow_accel": macro_snap.btc_etf_flow_accel,
                "btc_etf_positive_streak": macro_snap.btc_etf_positive_streak,
                "btc_etf_negative_streak": macro_snap.btc_etf_negative_streak,
                "cme_asset_mgr_net": macro_snap.cme_asset_mgr_net,
                "cme_asset_mgr_net_z": macro_snap.cme_asset_mgr_net_z,
                "cme_lev_money_net": macro_snap.cme_lev_money_net,
                "cme_lev_money_net_z": macro_snap.cme_lev_money_net_z,
                "cme_dealer_net": macro_snap.cme_dealer_net,
                "cme_dealer_net_z": macro_snap.cme_dealer_net_z,
                "cme_total_oi": macro_snap.cme_total_oi,
                "cme_total_oi_z": macro_snap.cme_total_oi_z,
            }
            for flag in macro_snap.quality_flags:
                if flag not in quality_flags:
                    quality_flags.append(flag)
            quality_flags = sorted(set(quality_flags))

        # Inject options indicator values if store is available
        if self._options_store is not None and hasattr(self._options_store, "lookup"):
            options_snap = self._options_store.lookup(candle.bar_end_ns)
            options_values = {
                "atm_iv_30d": options_snap.atm_iv_30d,
                "atm_iv_z": options_snap.atm_iv_z,
                "term_structure_slope": options_snap.term_structure_slope,
                "term_slope_z": options_snap.term_slope_z,
                "put_call_skew_25d": options_snap.put_call_skew_25d,
                "skew_z": options_snap.skew_z,
                "skew_velocity": options_snap.skew_velocity,
                "gex_net": options_snap.net_gex,
                "gex_regime": options_snap.gex_regime,
                "gamma_wall_strike": options_snap.gamma_wall_strike,
            }
            macro_values.update(options_values)
            for flag in options_snap.quality_flags:
                if flag not in quality_flags:
                    quality_flags.append(flag)
            quality_flags = sorted(set(quality_flags))

        # Accumulate daily closes for 200MA cycle position
        if candle.timeframe is Timeframe.TF_1D:
            self._daily_closes[candle.symbol].append(candle.close)
        # Compute cycle_position = current_price / MA_200
        daily_closes = self._daily_closes.get(candle.symbol)
        if daily_closes and len(daily_closes) >= 200:
            ma_200 = sum(list(daily_closes)[-200:]) / 200
            cycle_position = candle.close / ma_200 if ma_200 > 0 else 1.0
        else:
            cycle_position = 1.0  # Neutral when insufficient data
        macro_values["cycle_position"] = round(cycle_position, 4)
        macro_values["directional_consistency"] = round(directional_consistency, 4)

        # OU process: mean-reversion speed κ and fragility ratio
        ou_kappa = _ou_kappa(candles, window=self._config.vol_window)
        total_depth = snapshot.bid_depth + snapshot.ask_depth
        fragility_ratio = (ou_kappa / total_depth) if total_depth > 1e-9 else 0.0
        macro_values["ou_kappa"] = round(ou_kappa, 6)
        macro_values["fragility_ratio"] = round(fragility_ratio, 6)

        # Future Basis proxy: funding_oi_weighted captures leveraged demand direction
        # Positive = longs pay shorts (bullish demand), negative = shorts pay longs
        # Already available as funding_oi_z in the pack, but raw value useful for thresholds
        macro_values["funding_oi_raw"] = round(funding_value, 6)

        # Volume ratio: current volume / 20-bar average
        vol_values = [c.volume for c in candles[-21:]] if len(candles) >= 21 else []
        if len(vol_values) >= 2:
            vol_avg = sum(vol_values[:-1]) / len(vol_values[:-1])
            volume_ratio = candle.volume / vol_avg if vol_avg > 0 else 1.0
        else:
            volume_ratio = 1.0
        macro_values["volume_ratio"] = round(volume_ratio, 2)
        macro_values["open"] = candle.open

        # Vol ratio: short-term RV / long-term RV (vol spike detection)
        rv_60 = _realized_vol(candles, window=60, timeframe=candle.timeframe)
        vol_ratio_rv = realized_vol / rv_60 if rv_60 > 0.01 else 1.0
        macro_values["vol_ratio_rv"] = round(vol_ratio_rv, 3)

        # Inject 5min HF features (rv_hf is strongest factor, IC=+0.248)
        if self._hf_store is not None and hasattr(self._hf_store, "lookup"):
            if candle.timeframe == Timeframe.TF_1H and hasattr(self._hf_store, "lookup_1h"):
                hf = self._hf_store.lookup_1h(candle.bar_end_ns)
                macro_values["rv_hf_1h"] = hf.realized_vol_hf
                macro_values["consistency_1h"] = hf.intra_bar_consistency
                macro_values["volume_front_ratio_1h"] = hf.volume_front_ratio
            else:
                hf = self._hf_store.lookup(candle.bar_end_ns)
            macro_values["rv_hf"] = hf.realized_vol_hf
            macro_values["intra_bar_max_dd"] = hf.intra_bar_max_dd

        # Inject CFTC COT institutional positioning (weekly, slow signal)
        if self._cot_store is not None and hasattr(self._cot_store, "lookup"):
            cot = self._cot_store.lookup(candle.bar_end_ns)
            macro_values["cot_asset_mgr_z"] = cot.asset_mgr_z
            macro_values["cot_leveraged_z"] = cot.leveraged_z

        # Inject spot-perp basis (whale flow detection)
        if self._basis_store is not None and hasattr(self._basis_store, "lookup"):
            basis = self._basis_store.lookup(candle.bar_end_ns)
            macro_values["basis_pct"] = basis.basis_pct
            macro_values["basis_z"] = basis.basis_z

        # Inject ETH→BTC Transfer Entropy (cross-asset information flow)
        if self._eth_store is not None and hasattr(self._eth_store, "get_returns_before"):
            # Track BTC returns for TE computation
            btc_ret = 0.0
            if len(self._candles.get((candle.symbol, candle.timeframe), [])) >= 2:
                prev = list(self._candles[(candle.symbol, candle.timeframe)])[-2]
                if prev.close > 0:
                    btc_ret = (candle.close - prev.close) / prev.close
            self._btc_returns_buffer.append(btc_ret)
            if len(self._btc_returns_buffer) > 200:
                self._btc_returns_buffer = self._btc_returns_buffer[-200:]

            eth_returns = self._eth_store.get_returns_before(candle.bar_end_ns, window=120)
            if len(eth_returns) >= 60 and len(self._btc_returns_buffer) >= 60:
                from zenquant.research.transfer_entropy import transfer_entropy

                n = min(len(eth_returns), len(self._btc_returns_buffer))
                te = transfer_entropy(eth_returns[-n:], self._btc_returns_buffer[-n:])
                macro_values["te_eth_to_btc"] = round(te, 4)
                # ETH momentum for direction
                if len(eth_returns) >= 6:
                    macro_values["eth_momentum"] = sum(eth_returns[-6:])

        # Liquidation data is aggregated venue pressure, not tick-precise execution truth.
        if self._liquidation_store is not None and hasattr(self._liquidation_store, "lookup"):
            liq = self._liquidation_store.lookup(candle.bar_end_ns)
            total_liq = max(0.0, float(liq.total_liq_usd))
            max_single = max(0.0, float(liq.max_single_liq_usd))
            macro_values["liq_total_usd"] = total_liq
            macro_values["liq_long_usd"] = float(liq.long_liq_usd)
            macro_values["liq_short_usd"] = float(liq.short_liq_usd)
            macro_values["liq_imbalance"] = float(liq.liq_imbalance)
            macro_values["liq_intensity_z"] = float(liq.liq_intensity_z)
            macro_values["liq_imbalance_z"] = float(liq.liq_imbalance_z)
            macro_values["liq_max_single_usd"] = max_single
            macro_values["liq_max_single_share"] = (max_single / total_liq) if total_liq > 0 else 0.0
            macro_values["liq_stale"] = "LIQ_STALE" in liq.quality_flags
            for flag in liq.quality_flags:
                if flag not in quality_flags:
                    quality_flags.append(flag)
            quality_flags = sorted(set(quality_flags))

        pack = IndicatorPack(
            ts_ns=candle.bar_end_ns,
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            cutoff_time_ns=candle.bar_end_ns,
            indicator_version=self._config.indicator_version,
            formula_hash=self._formula_hash,
            quality_flags=quality_flags,
            coverage=IndicatorCoverage(
                venue_count=snapshot.venue_count,
                oi_coverage=snapshot.oi_coverage,
                staleness_ms=snapshot.staleness_ms,
            ),
            values={
                "realized_vol": realized_vol,
                "atr": atr,
                "ob_liq_z": ob_liq_z,
                "overall_ob_liq_z": 0.0,
                "cvd_slope": cvd_slope,
                "vpin": snapshot.vpin,
                "large_trade_count": snapshot.large_trade_count,
                "funding_oi_z": funding_oi_z,
                "funding_oi_weighted": snapshot.funding_oi_weighted,
                "last_price": snapshot.last_price,
                "zen_pulse": zen_pulse,
                "zen_pulse_weights": weights,
                "book_age_ms": snapshot.book_age_ms,
                "venue_count": snapshot.venue_count,
                "oi_coverage": snapshot.oi_coverage,
                # Phase 2 additions
                "structure_state": swing_struct.structure_state,
                "structure_break": swing_struct.structure_break,
                "last_swing_high": swing_struct.last_swing_high,
                "last_swing_low": swing_struct.last_swing_low,
                "bars_since_break": swing_struct.bars_since_break,
                "momentum_roc": roc_raw,
                "momentum_roc_z": momentum_roc_z,
                "multi_momentum_z": multi_momentum_z,
                **macro_values,
            },
            components=components,
        )
        # SwingStructure does not depend on cross-symbol aggregation —
        # publish unconditionally before the overall_ob_liq_z gate.
        self._bus.publish(
            f"indicator.structure.{pack.timeframe.value}.{pack.symbol}",
            swing_struct,
        )

        overall_known = self._overall_by_cutoff.get(index_key)
        if overall_known is not None:
            pack.values["overall_ob_liq_z"] = overall_known
            self._store.append(pack)
            self._bus.publish(
                f"indicator.{self._config.indicator_version}.{pack.timeframe.value}.{pack.symbol}",
                pack,
            )
        else:
            self._pending_overall[index_key].append(pack)
            self._flush_overall_if_ready(index_key)
            self._flush_stale_overall(candle.timeframe, candle.bar_end_ns)

        self._pending.pop(key, None)
        self._gc_pending(candle.timeframe, candle.bar_end_ns)
        self._gc_ob_index(candle.timeframe, candle.bar_end_ns)

    def _is_full_symbol_coverage(self, symbols_for_bar: dict[str, float]) -> bool:
        expected = self._config.expected_symbols
        if not expected:
            return len(symbols_for_bar) > 0
        return all(symbol in symbols_for_bar for symbol in expected)

    def _flush_overall_if_ready(self, index_key: tuple[Timeframe, int]) -> None:
        """Publish cross-symbol liquidity context once expected symbols are present."""
        symbols_for_bar = self._ob_index_by_cutoff.get(index_key)
        if not symbols_for_bar:
            return
        if not self._is_full_symbol_coverage(symbols_for_bar):
            return
        self._flush_overall(index_key, symbols_for_bar)

    def _flush_stale_overall(self, timeframe: Timeframe, bar_end_ns: int) -> None:
        """Fail closed when full symbol coverage never arrives within the TTL window."""
        ttl_ns = self._config.pending_ttl_bars * timeframe.seconds * 1_000_000_000
        cutoff_ns = bar_end_ns - ttl_ns
        stale_keys = [key for key in self._pending_overall if key[0] is timeframe and key[1] < cutoff_ns]
        for key in stale_keys:
            symbols_for_bar = self._ob_index_by_cutoff.get(key, {})
            self._flush_overall(key, symbols_for_bar)

    def _flush_overall(self, index_key: tuple[Timeframe, int], symbols_for_bar: dict[str, float]) -> None:
        packs = self._pending_overall.pop(index_key, [])
        if not packs:
            return
        if symbols_for_bar:
            # TODO(Phase2): replace equal-weight with market-cap/OI weighted aggregation.
            overall_ob_liq_z = sum(symbols_for_bar.values()) / len(symbols_for_bar)
        else:
            overall_ob_liq_z = 0.0
        self._overall_by_cutoff[index_key] = overall_ob_liq_z
        for pack in packs:
            pack.values["overall_ob_liq_z"] = overall_ob_liq_z
            self._store.append(pack)
            self._bus.publish(
                f"indicator.{self._config.indicator_version}.{pack.timeframe.value}.{pack.symbol}",
                pack,
            )

    def _gc_pending(self, timeframe: Timeframe, bar_end_ns: int) -> None:
        ttl_ns = self._config.pending_ttl_bars * timeframe.seconds * 1_000_000_000
        cutoff_ns = bar_end_ns - ttl_ns
        stale = [key for key in self._pending if key[1] is timeframe and key[2] < cutoff_ns]
        for key in stale:
            self._pending.pop(key, None)

    def _gc_ob_index(self, timeframe: Timeframe, bar_end_ns: int) -> None:
        cutoff_to_keep = bar_end_ns - (4 * timeframe.seconds * 1_000_000_000)
        stale = [key for key in self._ob_index_by_cutoff if key[0] is timeframe and key[1] < cutoff_to_keep]
        for key in stale:
            self._ob_index_by_cutoff.pop(key, None)
            self._pending_overall.pop(key, None)
            self._overall_by_cutoff.pop(key, None)


def _formula_hash(
    config: IndicatorEngineConfig,
    *,
    has_macro: bool = False,
    has_options: bool = False,
    has_liquidation: bool = False,
) -> str:
    """Fingerprint the indicator formula so decisions stay auditable across versions."""
    formula = (
        f"rv=std(logret,w={config.vol_window});"
        f"atr=mean(tr,w={config.atr_window});"
        f"ob=log(bid/ask);"
        f"z=0.6745*(x-med)/mad,min_samples={config.robust_z_min_samples};"
        f"cvd_window={config.cvd_window};"
        f"z_window={config.z_window};"
        f"pivot_lookback={config.pivot_lookback};"
        f"roc_lookback={config.roc_lookback};"
        f"roc_z_window={config.roc_z_window};"
        f"roc_horizons={','.join(str(h) for h in config.roc_horizons)};"
        f"expected_symbols={','.join(config.expected_symbols)};"
        f"macro={'vix,dxy,yield_10y' if has_macro else 'none'};"
        f"options={'atm_iv,skew,term' if has_options else 'none'};"
        f"liquidation={'total,imbalance,max_single' if has_liquidation else 'none'};"
        f"cycle=200ma;ou_kappa=vol_window"
    )
    return f"sha256:{hashlib.sha256(formula.encode('utf-8')).hexdigest()}"


def _ob_imbalance(bid_depth: float, ask_depth: float) -> float:
    """Compute log-depth imbalance so equal ratios stay symmetric around zero."""
    return math.log((bid_depth + 1e-9) / (ask_depth + 1e-9))


from zenquant.data._z_utils import robust_z as _robust_z  # noqa: E402


def _slope(values: list[float]) -> float:
    """Return a scale-normalized linear slope for a short indicator series."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    var_x = 0.0
    cov_xy = 0.0
    for idx, value in enumerate(values):
        dx = idx - x_mean
        var_x += dx * dx
        cov_xy += dx * (value - y_mean)
    if var_x <= 1e-12:
        return 0.0
    # NOTE: normalized by mean absolute value to keep BTC/ETH slope scales comparable.
    slope = cov_xy / var_x
    scale = max(1.0, sum(abs(v) for v in values) / n)
    return slope / scale


def _realized_vol(candles: list[Candle], window: int, timeframe: Timeframe) -> float:
    """Estimate annualized realized volatility from log returns."""
    if len(candles) < 2:
        return 0.0
    closes = [c.close for c in candles[-(window + 1) :]]
    if len(closes) < 2:
        return 0.0
    returns: list[float] = []
    for idx in range(1, len(closes)):
        prior = closes[idx - 1]
        now = closes[idx]
        if prior <= 0 or now <= 0:
            continue
        returns.append(math.log(now / prior))
    if len(returns) < 2:
        return 0.0
    mean_ret = sum(returns) / len(returns)
    variance = sum((value - mean_ret) ** 2 for value in returns) / (len(returns) - 1)
    bar_per_day = {Timeframe.TF_5M: 288, Timeframe.TF_1H: 24, Timeframe.TF_4H: 6, Timeframe.TF_1D: 1}.get(
        timeframe, 6
    )
    annualizer = math.sqrt(365 * bar_per_day)
    return max(0.0, math.sqrt(max(0.0, variance)) * annualizer)


def _atr(candles: list[Candle], window: int) -> float:
    """Average true range over the configured window."""
    if len(candles) < 2:
        return 0.0
    subset = candles[-(window + 1) :]
    true_ranges: list[float] = []
    for idx in range(1, len(subset)):
        candle = subset[idx]
        prev = subset[idx - 1]
        tr = max(
            candle.high - candle.low,
            abs(candle.high - prev.close),
            abs(candle.low - prev.close),
        )
        true_ranges.append(tr)
    if not true_ranges:
        return 0.0
    return sum(true_ranges) / len(true_ranges)


def _ou_kappa(candles: list[Candle], window: int) -> float:
    """Estimate OU mean-reversion speed κ from price series.

    Uses OLS regression: ΔX_t = a + b*X_{t-1}
    where κ = -b and half_life = ln(2)/κ.

    Higher κ → market reverts quickly (stable).
    Lower κ → market has shock memory (fragile).
    Returns 0.0 if insufficient data.
    """
    if len(candles) < window + 1:
        return 0.0
    closes = [c.close for c in candles[-(window + 1) :]]
    if len(closes) < 3:
        return 0.0

    # Log prices for stationarity
    log_prices = [math.log(max(p, 1e-9)) for p in closes]

    # ΔX_t = log(P_t) - log(P_{t-1}), regress on X_{t-1}
    n = len(log_prices) - 1
    x_vals = log_prices[:-1]  # X_{t-1}
    y_vals = [log_prices[i + 1] - log_prices[i] for i in range(n)]  # ΔX_t

    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n

    var_x = 0.0
    cov_xy = 0.0
    for i in range(n):
        dx = x_vals[i] - x_mean
        var_x += dx * dx
        cov_xy += dx * (y_vals[i] - y_mean)

    if var_x < 1e-15:
        return 0.0

    beta = cov_xy / var_x
    kappa = -beta  # Mean reversion speed
    return max(0.0, kappa)  # Clamp to non-negative


ZEN_PULSE_WEIGHTS = {
    "trend": 0.33,
    "liquidity": 0.23,
    "crowding": 0.22,
    "volatility": 0.22,
}


def _zen_pulse(
    ob_liq_z: float,
    cvd_slope: float,
    funding_oi_z: float,
    realized_vol: float,
    target_vol: float,
    quality_flags: list[str],
) -> tuple[float, dict[str, float], dict[str, float]]:
    """Blend trend, liquidity, crowding, and volatility into a 0-100 score."""
    trend_component = _clip01(0.5 + (cvd_slope * 3.0))
    liquidity_component = _clip01(0.5 + (ob_liq_z / 6.0))
    crowding_component = _clip01(1.0 - min(1.0, abs(funding_oi_z) / 4.0))
    vol_component = _clip01(1.0 - max(0.0, realized_vol - target_vol) / max(target_vol, 1e-6))

    quality_penalty = min(0.35, 0.12 * sum(1 for flag in quality_flags if flag != "OK"))
    raw = (
        (trend_component * ZEN_PULSE_WEIGHTS["trend"])
        + (liquidity_component * ZEN_PULSE_WEIGHTS["liquidity"])
        + (crowding_component * ZEN_PULSE_WEIGHTS["crowding"])
        + (vol_component * ZEN_PULSE_WEIGHTS["volatility"])
        - quality_penalty
    )
    score = _clip01(raw) * 100.0
    return (
        round(score, 2),
        {
            "trend": round(trend_component, 4),
            "liquidity": round(liquidity_component, 4),
            "crowding": round(crowding_component, 4),
            "volatility": round(vol_component, 4),
        },
        dict(ZEN_PULSE_WEIGHTS),
    )


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Phase 2: Swing structure detection
# ---------------------------------------------------------------------------


def _detect_pivots(candles: list[Candle], lookback: int) -> list[SwingPoint]:
    """Detect N-bar pivot highs and lows.

    A swing high is confirmed when bar[i] has the highest high in the window
    [i-lookback .. i+lookback].  Similarly for swing lows.  Only the most
    recently confirmable pivot(s) are returned (i.e. the bar at index
    ``len(candles) - 1 - lookback`` if it qualifies).
    """
    n = len(candles)
    if n < 2 * lookback + 1:
        return []

    results: list[SwingPoint] = []
    # Only check the single bar that just became confirmable
    center = n - 1 - lookback
    c = candles[center]
    window = candles[center - lookback : center + lookback + 1]

    # Swing high: center bar high >= all neighbours' highs
    if all(c.high >= w.high for w in window):
        results.append(SwingPoint(bar_end_ns=c.bar_end_ns, price=c.high, is_high=True))

    # Swing low: center bar low <= all neighbours' lows
    if all(c.low <= w.low for w in window):
        results.append(SwingPoint(bar_end_ns=c.bar_end_ns, price=c.low, is_high=False))

    return results


def _classify_structure(
    symbol: str,
    timeframe: Timeframe,
    cutoff_time_ns: int,
    swing_points: list[SwingPoint],
    prev_structure: SwingStructure | None,
    pivot_lookback: int,
) -> SwingStructure:
    """Classify price structure as UPTREND / DOWNTREND / NEUTRAL from swing points.

    Logic:
    - Extract the last two swing highs and last two swing lows.
    - UPTREND  requires HH (higher high) AND HL (higher low).
    - DOWNTREND requires LH (lower high) AND LL (lower low).
    - Otherwise NEUTRAL.
    - ``structure_break`` is True on the bar where state changes from prev.
    """
    highs = [sp for sp in swing_points if sp.is_high]
    lows = [sp for sp in swing_points if not sp.is_high]

    last_swing_high = highs[-1].price if highs else 0.0
    last_swing_low = lows[-1].price if lows else 0.0

    state = "NEUTRAL"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price  # higher high
        hl = lows[-1].price > lows[-2].price  # higher low
        lh = highs[-1].price < highs[-2].price  # lower high
        ll = lows[-1].price < lows[-2].price  # lower low

        if hh and hl:
            state = "UPTREND"
        elif lh and ll:
            state = "DOWNTREND"

    prev_state = prev_structure.structure_state if prev_structure else "NEUTRAL"
    structure_break = state != prev_state

    if structure_break:
        bars_since_break = 0
    elif prev_structure is not None:
        bars_since_break = prev_structure.bars_since_break + 1
    else:
        bars_since_break = 0

    return SwingStructure(
        symbol=symbol,
        timeframe=timeframe,
        cutoff_time_ns=cutoff_time_ns,
        structure_state=state,
        last_swing_high=last_swing_high,
        last_swing_low=last_swing_low,
        structure_break=structure_break,
        bars_since_break=bars_since_break,
        pivot_lookback=pivot_lookback,
    )


# ---------------------------------------------------------------------------
# Phase 2: Momentum Rate of Change
# ---------------------------------------------------------------------------


def _rate_of_change(candles: list[Candle], lookback: int) -> float:
    """Percentage rate of change over ``lookback`` bars.

    Returns ``(close[-1] / close[-1-lookback]) - 1.0``.
    Returns 0.0 if insufficient data.
    """
    if len(candles) < lookback + 1:
        return 0.0
    now = candles[-1].close
    then = candles[-(lookback + 1)].close
    if then <= 0 or now <= 0:
        return 0.0
    return (now / then) - 1.0
