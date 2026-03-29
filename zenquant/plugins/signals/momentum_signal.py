"""Directional signal from swing structure + momentum ROC z-score.

Extracted from ``ARCStrategyEngine._direction()`` TREND-regime logic.
Only fires when the regime is TREND and structure + momentum confirm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


@dataclass(frozen=True, slots=True)
class Config:
    momentum_threshold: float = 0.3


meta = PluginMeta(
    name="momentum_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Structure + momentum confirmation in TREND regime",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime != "TREND":
        return SignalResult(value=0.0, confidence=0.0)

    structure = str(indicators.get("structure_state", "NEUTRAL"))
    roc_z = float(indicators.get("momentum_roc_z", 0.0) or 0.0)
    threshold = config.get("momentum_threshold", 0.3)

    if structure == "UPTREND" and roc_z > threshold:
        confidence = min(1.0, 0.6 + abs(roc_z) / 8.0)
        return SignalResult(
            value=1.0,
            confidence=confidence,
            reason_codes=["STRUCTURE_UPTREND", "MOMENTUM_BULL"],
        )
    if structure == "DOWNTREND" and roc_z < -threshold:
        confidence = min(1.0, 0.6 + abs(roc_z) / 8.0)
        return SignalResult(
            value=-1.0,
            confidence=confidence,
            reason_codes=["STRUCTURE_DOWNTREND", "MOMENTUM_BEAR"],
        )

    return SignalResult(value=0.0, confidence=0.3)
