"""Multi-horizon momentum signal.

Aggregates momentum z-scores across 4 time horizons (6, 12, 24, 48 bars
on 4H = 1-day, 2-day, 4-day, 8-day) into a single direction signal.

Signal returns honest confidence — orchestrator uses confidence to weight
direction contribution. No manual dampening thresholds needed.

Based on Fieberg et al. (JFQA 2025): multi-horizon trend aggregation.
"""

from __future__ import annotations

from typing import Any

from zenquant.plugins.protocols import PluginMeta, SignalResult

meta = PluginMeta(
    name="multi_momentum_signal",
    version="2.0.0",
    plugin_type="signal",
    description="Multi-horizon momentum with confidence-based self-regulation",
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SignalResult:
    regime = state.get("regime", "RANGE")
    if regime not in ("TREND", "RANGE"):
        return SignalResult(value=0.0, confidence=0.0)

    multi_z = indicators.get("multi_momentum_z")
    if multi_z is None and config.get("use_single_fallback", True):
        multi_z = indicators.get("momentum_roc_z", 0.0)
    if multi_z is None:
        return SignalResult(value=0.0, confidence=0.0)

    roc_z = float(multi_z or 0.0)
    threshold = config.get("momentum_threshold", 0.3)
    sign = float(config.get("signal_sign", 1.0))
    structure = str(indicators.get("structure_state", "NEUTRAL"))
    range_profit_run_enabled = bool(config.get("range_profit_run_enabled", False))
    range_profit_run_threshold = float(config.get("range_profit_run_threshold", threshold))
    range_profit_run_value = float(config.get("range_profit_run_value", 0.75))
    range_profit_run_confidence = float(config.get("range_profit_run_confidence", 0.5))
    range_profit_run_max_transition_risk = float(config.get("range_profit_run_max_transition_risk", 1.0))
    range_profit_run_min_cycle_position = float(config.get("range_profit_run_min_cycle_position", 0.0))
    range_profit_run_block_distorted_volatility = bool(config.get("range_profit_run_block_distorted_volatility", False))
    range_profit_run_block_unsafe_quality = bool(config.get("range_profit_run_block_unsafe_quality", False))
    range_profit_run_cycle_fallback_enabled = bool(config.get("range_profit_run_cycle_fallback_enabled", False))
    range_profit_run_cycle_threshold = float(config.get("range_profit_run_cycle_threshold", 1.0))
    range_profit_run_cycle_momentum_threshold = float(
        config.get("range_profit_run_cycle_momentum_threshold", range_profit_run_threshold)
    )
    range_profit_run_block_confirmed_downtrend = bool(config.get("range_profit_run_block_confirmed_downtrend", False))
    range_profit_run_min_state_direction_signal = float(config.get("range_profit_run_min_state_direction_signal", -1.0))
    range_profit_run_require_uptrend_state = bool(config.get("range_profit_run_require_uptrend_state", False))
    range_bear_profit_run_enabled = bool(config.get("range_bear_profit_run_enabled", False))
    range_bear_profit_run_threshold = float(config.get("range_bear_profit_run_threshold", range_profit_run_threshold))
    range_bear_profit_run_value = float(config.get("range_bear_profit_run_value", range_profit_run_value))
    range_bear_profit_run_confidence = float(
        config.get("range_bear_profit_run_confidence", range_profit_run_confidence)
    )
    range_bear_profit_run_max_transition_risk = float(
        config.get("range_bear_profit_run_max_transition_risk", range_profit_run_max_transition_risk)
    )
    range_bear_profit_run_cycle_fallback_enabled = bool(
        config.get("range_bear_profit_run_cycle_fallback_enabled", False)
    )
    range_bear_profit_run_cycle_threshold = float(config.get("range_bear_profit_run_cycle_threshold", 0.95))
    range_bear_profit_run_cycle_momentum_threshold = float(
        config.get("range_bear_profit_run_cycle_momentum_threshold", range_bear_profit_run_threshold)
    )
    range_bear_profit_run_block_confirmed_uptrend = bool(
        config.get("range_bear_profit_run_block_confirmed_uptrend", True)
    )
    range_bear_profit_run_max_state_direction_signal = float(
        config.get("range_bear_profit_run_max_state_direction_signal", 0.25)
    )
    range_bear_profit_run_panic_exhaustion_veto_enabled = bool(
        config.get("range_bear_profit_run_panic_exhaustion_veto_enabled", False)
    )
    range_bear_profit_run_panic_exhaustion_momentum_threshold = float(
        config.get("range_bear_profit_run_panic_exhaustion_momentum_threshold", -0.85)
    )
    range_bear_profit_run_panic_exhaustion_transition_ceiling = float(
        config.get("range_bear_profit_run_panic_exhaustion_transition_ceiling", 0.48)
    )

    # Confidence naturally encodes regime suitability.
    # Orchestrator uses confidence to weight direction contribution.
    # No manual dampening — confidence does the work.

    if regime == "RANGE" and range_bear_profit_run_enabled:
        cycle_position = float(indicators.get("cycle_position", 1.0) or 1.0)
        structure_candidate = structure == "DOWNTREND" and roc_z < -range_bear_profit_run_threshold
        cycle_candidate = (
            range_bear_profit_run_cycle_fallback_enabled
            and structure != "UPTREND"
            and cycle_position <= range_bear_profit_run_cycle_threshold
            and roc_z < -range_bear_profit_run_cycle_momentum_threshold
        )
        if structure_candidate or cycle_candidate:
            transition_risk = float(state.get("transition_risk", 0.0) or 0.0)
            volatility_state = str(state.get("volatility_state") or "NORMAL")
            quality_state = str(state.get("quality_state") or "TRADABLE")
            directional_state = str(state.get("directional_state") or "NEUTRAL")
            directional_confidence = float(state.get("directional_confidence", 0.0) or 0.0)
            state_direction_signal = float(state.get("direction_signal", 0.0) or 0.0)
            guard_reasons: list[str] = []
            if transition_risk > range_bear_profit_run_max_transition_risk:
                guard_reasons.append("TRANSITION_RISK")
            if range_profit_run_block_distorted_volatility and volatility_state in {"HIGH", "DISTORTED"}:
                guard_reasons.append("VOLATILITY_STATE")
            if range_profit_run_block_unsafe_quality and quality_state == "UNSAFE":
                guard_reasons.append("QUALITY_STATE")
            if (
                range_bear_profit_run_block_confirmed_uptrend
                and directional_state == "UPTREND"
                and directional_confidence >= 0.55
            ):
                guard_reasons.append("CONFIRMED_UPTREND")
            if state_direction_signal > range_bear_profit_run_max_state_direction_signal:
                guard_reasons.append("STATE_DIRECTION_SIGNAL")
            if (
                range_bear_profit_run_panic_exhaustion_veto_enabled
                and volatility_state == "HIGH"
                and roc_z <= range_bear_profit_run_panic_exhaustion_momentum_threshold
                and transition_risk <= range_bear_profit_run_panic_exhaustion_transition_ceiling
            ):
                guard_reasons.append("PANIC_EXHAUSTION")
            if guard_reasons:
                return SignalResult(
                    value=0.0,
                    confidence=0.0,
                    reason_codes=tuple(f"MULTI_MOM_RANGE_BEAR_PROFIT_RUN_GUARDED_{reason}" for reason in guard_reasons),
                )
            confidence = min(1.0, max(0.0, range_bear_profit_run_confidence + min(0.15, abs(roc_z) / 10.0)))
            reason_codes = ["MULTI_MOM_RANGE_BEAR_PROFIT_RUN"]
            if structure_candidate:
                reason_codes.append("STRUCTURE_DOWNTREND")
            if cycle_candidate:
                reason_codes.append("CYCLE_BEAR_TREND")
            return SignalResult(
                value=sign * -min(1.0, max(0.0, range_bear_profit_run_value)),
                confidence=confidence,
                reason_codes=tuple(reason_codes),
            )

    if regime == "RANGE" and range_profit_run_enabled:
        cycle_position = float(indicators.get("cycle_position", 1.0) or 1.0)
        structure_candidate = structure == "UPTREND" and roc_z > range_profit_run_threshold
        cycle_candidate = (
            range_profit_run_cycle_fallback_enabled
            and structure != "DOWNTREND"
            and cycle_position >= range_profit_run_cycle_threshold
            and roc_z > range_profit_run_cycle_momentum_threshold
        )
        if structure_candidate or cycle_candidate:
            transition_risk = float(state.get("transition_risk", 0.0) or 0.0)
            volatility_state = str(state.get("volatility_state") or "NORMAL")
            quality_state = str(state.get("quality_state") or "TRADABLE")
            directional_state = str(state.get("directional_state") or "NEUTRAL")
            directional_confidence = float(state.get("directional_confidence", 0.0) or 0.0)
            state_direction_signal = float(state.get("direction_signal", 0.0) or 0.0)
            guard_reasons: list[str] = []
            if transition_risk > range_profit_run_max_transition_risk:
                guard_reasons.append("TRANSITION_RISK")
            if cycle_position < range_profit_run_min_cycle_position:
                guard_reasons.append("CYCLE_POSITION")
            if range_profit_run_block_distorted_volatility and volatility_state in {"HIGH", "DISTORTED"}:
                guard_reasons.append("VOLATILITY_STATE")
            if range_profit_run_block_unsafe_quality and quality_state == "UNSAFE":
                guard_reasons.append("QUALITY_STATE")
            if (
                range_profit_run_block_confirmed_downtrend
                and directional_state == "DOWNTREND"
                and directional_confidence >= 0.55
            ):
                guard_reasons.append("CONFIRMED_DOWNTREND")
            if range_profit_run_require_uptrend_state and (
                directional_state != "UPTREND" or directional_confidence < 0.55
            ):
                guard_reasons.append("UPTREND_STATE")
            if state_direction_signal < range_profit_run_min_state_direction_signal:
                guard_reasons.append("STATE_DIRECTION_SIGNAL")
            if guard_reasons:
                return SignalResult(
                    value=0.0,
                    confidence=0.0,
                    reason_codes=tuple(f"MULTI_MOM_RANGE_BULL_PROFIT_RUN_GUARDED_{reason}" for reason in guard_reasons),
                )
            confidence = min(1.0, max(0.0, range_profit_run_confidence + min(0.15, abs(roc_z) / 10.0)))
            reason_codes = ["MULTI_MOM_RANGE_BULL_PROFIT_RUN"]
            if structure_candidate:
                reason_codes.append("STRUCTURE_UPTREND")
            if cycle_candidate:
                reason_codes.append("CYCLE_PROFIT_RUN")
            return SignalResult(
                value=sign * min(1.0, max(0.0, range_profit_run_value)),
                confidence=confidence,
                reason_codes=tuple(reason_codes),
            )

    if regime == "TREND":
        if structure == "UPTREND" and roc_z > threshold:
            confidence = min(1.0, 0.6 + abs(roc_z) / 8.0)
            return SignalResult(
                value=sign * 1.0,
                confidence=confidence,
                reason_codes=("MULTI_MOM_BULL", "STRUCTURE_UPTREND"),
            )
        if structure == "DOWNTREND" and roc_z < -threshold:
            confidence = min(1.0, 0.6 + abs(roc_z) / 8.0)
            return SignalResult(
                value=sign * -1.0,
                confidence=confidence,
                reason_codes=("MULTI_MOM_BEAR", "STRUCTURE_DOWNTREND"),
            )
        if abs(roc_z) > threshold * 2:
            direction = 1.0 if roc_z > 0 else -1.0
            return SignalResult(
                value=sign * direction * 0.6,
                confidence=0.4,
                reason_codes=("MULTI_MOM_WEAK",),
            )

    return SignalResult(value=0.0, confidence=0.2)
