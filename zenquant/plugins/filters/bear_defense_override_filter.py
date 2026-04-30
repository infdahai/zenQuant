"""Research-only filter for noisy BEAR_DEFENSE override shorts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import FilterResult, PluginMeta


@dataclass(frozen=True, slots=True)
class Config:
    max_directional_confidence: float = 0.58
    min_transition_risk: float = 0.40


meta = PluginMeta(
    name="bear_defense_override_filter",
    version="1.0.0",
    plugin_type="filter",
    description="Research-only block for noisy RANGE/TRANSITION BEAR_DEFENSE short overrides",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> FilterResult:
    signal_direction = float(state.get("signal_direction", 0.0) or 0.0)
    if signal_direction >= 0.0:
        return FilterResult(passed=True, reason_codes=("BEAR_DEFENSE_OVERRIDE_FILTER_NOT_SHORT",))

    if state.get("playbook_id") != "BEAR_DEFENSE":
        return FilterResult(passed=True, reason_codes=("BEAR_DEFENSE_OVERRIDE_FILTER_OTHER_PLAYBOOK",))

    regime = str(state.get("regime") or "")
    directional_state = str(state.get("directional_state") or "")
    quality_state = str(state.get("quality_state") or "")
    directional_confidence = float(state.get("directional_confidence", 0.0) or 0.0)
    transition_risk = float(state.get("transition_risk", 0.0) or 0.0)
    max_confidence = float(config.get("max_directional_confidence", 0.58))
    min_transition = float(config.get("min_transition_risk", 0.40))

    if (
        regime == "RANGE"
        and directional_state == "TRANSITION"
        and quality_state == "NOISY"
        and directional_confidence <= max_confidence
        and transition_risk >= min_transition
    ):
        return FilterResult(
            passed=False,
            reason="Block noisy RANGE/TRANSITION BEAR_DEFENSE short override",
            reason_codes=("BEAR_DEFENSE_NOISY_TRANSITION_OVERRIDE_BLOCK",),
        )

    return FilterResult(passed=True, reason_codes=("BEAR_DEFENSE_OVERRIDE_FILTER_PASS",))
