"""Reduce stale unprofitable positions.

Extracted from ``ExitEngine._check_time_decay()``.
Triggers gradual 50% reduction when a position has been held past the
playbook horizon and is underwater.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import ExitResult, PluginMeta

meta = PluginMeta(
    name="time_decay_exit",
    version="1.0.0",
    plugin_type="exit",
    description="Reduce stale unprofitable positions after time horizon",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    position: dict[str, Any],
    config: dict[str, Any],
) -> ExitResult:
    time_decay_bars = state.get("time_decay_bars")
    if time_decay_bars is None:
        return ExitResult(should_exit=False)

    bars_held = int(position.get("bars_held", 0))
    if bars_held <= int(time_decay_bars):
        return ExitResult(should_exit=False)

    price = float(indicators.get("last_price", 0.0) or indicators.get("price", 0.0) or 0.0)
    entry_price = float(position.get("entry_price", 0.0))
    direction = float(position.get("direction", 0.0))
    current_qty = float(position.get("current_qty", 0.0))

    if price <= 0:
        return ExitResult(should_exit=False)

    unrealized_pnl = (price - entry_price) * direction * current_qty
    if unrealized_pnl < 0:
        return ExitResult(
            should_exit=True,
            exit_type="TIME_DECAY",
            urgency="GRADUAL",
            reduction_pct=0.5,
            reason=f"Held {bars_held} bars without profit",
            reason_codes=["EXIT_TIME_DECAY", "UNPROFITABLE"],
        )
    return ExitResult(should_exit=False)
