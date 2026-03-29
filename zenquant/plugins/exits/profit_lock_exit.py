"""Lock in a minimum percentage of peak profit when position is highly profitable.

When unrealized PnL exceeds threshold (default 5%), ensures at least
lock_ratio (default 40%) of peak profit is preserved. Exits if current
PnL falls below the locked floor.

This prevents giving back large gains during extended trend reversals,
while allowing normal ATR-based trailing stops to handle smaller moves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import ExitResult, PluginMeta


@dataclass(frozen=True, slots=True)
class Config:
    threshold: float = 0.05  # activate when unrealized PnL > 5%
    lock_ratio: float = 0.40  # lock at least 40% of peak PnL


meta = PluginMeta(
    name="profit_lock_exit",
    version="1.0.0",
    plugin_type="exit",
    description="Lock minimum profit fraction once position is highly profitable",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    position: dict[str, Any],
    config: dict[str, Any],
) -> ExitResult:
    unrealized_pnl_pct = float(position.get("unrealized_pnl_pct", 0.0) or 0.0)
    peak_pnl_pct = float(position.get("peak_pnl_pct", 0.0) or 0.0)

    threshold = config.get("threshold", 0.05)
    lock_ratio = config.get("lock_ratio", 0.40)

    if peak_pnl_pct < threshold:
        return ExitResult(should_exit=False)

    locked_floor = peak_pnl_pct * lock_ratio
    if unrealized_pnl_pct < locked_floor:
        return ExitResult(
            should_exit=True,
            exit_type="PROFIT_LOCK",
            urgency="IMMEDIATE",
            reduction_pct=1.0,
            reason=(
                f"PnL {unrealized_pnl_pct:.1%} fell below locked floor {locked_floor:.1%} (peak {peak_pnl_pct:.1%})"
            ),
            reason_codes=("EXIT_PROFIT_LOCK",),
        )
    return ExitResult(should_exit=False)
