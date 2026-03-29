"""Stress discount from crowding, volatility, and quality.

Extracted from ``SizingEngine._stress_discount()``.
Combines three independent discount factors multiplicatively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SizerResult


@dataclass(frozen=True, slots=True)
class Config:
    positioning_discount_max: float = 0.65
    positioning_discount_floor: float = 0.35
    volatility_distorted_discount: float = 0.75
    volatility_high_discount: float = 0.90


meta = PluginMeta(
    name="crowding_discount_sizer",
    version="1.0.0",
    plugin_type="sizer",
    description="Discount leverage for crowding, vol stress, and quality degradation",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SizerResult:
    funding_oi_z = abs(float(indicators.get("funding_oi_z", 0.0) or 0.0))
    vol_state = state.get("volatility_state", "NORMAL")
    quality_state = state.get("quality_state", "TRADABLE")

    # Positioning discount
    pos_max = config.get("positioning_discount_max", 0.65)
    pos_floor = config.get("positioning_discount_floor", 0.35)
    positioning_discount = max(pos_floor, 1.0 - min(pos_max, funding_oi_z / 4.0))

    # Volatility discount
    vol_distorted = config.get("volatility_distorted_discount", 0.75)
    vol_high = config.get("volatility_high_discount", 0.90)
    if vol_state == "DISTORTED":
        volatility_discount = vol_distorted
    elif vol_state == "HIGH":
        volatility_discount = vol_high
    else:
        volatility_discount = 1.0

    # Quality discount
    quality_discount = {
        "TRADABLE": 1.0,
        "NOISY": 1.0,
        "DEGRADED": 0.9,
        "UNSAFE": 0.0,
    }.get(quality_state, 0.9)

    factor = max(0.0, positioning_discount * volatility_discount * quality_discount)

    codes = []
    if funding_oi_z > 1.8:
        codes.append("CROWDING_DISCOUNT")
    if vol_state in ("DISTORTED", "HIGH"):
        codes.append("VOLATILITY_STRESS")
    if quality_state in ("DEGRADED", "UNSAFE"):
        codes.append("QUALITY_DEGRADED")

    return SizerResult(factor=round(factor, 6), reason_codes=codes)
