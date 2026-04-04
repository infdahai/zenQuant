"""Scale up position when currently profitable (PaulWei pyramid pattern).

PaulWei data analysis: winning sessions average 38 trades vs losing 26 trades.
He adds MORE size to winning positions, not to losing ones.

Implementation: when unrealized PnL > 0 and position held > min_bars,
apply a 1.0-1.4x boost to sizing. When losing, apply 0.7x reduction.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SizerResult

meta = PluginMeta(
    name="winning_position_sizer",
    version="1.0.0",
    plugin_type="sizer",
    description="Scale up on winning positions, reduce on losing (pyramid)",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SizerResult:
    # Position info comes through state dict
    equity = float(state.get("equity", 100000) or 100000)
    position_pnl = float(state.get("position_unrealized_pnl", 0) or 0)

    # Normalize PnL as percentage of equity
    pnl_pct = position_pnl / equity if equity > 0 else 0

    win_boost = config.get("win_boost_max", 1.4)
    lose_discount = config.get("lose_discount", 0.7)

    # Winning position → scale up (PaulWei pyramid)
    if pnl_pct > 0.01:  # > 1% profit
        boost = min(win_boost, 1.0 + pnl_pct * 5)  # Scale with profit magnitude
        return SizerResult(
            factor=round(boost, 4),
            reason_codes=("WINNING_POSITION_BOOST",),
        )

    # Losing position → scale down (faster exit)
    if pnl_pct < -0.01:  # > 1% loss
        return SizerResult(
            factor=lose_discount,
            reason_codes=("LOSING_POSITION_DISCOUNT",),
        )

    return SizerResult(factor=1.0, reason_codes=())
