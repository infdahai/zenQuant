"""Exit when playbook directional assumption is invalidated.

Extracted from ``ExitEngine._check_invalidation()``.
Triggers immediate full flatten when trend flips against position.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import ExitResult, PluginMeta

meta = PluginMeta(
    name="invalidation_exit",
    version="1.0.0",
    plugin_type="exit",
    description="Immediate exit when directional state flips against position",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    position: dict[str, Any],
    config: dict[str, Any],
) -> ExitResult:
    direction = float(position.get("direction", 0.0))
    playbook_id = str(state.get("playbook_id", ""))
    directional_state = state.get("directional_state", "NEUTRAL")

    if direction > 0 and "BULL" in playbook_id and directional_state == "DOWNTREND":
        return ExitResult(
            should_exit=True,
            exit_type="INVALIDATION",
            urgency="IMMEDIATE",
            reduction_pct=1.0,
            reason="Bull playbook invalidated by downtrend",
            reason_codes=("EXIT_INVALIDATION", "DIRECTIONAL_FLIP"),
        )
    if direction < 0 and "BEAR" in playbook_id and directional_state == "UPTREND":
        return ExitResult(
            should_exit=True,
            exit_type="INVALIDATION",
            urgency="IMMEDIATE",
            reduction_pct=1.0,
            reason="Bear playbook invalidated by uptrend",
            reason_codes=("EXIT_INVALIDATION", "DIRECTIONAL_FLIP"),
        )
    return ExitResult(should_exit=False)
