"""Central signal-role definitions shared by runtime and research paths."""

from __future__ import annotations

from typing import Literal

SignalRole = Literal["direction", "state", "execution_modifier"]

_ROLE_MAP: dict[str, SignalRole] = {
    "multi_momentum_signal": "direction",
    "crash_reversal_signal": "direction",
    "cvd_signal": "direction",
    "funding_alpha_signal": "state",
    "crowding_signal": "direction",
    "options_vol_signal": "state",
    "perp_carry_stress_signal": "state",
    "vol_structure_signal": "state",
    "macro_regime_signal": "state",
    "etf_flow_state_signal": "state",
    "cme_positioning_state_signal": "state",
    "cross_asset_flow_signal": "state",
    "vol_mean_reversion_signal": "direction",
    "liquidity_signal": "execution_modifier",
    "vacuum_signal": "execution_modifier",
}


def infer_signal_role(name: str) -> SignalRole:
    return _ROLE_MAP.get(name, "direction")
