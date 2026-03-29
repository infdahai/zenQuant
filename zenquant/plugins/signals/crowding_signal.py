"""Contrarian signal from funding rate × OI crowding.

Extreme crowding indicates potential mean-reversion. This signal is
weak by default — it serves as a tiebreaker or sentiment modifier,
not a primary directional driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


@dataclass(frozen=True, slots=True)
class Config:
    extreme_threshold: float = 1.8


meta = PluginMeta(
    name="crowding_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Contrarian signal from funding×OI crowding extremes",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    funding_oi_z = float(indicators.get("funding_oi_z", 0.0) or 0.0)
    threshold = config.get("extreme_threshold", 1.8)

    if abs(funding_oi_z) < threshold:
        return SignalResult(value=0.0, confidence=0.2)

    # Extreme crowding → contrarian signal
    # High positive funding_oi_z = crowded long → bearish signal
    direction = -1.0 if funding_oi_z > 0 else 1.0
    confidence = min(1.0, abs(funding_oi_z) / 4.0)

    return SignalResult(
        value=direction,
        confidence=confidence,
        reason_codes=[f"CROWDING_{'LONG' if funding_oi_z > 0 else 'SHORT'}"],
    )
