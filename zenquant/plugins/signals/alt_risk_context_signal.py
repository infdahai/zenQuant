"""Alt-asset risk appetite state modifier for BTC research."""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="alt_risk_context_signal",
    version="1.0.0",
    plugin_type="signal",
    description="SOL/BNB/ETH context as a BTC state confidence modifier",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    spread_z = float(indicators.get("alt_btc_spread_z", 0.0) or 0.0)
    alt_momentum = float(indicators.get("alt_momentum_7d", 0.0) or 0.0)
    eth_momentum = float(indicators.get("eth_momentum_7d", 0.0) or indicators.get("eth_momentum", 0.0) or 0.0)

    spread_threshold = float(config.get("spread_z_threshold", 1.25))
    momentum_threshold = float(config.get("momentum_threshold", 0.04))
    max_magnitude = float(config.get("max_magnitude", 0.45))

    if abs(spread_z) < spread_threshold and abs(alt_momentum) < momentum_threshold:
        return SignalResult(value=0.0, confidence=0.1)

    risk_on = spread_z > spread_threshold and alt_momentum > momentum_threshold and eth_momentum >= 0.0
    risk_off = spread_z < -spread_threshold and alt_momentum < -momentum_threshold and eth_momentum <= 0.0
    if not risk_on and not risk_off:
        return SignalResult(value=0.0, confidence=0.15)

    magnitude = min(max_magnitude, 0.15 + abs(spread_z) * 0.08 + abs(alt_momentum) * 1.2)
    confidence = min(0.65, 0.25 + abs(spread_z) * 0.08 + abs(alt_momentum) * 1.5)
    if risk_on:
        return SignalResult(value=magnitude, confidence=confidence, reason_codes=("ALT_RISK_ON_CONTEXT",))
    return SignalResult(value=-magnitude, confidence=confidence, reason_codes=("ALT_RISK_OFF_CONTEXT",))
