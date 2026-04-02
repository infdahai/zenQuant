"""Multi-horizon momentum signal.

Aggregates momentum z-scores across 4 time horizons (6, 12, 24, 48 bars
on 4H = 1-day, 2-day, 4-day, 8-day) into a single direction signal.

Signal returns honest confidence — orchestrator uses confidence to weight
direction contribution. No manual dampening thresholds needed.

Based on Fieberg et al. (JFQA 2025): multi-horizon trend aggregation.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


meta = PluginMeta(
    name="multi_momentum_signal",
    version="2.0.0",
    plugin_type="signal",
    description="Multi-horizon momentum with confidence-based self-regulation",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime not in ("TREND", "RANGE"):
        return SignalResult(value=0.0, confidence=0.0)

    multi_z = indicators.get("multi_momentum_z")
    if multi_z is None and config.get("use_single_fallback", True):
        multi_z = indicators.get("momentum_roc_z", 0.0)
    if multi_z is None:
        return SignalResult(value=0.0, confidence=0.0)

    roc_z = float(multi_z or 0.0)
    threshold = config.get("momentum_threshold", 0.3)
    sign = float(config.get("signal_sign", 1.0))
    structure = str(indicators.get("structure_state", "NEUTRAL"))

    # Confidence naturally encodes regime suitability.
    # Orchestrator uses confidence to weight direction contribution.
    # No manual dampening — confidence does the work.

    if regime == "TREND":
        if structure == "UPTREND" and roc_z > threshold:
            confidence = min(1.0, 0.6 + abs(roc_z) / 8.0)
            return SignalResult(
                value=sign * 1.0,
                confidence=confidence,
                reason_codes=("MULTI_MOM_BULL", "STRUCTURE_UPTREND"),
            )
        if structure == "DOWNTREND" and roc_z < -threshold:
            confidence = min(1.0, 0.6 + abs(roc_z) / 8.0)
            return SignalResult(
                value=sign * -1.0,
                confidence=confidence,
                reason_codes=("MULTI_MOM_BEAR", "STRUCTURE_DOWNTREND"),
            )
        if abs(roc_z) > threshold * 2:
            direction = 1.0 if roc_z > 0 else -1.0
            return SignalResult(
                value=sign * direction * 0.6,
                confidence=0.4,
                reason_codes=("MULTI_MOM_WEAK",),
            )

    return SignalResult(value=0.0, confidence=0.2)
