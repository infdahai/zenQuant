"""Audited Binance metrics/OI state modifier for BTC research."""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="binance_metrics_state_signal",
    version="1.0.0",
    plugin_type="signal",
    description="Audited Binance OI and trader-ratio metrics as BTC state context",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    oi_value_growth = float(indicators.get("binance_oi_value_growth_24h_z", 0.0) or 0.0)
    top_account_lsr = float(indicators.get("binance_top_trader_account_lsr_z", 0.0) or 0.0)
    unwind_pressure = float(indicators.get("binance_crowding_unwind_pressure", 0.0) or 0.0)

    oi_threshold = float(config.get("oi_value_growth_threshold", 0.75))
    top_account_threshold = float(config.get("top_account_lsr_threshold", 0.75))
    unwind_threshold = float(config.get("unwind_pressure_threshold", 0.35))
    max_magnitude = float(config.get("max_magnitude", 0.4))

    # Audit signs: OI value growth had negative IC, while top-account L/S and
    # crowding-unwind pressure had positive IC in train-only review.
    score = 0.0
    reason_codes: list[str] = []
    if abs(oi_value_growth) >= oi_threshold:
        score += -0.45 * oi_value_growth
        reason_codes.append("BINANCE_OI_VALUE_GROWTH")
    if abs(top_account_lsr) >= top_account_threshold:
        score += 0.3 * top_account_lsr
        reason_codes.append("BINANCE_TOP_ACCOUNT_LS")
    if abs(unwind_pressure) >= unwind_threshold:
        score += 0.25 * unwind_pressure
        reason_codes.append("BINANCE_CROWDING_UNWIND")

    if not reason_codes:
        return SignalResult(value=0.0, confidence=0.1)

    clipped = max(-max_magnitude, min(max_magnitude, score))
    confidence = min(0.55, 0.18 + abs(score) * 0.45)
    return SignalResult(value=round(clipped, 6), confidence=round(confidence, 6), reason_codes=tuple(reason_codes))
