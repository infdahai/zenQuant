"""Confidence-blended sizing multiplier.

Extracted from ``SizingEngine._confidence_multiplier()``.
Blends state confidence with playbook confidence.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SizerResult

meta = PluginMeta(
    name="confidence_sizer",
    version="1.0.0",
    plugin_type="sizer",
    description="Scale leverage by blended state + playbook confidence",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SizerResult:
    state_conf = float(state.get("overall_confidence", 0.5) or 0.5)
    playbook_conf = float(state.get("playbook_confidence", 0.5) or 0.5)

    factor = max(0.0, min(1.0, (state_conf + playbook_conf) / 2.0))

    return SizerResult(
        factor=round(factor, 6),
        reason_codes=("SIZE_CONFIDENCE_BLEND",),
    )
