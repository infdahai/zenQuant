"""Cross-asset macro regime signal from VIX, DXY, and Treasury yields.

Synthesizes traditional macro indicators into a risk-on/risk-off directional
signal for crypto.  High VIX + strong dollar = risk-off (bearish crypto);
low VIX + weak dollar = risk-on (bullish crypto).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


@dataclass(frozen=True, slots=True)
class Config:
    vix_extreme_threshold: float = 1.5  # z-score for extreme VIX
    dxy_strong_threshold: float = 1.5  # z-score for strong dollar move
    yield_stress_threshold: float = 2.0  # z-score for yield stress


meta = PluginMeta(
    name="macro_regime_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Cross-asset macro regime signal from VIX + DXY + Treasury yields",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    # Suppress in STRESS regime — consistent with other plugins
    regime = state.get("regime", "RANGE")
    if regime == "STRESS":
        return SignalResult(value=0.0, confidence=0.0)

    vix_z = float(indicators.get("vix_z", 0.0) or 0.0)
    dxy_z = float(indicators.get("dxy_z", 0.0) or 0.0)
    yield_z = float(indicators.get("yield_10y_z", 0.0) or 0.0)

    vix_threshold = config.get("vix_extreme_threshold", 1.5)
    dxy_threshold = config.get("dxy_strong_threshold", 1.5)
    yield_threshold = config.get("yield_stress_threshold", 2.0)

    # No macro data available — stay neutral
    if vix_z == 0.0 and dxy_z == 0.0 and yield_z == 0.0:
        return SignalResult(value=0.0, confidence=0.0)

    # Risk-off: high VIX + strong dollar → bearish crypto
    if vix_z > vix_threshold and dxy_z > 0:
        magnitude = min(1.0, (vix_z - vix_threshold) / 2.0 + 0.6)
        confidence = min(1.0, 0.4 + abs(vix_z) / 6.0)
        # Yield stress amplifies the signal
        if yield_z > yield_threshold:
            confidence = min(1.0, confidence + 0.15)
        return SignalResult(
            value=-magnitude,
            confidence=confidence,
            reason_codes=("MACRO_RISK_OFF",),
        )

    # Risk-on: low VIX + weak dollar → bullish crypto
    if vix_z < -vix_threshold and dxy_z < 0:
        magnitude = min(0.7, (abs(vix_z) - vix_threshold) / 3.0 + 0.4)
        confidence = min(1.0, 0.35 + abs(vix_z) / 8.0)
        return SignalResult(
            value=magnitude,
            confidence=confidence,
            reason_codes=("MACRO_RISK_ON",),
        )

    # Strong dollar alone (without extreme VIX) — mild bearish
    if dxy_z > dxy_threshold:
        return SignalResult(
            value=-0.3,
            confidence=0.3,
            reason_codes=("MACRO_STRONG_USD",),
        )

    # Weak dollar alone — mild bullish
    if dxy_z < -dxy_threshold:
        return SignalResult(
            value=0.3,
            confidence=0.3,
            reason_codes=("MACRO_WEAK_USD",),
        )

    # Below thresholds — neutral
    return SignalResult(value=0.0, confidence=0.2)
