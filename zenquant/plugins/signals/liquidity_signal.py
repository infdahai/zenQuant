"""Mean-reversion signal from order book liquidity imbalance.

Fades extreme order-book imbalance when CVD is calm.
Returns honest confidence — orchestrator handles regime weighting.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="liquidity_signal",
    version="2.0.0",
    plugin_type="signal",
    description="Mean-reversion on order book imbalance, confidence-based",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime == "STRESS":
        return SignalResult(value=0.0, confidence=0.0)

    cvd_slope = float(indicators.get("cvd_slope", 0.0) or 0.0)
    ob_liq_z = float(indicators.get("ob_liq_z", 0.0) or 0.0)
    cvd_calm = config.get("cvd_calm_threshold", 0.03)
    ob_extreme = config.get("ob_extreme_threshold", 1.2)

    if abs(cvd_slope) < cvd_calm and abs(ob_liq_z) > ob_extreme:
        direction = -1.0 if ob_liq_z > 0 else 1.0
        # Confidence encodes regime suitability:
        # RANGE → higher confidence (primary domain)
        # TREND → lower confidence (secondary, turning points only)
        base_conf = min(1.0, abs(ob_liq_z) / 4.0)
        if regime == "TREND":
            base_conf *= 0.4  # Naturally reduced via confidence, not value dampening
        return SignalResult(
            value=direction,
            confidence=base_conf,
            reason_codes=("LIQUIDITY_MEAN_REVERSION",),
        )
    return SignalResult(value=0.0, confidence=0.2)
