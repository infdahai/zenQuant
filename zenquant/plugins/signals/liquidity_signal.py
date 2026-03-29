"""Mean-reversion signal from order book liquidity imbalance.

Extracted from ``ARCStrategyEngine._direction()`` RANGE-regime logic.
Fades extreme order-book imbalance when CVD is calm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


@dataclass(frozen=True, slots=True)
class Config:
    cvd_calm_threshold: float = 0.03
    ob_extreme_threshold: float = 1.2


meta = PluginMeta(
    name="liquidity_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Mean-reversion on order book imbalance in RANGE regime",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime != "RANGE":
        return SignalResult(value=0.0, confidence=0.0)

    cvd_slope = float(indicators.get("cvd_slope", 0.0) or 0.0)
    ob_liq_z = float(indicators.get("ob_liq_z", 0.0) or 0.0)
    cvd_calm = config.get("cvd_calm_threshold", 0.03)
    ob_extreme = config.get("ob_extreme_threshold", 1.2)

    if abs(cvd_slope) < cvd_calm and abs(ob_liq_z) > ob_extreme:
        # Fade the imbalance: high ob_liq_z → go short, low → go long
        direction = -1.0 if ob_liq_z > 0 else 1.0
        confidence = min(1.0, abs(ob_liq_z) / 4.0)
        return SignalResult(
            value=direction,
            confidence=confidence,
            reason_codes=["LIQUIDITY_MEAN_REVERSION"],
        )
    return SignalResult(value=0.0, confidence=0.2)
