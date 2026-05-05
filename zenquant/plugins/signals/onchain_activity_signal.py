"""CoinMetrics-derived on-chain activity state modifier for BTC research."""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="onchain_activity_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Audited on-chain activity momentum as BTC state context",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    active_momentum = float(indicators.get("active_address_momentum_14d", 0.0) or 0.0)
    supply_activity = float(indicators.get("supply_activity_regime", 0.0) or 0.0)

    active_threshold = float(config.get("active_momentum_threshold", 0.015))
    supply_threshold = float(config.get("supply_activity_threshold", 0.01))
    max_magnitude = float(config.get("max_magnitude", 0.35))

    risk_on = active_momentum > active_threshold and supply_activity > supply_threshold
    risk_off = active_momentum < -active_threshold and supply_activity < -supply_threshold
    if not risk_on and not risk_off:
        return SignalResult(value=0.0, confidence=0.1)

    magnitude = min(max_magnitude, 0.12 + abs(active_momentum) * 1.8 + abs(supply_activity) * 1.2)
    confidence = min(0.55, 0.2 + abs(active_momentum) * 2.0 + abs(supply_activity) * 1.5)
    if risk_on:
        return SignalResult(value=magnitude, confidence=confidence, reason_codes=("ONCHAIN_ACTIVITY_RISK_ON",))
    return SignalResult(value=-magnitude, confidence=confidence, reason_codes=("ONCHAIN_ACTIVITY_RISK_OFF",))
