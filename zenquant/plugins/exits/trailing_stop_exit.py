"""ATR-based trailing stop exit.

Extracted from ``ExitEngine._check_trailing_stop()``.
Updates and checks the trailing stop each bar using the ATR multiple.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import ExitResult, PluginMeta


@dataclass(frozen=True, slots=True)
class Config:
    atr_mult: float = 2.5
    min_bars_before_trail: int = 1


meta = PluginMeta(
    name="trailing_stop_exit",
    version="1.0.0",
    plugin_type="exit",
    description="ATR-based trailing stop with ratchet logic",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    position: dict[str, Any],
    config: dict[str, Any],
) -> ExitResult:
    price = float(indicators.get("last_price", 0.0) or indicators.get("price", 0.0) or 0.0)
    atr = float(indicators.get("atr", 0.0) or 0.0)
    direction = float(position.get("direction", 0.0))
    bars_held = int(position.get("bars_held", 0))
    trailing_stop = float(position.get("trailing_stop", 0.0))
    min_bars = config.get("min_bars_before_trail", 1)

    if price <= 0 or atr <= 0 or bars_held < min_bars:
        return ExitResult(should_exit=False)

    atr_mult = config.get("atr_mult", 2.5)
    stop_distance = atr * atr_mult

    # Check if trailing stop is breached
    if trailing_stop != 0.0:
        if direction > 0 and price <= trailing_stop:
            return ExitResult(
                should_exit=True,
                exit_type="TRAILING",
                urgency="IMMEDIATE",
                reduction_pct=1.0,
                stop_price=trailing_stop,
                reason=f"Price {price:.2f} breached trailing stop {trailing_stop:.2f}",
                reason_codes=["EXIT_TRAILING_STOP"],
            )
        if direction < 0 and price >= trailing_stop:
            return ExitResult(
                should_exit=True,
                exit_type="TRAILING",
                urgency="IMMEDIATE",
                reduction_pct=1.0,
                stop_price=trailing_stop,
                reason=f"Price {price:.2f} breached trailing stop {trailing_stop:.2f}",
                reason_codes=["EXIT_TRAILING_STOP"],
            )

    return ExitResult(should_exit=False)
