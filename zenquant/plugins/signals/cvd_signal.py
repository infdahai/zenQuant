"""Directional signal from Cumulative Volume Delta slope.

Extracted from ``ARCStrategyEngine._direction()`` TREND-regime fallback.
Returns the sign of CVD slope as a directional bias when the regime is TREND.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="cvd_signal",
    version="1.0.0",
    plugin_type="signal",
    description="CVD slope directional bias in TREND regime",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime != "TREND":
        return SignalResult(value=0.0, confidence=0.0)

    cvd_slope = float(indicators.get("cvd_slope", 0.0) or 0.0)

    if cvd_slope > 0:
        return SignalResult(
            value=1.0,
            confidence=0.5,
            reason_codes=["CVD_POSITIVE"],
        )
    if cvd_slope < 0:
        return SignalResult(
            value=-1.0,
            confidence=0.5,
            reason_codes=["CVD_NEGATIVE"],
        )
    return SignalResult(value=0.0, confidence=0.2)
