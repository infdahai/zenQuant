"""Liquidity vacuum harvesting signal.

Places deep limit orders at 10-15% below/above current price.
When extreme liquidation cascades cause price to "wick" to these levels,
the orders fill on panic selling/buying. Mean reversion follows within minutes.

This is crash_reversal's aggressive cousin — instead of waiting for the
crash to appear in 4H bars, it pre-positions limit orders to catch the wick.

Live-only signal: backtest can detect the conditions but cannot perfectly
simulate limit order fills at extreme prices.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="vacuum_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Deep limit order positioning for liquidity vacuum events",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    """Detect conditions favorable for vacuum harvesting.

    This signal doesn't predict direction — it identifies when the market
    is primed for a liquidation cascade (high leverage + thin books).
    The actual limit orders are managed by the execution layer.
    """
    # Conditions that increase vacuum probability:
    # 1. High funding = crowded longs → long squeeze risk
    # 2. High VPIN = toxic flow building
    # 3. Low weekend vol = thin books
    funding_oi_z = float(indicators.get("funding_oi_z", 0.0) or 0.0)
    vpin = float(indicators.get("vpin", 0.0) or 0.0)
    realized_vol = float(indicators.get("realized_vol", 0.0) or 0.0)

    vacuum_score = 0.0

    # Extreme funding = liquidation cascade risk
    if abs(funding_oi_z) > 2.0:
        vacuum_score += 0.3

    # High VPIN = informed trading, possible large move incoming
    if vpin > 0.08:
        vacuum_score += 0.2

    # Low vol + high leverage = spring loaded
    if realized_vol < 0.3 and abs(funding_oi_z) > 1.5:
        vacuum_score += 0.3

    if vacuum_score < 0.3:
        return SignalResult(value=0.0, confidence=0.0)

    # Direction: opposite of crowding (catch the squeeze)
    direction = -1.0 if funding_oi_z > 0 else 1.0

    return SignalResult(
        value=direction * min(0.5, vacuum_score),
        confidence=min(0.6, vacuum_score),
        reason_codes=("VACUUM_SETUP",),
    )
