"""Cross-asset macro regime signal from VIX, DXY, Treasury yields, and TLT.

Synthesizes traditional macro indicators into a risk-on/risk-off directional
signal for crypto.  High VIX + strong dollar = risk-off (bearish crypto);
low VIX + weak dollar = risk-on (bullish crypto).

TLT (20Y+ Treasury Bond ETF) momentum: IC=0.085 for BTC 5-day forward.
TLT rising (yields falling) = liquidity easing = bullish crypto.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="macro_regime_signal",
    version="2.0.0",
    plugin_type="signal",
    description="Cross-asset macro regime: VIX + DXY + TLT liquidity",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime == "STRESS":
        return SignalResult(value=0.0, confidence=0.0)

    vix_z = float(indicators.get("vix_z", 0.0) or 0.0)
    dxy_z = float(indicators.get("dxy_z", 0.0) or 0.0)
    tlt_z = float(indicators.get("tlt_z", 0.0) or 0.0)

    vix_threshold = config.get("vix_extreme_threshold", 1.5)
    dxy_threshold = config.get("dxy_strong_threshold", 1.5)

    # No macro data available
    if vix_z == 0.0 and dxy_z == 0.0 and tlt_z == 0.0:
        return SignalResult(value=0.0, confidence=0.0)

    # Risk-off: high VIX + strong dollar → bearish crypto
    if vix_z > vix_threshold and dxy_z > 0:
        magnitude = min(1.0, (vix_z - vix_threshold) / 2.0 + 0.6)
        confidence = min(1.0, 0.4 + abs(vix_z) / 6.0)
        return SignalResult(
            value=-magnitude,
            confidence=confidence,
            reason_codes=("MACRO_RISK_OFF",),
        )

    # Risk-on: low VIX + weak dollar → bullish crypto
    if vix_z < -vix_threshold and dxy_z < 0:
        magnitude = min(0.7, (abs(vix_z) - vix_threshold) / 3.0 + 0.4)
        confidence = min(1.0, 0.35 + abs(vix_z) / 8.0)
        return SignalResult(
            value=magnitude,
            confidence=confidence,
            reason_codes=("MACRO_RISK_ON",),
        )

    # TLT liquidity signal: TLT rising (yields falling) → bullish
    # IC = 0.085 (validated on 1571 daily observations)
    if abs(tlt_z) > 1.5:
        direction = 0.4 if tlt_z > 0 else -0.3  # TLT up=bullish, TLT down=bearish
        confidence = min(0.6, 0.3 + abs(tlt_z) / 8.0)
        code = "MACRO_LIQUIDITY_EASING" if tlt_z > 0 else "MACRO_LIQUIDITY_TIGHTENING"
        return SignalResult(
            value=direction,
            confidence=confidence,
            reason_codes=(code,),
        )

    # Strong/weak dollar alone
    if dxy_z > dxy_threshold:
        return SignalResult(value=-0.3, confidence=0.3, reason_codes=("MACRO_STRONG_USD",))
    if dxy_z < -dxy_threshold:
        return SignalResult(value=0.3, confidence=0.3, reason_codes=("MACRO_WEAK_USD",))

    return SignalResult(value=0.0, confidence=0.2)
