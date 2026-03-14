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
from zenquant.infra import InMemoryJetStream


@dataclass(slots=True)
class IndicatorStoreConfig:
    max_points: int = 1_500


class IndicatorStore:
    def __init__(self, config: IndicatorStoreConfig | None = None) -> None:
        self._config = config or IndicatorStoreConfig()
        self._history: dict[tuple[str, Timeframe], deque[IndicatorPack]] = defaultdict(
            lambda: deque(maxlen=self._config.max_points)
        )

    def append(self, pack: IndicatorPack) -> None:
        self._history[(pack.symbol, pack.timeframe)].append(pack)

    def latest(self, symbol: str, timeframe: Timeframe) -> IndicatorPack | None:
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
        history = list(self._history.get((symbol, timeframe), []))
        if from_ns is not None:
            history = [item for item in history if item.cutoff_time_ns >= from_ns]
        if to_ns is not None:
            history = [item for item in history if item.cutoff_time_ns <= to_ns]
        return history


@dataclass(slots=True)
class IndicatorEngineConfig:
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
    target_vol_4h: float = 0.60
    target_vol_1d: float = 0.50
    # Swing structure: N-bar pivot lookback (confirmed after N bars on each side)
    pivot_lookback: int = 5
    # Max swing points retained for structure classification
    max_swing_points: int = 50
    # Momentum ROC: lookback period in bars and z-score window
    roc_lookback: int = 12
    roc_z_window: int = 40


@dataclass(slots=True)
class _PendingPoint:
    candle: Candle | None = None
    snapshot: MarketSnapshot | None = None


