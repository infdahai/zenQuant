"""Block trades that conflict with the 1D directional bias.

Extracted from ``ARCStrategyEngine._build_target()`` multi-timeframe
coordination logic. Only applies to 4H execution timeframe.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import FilterResult, PluginMeta

meta = PluginMeta(
    name="trend_filter",
    version="1.0.0",
    plugin_type="filter",
    description="Block 4H trades conflicting with 1D directional bias",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> FilterResult:
    bias_direction = float(state.get("bias_direction", 0.0) or 0.0)
    signal_direction = float(state.get("signal_direction", 0.0) or 0.0)

    if bias_direction == 0.0 or signal_direction == 0.0:
        return FilterResult(passed=True, reason_codes=["TREND_NO_CONFLICT"])

    if bias_direction * signal_direction < 0:
        return FilterResult(
            passed=False,
            reason=f"Signal direction {signal_direction} conflicts with 1D bias {bias_direction}",
            reason_codes=["TREND_BIAS_CONFLICT"],
        )
    return FilterResult(passed=True, reason_codes=["TREND_ALIGNED"])
