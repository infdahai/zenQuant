"""ATR-based trailing stop exit.

Extracted from ``ExitEngine._check_trailing_stop()``.
Checks whether the trailing stop has been breached.

Note: The trailing stop ratchet (initialization and advancement) is handled
by the engine in ``_plugin_exit()`` before calling this plugin.  This keeps
the plugin as a pure function — it only reads the current stop level and
checks for breach.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import ExitResult, PluginMeta


@dataclass(frozen=True, slots=True)
class Config:
    # These fields are consumed by the engine's ratchet logic (not by this plugin),
    # but kept here so TOML profile values merge without "unknown key" warnings.
    atr_mult: float = 2.5
    min_bars_before_trail: int = 1


meta = PluginMeta(
    name="trailing_stop_exit",
    version="1.1.0",
    plugin_type="exit",
    description="ATR-based trailing stop breach check",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    position: dict[str, Any],
    config: dict[str, Any],
) -> ExitResult:
    price = float(indicators.get("last_price", 0.0) or indicators.get("price", 0.0) or 0.0)
    direction = float(position.get("direction", 0.0))
    trailing_stop = float(position.get("trailing_stop", 0.0))

    if price <= 0 or trailing_stop == 0.0:
        return ExitResult(should_exit=False)

    # Check if trailing stop is breached
    if direction > 0 and price <= trailing_stop:
        return ExitResult(
            should_exit=True,
            exit_type="TRAILING",
            urgency="IMMEDIATE",
            reduction_pct=1.0,
            stop_price=trailing_stop,
            reason=f"Price {price:.2f} breached trailing stop {trailing_stop:.2f}",
            reason_codes=("EXIT_TRAILING_STOP",),
        )
    if direction < 0 and price >= trailing_stop:
        return ExitResult(
            should_exit=True,
            exit_type="TRAILING",
            urgency="IMMEDIATE",
            reduction_pct=1.0,
            stop_price=trailing_stop,
            reason=f"Price {price:.2f} breached trailing stop {trailing_stop:.2f}",
            reason_codes=("EXIT_TRAILING_STOP",),
        )

    return ExitResult(should_exit=False)