class IndicatorEngine:
    def __init__(
        self,
        bus: InMemoryJetStream,
        store: IndicatorStore,
        config: IndicatorEngineConfig | None = None,
    ) -> None:
        self._bus = bus
        self._store = store
        self._config = config or IndicatorEngineConfig()
        self._formula_hash = _formula_hash(self._config)

        self._pending: dict[tuple[str, Timeframe, int], _PendingPoint] = {}
        # Buffer must hold enough bars for vol, atr, pivot lookback, and ROC
        _buf = (
            max(
                self._config.vol_window,
                self._config.atr_window,
                self._config.roc_lookback + self._config.roc_z_window,
                2 * self._config.pivot_lookback + 4,
            )
            + 8
        )
        self._candles: dict[tuple[str, Timeframe], deque[Candle]] = defaultdict(
            lambda: deque(maxlen=_buf)
        )
        # Phase 2: swing point history per (symbol, timeframe)
        self._swing_points: dict[tuple[str, Timeframe], deque[SwingPoint]] = (
            defaultdict(lambda: deque(maxlen=self._config.max_swing_points))
        )
        self._last_swing_structure: dict[tuple[str, Timeframe], SwingStructure] = {}
        # Phase 2: momentum ROC z-score history
        self._roc_values: dict[tuple[str, Timeframe], deque[float]] = defaultdict(
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
        self._ob_index_by_cutoff: dict[tuple[Timeframe, int], dict[str, float]] = (
            defaultdict(dict)
        )
        self._pending_overall: dict[tuple[Timeframe, int], list[IndicatorPack]] = (
            defaultdict(list)
        )
        self._overall_by_cutoff: dict[tuple[Timeframe, int], float] = {}

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
        ob_liq_z = _robust_z(
            list(ob_series), ob_raw, min_samples=self._config.robust_z_min_samples
        )

        funding_value = (
            snapshot.funding_oi_weighted
            if snapshot.funding_oi_weighted is not None
            else 0.0
        )
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
        realized_vol = _realized_vol(
            candles, window=self._config.vol_window, timeframe=candle.timeframe
        )
        atr = _atr(candles, window=self._config.atr_window)

        # --- Phase 2: Swing structure detection ---
        new_pivots = _detect_pivots(candles, lookback=self._config.pivot_lookback)
        swing_points = self._swing_points[history_key]
        for sp in new_pivots:
            # Avoid duplicates (same bar_end_ns + is_high)
            if not swing_points or (
                sp.bar_end_ns != swing_points[-1].bar_end_ns
                or sp.is_high != swing_points[-1].is_high
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

        # --- Phase 2: Momentum ROC z-score ---
        roc_raw = _rate_of_change(candles, lookback=self._config.roc_lookback)
        roc_series = self._roc_values[history_key]
        roc_series.append(roc_raw)
        momentum_roc_z = _robust_z(
            list(roc_series), roc_raw, min_samples=self._config.robust_z_min_samples
        )

        index_key = (candle.timeframe, candle.bar_end_ns)
        self._ob_index_by_cutoff[index_key][candle.symbol] = ob_liq_z

        target_vol = (
            self._config.target_vol_4h
            if candle.timeframe is Timeframe.TF_4H
            else self._config.target_vol_1d
        )
        zen_pulse, components, weights = _zen_pulse(
            ob_liq_z=ob_liq_z,
            cvd_slope=cvd_slope,
            funding_oi_z=funding_oi_z,
            realized_vol=realized_vol,
            target_vol=target_vol,
            quality_flags=snapshot.quality_flags,
        )

        quality_flags = (
            sorted(set(snapshot.quality_flags)) if snapshot.quality_flags else ["OK"]
        )
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
        symbols_for_bar = self._ob_index_by_cutoff.get(index_key)
        if not symbols_for_bar:
            return
        if not self._is_full_symbol_coverage(symbols_for_bar):
            return
        self._flush_overall(index_key, symbols_for_bar)

    def _flush_stale_overall(self, timeframe: Timeframe, bar_end_ns: int) -> None:
        ttl_ns = self._config.pending_ttl_bars * timeframe.seconds * 1_000_000_000
        cutoff_ns = bar_end_ns - ttl_ns
        stale_keys = [
            key
            for key in self._pending_overall
            if key[0] is timeframe and key[1] < cutoff_ns
        ]
        for key in stale_keys:
            symbols_for_bar = self._ob_index_by_cutoff.get(key, {})
            self._flush_overall(key, symbols_for_bar)

    def _flush_overall(
        self, index_key: tuple[Timeframe, int], symbols_for_bar: dict[str, float]
    ) -> None:
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
        stale = [
            key for key in self._pending if key[1] is timeframe and key[2] < cutoff_ns
        ]
        for key in stale:
            self._pending.pop(key, None)

    def _gc_ob_index(self, timeframe: Timeframe, bar_end_ns: int) -> None:
        cutoff_to_keep = bar_end_ns - (4 * timeframe.seconds * 1_000_000_000)
        stale = [
            key
            for key in self._ob_index_by_cutoff
            if key[0] is timeframe and key[1] < cutoff_to_keep
        ]
        for key in stale:
            self._ob_index_by_cutoff.pop(key, None)
            self._pending_overall.pop(key, None)
            self._overall_by_cutoff.pop(key, None)


def _formula_hash(config: IndicatorEngineConfig) -> str:
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
        f"expected_symbols={','.join(config.expected_symbols)}"
    )
    return f"sha256:{hashlib.sha256(formula.encode('utf-8')).hexdigest()}"


def _ob_imbalance(bid_depth: float, ask_depth: float) -> float:
    return math.log((bid_depth + 1e-9) / (ask_depth + 1e-9))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _mad(values: list[float], med: float) -> float:
    deviations = [abs(v - med) for v in values]
    return _median(deviations)


def _robust_z(values: list[float], current: float, min_samples: int) -> float:
    reference = values[:-1]
    if len(reference) < min_samples:
        return 0.0
    med = _median(reference)
    mad = _mad(reference, med)
    if mad < 1e-9:
        return 0.0
    z = 0.6745 * (current - med) / mad
    return max(-6.0, min(6.0, z))


def _slope(values: list[float]) -> float:
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
    bar_per_day = 6 if timeframe is Timeframe.TF_4H else 1
    annualizer = math.sqrt(365 * bar_per_day)
    return max(0.0, math.sqrt(max(0.0, variance)) * annualizer)


def _atr(candles: list[Candle], window: int) -> float:
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
    trend_component = _clip01(0.5 + (cvd_slope * 3.0))
    liquidity_component = _clip01(0.5 + (ob_liq_z / 6.0))
    crowding_component = _clip01(1.0 - min(1.0, abs(funding_oi_z) / 4.0))
    vol_component = _clip01(
        1.0 - max(0.0, realized_vol - target_vol) / max(target_vol, 1e-6)
    )

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
