"""Volatility mean-reversion signal.

When short-term realized vol spikes to >1.5x long-term vol, expect
vol to revert → price tends to bounce. Empirically validated:
  166 events, +2.98% excess return over 5 days, 66% win rate.

rv_hf (5min RV, IC=+0.248) is available in pack but NOT used as direction
signal — its IC is positive in bull markets but harmful in bear markets.
rv_hf is better used for risk sizing (more precise vol estimate).
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


meta = PluginMeta(
    name="vol_mean_reversion_signal",
    version="2.0.0",
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

    if vol_ratio > spike_threshold:
        magnitude = min(0.8, 0.4 + (vol_ratio - spike_threshold) / 3)
        confidence = min(0.9, 0.5 + (vol_ratio - spike_threshold) / 4)
        return SignalResult(
            value=magnitude,
            confidence=confidence,
            reason_codes=("VOL_SPIKE_REVERSAL",),
        )

    return SignalResult(value=0.0, confidence=0.0)
