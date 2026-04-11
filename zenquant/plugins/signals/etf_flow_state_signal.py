"""BTC ETF net-flow state signal.

This is a state/liquidity signal, not a primary direction alpha. It should
only influence confidence haircuts and execution urgency through the state
layer.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="etf_flow_state_signal",
    version="1.0.0",
    plugin_type="signal",
    description="BTC spot ETF net-flow state used for confidence and execution bias",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    _ = state
    flow_z = float(indicators.get("btc_etf_netflow_z", 0.0) or 0.0)
    flow_3d = float(indicators.get("btc_etf_flow_3d", 0.0) or 0.0)
    flow_accel = float(indicators.get("btc_etf_flow_accel", 0.0) or 0.0)
    pos_streak = int(indicators.get("btc_etf_positive_streak", 0) or 0)
    neg_streak = int(indicators.get("btc_etf_negative_streak", 0) or 0)

    if flow_z == 0.0 and flow_3d == 0.0 and flow_accel == 0.0 and pos_streak == 0 and neg_streak == 0:
        return SignalResult(value=0.0, confidence=0.0)

    positive_flow_z_threshold = float(config.get("positive_flow_z_threshold", 0.75) or 0.75)
    negative_flow_z_threshold = float(config.get("negative_flow_z_threshold", -0.75) or -0.75)
    positive_streak_threshold = int(config.get("positive_streak_threshold", 3) or 3)
    negative_streak_threshold = int(config.get("negative_streak_threshold", 3) or 3)

    if flow_z <= negative_flow_z_threshold or neg_streak >= negative_streak_threshold:
        magnitude = min(1.0, 0.35 + abs(flow_z) / 3.0 + max(0.0, -flow_accel) / 2_000_000_000.0)
        confidence = min(1.0, 0.35 + abs(flow_z) / 4.0 + neg_streak * 0.08)
        return SignalResult(value=-magnitude, confidence=confidence, reason_codes=("ETF_OUTFLOW_STRESS",))

    if flow_z >= positive_flow_z_threshold or pos_streak >= positive_streak_threshold:
        magnitude = min(0.45, 0.15 + flow_z / 4.5 + max(0.0, flow_accel) / 4_000_000_000.0)
        confidence = min(0.85, 0.3 + flow_z / 6.0 + pos_streak * 0.05)
        return SignalResult(value=magnitude, confidence=confidence, reason_codes=("ETF_INFLOW_SUPPORT",))

    return SignalResult(value=0.0, confidence=0.2)
