"""Volatility mean-reversion signal.

When short-term realized vol spikes to >1.5x long-term vol, expect
vol to revert → price tends to bounce. Empirically validated:
  166 events, +2.98% excess return over 5 days, 66% win rate.

Complements crash_reversal_signal:
  crash_reversal: detects price extreme (bar drop >5%)
  vol_mean_reversion: detects volatility extreme (rv20/rv60 >1.5)
  Both fire during black swan events but capture different dimensions.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


meta = PluginMeta(
    name="vol_mean_reversion_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Bullish bias when short-term vol spikes (vol mean-reversion)",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    vol_ratio = float(indicators.get("vol_ratio_rv", 1.0) or 1.0)

    spike_threshold = config.get("spike_threshold", 1.5)
    compression_threshold = config.get("compression_threshold", 0.5)

    # Vol spike: short-term vol >> long-term vol
    # Empirical: excess +2.98%/5d, 66% win rate
    if vol_ratio > spike_threshold:
        magnitude = min(0.8, 0.4 + (vol_ratio - spike_threshold) / 3)
        confidence = min(0.9, 0.5 + (vol_ratio - spike_threshold) / 4)
        return SignalResult(
            value=magnitude,  # Bullish (expect vol reversion + price recovery)
            confidence=confidence,
            reason_codes=("VOL_SPIKE_REVERSAL",),
        )

    # Vol compression: very low vol → breakout imminent, direction unknown
    if vol_ratio < compression_threshold:
        return SignalResult(
            value=0.0,  # No direction
            confidence=0.15,  # Low confidence → orchestrator reduces contribution
            reason_codes=("VOL_COMPRESSED",),
        )

    return SignalResult(value=0.0, confidence=0.0)
