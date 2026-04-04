"""Asymmetric time decay exit (PaulWei philosophy).

BTC is a scarce asset in a fiat-debasement world → long-term upward bias.
Long positions: be patient (small losses may recover)
Short positions: strict exit (unlimited upside risk)

Long: only exit if loss > 5% AND held past time horizon
Short: exit if loss > 2% AND held past time horizon
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import ExitResult, PluginMeta

meta = PluginMeta(
    name="time_decay_exit",
    version="2.0.0",
    plugin_type="exit",
    description="Asymmetric time decay: patient on longs, strict on shorts",
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

    if price <= 0 or entry_price <= 0:
        return ExitResult(should_exit=False)

    # PnL as percentage of entry
    pnl_pct = (price - entry_price) / entry_price * direction

    is_long = direction > 0

    # Asymmetric thresholds
    long_loss_threshold = config.get("long_loss_threshold", -0.05)  # -5%: patient
    short_loss_threshold = config.get("short_loss_threshold", -0.02)  # -2%: strict

    if is_long:
        # Long position: only exit if loss exceeds 5%
        # PaulWei: "暂时被套 → 等待, 法币贬值 → 稀缺资产长期涨"
        if pnl_pct < long_loss_threshold:
            return ExitResult(
                should_exit=True,
                exit_type="TIME_DECAY",
                urgency="GRADUAL",
                reduction_pct=0.5,
                reason=f"Long held {bars_held} bars, loss {pnl_pct:.1%} > {long_loss_threshold:.0%}",
                reason_codes=("EXIT_TIME_DECAY", "LONG_PATIENCE_EXCEEDED"),
            )
    else:
        # Short position: exit quickly if losing
        # Shorts have unlimited risk in an upward-biased asset
        if pnl_pct < short_loss_threshold:
            return ExitResult(
                should_exit=True,
                exit_type="TIME_DECAY",
                urgency="IMMEDIATE",
                reduction_pct=1.0,  # Full exit for losing shorts
                reason=f"Short held {bars_held} bars, loss {pnl_pct:.1%} > {short_loss_threshold:.0%}",
                reason_codes=("EXIT_TIME_DECAY", "SHORT_STRICT_EXIT"),
            )

    return ExitResult(should_exit=False)
