"""Block trades when data quality is degraded or unsafe.

Extracted from ``PlaybookEngine._should_preserve_capital()`` quality checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import FilterResult, PluginMeta


@dataclass(frozen=True, slots=True)
class Config:
    block_on_degraded: bool = True
    block_on_unsafe: bool = True


meta = PluginMeta(
    name="quality_filter",
    version="1.0.0",
    plugin_type="filter",
    description="Block trades on degraded or unsafe data quality",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> FilterResult:
    quality = state.get("quality_state", "TRADABLE")

    if quality == "UNSAFE" and config.get("block_on_unsafe", True):
        return FilterResult(
            passed=False,
            reason="Data quality UNSAFE",
            reason_codes=("QUALITY_UNSAFE_BLOCK",),
        )
    if quality == "DEGRADED" and config.get("block_on_degraded", True):
        return FilterResult(
            passed=False,
            reason="Data quality DEGRADED",
            reason_codes=("QUALITY_DEGRADED_BLOCK",),
        )
    return FilterResult(passed=True, reason_codes=("QUALITY_OK",))
