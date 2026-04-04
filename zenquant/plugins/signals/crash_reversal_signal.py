"""Black swan crash reversal signal.

Detects extreme price drops and generates contrarian buy signals.
Based on empirical evidence: 4H drops > 5% have 72% win rate for
1-day forward returns, with +1.49% excess return vs normal.

This is the strongest alpha signal discovered in zenQuant's research.
Active only after extreme dislocations — fires rarely but with high confidence.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


meta = PluginMeta(
    name="crash_reversal_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Contrarian buy after extreme crash (black swan grab)",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    # Current bar's return
    close = float(indicators.get("close", 0) or indicators.get("last_price", 0) or 0)
    open_price = float(indicators.get("open", 0) or 0)

    if close <= 0 or open_price <= 0:
        return SignalResult(value=0.0, confidence=0.0)

    bar_return = (close - open_price) / open_price

    # Thresholds from empirical analysis (64 events, 2019-2026)
    crash_threshold = config.get("crash_threshold", -0.05)
    deep_crash_threshold = config.get("deep_crash_threshold", -0.08)

    # Volume confirmation: crashes with high volume are more reliable
    vol_ratio = float(indicators.get("volume_ratio", 1.0) or 1.0)
    vol_confirm = vol_ratio > 2.0  # Volume > 2x average

    if bar_return < deep_crash_threshold:
        # Deep crash (>8% drop): highest confidence reversal
        # Empirical: +2.45% next day, 78% win rate
        confidence = min(1.0, 0.7 + abs(bar_return) * 2)
        if vol_confirm:
            confidence = min(1.0, confidence + 0.1)
        return SignalResult(
            value=0.8,  # Strong bullish
            confidence=confidence,
            reason_codes=("CRASH_DEEP_REVERSAL",),
        )

    if bar_return < crash_threshold:
        # Standard crash (>5% drop): good confidence reversal
        # Empirical: +1.49% next day, 72% win rate
        confidence = 0.5 + abs(bar_return) * 3
        if vol_confirm:
            confidence = min(1.0, confidence + 0.1)
        return SignalResult(
            value=0.6,  # Moderate bullish
            confidence=min(1.0, confidence),
            reason_codes=("CRASH_REVERSAL",),
        )

    # No crash detected
    return SignalResult(value=0.0, confidence=0.0)
