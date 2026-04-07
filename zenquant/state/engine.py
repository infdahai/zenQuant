"""Explicit market-state classification layer for Phase 3 evolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.domain import IndicatorPack, MarketState, Timeframe
from zenquant.infra import EventBus


@dataclass(slots=True)
class _IndicatorValues:
    realized_vol: float
    cvd_slope: float
    funding_oi_z: float
    zen_pulse: float
    structure_state: str
    momentum_roc_z: float
    vix_z: float
    dxy_z: float
    yield_10y_z: float
    liq_intensity_z: float = 0.0
    liq_imbalance: float = 0.0
    liq_max_single_share: float = 0.0
    liq_stale: bool = False
    cycle_position: float = 1.0  # price/MA200: <0.8 bear, >1.3 overheated


@dataclass(slots=True, frozen=True)
class StateEngineConfig:
    """Feature flags for deterministic state classification research."""

    enable_liquidation_overlay: bool = True


class StateEngine:
    """Publish a deterministic 4D ``MarketState`` for every indicator pack.

    Implements hysteresis to avoid rapid state flipping and computes transition
    risk to warn when state is about to change.
    """

    def __init__(self, bus: EventBus, config: StateEngineConfig | None = None) -> None:
        self._bus = bus
        self._config = config or StateEngineConfig()
        self._last_regime: dict[tuple[str, Timeframe], str] = {}
        self._last_directional: dict[tuple[str, Timeframe], str] = {}
        # Track structure_state history for directional consistency
        from collections import defaultdict, deque

        self._structure_history: dict[tuple[str, Timeframe], deque[str]] = defaultdict(
            lambda: deque(maxlen=120)  # ~20 days of 4H bars
        )
        self._bus.subscribe("indicator.>", self._on_indicator)

    def _on_indicator(self, _: str, payload: object) -> None:
        if not isinstance(payload, IndicatorPack):
            return
        state = self.classify(payload)
        self._bus.publish(
            f"state.market.{payload.timeframe.value}.{payload.symbol}",
            state,
        )

    def classify(self, pack: IndicatorPack) -> MarketState:
        vals = _extract_values(pack.values)
        key = (pack.symbol, pack.timeframe)
        last_regime = self._last_regime.get(key, "RANGE")
        last_directional = self._last_directional.get(key, "NEUTRAL")

        directional_state, directional_confidence, direction_signal = _classify_directional_state(
            structure_state=vals.structure_state,
            momentum_roc_z=vals.momentum_roc_z,
            cvd_slope=vals.cvd_slope,
            zen_pulse=vals.zen_pulse,
            liq_intensity_z=vals.liq_intensity_z if self._config.enable_liquidation_overlay else 0.0,
            liq_imbalance=vals.liq_imbalance if self._config.enable_liquidation_overlay else 0.0,
            liq_stale=vals.liq_stale if self._config.enable_liquidation_overlay else True,
            cycle_position=vals.cycle_position,
            last_directional=last_directional,
        )
        volatility_state, volatility_confidence = _classify_volatility_state(
            quality_flags=pack.quality_flags,
            realized_vol=vals.realized_vol,
            vix_z=vals.vix_z,
        )
        positioning_state, positioning_confidence = _classify_positioning_state(
            funding_oi_z=vals.funding_oi_z,
        )
        quality_state, quality_confidence = _classify_quality_state(
            quality_flags=pack.quality_flags,
        )
        # Track structure history for directional consistency
        self._structure_history[key].append(vals.structure_state)
        hist = self._structure_history[key]
        up = sum(1 for s in hist if s == "UPTREND")
        down = sum(1 for s in hist if s == "DOWNTREND")
        directional_consistency = abs(up - down) / max(up + down, 1)  # 0=chop, 1=consistent

        regime, regime_confidence = _classify_regime(
            last_regime=last_regime,
            quality_flags=pack.quality_flags,
            realized_vol=vals.realized_vol,
            cvd_slope=vals.cvd_slope,
            zen_pulse=vals.zen_pulse,
            liq_intensity_z=vals.liq_intensity_z if self._config.enable_liquidation_overlay else 0.0,
            liq_imbalance=vals.liq_imbalance if self._config.enable_liquidation_overlay else 0.0,
            liq_stale=vals.liq_stale if self._config.enable_liquidation_overlay else True,
            yield_10y_z=vals.yield_10y_z,
            cycle_position=vals.cycle_position,
            directional_consistency=directional_consistency,
        )
        self._last_regime[key] = regime
        self._last_directional[key] = directional_state

        # Compute transition risk: low confidence = high transition risk
        min_confidence = min(
            directional_confidence,
            volatility_confidence,
            positioning_confidence,
            quality_confidence,
        )
        transition_risk = max(0.0, 1.0 - min_confidence)
        reason_codes = [
            f"DIRECTION_{directional_state}",
            f"VOL_{volatility_state}",
            f"POSITIONING_{positioning_state}",
            f"QUALITY_{quality_state}",
            f"REGIME_{regime}",
        ]
        if self._config.enable_liquidation_overlay and not vals.liq_stale and vals.liq_intensity_z >= 2.2:
            reason_codes.append("LIQUIDATION_CASCADE")
        if self._config.enable_liquidation_overlay and not vals.liq_stale and vals.liq_imbalance >= 0.2:
            reason_codes.append("LIQ_LONGS_FLUSHED")
        if self._config.enable_liquidation_overlay and not vals.liq_stale and vals.liq_imbalance <= -0.2:
            reason_codes.append("LIQ_SHORTS_FLUSHED")

        return MarketState(
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            cutoff_time_ns=pack.cutoff_time_ns,
            directional_state=directional_state,
            directional_confidence=round(directional_confidence, 4),
            volatility_state=volatility_state,
            volatility_confidence=round(volatility_confidence, 4),
            positioning_state=positioning_state,
            positioning_confidence=round(positioning_confidence, 4),
            quality_state=quality_state,
            quality_confidence=round(quality_confidence, 4),
            regime=regime,
            regime_confidence=round(regime_confidence, 4),
            direction_signal=direction_signal,
            transition_risk=round(transition_risk, 4),
            quality_flags=list(pack.quality_flags),
            reason_codes=reason_codes,
        )


def _extract_values(values: dict[str, Any]) -> _IndicatorValues:
    return _IndicatorValues(
        realized_vol=float(values.get("realized_vol", 0.0) or 0.0),
        cvd_slope=float(values.get("cvd_slope", 0.0) or 0.0),
        funding_oi_z=float(values.get("funding_oi_z", 0.0) or 0.0),
        zen_pulse=float(values.get("zen_pulse", 50.0) or 50.0),
        structure_state=str(values.get("structure_state") or values.get("swing_structure") or "NEUTRAL"),
        momentum_roc_z=float(values.get("momentum_roc_z", 0.0) or 0.0),
        vix_z=float(values.get("vix_z", 0.0) or 0.0),
        dxy_z=float(values.get("dxy_z", 0.0) or 0.0),
        yield_10y_z=float(values.get("yield_10y_z", 0.0) or 0.0),
        liq_intensity_z=float(values.get("liq_intensity_z", 0.0) or 0.0),
        liq_imbalance=float(values.get("liq_imbalance", 0.0) or 0.0),
        liq_max_single_share=float(values.get("liq_max_single_share", 0.0) or 0.0),
        liq_stale=bool(values.get("liq_stale", False)),
        cycle_position=float(values.get("cycle_position", 1.0) or 1.0),
    )


def _classify_directional_state(
    *,
    structure_state: str,
    momentum_roc_z: float,
    cvd_slope: float,
    zen_pulse: float,
    liq_intensity_z: float,
    liq_imbalance: float,
    liq_stale: bool,
    cycle_position: float,
    last_directional: str,
) -> tuple[str, float, float]:
    """Classify directional state with hysteresis to avoid rapid flipping.

    Args:
        structure_state: Swing structure (UPTREND | DOWNTREND | NEUTRAL)
        momentum_roc_z: Momentum z-score
        last_directional: Previous directional state for hysteresis

    Returns:
        (directional_state, confidence, direction_signal)
    """
    bearish_grind = momentum_roc_z <= -0.18 and cvd_slope <= -0.025 and zen_pulse <= 49.0 and cycle_position <= 1.15
    bearish_transition = (
        momentum_roc_z <= -0.10 and cvd_slope <= -0.015 and zen_pulse <= 50.0 and cycle_position <= 1.20
    )
    liquidation_bearish = not liq_stale and liq_intensity_z >= 2.2 and liq_imbalance >= 0.20

    # Strong trend signals (high threshold for entry)
    if structure_state == "UPTREND" and momentum_roc_z > 0.5:
        return "UPTREND", min(1.0, 0.55 + min(0.35, momentum_roc_z / 4.0)), 1.0
    if structure_state == "DOWNTREND" and momentum_roc_z < -0.5:
        return "DOWNTREND", min(1.0, 0.55 + min(0.35, abs(momentum_roc_z) / 4.0)), -1.0
    if structure_state == "DOWNTREND" and bearish_grind:
        confidence = min(1.0, 0.58 + min(0.18, abs(momentum_roc_z) / 5.0))
        if liquidation_bearish:
            confidence = min(1.0, confidence + 0.12)
        return "DOWNTREND", confidence, -0.9
    if liquidation_bearish and bearish_transition:
        confidence = min(1.0, 0.60 + min(0.15, liq_intensity_z / 10.0))
        return "DOWNTREND", confidence, -0.9

    # Hysteresis: stay in trend with lower threshold
    if last_directional == "UPTREND" and structure_state == "UPTREND" and momentum_roc_z > 0.2:
        return "UPTREND", 0.5, 1.0  # lower confidence but stay in trend
    if last_directional == "DOWNTREND" and structure_state == "DOWNTREND" and momentum_roc_z < -0.2:
        return "DOWNTREND", 0.5, -1.0
    if last_directional == "DOWNTREND" and structure_state in {"DOWNTREND", "NEUTRAL"} and bearish_transition:
        return "DOWNTREND", 0.60 if liquidation_bearish else 0.52, -0.8

    # Clear neutral state
    if structure_state == "NEUTRAL" and abs(momentum_roc_z) < 0.3:
        if bearish_transition:
            return "TRANSITION", 0.64 if liquidation_bearish else 0.56, -0.70 if liquidation_bearish else -0.65
        return "NEUTRAL", 0.75, 0.0

    if bearish_transition:
        return "TRANSITION", 0.60 if liquidation_bearish else 0.52, -0.70 if liquidation_bearish else -0.55

    # Transition state (conflicting signals or weak trend)
    return "TRANSITION", 0.45, 0.0


def _classify_volatility_state(
    *,
    quality_flags: list[str],
    realized_vol: float,
    vix_z: float = 0.0,
) -> tuple[str, float]:
    degraded = any(flag in {"BOOK_STALE", "VENUE_DOWN", "DATA_STALE", "DATA_FUTURE"} for flag in quality_flags)
    if degraded or realized_vol > 2.8:
        return "DISTORTED", 0.9
    if vix_z > 3.0:
        return "DISTORTED", 0.85
    if realized_vol > 1.5 or vix_z > 2.0:
        return "HIGH", 0.75
    if realized_vol < 0.45:
        return "LOW", 0.7
    return "NORMAL", 0.8


def _classify_positioning_state(*, funding_oi_z: float) -> tuple[str, float]:
    if funding_oi_z >= 1.8:
        return "CROWDED_LONG", min(1.0, 0.65 + min(0.25, funding_oi_z / 8.0))
    if funding_oi_z <= -1.8:
        return "CROWDED_SHORT", min(1.0, 0.65 + min(0.25, abs(funding_oi_z) / 8.0))
    if abs(funding_oi_z) <= 0.5:
        return "HEALTHY", 0.8
    return "UNCERTAIN", 0.6


def _classify_quality_state(*, quality_flags: list[str]) -> tuple[str, float]:
    flags = set(quality_flags or ["OK"])
    if flags & {"DATA_FUTURE", "VENUE_DOWN"}:
        return "UNSAFE", 0.95
    if flags & {"DATA_STALE", "BOOK_STALE"}:
        return "DEGRADED", 0.85
    if flags & {"MACRO_STALE"}:
        return "NOISY", 0.7
    if flags != {"OK"}:
        return "NOISY", 0.7
    return "TRADABLE", 0.9


def _classify_regime(
    *,
    last_regime: str,
    quality_flags: list[str],
    realized_vol: float,
    cvd_slope: float,
    zen_pulse: float,
    liq_intensity_z: float,
    liq_imbalance: float,
    liq_stale: bool,
    yield_10y_z: float = 0.0,
    cycle_position: float = 1.0,
    directional_consistency: float = 1.0,
) -> tuple[str, float]:
    degraded = any(flag in {"BOOK_STALE", "VENUE_DOWN", "DATA_STALE", "DATA_FUTURE"} for flag in quality_flags)
    if degraded or realized_vol > 2.8:
        return "STRESS", 0.9
    if not liq_stale and liq_intensity_z >= 2.4 and abs(liq_imbalance) >= 0.2:
        return "STRESS", min(0.95, 0.78 + min(0.12, liq_intensity_z / 12.0))

    trend_strength = min(1.0, abs(cvd_slope) * 10.0)
    pulse_strength = min(1.0, max(0.0, (zen_pulse - 50.0) / 50.0))

    if last_regime == "TREND":
        trend_gate = trend_strength > 0.12 and zen_pulse >= 50.0
    else:
        trend_gate = trend_strength > 0.22 and zen_pulse >= 58.0

    if trend_gate:
        confidence = min(1.0, 0.45 + (trend_strength * 0.4) + (pulse_strength * 0.15))
        if abs(yield_10y_z) > 2.0:
            confidence *= 0.9
        if cycle_position > 1.3:
            confidence *= 0.8
        elif cycle_position < 0.8:
            confidence *= 0.85
        return "TREND", confidence

    confidence = max(0.35, 1.0 - min(0.6, abs(cvd_slope) * 5.0))
    return "RANGE", confidence
