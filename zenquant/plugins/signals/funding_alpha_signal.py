"""Funding rate as alpha signal (contrarian).

Extreme funding rates predict short-term reversals:
- Very high funding (>0.05%/8h): longs overloaded → expect pullback
- Very negative funding (<-0.03%/8h): shorts overloaded → expect bounce

Distinct from crowding_signal which uses funding as risk indicator.
This uses funding as direction signal (contrarian mean-reversion).

Based on: BIS (2024) "funding rate is a leading indicator of market sentiment"
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="funding_alpha_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Contrarian direction from extreme funding rates",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime == "STRESS":
        return SignalResult(value=0.0, confidence=0.0)

    funding_oi_z = float(indicators.get("funding_oi_z", 0.0) or 0.0)

    high_threshold = config.get("high_funding_z", 2.0)
    low_threshold = config.get("low_funding_z", -2.0)

    # Extreme positive funding: longs paying high → overleveraged → bearish
    if funding_oi_z > high_threshold:
        magnitude = min(0.8, 0.4 + (funding_oi_z - high_threshold) / 5)
        confidence = min(0.8, 0.4 + abs(funding_oi_z) / 8)
        return SignalResult(
            value=-magnitude,  # Bearish (contrarian to crowded longs)
            confidence=confidence,
            reason_codes=("FUNDING_LONGS_CROWDED",),
        )

    # Extreme negative funding: shorts paying → overleveraged shorts → bullish
    if funding_oi_z < low_threshold:
        magnitude = min(0.8, 0.4 + abs(funding_oi_z + low_threshold) / 5)
        confidence = min(0.8, 0.4 + abs(funding_oi_z) / 8)
        return SignalResult(
            value=magnitude,  # Bullish (contrarian to crowded shorts)
            confidence=confidence,
            reason_codes=("FUNDING_SHORTS_CROWDED",),
        )

    return SignalResult(value=0.0, confidence=0.1)
