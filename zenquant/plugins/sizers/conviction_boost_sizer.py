"""Scale up position size when all market dimensions confirm high conviction.

Activates a 1.0-1.3x multiplier only when ALL conditions are ideal:
  - Directional state is UPTREND or DOWNTREND (not ranging)
  - Volatility is LOW or NORMAL (not stressed)
  - Positioning is HEALTHY (not crowded)
  - Quality is TRADABLE
  - Transition risk is low
  - Overall confidence is high

Based on Barroso (FMPM 2025): vol-managed momentum gains come partly
from scaling UP during low-vol favorable regimes, not just scaling down
during stress.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SizerResult


@dataclass(frozen=True, slots=True)
class Config:
    confidence_threshold: float = 0.75
    transition_risk_max: float = 0.30
    boost_max: float = 1.30


meta = PluginMeta(
    name="conviction_boost_sizer",
    version="1.0.0",
    plugin_type="sizer",
    description="Scale up 1.0-1.3x when all dimensions confirm ideal conditions",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SizerResult:
    directional = state.get("directional_state", "NEUTRAL")
    volatility = state.get("volatility_state", "NORMAL")
    positioning = state.get("positioning_state", "HEALTHY")
    quality = state.get("quality_state", "TRADABLE")
    transition_risk = float(state.get("transition_risk", 1.0) or 1.0)
    overall_confidence = float(state.get("overall_confidence", 0.0) or 0.0)

    conf_threshold = config.get("confidence_threshold", 0.75)
    tr_max = config.get("transition_risk_max", 0.30)
    boost_max = config.get("boost_max", 1.30)

    if (
        directional in ("UPTREND", "DOWNTREND")
        and volatility in ("LOW", "NORMAL")
        and positioning == "HEALTHY"
        and quality == "TRADABLE"
        and transition_risk < tr_max
        and overall_confidence > conf_threshold
    ):
        excess = overall_confidence - conf_threshold
        boost = 1.0 + min(boost_max - 1.0, excess * 1.2)
        return SizerResult(
            factor=round(boost, 4),
            reason_codes=["CONVICTION_BOOST"],
        )
    return SizerResult(factor=1.0, reason_codes=[])
