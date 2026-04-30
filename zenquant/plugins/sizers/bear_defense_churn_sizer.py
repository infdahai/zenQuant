"""Research-only sizing haircut for BEAR_DEFENSE churn control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SizerResult


@dataclass(frozen=True, slots=True)
class Config:
    weak_confidence_threshold: float = 0.58
    transition_risk_threshold: float = 0.42
    weak_transition_discount: float = 0.88
    discount_floor: float = 0.55


meta = PluginMeta(
    name="bear_defense_churn_sizer",
    version="1.0.0",
    plugin_type="sizer",
    description="Research-only BEAR_DEFENSE haircut for weak short conviction and transition churn",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SizerResult:
    if state.get("playbook_id") != "BEAR_DEFENSE":
        return SizerResult(factor=1.0, reason_codes=())

    signal_direction = float(state.get("signal_direction", 0.0) or 0.0)
    if signal_direction >= 0.0:
        return SizerResult(factor=1.0, reason_codes=())

    directional_confidence = float(state.get("directional_confidence", 0.0) or 0.0)
    transition_risk = float(state.get("transition_risk", 0.0) or 0.0)
    weak_threshold = float(config.get("weak_confidence_threshold", 0.58))
    transition_threshold = float(config.get("transition_risk_threshold", 0.42))
    floor = float(config.get("discount_floor", 0.55))

    factor = 1.0
    codes: list[str] = []

    if directional_confidence < weak_threshold and transition_risk >= transition_threshold:
        factor *= float(config.get("weak_transition_discount", 0.88))
        codes.append("BEAR_DEFENSE_WEAK_TRANSITION_CHURN_HAIRCUT")

    if not codes:
        return SizerResult(factor=1.0, reason_codes=())

    return SizerResult(factor=round(max(floor, factor), 6), reason_codes=tuple(codes))
