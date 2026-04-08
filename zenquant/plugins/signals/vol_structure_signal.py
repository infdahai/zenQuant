"""Volatility-structure state modifier for execution/risk quality."""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="vol_structure_signal",
    version="1.0.0",
    plugin_type="signal",
    description="React to vol expansion/compression and trend persistence quality",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime == "STRESS":
        return SignalResult(value=0.0, confidence=0.0)

    rv_term_ratio = float(indicators.get("rv_term_ratio", 1.0) or 1.0)
    vol_of_vol = float(indicators.get("vol_of_vol", 0.0) or 0.0)
    trend_persistence = float(indicators.get("trend_persistence", 0.0) or 0.0)
    momentum_z = float(indicators.get("multi_momentum_z", 0.0) or 0.0)

    expansion_threshold = float(config.get("expansion_threshold", 1.30))
    compression_threshold = float(config.get("compression_threshold", 0.85))
    vol_of_vol_threshold = float(config.get("vol_of_vol_threshold", 0.12))
    persistence_threshold = float(config.get("persistence_threshold", 0.65))

    if rv_term_ratio >= expansion_threshold and vol_of_vol >= vol_of_vol_threshold:
        magnitude = min(0.7, 0.2 + (rv_term_ratio - expansion_threshold) * 0.6 + vol_of_vol * 0.8)
        return SignalResult(
            value=-magnitude,
            confidence=min(0.85, 0.45 + vol_of_vol * 1.5),
            reason_codes=("VOL_STRUCTURE_EXPANSION",),
        )

    if rv_term_ratio <= compression_threshold and trend_persistence >= persistence_threshold and momentum_z > 0.0:
        magnitude = min(0.6, 0.2 + (compression_threshold - rv_term_ratio) * 0.5 + (trend_persistence - persistence_threshold) * 0.8)
        return SignalResult(
            value=magnitude,
            confidence=min(0.8, 0.4 + trend_persistence * 0.4),
            reason_codes=("VOL_STRUCTURE_TREND_SUPPORT",),
        )

    return SignalResult(value=0.0, confidence=0.1)
