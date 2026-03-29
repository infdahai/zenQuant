"""Exit on quality or volatility regime deterioration.

Extracted from ``ExitEngine._check_regime_change()``.
Responds to softer state deterioration that does not fully invalidate
the playbook but warrants risk reduction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import ExitResult, PluginMeta


@dataclass(frozen=True, slots=True)
class Config:
    transition_risk_threshold: float = 0.85


meta = PluginMeta(
    name="regime_change_exit",
    version="1.0.0",
    plugin_type="exit",
    description="De-risk on quality, volatility, or transition risk deterioration",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    position: dict[str, Any],
    config: dict[str, Any],
) -> ExitResult:
    quality = state.get("quality_state", "TRADABLE")
    vol_state = state.get("volatility_state", "NORMAL")
    transition_risk = float(state.get("transition_risk", 0.0) or 0.0)
    playbook_id = str(state.get("playbook_id", ""))
    threshold = config.get("transition_risk_threshold", 0.85)

    if quality == "UNSAFE":
        return ExitResult(
            should_exit=True,
            exit_type="REGIME_CHANGE",
            urgency="IMMEDIATE",
            reduction_pct=1.0,
            reason="Data quality unsafe; flatten position",
            reason_codes=("EXIT_REGIME_CHANGE", "QUALITY_UNSAFE"),
        )

    if vol_state == "DISTORTED":
        return ExitResult(
            should_exit=True,
            exit_type="REGIME_CHANGE",
            urgency="GRADUAL",
            reduction_pct=0.5,
            reason="Volatility regime distorted; de-risk",
            reason_codes=("EXIT_REGIME_CHANGE", "VOLATILITY_STRESS"),
        )

    if playbook_id != "CAPITAL_PRESERVATION" and transition_risk >= threshold:
        return ExitResult(
            should_exit=True,
            exit_type="REGIME_CHANGE",
            urgency="GRADUAL",
            reduction_pct=0.5,
            reason="Elevated transition risk; de-risk",
            reason_codes=("EXIT_REGIME_CHANGE", "TRANSITION_RISK"),
        )

    return ExitResult(should_exit=False)
