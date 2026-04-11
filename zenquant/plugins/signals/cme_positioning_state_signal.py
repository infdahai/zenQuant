"""CME institutional positioning state signal.

Weekly CME positioning is used as a state/risk input rather than a direct
directional alpha. It informs confidence haircuts and crowding/urgency bias.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="cme_positioning_state_signal",
    version="1.0.0",
    plugin_type="signal",
    description="CME institutional positioning state used for crowding and execution bias",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    _ = state
    asset_mgr_z = float(indicators.get("cme_asset_mgr_net_z", 0.0) or 0.0)
    lev_money_z = float(indicators.get("cme_lev_money_net_z", 0.0) or 0.0)
    dealer_z = float(indicators.get("cme_dealer_net_z", 0.0) or 0.0)
    total_oi_z = float(indicators.get("cme_total_oi_z", 0.0) or 0.0)

    if asset_mgr_z == 0.0 and lev_money_z == 0.0 and dealer_z == 0.0 and total_oi_z == 0.0:
        return SignalResult(value=0.0, confidence=0.0)

    crowded_long_threshold = float(config.get("crowded_long_threshold", 1.4) or 1.4)
    crowded_short_threshold = float(config.get("crowded_short_threshold", -1.4) or -1.4)
    oi_stretch_threshold = float(config.get("oi_stretch_threshold", 1.2) or 1.2)

    if lev_money_z >= crowded_long_threshold and total_oi_z >= oi_stretch_threshold:
        magnitude = min(0.55, 0.20 + lev_money_z / 4.0 + total_oi_z / 6.0)
        confidence = min(0.9, 0.35 + lev_money_z / 5.0 + total_oi_z / 8.0)
        return SignalResult(value=-magnitude, confidence=confidence, reason_codes=("CME_CROWDED_LONG",))

    if lev_money_z <= crowded_short_threshold and total_oi_z >= oi_stretch_threshold:
        magnitude = min(0.45, 0.15 + abs(lev_money_z) / 5.0 + total_oi_z / 8.0)
        confidence = min(0.85, 0.30 + abs(lev_money_z) / 6.0 + total_oi_z / 10.0)
        return SignalResult(value=magnitude, confidence=confidence, reason_codes=("CME_CROWDED_SHORT",))

    if asset_mgr_z > 0.75 and dealer_z < -0.5:
        return SignalResult(value=0.15, confidence=0.35, reason_codes=("CME_INSTITUTIONAL_SUPPORT",))

    return SignalResult(value=0.0, confidence=0.2)
