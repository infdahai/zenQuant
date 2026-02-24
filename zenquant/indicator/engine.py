from __future__ import annotations

import hashlib
import math
from collections import defaultdict, deque
from dataclasses import dataclass

from zenquant.domain import Candle, IndicatorCoverage, IndicatorPack, MarketSnapshot, Timeframe
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
    target_vol_4h: float = 0.025
    target_vol_1d: float = 0.04


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
        self._formula_hash = _formula_hash()

        self._pending: dict[tuple[str, Timeframe, int], _PendingPoint] = {}
        self._candles: dict[tuple[str, Timeframe], deque[Candle]] = defaultdict(
            lambda: deque(maxlen=max(self._config.vol_window, self._config.atr_window) + 8)
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
            return

        candle = pending.candle
        snapshot = pending.snapshot
        history_key = (candle.symbol, candle.timeframe)
        self._candles[history_key].append(candle)

        ob_raw = _ob_imbalance(snapshot.bid_depth, snapshot.ask_depth)
        ob_series = self._ob_values[history_key]
        ob_series.append(ob_raw)
        ob_liq_z = _robust_z(list(ob_series), ob_raw)

        funding_value = snapshot.funding_oi_weighted if snapshot.funding_oi_weighted is not None else 0.0
        funding_series = self._funding_values[history_key]
        funding_series.append(funding_value)
        funding_oi_z = _robust_z(list(funding_series), funding_value)

        cvd_series = self._cvd_values[history_key]
        cvd_series.append(snapshot.cvd_delta)
        cvd_slope = _slope(list(cvd_series))

        candles = list(self._candles[history_key])
        realized_vol = _realized_vol(candles, window=self._config.vol_window, timeframe=candle.timeframe)
        atr = _atr(candles, window=self._config.atr_window)

        index_key = (candle.timeframe, candle.bar_end_ns)
        self._ob_index_by_cutoff[index_key][candle.symbol] = ob_liq_z
        overall_ob_liq_z = sum(self._ob_index_by_cutoff[index_key].values()) / len(self._ob_index_by_cutoff[index_key])

        target_vol = self._config.target_vol_4h if candle.timeframe is Timeframe.TF_4H else self._config.target_vol_1d
        zen_pulse, components = _zen_pulse(
            ob_liq_z=ob_liq_z,
            cvd_slope=cvd_slope,
            funding_oi_z=funding_oi_z,
            realized_vol=realized_vol,
            target_vol=target_vol,
            quality_flags=snapshot.quality_flags,
        )

        quality_flags = sorted(set(snapshot.quality_flags)) if snapshot.quality_flags else ["OK"]
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
                "overall_ob_liq_z": overall_ob_liq_z,
                "cvd_slope": cvd_slope,
                "funding_oi_z": funding_oi_z,
                "funding_oi_weighted": snapshot.funding_oi_weighted,
                "last_price": snapshot.last_price,
                "zen_pulse": zen_pulse,
                "book_age_ms": snapshot.book_age_ms,
                "venue_count": snapshot.venue_count,
                "oi_coverage": snapshot.oi_coverage,
            },
            components=components,
        )
        self._store.append(pack)
        self._bus.publish(f"indicator.pack.v1.{candle.timeframe.value}.{candle.symbol}", pack)
        self._pending.pop(key, None)


def _formula_hash() -> str:
    formula = (
        "rv=std(logret);atr=mean(tr);ob=log(bid/ask);z=0.6745*(x-med)/mad;"
        "cvd=slope(cvd_delta);funding_oi_z=robust_z(funding_oi_weighted);"
        "zenpulse=weighted_components"
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


def _robust_z(values: list[float], current: float) -> float:
    if len(values) < 6:
        return 0.0
    reference = values[:-1] if len(values) > 8 else values
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


def _zen_pulse(
    ob_liq_z: float,
    cvd_slope: float,
    funding_oi_z: float,
    realized_vol: float,
    target_vol: float,
    quality_flags: list[str],
) -> tuple[float, dict[str, float]]:
    trend_component = _clip01(0.5 + (cvd_slope * 3.0))
    liquidity_component = _clip01(0.5 + (ob_liq_z / 6.0))
    crowding_component = _clip01(1.0 - min(1.0, abs(funding_oi_z) / 4.0))
    vol_component = _clip01(1.0 - max(0.0, realized_vol - target_vol) / max(target_vol, 1e-6))

    quality_penalty = 0.12 * max(0, len([flag for flag in quality_flags if flag != "OK"]))
    raw = (
        (trend_component * 0.33)
        + (liquidity_component * 0.23)
        + (crowding_component * 0.22)
        + (vol_component * 0.22)
        - quality_penalty
    )
    score = _clip01(raw) * 100.0
    return round(score, 2), {
        "trend": round(trend_component, 4),
        "liquidity": round(liquidity_component, 4),
        "crowding": round(crowding_component, 4),
        "volatility": round(vol_component, 4),
    }


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))
