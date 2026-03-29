"""Vol-targeted base leverage sizing.

Extracted from ``SizingEngine._base_size()``.
Scales leverage inversely with realized volatility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SizerResult


@dataclass(frozen=True, slots=True)
class Config:
    target_vol_4h: float = 0.60
    target_vol_1d: float = 0.50
    min_realized_vol_ratio: float = 0.25
    max_factor: float = 3.5  # hard safety cap on the output factor


meta = PluginMeta(
    name="vol_target_sizer",
    version="1.0.0",
    plugin_type="sizer",
    description="Vol-targeted base leverage from realized volatility",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SizerResult:
    timeframe = state.get("timeframe", "4h")
    if timeframe == "1d":
        target_vol = config.get("target_vol_1d", 0.50)
    else:
        target_vol = config.get("target_vol_4h", 0.60)

    realized_vol = float(indicators.get("realized_vol", 0.0) or 0.0)
    min_ratio = config.get("min_realized_vol_ratio", 0.25)

    realized_floor = max(realized_vol, target_vol * min_ratio)
    factor = target_vol / max(realized_floor, 1e-9)

    max_factor = config.get("max_factor", 3.5)
    capped = factor > max_factor
    factor = min(factor, max_factor)

    reason_codes = ["SIZE_BASE_VOL_TARGET"]
    if capped:
        reason_codes.append("SIZE_FACTOR_CAPPED")

    return SizerResult(
        factor=round(factor, 6),
        reason_codes=tuple(reason_codes),
    )
