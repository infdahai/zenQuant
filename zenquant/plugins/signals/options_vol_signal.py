"""Vol regime signal from options ATM IV, skew, and term structure.

Synthesizes Deribit options market data into a directional bias using mean-reversion logic:
- Low IV (calm regime) → bullish bias (complacency, expect vol expansion + upside)
- High IV (stress regime) → bearish bias (fear premium, reduce risk)
- Extreme IV → strong bearish (distortion, wait for normalization)
- Inverted skew (call IV > put IV) → amplify bearish bias
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult


@dataclass(frozen=True, slots=True)
class Config:
    """Options vol signal configuration (all z-score thresholds)."""

    low_iv_threshold: float = -0.5  # z-score: calm vol regime
    high_iv_threshold: float = 1.5  # z-score: elevated vol
    extreme_iv_threshold: float = 2.5  # z-score: extreme vol (distortion)
    inverted_skew_threshold: float = -1.0  # z-score: call IV > put IV


meta = PluginMeta(
    name="options_vol_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Vol regime directional bias from Deribit options IV (mean-reversion)",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    """
    Bias direction based on options-implied volatility regime.

    Logic (mean-reversion):
    - Low IV → buy signal (vol mean-reversion expectation)
    - High IV → sell/reduce signal (risk-off)
    - Extreme IV → strong sell (wait for normalization)
    - Inverted skew → amplify bearish component
    """

    # Suppress in STRESS regime (consistent with other signals)
    regime = state.get("regime", "RANGE")
    if regime == "STRESS":
        return SignalResult(value=0.0, confidence=0.0)

    # Extract options indicators (z-scored) — safe defaults if unavailable
    atm_iv_z = float(indicators.get("atm_iv_z", 0.0) or 0.0)
    skew_z = float(indicators.get("skew_z", 0.0) or 0.0)
    # term_slope_z could be used later for regime transitions

    # No options data available — stay neutral
    if atm_iv_z == 0.0 and skew_z == 0.0:
        return SignalResult(value=0.0, confidence=0.0)

    # Get config thresholds
    low_iv = config.get("low_iv_threshold", -0.5)
    high_iv = config.get("high_iv_threshold", 1.5)
    extreme_iv = config.get("extreme_iv_threshold", 2.5)
    skew_threshold = config.get("inverted_skew_threshold", -1.0)

    # --- Low IV regime: Bullish (calm vol = complacency) ---
    if atm_iv_z < low_iv:
        magnitude = min(0.8, 0.6 + abs(atm_iv_z) / 10)
        confidence = min(1.0, 0.5 + abs(atm_iv_z) / 10)
        return SignalResult(
            value=magnitude,
            confidence=confidence,
            reason_codes=("OPTIONS_LOW_IV",),
        )

    # --- High IV regime: Bearish (fear premium = reduce risk) ---
    if atm_iv_z > high_iv:
        # Base bearish magnitude
        magnitude = -min(0.7, 0.5 + (atm_iv_z - high_iv) / 5)
        confidence = min(1.0, 0.4 + atm_iv_z / 12)
        reason_codes = ["OPTIONS_HIGH_IV"]

        # Extreme IV: Amplify bearish (distortion regime)
        if atm_iv_z > extreme_iv:
            magnitude = -1.0
            confidence = 0.6
            reason_codes = ["OPTIONS_DISTORTED"]

        # Inverted skew: Amplify bearish (unusual call skew = bearish)
        if skew_z < skew_threshold:
            magnitude *= 1.2
            magnitude = max(-1.0, magnitude)  # Clamp to -1.0
            reason_codes.append("OPTIONS_INVERTED_SKEW")

        return SignalResult(
            value=magnitude,
            confidence=confidence,
            reason_codes=tuple(reason_codes),
        )

    # --- Skew velocity: rapid put skew steepening = informed selling ---
    # Cao et al. (2024): d(skew)/dt has 3-5x higher IC than skew level
    skew_vel = float(indicators.get("skew_velocity", 0.0) or 0.0)
    skew_vel_threshold = config.get("skew_velocity_threshold", 0.5)

    if skew_vel > skew_vel_threshold:
        # Puts getting expensive fast → informed traders buying protection → bearish
        magnitude = -min(0.6, 0.3 + skew_vel / 5)
        confidence = min(0.8, 0.4 + skew_vel / 8)
        return SignalResult(
            value=magnitude,
            confidence=confidence,
            reason_codes=("OPTIONS_SKEW_STEEPENING",),
        )

    if skew_vel < -skew_vel_threshold:
        # Puts getting cheaper fast → fear subsiding → mildly bullish
        magnitude = min(0.4, 0.2 + abs(skew_vel) / 8)
        confidence = min(0.6, 0.3 + abs(skew_vel) / 10)
        return SignalResult(
            value=magnitude,
            confidence=confidence,
            reason_codes=("OPTIONS_SKEW_FLATTENING",),
        )

    # --- Mid-range IV: Neutral (defer to other signals) ---
    return SignalResult(value=0.0, confidence=0.2)
