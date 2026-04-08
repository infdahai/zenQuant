"""Perpetual carry/leverage stress state modifier."""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="perp_carry_stress_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Penalize crowded leveraged trends; fade overstretched carry pressure",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime == "STRESS":
        return SignalResult(value=0.0, confidence=0.0)

    carry_stress = float(indicators.get("carry_stress_score", 0.0) or 0.0)
    leverage_pressure = float(indicators.get("leverage_pressure_score", 0.0) or 0.0)
    trend_persistence = float(indicators.get("trend_persistence", 0.0) or 0.0)

    carry_threshold = float(config.get("carry_stress_threshold", 0.45))
    leverage_threshold = float(config.get("leverage_pressure_threshold", 0.45))

    if leverage_pressure < leverage_threshold or abs(carry_stress) < carry_threshold:
        return SignalResult(value=0.0, confidence=0.1)

    magnitude = min(0.7, 0.25 + (abs(carry_stress) - carry_threshold) * 0.9 + max(trend_persistence - 0.5, 0.0) * 0.3)
    confidence = min(0.9, 0.4 + leverage_pressure * 0.5)

    if carry_stress > 0:
        return SignalResult(
            value=-magnitude,
            confidence=confidence,
            reason_codes=("PERP_CARRY_LONG_STRESS",),
        )

    return SignalResult(
        value=magnitude,
        confidence=confidence,
        reason_codes=("PERP_CARRY_SHORT_STRESS",),
    )
