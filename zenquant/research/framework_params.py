"""Shared reduced-core framework parameter mapping for clean tuning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from zenquant.indicator import IndicatorEngineConfig
from zenquant.research.rl_tuner import (
    build_execution_config as _build_execution_config,
)
from zenquant.research.rl_tuner import (
    build_indicator_config as _build_indicator_config,
)
from zenquant.research.rl_tuner import (
    build_risk_config as _build_risk_config,
)
from zenquant.research.rl_tuner import (
    build_signal_weight_overrides as _build_signal_weight_overrides,
)
from zenquant.research.rl_tuner import (
    build_strategy_config as _build_strategy_config,
)
from zenquant.risk import RiskOverlayConfig
from zenquant.strategy import ARCStrategyConfig, ExitEngineConfig, PlaybookEngineConfig

FRAMEWORK_CAMPAIGN_BUDGETS: tuple[str, ...] = ("smoke", "fast", "high")
FRAMEWORK_FAST_WORKER_PROFILES: tuple[str, ...] = ("fast_low", "fast_default", "fast_high_cpu")

SEARCH_SPACE_MODES: tuple[str, ...] = (
    "baseline_anchored_train_only",
    "baseline_anchored_defensive_candidate_existence",
    "baseline_anchored_2023_08_hostile_repair",
    "execution_inventory_rethink_train_only",
    "intent_churn_ablation_train_only",
    "intent_churn_repair_train_only",
    "binance_factor_weight_repair_train_only",
    "ralph_protective_reduce_train_only",
    "ralph_protective_reduce_fee_repair_train_only",
    "ralph_execution_fee_cooldown_train_only",
    "ralph_intent_reduce_cadence_train_only",
    "paulwei_balanced_tranche_train_only",
    "paulwei_dual_phase_train_only",
    "paulwei_dual_phase_fee_repair_train_only",
    "paulwei_dual_phase_spot_context_train_only",
    "paulwei_dual_phase_spot_context_relaxed_fee_train_only",
    "execution_first_v2_correlation_constrained",
)

BASE_TIMEFRAME_SMOKE_MODES: frozenset[str] = frozenset(
    {
        "intent_churn_ablation_train_only",
        "intent_churn_repair_train_only",
        "ralph_protective_reduce_train_only",
        "ralph_protective_reduce_fee_repair_train_only",
        "ralph_execution_fee_cooldown_train_only",
        "ralph_intent_reduce_cadence_train_only",
        "paulwei_balanced_tranche_train_only",
        "paulwei_dual_phase_train_only",
        "paulwei_dual_phase_fee_repair_train_only",
        "paulwei_dual_phase_spot_context_train_only",
        "paulwei_dual_phase_spot_context_relaxed_fee_train_only",
    }
)

STRICT_TRAIN_FEE_STOP_PROFILE_FLAGS: tuple[str, ...] = (
    "intent_churn_ablation_search",
    "intent_churn_repair_search",
    "binance_factor_weight_repair_search",
    "paulwei_balanced_tranche_search",
    "paulwei_dual_phase_search",
    "paulwei_dual_phase_fee_repair_search",
    "paulwei_dual_phase_spot_context_search",
)

STRICT_TRAIN_TRUTH_PROFILE_FLAGS: tuple[str, ...] = (
    "intent_churn_ablation_search",
    "intent_churn_repair_search",
    "ralph_protective_reduce_search",
    "ralph_protective_reduce_fee_repair_search",
    "ralph_execution_fee_cooldown_search",
    "ralph_intent_reduce_cadence_search",
    "paulwei_balanced_tranche_search",
    "paulwei_dual_phase_search",
    "paulwei_dual_phase_fee_repair_search",
    "paulwei_dual_phase_spot_context_search",
)


@dataclass(frozen=True, slots=True)
class DevFeeStabilityLimits:
    dev_fee_drag_hard_cap: float = 0.05
    dev_fee_drag_fail_fast_cap: float = 0.075
    dev_hostile_window_fee_drag_hard_cap: float = 0.065
    dev_to_train_fee_drag_ratio_cap: float = 4.0
    dev_to_train_fee_drag_gap_cap: float = 0.04


DEFAULT_DEV_FEE_STABILITY_LIMITS = DevFeeStabilityLimits()
RELAXED_DEV_FEE_STABILITY_LIMITS = DevFeeStabilityLimits(
    dev_fee_drag_hard_cap=0.20,
    dev_fee_drag_fail_fast_cap=0.25,
    dev_hostile_window_fee_drag_hard_cap=0.25,
    dev_to_train_fee_drag_ratio_cap=12.0,
    dev_to_train_fee_drag_gap_cap=0.20,
)


def smoke_uses_base_timeframe(*, budget: str, search_space_mode: str) -> bool:
    return budget == "smoke" and search_space_mode in BASE_TIMEFRAME_SMOKE_MODES


def build_strategy_config(params: dict[str, Any]) -> ARCStrategyConfig:
    config = _build_strategy_config(params)
    strategy_kwargs: dict[str, Any] = {}
    playbook_kwargs: dict[str, Any] = {}
    exit_kwargs: dict[str, Any] = {}
    for key, value in params.items():
        if key == "intent_entry_confirmation_bars":
            strategy_kwargs["intent_entry_confirmation_bars"] = int(value)
        elif key == "min_bars_before_trail":
            strategy_kwargs["min_bars_before_trail"] = int(value)
        elif key == "min_hours_before_trail":
            strategy_kwargs["min_hours_before_trail"] = float(value)
        elif key == "intent_flip_confirmation_bars":
            strategy_kwargs["intent_flip_confirmation_bars"] = int(value)
        elif key == "intent_flat_confirmation_bars":
            strategy_kwargs["intent_flat_confirmation_bars"] = int(value)
        elif key == "intent_flat_reduce_step_pct":
            strategy_kwargs["intent_flat_reduce_step_pct"] = float(value)
        elif key == "intent_flat_step_cadence_bars":
            strategy_kwargs["intent_flat_step_cadence_bars"] = int(value)
        elif key == "intent_range_entry_confirmation_bars":
            strategy_kwargs["intent_range_entry_confirmation_bars"] = int(value)
        elif key == "intent_range_reentry_cooldown_bars":
            strategy_kwargs["intent_range_reentry_cooldown_bars"] = int(value)
        elif key == "intent_bearish_override_confirmation_bars":
            strategy_kwargs["intent_bearish_override_confirmation_bars"] = int(value)
        elif key == "intent_bearish_override_chop_guard_enabled":
            strategy_kwargs["intent_bearish_override_chop_guard_enabled"] = bool(round(float(value)))
        elif key == "intent_bearish_override_chop_max_confidence":
            strategy_kwargs["intent_bearish_override_chop_max_confidence"] = float(value)
        elif key == "intent_bearish_override_chop_min_transition_risk":
            strategy_kwargs["intent_bearish_override_chop_min_transition_risk"] = float(value)
        elif key == "intent_unconfirmed_flip_reduce_only":
            strategy_kwargs["intent_unconfirmed_flip_reduce_only"] = bool(round(float(value)))
        elif key == "direction_signal_threshold":
            strategy_kwargs["direction_signal_threshold"] = float(value)
        elif key == "intent_add_signal_threshold":
            strategy_kwargs["intent_add_signal_threshold"] = float(value)
        elif key == "intent_add_confirmation_bars":
            strategy_kwargs["intent_add_confirmation_bars"] = int(value)
        elif key == "intent_time_leverage_hold_enabled":
            strategy_kwargs["intent_time_leverage_hold_enabled"] = bool(round(float(value)))
        elif key == "intent_time_leverage_min_hold_bars":
            strategy_kwargs["intent_time_leverage_min_hold_bars"] = int(value)
        elif key == "intent_time_leverage_flip_signal_threshold":
            strategy_kwargs["intent_time_leverage_flip_signal_threshold"] = float(value)
        elif key == "intent_time_leverage_confidence_floor":
            strategy_kwargs["intent_time_leverage_confidence_floor"] = float(value)
        elif key == "intent_time_leverage_transition_risk_floor":
            strategy_kwargs["intent_time_leverage_transition_risk_floor"] = float(value)
        elif key == "intent_time_leverage_trailing_stop_atr_mult":
            strategy_kwargs["intent_time_leverage_trailing_stop_atr_mult"] = float(value)
        elif key == "intent_time_leverage_trailing_exit_hold_enabled":
            strategy_kwargs["intent_time_leverage_trailing_exit_hold_enabled"] = bool(round(float(value)))
        elif key == "intent_time_leverage_trailing_exit_max_adverse_atr":
            strategy_kwargs["intent_time_leverage_trailing_exit_max_adverse_atr"] = float(value)
        elif key == "intent_same_direction_reduce_confirmation_bars":
            strategy_kwargs["intent_same_direction_reduce_confirmation_bars"] = int(value)
        elif key == "intent_time_leverage_size_hysteresis_enabled":
            strategy_kwargs["intent_time_leverage_size_hysteresis_enabled"] = bool(round(float(value)))
        elif key == "intent_time_leverage_add_deadband_pct":
            strategy_kwargs["intent_time_leverage_add_deadband_pct"] = float(value)
        elif key == "intent_time_leverage_reduce_deadband_pct":
            strategy_kwargs["intent_time_leverage_reduce_deadband_pct"] = float(value)
        elif key == "intent_time_leverage_long_bias_enabled":
            strategy_kwargs["intent_time_leverage_long_bias_enabled"] = bool(round(float(value)))
        elif key == "intent_time_leverage_long_bias_confidence_floor":
            strategy_kwargs["intent_time_leverage_long_bias_confidence_floor"] = float(value)
        elif key == "intent_time_leverage_long_bias_transition_ceiling":
            strategy_kwargs["intent_time_leverage_long_bias_transition_ceiling"] = float(value)
        elif key == "intent_time_leverage_long_bias_block_short":
            strategy_kwargs["intent_time_leverage_long_bias_block_short"] = bool(round(float(value)))
        elif key == "intent_time_leverage_long_bias_hold_reductions":
            strategy_kwargs["intent_time_leverage_long_bias_hold_reductions"] = bool(round(float(value)))
        elif key == "intent_time_leverage_dip_accumulation_enabled":
            strategy_kwargs["intent_time_leverage_dip_accumulation_enabled"] = bool(round(float(value)))
        elif key == "intent_time_leverage_dip_min_adverse_atr":
            strategy_kwargs["intent_time_leverage_dip_min_adverse_atr"] = float(value)
        elif key == "intent_time_leverage_dip_max_adverse_atr":
            strategy_kwargs["intent_time_leverage_dip_max_adverse_atr"] = float(value)
        elif key == "intent_time_leverage_dip_add_pct":
            strategy_kwargs["intent_time_leverage_dip_add_pct"] = float(value)
        elif key == "intent_time_leverage_dip_max_leverage":
            strategy_kwargs["intent_time_leverage_dip_max_leverage"] = float(value)
        elif key == "preserve_capital_risk":
            playbook_kwargs["preserve_capital_transition_risk"] = value
        elif key == "bull_leverage":
            playbook_kwargs["bull_max_leverage"] = value
        elif key == "bear_leverage":
            playbook_kwargs["bear_max_leverage"] = value
        elif key == "range_leverage":
            playbook_kwargs["range_max_leverage"] = value
        elif key == "range_time_decay_bars":
            playbook_kwargs["range_time_decay_bars"] = int(value)
        elif key == "preserve_capital_time_decay_bars":
            playbook_kwargs["preserve_capital_time_decay_bars"] = int(value)
        elif key == "range_trailing_stop_atr_mult":
            playbook_kwargs["range_trailing_stop_atr_mult"] = float(value)
        elif key == "preserve_capital_trailing_stop_atr_mult":
            playbook_kwargs["preserve_capital_trailing_stop_atr_mult"] = float(value)
        elif key == "bull_trailing_stop_atr_mult":
            playbook_kwargs["bull_trailing_stop_atr_mult"] = float(value)
        elif key == "bear_trailing_stop_atr_mult":
            playbook_kwargs["bear_trailing_stop_atr_mult"] = float(value)
        elif key == "trade_directional_confidence_floor":
            playbook_kwargs["trade_directional_confidence_floor"] = float(value)
        elif key == "bearish_override_confidence_floor":
            playbook_kwargs["bearish_override_confidence_floor"] = float(value)
        elif key == "exit_transition_risk":
            exit_kwargs["transition_risk_exit_threshold"] = float(value)
    existing_playbook = asdict(config.playbook_config) if config.playbook_config is not None else {}
    existing_exit = asdict(config.exit_config) if config.exit_config is not None else {}
    return ARCStrategyConfig(
        **{
            **asdict(config),
            **strategy_kwargs,
            "playbook_config": (
                PlaybookEngineConfig(**{**existing_playbook, **playbook_kwargs})
                if config.playbook_config is not None or playbook_kwargs
                else None
            ),
            "exit_config": (
                ExitEngineConfig(**{**existing_exit, **exit_kwargs})
                if config.exit_config is not None or exit_kwargs
                else None
            ),
        }
    )


def build_risk_config(params: dict[str, Any]) -> RiskOverlayConfig:
    config = _build_risk_config(params)
    config.liq_risk_guard_threshold = float(params.get("liq_risk_guard_threshold", config.liq_risk_guard_threshold))
    config.liq_risk_guard_mult = float(params.get("liq_risk_guard_mult", config.liq_risk_guard_mult))
    config.enable_liq_risk_guard = bool(round(float(params.get("liq_risk_guard_enabled", 0.0))))
    return config


def build_execution_config(params: dict[str, Any]):
    return _build_execution_config(params)


def build_indicator_config(params: dict[str, Any]) -> IndicatorEngineConfig:
    return _build_indicator_config(params)


def build_signal_weight_overrides(params: dict[str, Any]) -> dict[str, float]:
    weights = _build_signal_weight_overrides(params)
    if "w_perp_carry" in params:
        weights["perp_carry_stress_signal"] = float(params["w_perp_carry"])
    if "w_vol_structure" in params:
        weights["vol_structure_signal"] = float(params["w_vol_structure"])
    if "w_binance_metrics_state" in params:
        weights["binance_metrics_state_signal"] = float(params["w_binance_metrics_state"])
    if "w_onchain_activity" in params:
        weights["onchain_activity_signal"] = float(params["w_onchain_activity"])
    if "w_alt_risk_context" in params:
        weights["alt_risk_context_signal"] = float(params["w_alt_risk_context"])
    if "w_cross_asset_flow" in params:
        weights["cross_asset_flow_signal"] = float(params["w_cross_asset_flow"])
    return weights


def build_plugin_config_overrides(params: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    overrides: dict[str, dict[str, dict[str, Any]]] = {}

    vol_target_keys = ("target_vol_5m", "target_vol_1h", "target_vol_4h", "target_vol_1d")
    if any(key in params for key in vol_target_keys):
        vol_target_config = overrides.setdefault("sizers", {}).setdefault("vol_target_sizer", {})
        for key in vol_target_keys:
            if key in params:
                vol_target_config[key] = float(params[key])

    if "w_binance_metrics_state" in params or "binance_metrics_state_enabled" in params:
        signal_weight = float(params.get("w_binance_metrics_state", 0.0) or 0.0)
        signal_enabled = bool(round(float(params.get("binance_metrics_state_enabled", 1.0 if signal_weight else 0.0))))
        overrides.setdefault("signals", {})["binance_metrics_state_signal"] = {
            "enabled": signal_enabled,
            "weight": signal_weight,
        }

    multi_momentum_profit_run_keys = (
        "multi_momentum_range_profit_run_enabled",
        "multi_momentum_range_profit_run_threshold",
        "multi_momentum_range_profit_run_value",
        "multi_momentum_range_profit_run_confidence",
        "multi_momentum_range_profit_run_max_transition_risk",
        "multi_momentum_range_profit_run_min_cycle_position",
        "multi_momentum_range_profit_run_block_distorted_volatility",
        "multi_momentum_range_profit_run_block_unsafe_quality",
    )
    if any(key in params for key in multi_momentum_profit_run_keys):
        multi_momentum_config = overrides.setdefault("signals", {}).setdefault("multi_momentum_signal", {})
        if "multi_momentum_range_profit_run_enabled" in params:
            multi_momentum_config["range_profit_run_enabled"] = bool(
                round(float(params["multi_momentum_range_profit_run_enabled"]))
            )
        if "multi_momentum_range_profit_run_threshold" in params:
            multi_momentum_config["range_profit_run_threshold"] = float(
                params["multi_momentum_range_profit_run_threshold"]
            )
        if "multi_momentum_range_profit_run_value" in params:
            multi_momentum_config["range_profit_run_value"] = float(params["multi_momentum_range_profit_run_value"])
        if "multi_momentum_range_profit_run_confidence" in params:
            multi_momentum_config["range_profit_run_confidence"] = float(
                params["multi_momentum_range_profit_run_confidence"]
            )
        if "multi_momentum_range_profit_run_max_transition_risk" in params:
            multi_momentum_config["range_profit_run_max_transition_risk"] = float(
                params["multi_momentum_range_profit_run_max_transition_risk"]
            )
        if "multi_momentum_range_profit_run_min_cycle_position" in params:
            multi_momentum_config["range_profit_run_min_cycle_position"] = float(
                params["multi_momentum_range_profit_run_min_cycle_position"]
            )
        if "multi_momentum_range_profit_run_block_distorted_volatility" in params:
            multi_momentum_config["range_profit_run_block_distorted_volatility"] = bool(
                round(float(params["multi_momentum_range_profit_run_block_distorted_volatility"]))
            )
        if "multi_momentum_range_profit_run_block_unsafe_quality" in params:
            multi_momentum_config["range_profit_run_block_unsafe_quality"] = bool(
                round(float(params["multi_momentum_range_profit_run_block_unsafe_quality"]))
            )

    if "binance_metrics_risk_sizer_enabled" in params:
        sizer_enabled = bool(round(float(params.get("binance_metrics_risk_sizer_enabled", 0.0) or 0.0)))
        overrides.setdefault("sizers", {})["binance_metrics_risk_sizer"] = {
            "enabled": sizer_enabled,
            "edge_threshold": float(params.get("binance_metrics_risk_edge_threshold", 0.2)),
            "edge_discount_scale": float(params.get("binance_metrics_risk_edge_discount_scale", 0.25)),
            "crowding_threshold": float(params.get("binance_metrics_risk_crowding_threshold", 1.0)),
            "crowding_discount_scale": float(params.get("binance_metrics_risk_crowding_discount_scale", 0.08)),
            "discount_floor": float(params.get("binance_metrics_risk_discount_floor", 0.65)),
            "max_discount": float(params.get("binance_metrics_risk_max_discount", 0.35)),
        }

    if "exit_transition_risk" in params:
        overrides.setdefault("exits", {}).setdefault("regime_change_exit", {})["transition_risk_threshold"] = float(
            params["exit_transition_risk"]
        )
    if "time_decay_exit_enabled" in params:
        overrides.setdefault("exits", {}).setdefault("time_decay_exit", {})["enabled"] = bool(
            round(float(params["time_decay_exit_enabled"]))
        )
    if "time_decay_long_loss_threshold" in params:
        overrides.setdefault("exits", {}).setdefault("time_decay_exit", {})["long_loss_threshold"] = float(
            params["time_decay_long_loss_threshold"]
        )
    if "time_decay_short_loss_threshold" in params:
        overrides.setdefault("exits", {}).setdefault("time_decay_exit", {})["short_loss_threshold"] = float(
            params["time_decay_short_loss_threshold"]
        )

    return overrides


def build_replay_toggles(params: dict[str, Any]) -> tuple[bool, bool]:
    liquidation_data_enabled = bool(round(float(params.get("liquidation_data_enabled", 0.0))))
    state_overlay_enabled = bool(round(float(params.get("state_liquidation_overlay_enabled", 0.0))))
    return liquidation_data_enabled, liquidation_data_enabled and state_overlay_enabled


def hostile_constraint_violations(params: dict[str, Any]) -> list[str]:
    """Return deterministic hostile-window guardrail violations for execution-first tuning."""

    violations: list[str] = []
    if {"target_vol_4h", "max_leverage"} <= params.keys():
        target_vol_4h = float(params.get("target_vol_4h", 0.0) or 0.0)
        max_leverage = float(params.get("max_leverage", 0.0) or 0.0)
    else:
        target_vol_4h = max_leverage = None
    if {"pyramid_add_pct", "pyramid_short_add_pct"} <= params.keys():
        pyramid_add_pct = float(params.get("pyramid_add_pct", 0.0) or 0.0)
        pyramid_short_add_pct = float(params.get("pyramid_short_add_pct", 0.0) or 0.0)
    else:
        pyramid_add_pct = pyramid_short_add_pct = None
    if {"dca_spacing_atr", "dca_size_decay"} <= params.keys():
        dca_spacing_atr = float(params.get("dca_spacing_atr", 0.0) or 0.0)
        dca_size_decay = float(params.get("dca_size_decay", 0.0) or 0.0)
    else:
        dca_spacing_atr = dca_size_decay = None
    flip_min_confidence = (
        float(params.get("flip_min_confidence", 0.0) or 0.0) if "flip_min_confidence" in params else None
    )

    if target_vol_4h is not None and max_leverage is not None and target_vol_4h >= 0.95 and max_leverage > 4.5:
        violations.append("high_target_vol_with_excess_leverage")
    if (
        pyramid_add_pct is not None
        and pyramid_short_add_pct is not None
        and pyramid_add_pct >= 0.20
        and pyramid_short_add_pct > 0.12
    ):
        violations.append("aggressive_long_and_short_pyramiding")
    if (
        dca_spacing_atr is not None
        and dca_size_decay is not None
        and dca_spacing_atr <= 0.75
        and dca_size_decay <= 0.60
    ):
        violations.append("tight_dca_spacing_with_heavy_inventory_reloading")
    if (
        flip_min_confidence is not None
        and flip_min_confidence <= 0.60
        and (
            float(params.get("pyramid_initial_pct", 0.0) or 0.0) >= 0.30
            or (pyramid_add_pct is not None and pyramid_add_pct >= 0.20)
        )
    ):
        violations.append("low_flip_confidence_with_fast_inventory_expansion")
    return violations


def is_hostile_aware_param_combo(params: dict[str, Any]) -> bool:
    return not hostile_constraint_violations(params)


def baseline_anchored_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    grid: dict[str, list[float]] = {
        "dd_step_1_long": [0.12, 0.15, 0.18],
        "dd_step_1_short": [0.06, 0.08, 0.10],
        "vpin_threshold": [0.08, 0.10],
        "vol_shock_threshold": [0.90, 0.95, 1.00],
        "target_vol_4h": [0.80, 0.95, 1.10],
        "max_leverage": [4.5, 6.0],
        "pyramid_initial_pct": [0.30],
        "pyramid_add_pct": [0.30],
        "pyramid_short_initial_pct": [0.15, 0.20],
        "pyramid_short_add_pct": [0.10, 0.12, 0.16, 0.20],
        "pyramid_spacing_atr": [0.50, 0.75, 1.00],
        "exit_unwind_pct": [0.30, 0.35, 0.40],
        "dca_max_adverse_atr": [2.5, 3.0],
        "dca_spacing_atr": [0.75, 1.25, 1.75],
        "dca_size_decay": [0.60, 0.75, 0.90],
        "flip_min_confidence": [0.50, 0.60, 0.70],
    }
    if include_liquidation_toggles:
        grid.update(
            {
                "liquidation_data_enabled": [0.0, 1.0],
                "state_liquidation_overlay_enabled": [0.0, 1.0],
                "liq_risk_guard_enabled": [0.0, 1.0],
                "liq_risk_guard_threshold": [1.8, 2.2],
                "liq_risk_guard_mult": [0.65, 0.75],
            }
        )
    return grid


def baseline_anchored_defensive_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    grid: dict[str, list[float]] = {
        "dd_step_1_long": [0.15, 0.18],
        "dd_step_1_short": [0.08, 0.10],
        "vpin_threshold": [0.10],
        "vol_shock_threshold": [0.95, 1.00],
        "target_vol_4h": [1.10],
        "max_leverage": [4.5, 6.0],
        "pyramid_initial_pct": [0.30],
        "pyramid_add_pct": [0.30],
        "pyramid_short_initial_pct": [0.15, 0.20],
        "pyramid_short_add_pct": [0.10, 0.12, 0.16],
        "pyramid_spacing_atr": [0.75, 1.00],
        "exit_unwind_pct": [0.30, 0.35],
        "dca_max_adverse_atr": [2.5],
        "dca_spacing_atr": [0.75, 1.25, 1.75],
        "dca_size_decay": [0.75, 0.90],
        "flip_min_confidence": [0.50, 0.60, 0.70],
    }
    if include_liquidation_toggles:
        grid.update(
            {
                "liquidation_data_enabled": [0.0],
                "state_liquidation_overlay_enabled": [0.0],
                "liq_risk_guard_enabled": [0.0],
                "liq_risk_guard_threshold": [1.8],
                "liq_risk_guard_mult": [0.65],
            }
        )
    return grid


def baseline_anchored_2023_08_hostile_repair_param_grid(
    *,
    include_liquidation_toggles: bool = False,
) -> dict[str, list[float]]:
    grid: dict[str, list[float]] = {
        "dd_step_1_long": [0.18],
        "dd_step_1_short": [0.10],
        "vpin_threshold": [0.10],
        "vol_shock_threshold": [1.00],
        "target_vol_4h": [1.10],
        "max_leverage": [4.5],
        "pyramid_initial_pct": [0.30],
        "pyramid_add_pct": [0.30],
        "pyramid_short_initial_pct": [0.15],
        "pyramid_short_add_pct": [0.10, 0.12],
        "pyramid_spacing_atr": [1.00],
        "exit_unwind_pct": [0.35],
        "dca_max_adverse_atr": [2.5],
        "dca_spacing_atr": [1.25, 1.75],
        "dca_size_decay": [0.75, 0.90],
        "flip_min_confidence": [0.60, 0.70],
    }
    if include_liquidation_toggles:
        grid.update(
            {
                "liquidation_data_enabled": [0.0],
                "state_liquidation_overlay_enabled": [0.0],
                "liq_risk_guard_enabled": [0.0],
                "liq_risk_guard_threshold": [1.8],
                "liq_risk_guard_mult": [0.65],
            }
        )
    return grid


def execution_inventory_rethink_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    grid: dict[str, list[float]] = {
        "dd_step_1_long": [0.15, 0.18],
        "dd_step_1_short": [0.08, 0.10],
        "vpin_threshold": [0.10],
        "vol_shock_threshold": [0.95, 1.00],
        "target_vol_4h": [0.80, 0.95],
        "max_leverage": [4.5],
        "pyramid_initial_pct": [0.30],
        "pyramid_add_pct": [0.30],
        "pyramid_short_initial_pct": [0.15],
        "pyramid_short_add_pct": [0.10, 0.12],
        "pyramid_spacing_atr": [1.00, 1.25],
        "exit_unwind_pct": [0.35, 0.40],
        "dca_max_adverse_atr": [2.0, 2.5],
        "dca_spacing_atr": [1.25, 1.75],
        "dca_size_decay": [0.90],
        "flip_min_confidence": [0.70, 0.75],
        "hostile_behavior_router_enabled": [0.0, 1.0],
        "squeeze_flip_flatten_only_enabled": [0.0],
        "squeeze_flip_cooldown_enabled": [0.0],
        "squeeze_conditional_cooldown_enabled": [0.0, 1.0],
        "squeeze_flip_cooldown_bars": [1, 2],
        "squeeze_conditional_cooldown_drawdown_threshold": [0.04, 0.06],
        "squeeze_conditional_cooldown_toxic_only": [0.0],
        "squeeze_cooldown_cancel_on_non_squeeze_enabled": [0.0, 1.0],
        "liquidation_data_enabled": [0.0],
        "state_liquidation_overlay_enabled": [0.0],
        "liq_risk_guard_enabled": [0.0],
    }
    if include_liquidation_toggles:
        grid.update(
            {
                "liquidation_data_enabled": [0.0],
                "state_liquidation_overlay_enabled": [0.0],
                "liq_risk_guard_enabled": [0.0],
                "liq_risk_guard_threshold": [1.8],
                "liq_risk_guard_mult": [0.65],
            }
        )
    return grid


def intent_churn_repair_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    grid: dict[str, list[float]] = {
        "dd_step_1_long": [0.15, 0.18],
        "dd_step_1_short": [0.08, 0.10],
        "vpin_threshold": [0.10],
        "vol_shock_threshold": [0.95, 1.00],
        "target_vol_4h": [0.80, 0.95],
        "max_leverage": [4.5],
        "pyramid_initial_pct": [0.30],
        "pyramid_add_pct": [0.30],
        "pyramid_short_initial_pct": [0.15],
        "pyramid_short_add_pct": [0.10, 0.12],
        "pyramid_spacing_atr": [1.00, 1.25],
        "exit_unwind_pct": [0.35, 0.40],
        "dca_max_adverse_atr": [2.0, 2.5],
        "dca_spacing_atr": [1.25, 1.75],
        "dca_size_decay": [0.90],
        "flip_min_confidence": [0.70, 0.75],
        "range_time_decay_bars": [5, 8, 12],
        "preserve_capital_time_decay_bars": [2, 3],
        "trade_directional_confidence_floor": [0.55, 0.60, 0.65],
        "bearish_override_confidence_floor": [0.55, 0.60, 0.65],
        "exit_transition_risk": [0.85, 0.90],
        "hostile_behavior_router_enabled": [0.0],
        "squeeze_flip_flatten_only_enabled": [0.0],
        "squeeze_flip_cooldown_enabled": [0.0],
        "squeeze_conditional_cooldown_enabled": [0.0],
        "squeeze_flip_cooldown_bars": [1],
        "squeeze_conditional_cooldown_drawdown_threshold": [0.06],
        "squeeze_conditional_cooldown_toxic_only": [0.0],
        "squeeze_cooldown_cancel_on_non_squeeze_enabled": [0.0],
        "liquidation_data_enabled": [0.0],
        "state_liquidation_overlay_enabled": [0.0],
        "liq_risk_guard_enabled": [0.0],
    }
    if include_liquidation_toggles:
        grid.update(
            {
                "liquidation_data_enabled": [0.0],
                "state_liquidation_overlay_enabled": [0.0],
                "liq_risk_guard_enabled": [0.0],
                "liq_risk_guard_threshold": [1.8],
                "liq_risk_guard_mult": [0.65],
            }
        )
    return grid


def intent_churn_ablation_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    """Train-only strategy-intent search biased toward PaulWei mature low-switch cadence."""

    grid: dict[str, list[float]] = {
        "dd_step_1_long": [0.18],
        "dd_step_1_short": [0.10],
        "vpin_threshold": [0.10],
        "vol_shock_threshold": [1.00],
        "target_vol_4h": [0.30, 0.35, 0.40, 0.45, 0.50, 0.65],
        "max_leverage": [2.0, 2.5, 3.0],
        "pyramid_initial_pct": [0.30],
        "pyramid_add_pct": [0.30],
        "pyramid_short_initial_pct": [0.15],
        "pyramid_short_add_pct": [0.10, 0.12],
        "pyramid_spacing_atr": [1.00],
        "exit_unwind_pct": [0.35],
        "dca_max_adverse_atr": [2.5],
        "dca_spacing_atr": [1.75],
        "dca_size_decay": [0.90],
        "flip_min_confidence": [0.70],
        "range_time_decay_bars": [8, 12, 16, 24],
        "preserve_capital_time_decay_bars": [3, 4, 6],
        "range_trailing_stop_atr_mult": [3.0, 3.5, 4.5],
        "preserve_capital_trailing_stop_atr_mult": [2.0, 2.5, 3.0],
        "trade_directional_confidence_floor": [0.50, 0.55, 0.60, 0.65],
        "bearish_override_confidence_floor": [0.50, 0.55, 0.60, 0.65],
        "exit_transition_risk": [0.80, 0.85, 0.90],
        "direction_signal_threshold": [0.30, 0.35, 0.40, 0.45, 0.50, 0.55],
        "intent_add_signal_threshold": [0.45, 0.50, 0.55, 0.60],
        "intent_add_confirmation_bars": [1, 2, 4],
        "min_bars_before_trail": [1],
        "min_hours_before_trail": [0.0, 12.0, 20.0, 36.0],
        "intent_entry_confirmation_bars": [3, 4],
        "intent_flip_confirmation_bars": [6, 8, 12, 16],
        "intent_flat_confirmation_bars": [8, 12, 16, 24],
        "intent_range_entry_confirmation_bars": [4, 6, 8, 12],
        "intent_range_reentry_cooldown_bars": [0, 12, 24, 48],
        "intent_bearish_override_confirmation_bars": [0, 4, 8, 12],
        "intent_bearish_override_chop_guard_enabled": [0.0, 1.0],
        "intent_bearish_override_chop_max_confidence": [0.40, 0.50, 0.58],
        "intent_bearish_override_chop_min_transition_risk": [0.40, 0.55, 0.65],
        "intent_unconfirmed_flip_reduce_only": [0.0, 1.0],
        "intent_time_leverage_hold_enabled": [1.0],
        "intent_time_leverage_min_hold_bars": [168, 240, 336, 480],
        "intent_time_leverage_flip_signal_threshold": [0.65, 0.75],
        "intent_time_leverage_confidence_floor": [0.65, 0.70],
        "intent_time_leverage_transition_risk_floor": [0.35, 0.45],
        "intent_time_leverage_trailing_stop_atr_mult": [0.0],
        "intent_time_leverage_size_hysteresis_enabled": [1.0],
        "intent_time_leverage_add_deadband_pct": [0.25, 0.40, 0.60],
        "intent_time_leverage_reduce_deadband_pct": [0.80, 1.00, 1.25],
        "intent_time_leverage_long_bias_enabled": [1.0],
        "intent_time_leverage_long_bias_confidence_floor": [0.65, 0.70],
        "intent_time_leverage_long_bias_transition_ceiling": [0.35],
        "intent_time_leverage_long_bias_block_short": [1.0],
        "intent_time_leverage_long_bias_hold_reductions": [1.0],
        "intent_time_leverage_dip_accumulation_enabled": [0.0, 1.0],
        "intent_time_leverage_dip_min_adverse_atr": [0.75, 1.25],
        "intent_time_leverage_dip_max_adverse_atr": [2.5, 3.0],
        "intent_time_leverage_dip_add_pct": [0.10, 0.15],
        "intent_time_leverage_dip_max_leverage": [2.0, 2.5, 3.0],
        "hostile_behavior_router_enabled": [0.0],
        "squeeze_flip_flatten_only_enabled": [0.0],
        "squeeze_flip_cooldown_enabled": [0.0],
        "squeeze_conditional_cooldown_enabled": [0.0],
        "squeeze_flip_cooldown_bars": [1],
        "squeeze_conditional_cooldown_drawdown_threshold": [0.06],
        "squeeze_conditional_cooldown_toxic_only": [0.0],
        "squeeze_cooldown_cancel_on_non_squeeze_enabled": [0.0],
        "liquidation_data_enabled": [0.0],
        "state_liquidation_overlay_enabled": [0.0],
        "liq_risk_guard_enabled": [0.0],
    }
    if include_liquidation_toggles:
        grid.update(
            {
                "liquidation_data_enabled": [0.0],
                "state_liquidation_overlay_enabled": [0.0],
                "liq_risk_guard_enabled": [0.0],
                "liq_risk_guard_threshold": [1.8],
                "liq_risk_guard_mult": [0.65],
            }
        )
    return grid


def binance_factor_weight_repair_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    """Train-only search space for audited Binance metrics/OI state-weight admission."""

    grid = intent_churn_repair_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "target_vol_4h": [0.50, 0.65, 0.80],
            "max_leverage": [2.5, 3.5, 4.5],
            "range_time_decay_bars": [8, 12, 16],
            "preserve_capital_time_decay_bars": [3, 4],
            "trade_directional_confidence_floor": [0.65, 0.70, 0.75, 0.80],
            "bearish_override_confidence_floor": [0.65, 0.70, 0.75, 0.80],
            "exit_transition_risk": [0.90, 0.95],
            "w_binance_metrics_state": [0.0, 0.07, 0.12, 0.18, 0.25],
            "w_perp_carry": [0.04, 0.08],
            "w_vol_structure": [0.04, 0.08],
            "binance_metrics_state_enabled": [0.0, 1.0],
            "binance_metrics_risk_sizer_enabled": [0.0, 1.0],
            "binance_metrics_risk_edge_threshold": [0.15, 0.25],
            "binance_metrics_risk_discount_floor": [0.60, 0.70],
            "flip_min_confidence": [0.70, 0.75, 0.80],
            "flip_flatten_only_enabled": [0.0],
            "flip_reverse_open_scale": [1.00],
            "loss_flip_reverse_open_confidence_floor": [0.0],
            "target_delta_deadband_qty": [0.0],
            "target_delta_deadband_pct": [0.0, 0.04, 0.06, 0.08],
            "target_reduce_deadband_qty": [0.0],
            "target_reduce_deadband_pct": [0.0],
            "min_hold_bars_before_flip": [0, 2, 4],
            "entry_cooldown_bars": [0, 1, 2],
            "weak_signal_no_flip_enabled": [0.0, 1.0],
            "weak_signal_no_flip_confidence": [0.70, 0.75],
            "tiny_reverse_reduce_only_enabled": [0.0, 1.0],
            "tiny_reverse_deadband_qty": [0.0],
            "tiny_reverse_deadband_pct": [0.05, 0.10, 0.20],
            "entry_confidence_floor": [0.0, 0.75, 0.80, 0.85],
            "reverse_open_confidence_floor": [0.0, 0.85, 0.90, 0.95],
            "range_entry_block_enabled": [0.0, 1.0],
            "degraded_entry_block_enabled": [0.0, 1.0],
            "degraded_entry_confidence_floor": [0.80, 0.85],
            "loss_dca_confidence_floor": [0.0, 0.80, 0.85],
            "loss_dca_range_block_enabled": [0.0, 1.0],
            "loss_dca_degraded_block_enabled": [0.0, 1.0],
            "profit_protect_reduce_enabled": [0.0],
            "profit_protect_min_move_atr": [0.75, 1.00, 1.25],
            "profit_protect_unwind_pct": [0.20, 0.25, 0.30],
            "profit_protect_confidence_ceiling": [0.70, 0.75, 0.80],
        }
    )
    return grid


def ralph_protective_reduce_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    """Train-only Ralph loop search biased toward flatten/reduce before reverse-open behavior."""

    grid = intent_churn_ablation_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "target_vol_4h": [0.35, 0.40, 0.50, 0.65],
            "max_leverage": [2.5, 3.0],
            "direction_signal_threshold": [0.40, 0.45, 0.50],
            "intent_add_signal_threshold": [0.50, 0.55, 0.60],
            "intent_entry_confirmation_bars": [4, 6],
            "intent_flip_confirmation_bars": [12, 16, 24],
            "intent_flat_confirmation_bars": [4, 8, 12],
            "intent_unconfirmed_flip_reduce_only": [1.0],
            "intent_time_leverage_min_hold_bars": [240, 336, 480],
            "intent_time_leverage_reduce_deadband_pct": [0.40, 0.80],
            "flip_min_confidence": [0.75, 0.80],
            "flip_flatten_only_enabled": [1.0],
            "flip_reverse_open_scale": [1.00],
            "loss_flip_reverse_open_confidence_floor": [0.95, 1.00],
            "target_delta_deadband_qty": [0.0],
            "target_delta_deadband_pct": [0.04, 0.06],
            "target_reduce_deadband_qty": [0.0],
            "target_reduce_deadband_pct": [0.0],
            "min_hold_bars_before_flip": [4, 8, 12],
            "entry_cooldown_bars": [1, 2, 4],
            "weak_signal_no_flip_enabled": [1.0],
            "weak_signal_no_flip_confidence": [0.75, 0.85],
            "tiny_reverse_reduce_only_enabled": [1.0],
            "tiny_reverse_deadband_qty": [0.0],
            "tiny_reverse_deadband_pct": [0.10, 0.20],
            "entry_confidence_floor": [0.80, 0.85, 0.90],
            "reverse_open_confidence_floor": [0.95, 1.00],
            "range_entry_block_enabled": [1.0],
            "degraded_entry_block_enabled": [1.0],
            "degraded_entry_confidence_floor": [0.85, 0.90],
            "loss_dca_confidence_floor": [0.80, 0.85, 0.90],
            "loss_dca_range_block_enabled": [1.0],
            "loss_dca_degraded_block_enabled": [1.0],
            "profit_protect_reduce_enabled": [1.0],
            "profit_protect_min_move_atr": [0.75, 1.00, 1.25],
            "profit_protect_unwind_pct": [0.20, 0.25, 0.30],
            "profit_protect_confidence_ceiling": [0.75, 0.80, 0.85],
        }
    )
    return grid


def ralph_protective_reduce_fee_repair_param_grid(
    *, include_liquidation_toggles: bool = False
) -> dict[str, list[float]]:
    """Ralph reduce-first search with tighter fee-drag controls after protective behavior is recovered."""

    grid = ralph_protective_reduce_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "target_vol_4h": [0.35, 0.40, 0.50],
            "max_leverage": [2.0, 2.5],
            "exit_unwind_pct": [0.45, 0.55],
            "intent_add_signal_threshold": [0.60, 0.65],
            "intent_add_confirmation_bars": [2, 4],
            "intent_entry_confirmation_bars": [6, 8],
            "intent_flip_confirmation_bars": [16, 24],
            "intent_flat_confirmation_bars": [2, 4, 8],
            "intent_time_leverage_reduce_deadband_pct": [0.20, 0.40],
            "target_delta_deadband_pct": [0.08, 0.10],
            "target_reduce_deadband_pct": [0.0],
            "entry_cooldown_bars": [4, 8],
            "weak_signal_no_flip_confidence": [0.85, 0.90],
            "profit_protect_min_move_atr": [1.25, 1.75, 2.25],
            "profit_protect_unwind_pct": [0.35, 0.50],
            "profit_protect_confidence_ceiling": [0.65, 0.75],
        }
    )
    return grid


def ralph_execution_fee_cooldown_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    """Ralph execution-fee search that throttles repeated protective reductions instead of killing activity."""

    grid = ralph_protective_reduce_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "target_vol_4h": [0.40, 0.50, 0.65],
            "max_leverage": [2.5, 3.0],
            "intent_add_signal_threshold": [0.50, 0.55, 0.60],
            "intent_entry_confirmation_bars": [4, 6],
            "intent_flip_confirmation_bars": [12, 16],
            "intent_flat_confirmation_bars": [4, 8],
            "target_delta_deadband_pct": [0.04, 0.06, 0.08],
            "entry_cooldown_bars": [2, 4],
            "profit_protect_min_move_atr": [0.75, 1.00, 1.25],
            "profit_protect_unwind_pct": [0.25, 0.35, 0.50],
            "profit_protect_confidence_ceiling": [0.75, 0.80],
            "profit_protect_cooldown_bars": [6, 12, 24],
        }
    )
    return grid


def ralph_intent_reduce_cadence_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    """Ralph train-only search for strategy-intent reduce confirmation and aggregation."""

    grid = ralph_protective_reduce_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "target_vol_4h": [0.40, 0.50, 0.65],
            "max_leverage": [2.5, 3.0],
            "intent_add_signal_threshold": [0.50, 0.55, 0.60],
            "intent_entry_confirmation_bars": [4, 6],
            "intent_flip_confirmation_bars": [12, 16],
            "intent_flat_confirmation_bars": [4, 8],
            "intent_flat_reduce_step_pct": [0.0, 0.15, 0.25, 0.35, 0.50],
            "intent_flat_step_cadence_bars": [0, 8, 12, 24],
            "intent_same_direction_reduce_confirmation_bars": [2, 4, 8, 12],
            "intent_time_leverage_reduce_deadband_pct": [0.40, 0.80, 1.00],
            "target_delta_deadband_pct": [0.04, 0.06, 0.08],
            "entry_cooldown_bars": [2, 4],
            "profit_protect_min_move_atr": [0.75, 1.00, 1.25],
            "profit_protect_unwind_pct": [0.25, 0.30, 0.35],
            "profit_protect_confidence_ceiling": [0.75, 0.80],
            "profit_protect_cooldown_bars": [0],
        }
    )
    return grid


def paulwei_balanced_tranche_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    """Train-only PaulWei-style intent search for balanced build and unwind tranches."""

    grid = intent_churn_ablation_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "target_vol_4h": [0.40, 0.50, 0.65],
            "max_leverage": [2.5, 3.0],
            "direction_signal_threshold": [0.30, 0.35, 0.40, 0.45],
            "intent_add_signal_threshold": [0.30, 0.35, 0.40, 0.45, 0.50],
            "intent_add_confirmation_bars": [1, 2],
            "intent_entry_confirmation_bars": [3, 4, 6],
            "intent_flip_confirmation_bars": [12, 16, 24],
            "intent_flat_confirmation_bars": [8, 12, 16, 24],
            "intent_range_entry_confirmation_bars": [6, 8, 12],
            "intent_range_reentry_cooldown_bars": [24, 48],
            "intent_bearish_override_confirmation_bars": [4, 8, 12],
            "intent_unconfirmed_flip_reduce_only": [0.0],
            "intent_time_leverage_min_hold_bars": [240, 336, 480],
            "intent_time_leverage_add_deadband_pct": [0.10, 0.15, 0.25],
            "intent_time_leverage_reduce_deadband_pct": [0.60, 0.80, 1.00],
            "intent_time_leverage_dip_accumulation_enabled": [1.0],
            "intent_time_leverage_dip_min_adverse_atr": [0.75, 1.25],
            "intent_time_leverage_dip_max_adverse_atr": [2.5, 3.0],
            "intent_time_leverage_dip_add_pct": [0.10, 0.15],
            "intent_time_leverage_dip_max_leverage": [2.5, 3.0],
            "intent_flat_reduce_step_pct": [0.15, 0.25, 0.35],
            "intent_flat_step_cadence_bars": [8, 12, 24],
            "intent_same_direction_reduce_confirmation_bars": [4, 8, 12],
            "range_time_decay_bars": [48, 96, 168],
            "preserve_capital_time_decay_bars": [12, 24, 48],
            "time_decay_exit_enabled": [1.0],
            "time_decay_long_loss_threshold": [-0.08, -0.12],
            "time_decay_short_loss_threshold": [-0.04, -0.06, -0.08],
            "flip_flatten_only_enabled": [0.0],
            "flip_reverse_open_scale": [1.00],
            "loss_flip_reverse_open_confidence_floor": [0.0],
            "target_delta_deadband_qty": [0.0],
            "target_delta_deadband_pct": [0.0],
            "target_reduce_deadband_qty": [0.0],
            "target_reduce_deadband_pct": [0.0],
            "min_hold_bars_before_flip": [0],
            "entry_cooldown_bars": [0],
            "weak_signal_no_flip_enabled": [0.0],
            "tiny_reverse_reduce_only_enabled": [0.0],
            "entry_confidence_floor": [0.0],
            "reverse_open_confidence_floor": [0.0],
            "range_entry_block_enabled": [0.0],
            "degraded_entry_block_enabled": [0.0],
            "loss_dca_confidence_floor": [0.0],
            "loss_dca_range_block_enabled": [0.0],
            "loss_dca_degraded_block_enabled": [0.0],
            "profit_protect_reduce_enabled": [0.0],
            "profit_protect_min_move_atr": [1.00],
            "profit_protect_unwind_pct": [0.25],
            "profit_protect_confidence_ceiling": [0.75],
            "profit_protect_cooldown_bars": [0],
        }
    )
    return grid


def paulwei_dual_phase_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    """Train-only PaulWei search that learns early expansion and mature control as separate targets."""

    grid = paulwei_balanced_tranche_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "paulwei_dual_phase_objective_enabled": [1.0],
            "target_vol_4h": [0.50, 0.65, 0.80],
            "max_leverage": [2.5, 3.0],
            "direction_signal_threshold": [0.30, 0.35, 0.40, 0.45],
            "intent_add_signal_threshold": [0.30, 0.35, 0.40, 0.45],
            "intent_entry_confirmation_bars": [3, 4, 6],
            "intent_flip_confirmation_bars": [16, 24, 36],
            "intent_flat_confirmation_bars": [12, 16, 24],
            "intent_range_entry_confirmation_bars": [8, 12],
            "intent_range_reentry_cooldown_bars": [24, 48],
            "intent_bearish_override_confirmation_bars": [8, 12],
            "intent_time_leverage_min_hold_bars": [336, 480, 672],
            "intent_time_leverage_add_deadband_pct": [0.10, 0.15, 0.25],
            "intent_time_leverage_reduce_deadband_pct": [0.80, 1.00, 1.25],
            "intent_time_leverage_dip_accumulation_enabled": [1.0],
            "intent_time_leverage_dip_min_adverse_atr": [0.75, 1.25],
            "intent_time_leverage_dip_max_adverse_atr": [2.5, 3.0],
            "intent_time_leverage_dip_add_pct": [0.10, 0.15],
            "intent_time_leverage_dip_max_leverage": [2.5, 3.0],
            "intent_flat_reduce_step_pct": [0.15, 0.25, 0.35],
            "intent_flat_step_cadence_bars": [12, 24],
            "intent_same_direction_reduce_confirmation_bars": [8, 12],
            "range_time_decay_bars": [96, 168],
            "preserve_capital_time_decay_bars": [24, 48],
            "time_decay_long_loss_threshold": [-0.08, -0.12],
            "time_decay_short_loss_threshold": [-0.06, -0.08],
            "target_delta_deadband_pct": [0.0],
            "target_reduce_deadband_pct": [0.0],
            "weak_signal_no_flip_enabled": [0.0],
            "tiny_reverse_reduce_only_enabled": [0.0],
            "profit_protect_reduce_enabled": [0.0],
        }
    )
    return grid


def paulwei_dual_phase_fee_repair_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    """Train-only dual-phase search anchored to the v102 near-miss fee profile."""

    grid = paulwei_dual_phase_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "target_vol_4h": [0.45, 0.50],
            "target_vol_5m": [0.60, 0.65, 0.70],
            "max_leverage": [3.0],
            "direction_signal_threshold": [0.40, 0.45],
            "intent_add_signal_threshold": [0.35, 0.40],
            "intent_entry_confirmation_bars": [4, 6],
            "intent_flip_confirmation_bars": [24, 36],
            "intent_flat_confirmation_bars": [16, 24],
            "intent_range_entry_confirmation_bars": [12],
            "intent_range_reentry_cooldown_bars": [48],
            "intent_bearish_override_confirmation_bars": [8, 12],
            "intent_time_leverage_min_hold_bars": [480],
            "intent_time_leverage_add_deadband_pct": [0.15],
            "intent_time_leverage_reduce_deadband_pct": [1.00],
            "intent_time_leverage_dip_accumulation_enabled": [1.0],
            "intent_time_leverage_dip_min_adverse_atr": [1.25],
            "intent_time_leverage_dip_max_adverse_atr": [3.0],
            "intent_time_leverage_dip_add_pct": [0.10],
            "intent_time_leverage_dip_max_leverage": [3.0],
            "intent_flat_reduce_step_pct": [0.25],
            "intent_flat_step_cadence_bars": [24],
            "intent_same_direction_reduce_confirmation_bars": [12],
            "range_time_decay_bars": [168],
            "preserve_capital_time_decay_bars": [48],
            "time_decay_long_loss_threshold": [-0.08, -0.12],
            "time_decay_short_loss_threshold": [-0.06, -0.08],
        }
    )
    return grid


def paulwei_dual_phase_spot_context_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    """Train-only dual-phase search biased toward spot-era trend holding context."""

    grid = paulwei_dual_phase_fee_repair_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "target_vol_4h": [0.50, 0.65, 0.80, 0.95],
            "target_vol_5m": [0.65, 0.70, 0.75, 0.80],
            "max_leverage": [3.0],
            "direction_signal_threshold": [0.25, 0.30, 0.35, 0.40],
            "intent_add_signal_threshold": [0.25, 0.30, 0.35, 0.40],
            "intent_entry_confirmation_bars": [4, 6, 8],
            "intent_flip_confirmation_bars": [36, 48, 72, 96, 120],
            "intent_flat_confirmation_bars": [24, 36, 48],
            "intent_time_leverage_min_hold_bars": [672, 960, 1344],
            "intent_time_leverage_add_deadband_pct": [0.10, 0.15],
            "intent_time_leverage_reduce_deadband_pct": [1.00, 1.25, 1.50, 1.75],
            "intent_time_leverage_long_bias_confidence_floor": [0.55, 0.60, 0.65],
            "intent_time_leverage_long_bias_transition_ceiling": [0.35, 0.45, 0.55],
            "intent_time_leverage_dip_min_adverse_atr": [0.75, 1.00, 1.25],
            "intent_time_leverage_dip_add_pct": [0.10, 0.15, 0.20],
            "intent_time_leverage_dip_max_leverage": [3.0],
            "range_time_decay_bars": [240, 336, 480],
            "preserve_capital_time_decay_bars": [48, 72, 96],
            "time_decay_long_loss_threshold": [-0.12, -0.16],
            "time_decay_short_loss_threshold": [-0.08, -0.10],
        }
    )
    return grid


def paulwei_dual_phase_spot_context_relaxed_fee_param_grid(
    *,
    include_liquidation_toggles: bool = False,
) -> dict[str, list[float]]:
    """Research-only upside probe that relaxes train fee blockers without changing promotion truth."""

    grid = paulwei_dual_phase_spot_context_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    grid.update(
        {
            "research_relaxed_fee_probe_enabled": [1.0],
            "target_vol_4h": [0.65, 0.80, 0.95, 1.10, 1.25],
            "target_vol_5m": [0.70, 0.80, 0.90, 1.00],
            "max_leverage": [3.0, 3.5, 4.0],
            "direction_signal_threshold": [0.20, 0.25, 0.30, 0.35],
            "intent_add_signal_threshold": [0.20, 0.25, 0.30, 0.35],
            "intent_entry_confirmation_bars": [2, 4, 6],
            "intent_flip_confirmation_bars": [36, 48, 72, 96, 120, 168],
            "intent_flat_confirmation_bars": [24, 36, 48, 72],
            "intent_bearish_override_confirmation_bars": [4, 8, 12, 16, 24],
            "intent_time_leverage_min_hold_bars": [672, 960, 1344, 2016],
            "intent_time_leverage_reduce_deadband_pct": [1.00, 1.25, 1.50, 1.75, 2.00],
            "intent_time_leverage_long_bias_confidence_floor": [0.50, 0.55, 0.60, 0.65],
            "intent_time_leverage_long_bias_transition_ceiling": [0.35, 0.45, 0.55, 0.65],
            "intent_time_leverage_long_bias_block_short": [0.0, 1.0],
            "intent_time_leverage_dip_min_adverse_atr": [0.50, 0.75, 1.00, 1.25],
            "intent_time_leverage_dip_add_pct": [0.10, 0.15, 0.20, 0.25],
            "intent_time_leverage_dip_max_leverage": [3.0, 3.5, 4.0],
            "multi_momentum_range_profit_run_enabled": [0.0, 1.0],
            "multi_momentum_range_profit_run_threshold": [0.15, 0.20, 0.25, 0.30],
            "multi_momentum_range_profit_run_value": [0.75, 0.85, 1.00],
            "multi_momentum_range_profit_run_confidence": [0.55, 0.65, 0.75],
            "multi_momentum_range_profit_run_max_transition_risk": [0.35, 0.45, 0.55, 0.65],
            "multi_momentum_range_profit_run_min_cycle_position": [0.90, 0.95, 1.00, 1.05],
            "multi_momentum_range_profit_run_block_distorted_volatility": [0.0, 1.0],
            "multi_momentum_range_profit_run_block_unsafe_quality": [1.0],
            "time_decay_long_loss_threshold": [-0.16, -0.20, -0.24],
            "time_decay_short_loss_threshold": [-0.10, -0.12, -0.16],
        }
    )
    return grid


def baseline_anchor_params() -> dict[str, float]:
    return {
        "dd_step_1_long": 0.15,
        "dd_step_1_short": 0.08,
        "vpin_threshold": 0.10,
        "vol_shock_threshold": 0.95,
        "target_vol_4h": 1.10,
        "max_leverage": 6.0,
        "pyramid_initial_pct": 0.30,
        "pyramid_add_pct": 0.30,
        "pyramid_short_initial_pct": 0.20,
        "pyramid_short_add_pct": 0.20,
        "pyramid_spacing_atr": 0.50,
        "exit_unwind_pct": 0.30,
        "dca_max_adverse_atr": 2.5,
        "dca_spacing_atr": 0.75,
        "dca_size_decay": 0.75,
        "flip_min_confidence": 0.50,
    }


def baseline_anchor_seed_trials() -> list[dict[str, float]]:
    baseline = baseline_anchor_params()
    return [
        dict(baseline),
        {
            **baseline,
            "max_leverage": 4.5,
            "target_vol_4h": 0.95,
        },
        {
            **baseline,
            "pyramid_add_pct": 0.30,
            "pyramid_short_add_pct": 0.16,
            "pyramid_spacing_atr": 0.75,
        },
        {
            **baseline,
            "exit_unwind_pct": 0.35,
            "flip_min_confidence": 0.60,
            "dca_spacing_atr": 1.25,
        },
        {
            **baseline,
            "dd_step_1_long": 0.18,
            "dd_step_1_short": 0.10,
            "dca_size_decay": 0.60,
            "pyramid_spacing_atr": 1.00,
        },
        {
            **baseline,
            "max_leverage": 4.5,
            "pyramid_initial_pct": 0.30,
            "pyramid_add_pct": 0.30,
            "pyramid_short_add_pct": 0.10,
            "exit_unwind_pct": 0.40,
            "flip_min_confidence": 0.70,
        },
        {
            **baseline,
            "target_vol_4h": 0.80,
            "max_leverage": 4.5,
            "pyramid_initial_pct": 0.30,
            "pyramid_add_pct": 0.30,
            "pyramid_short_initial_pct": 0.15,
            "pyramid_short_add_pct": 0.10,
            "dca_spacing_atr": 1.75,
            "dca_size_decay": 0.90,
            "flip_min_confidence": 0.70,
        },
        {
            **baseline,
            "target_vol_4h": 0.95,
            "max_leverage": 4.5,
            "pyramid_initial_pct": 0.30,
            "pyramid_add_pct": 0.30,
            "pyramid_short_initial_pct": 0.15,
            "pyramid_short_add_pct": 0.10,
            "pyramid_spacing_atr": 1.00,
            "exit_unwind_pct": 0.35,
            "dca_spacing_atr": 1.25,
            "flip_min_confidence": 0.70,
        },
    ]


def execution_inventory_rethink_seed_trials() -> list[dict[str, float]]:
    baseline = baseline_anchor_params()
    common = {
        **baseline,
        "target_vol_4h": 0.80,
        "max_leverage": 4.5,
        "pyramid_initial_pct": 0.30,
        "pyramid_add_pct": 0.30,
        "pyramid_short_initial_pct": 0.15,
        "pyramid_short_add_pct": 0.10,
        "pyramid_spacing_atr": 1.00,
        "exit_unwind_pct": 0.40,
        "dca_max_adverse_atr": 2.0,
        "dca_spacing_atr": 1.75,
        "dca_size_decay": 0.90,
        "flip_min_confidence": 0.70,
    }
    return [
        {
            **common,
            "hostile_behavior_router_enabled": 1.0,
            "squeeze_flip_flatten_only_enabled": 0.0,
            "squeeze_flip_cooldown_enabled": 0.0,
            "squeeze_conditional_cooldown_enabled": 0.0,
            "squeeze_flip_cooldown_bars": 1,
            "squeeze_conditional_cooldown_drawdown_threshold": 0.04,
            "squeeze_conditional_cooldown_toxic_only": 0.0,
            "squeeze_cooldown_cancel_on_non_squeeze_enabled": 0.0,
        },
        {
            **common,
            "hostile_behavior_router_enabled": 0.0,
            "squeeze_flip_flatten_only_enabled": 0.0,
            "squeeze_flip_cooldown_enabled": 0.0,
            "squeeze_conditional_cooldown_enabled": 1.0,
            "squeeze_flip_cooldown_bars": 1,
            "squeeze_conditional_cooldown_drawdown_threshold": 0.06,
            "squeeze_conditional_cooldown_toxic_only": 0.0,
            "squeeze_cooldown_cancel_on_non_squeeze_enabled": 1.0,
        },
        {
            **common,
            "target_vol_4h": 0.95,
            "pyramid_spacing_atr": 1.25,
            "exit_unwind_pct": 0.35,
            "dca_max_adverse_atr": 2.5,
            "dca_spacing_atr": 1.25,
            "flip_min_confidence": 0.75,
            "hostile_behavior_router_enabled": 1.0,
            "squeeze_flip_flatten_only_enabled": 0.0,
            "squeeze_flip_cooldown_enabled": 0.0,
            "squeeze_conditional_cooldown_enabled": 1.0,
            "squeeze_flip_cooldown_bars": 2,
            "squeeze_conditional_cooldown_drawdown_threshold": 0.04,
            "squeeze_conditional_cooldown_toxic_only": 0.0,
            "squeeze_cooldown_cancel_on_non_squeeze_enabled": 0.0,
        },
    ]


def intent_churn_repair_seed_trials() -> list[dict[str, float]]:
    baseline = baseline_anchor_params()
    common = {
        **baseline,
        "target_vol_4h": 0.80,
        "max_leverage": 4.5,
        "pyramid_initial_pct": 0.30,
        "pyramid_add_pct": 0.30,
        "pyramid_short_initial_pct": 0.15,
        "pyramid_short_add_pct": 0.10,
        "pyramid_spacing_atr": 1.00,
        "exit_unwind_pct": 0.40,
        "dca_max_adverse_atr": 2.0,
        "dca_spacing_atr": 1.75,
        "dca_size_decay": 0.90,
        "flip_min_confidence": 0.70,
        "hostile_behavior_router_enabled": 0.0,
        "squeeze_flip_flatten_only_enabled": 0.0,
        "squeeze_flip_cooldown_enabled": 0.0,
        "squeeze_conditional_cooldown_enabled": 0.0,
        "squeeze_flip_cooldown_bars": 1,
        "squeeze_conditional_cooldown_drawdown_threshold": 0.06,
        "squeeze_conditional_cooldown_toxic_only": 0.0,
        "squeeze_cooldown_cancel_on_non_squeeze_enabled": 0.0,
        "liquidation_data_enabled": 0.0,
        "state_liquidation_overlay_enabled": 0.0,
        "liq_risk_guard_enabled": 0.0,
    }
    return [
        {
            **common,
            "range_time_decay_bars": 8,
            "preserve_capital_time_decay_bars": 2,
            "trade_directional_confidence_floor": 0.60,
            "bearish_override_confidence_floor": 0.60,
            "exit_transition_risk": 0.85,
        },
        {
            **common,
            "range_time_decay_bars": 5,
            "preserve_capital_time_decay_bars": 3,
            "trade_directional_confidence_floor": 0.55,
            "bearish_override_confidence_floor": 0.55,
            "exit_transition_risk": 0.90,
        },
        {
            **common,
            "target_vol_4h": 0.95,
            "pyramid_spacing_atr": 1.25,
            "dca_spacing_atr": 1.25,
            "flip_min_confidence": 0.75,
            "range_time_decay_bars": 12,
            "preserve_capital_time_decay_bars": 3,
            "trade_directional_confidence_floor": 0.65,
            "bearish_override_confidence_floor": 0.65,
            "exit_transition_risk": 0.90,
        },
    ]


def intent_churn_ablation_seed_trials() -> list[dict[str, float]]:
    base = {
        **baseline_anchor_params(),
        "dd_step_1_long": 0.18,
        "dd_step_1_short": 0.10,
        "vol_shock_threshold": 1.00,
        "target_vol_4h": 0.80,
        "max_leverage": 3.5,
        "pyramid_short_initial_pct": 0.15,
        "pyramid_short_add_pct": 0.12,
        "pyramid_spacing_atr": 1.00,
        "exit_unwind_pct": 0.35,
        "dca_max_adverse_atr": 2.5,
        "dca_spacing_atr": 1.75,
        "dca_size_decay": 0.90,
        "flip_min_confidence": 0.70,
        "range_time_decay_bars": 3,
        "preserve_capital_time_decay_bars": 1,
        "range_trailing_stop_atr_mult": 2.5,
        "preserve_capital_trailing_stop_atr_mult": 1.5,
        "trade_directional_confidence_floor": 0.50,
        "bearish_override_confidence_floor": 0.50,
        "exit_transition_risk": 0.80,
        "direction_signal_threshold": 0.15,
        "intent_add_signal_threshold": 0.0,
        "intent_add_confirmation_bars": 1,
        "min_bars_before_trail": 1,
        "min_hours_before_trail": 0.0,
        "intent_entry_confirmation_bars": 1,
        "intent_flip_confirmation_bars": 1,
        "intent_range_entry_confirmation_bars": 0,
        "intent_range_reentry_cooldown_bars": 0,
        "intent_bearish_override_confirmation_bars": 0,
        "intent_bearish_override_chop_guard_enabled": 0.0,
        "intent_bearish_override_chop_max_confidence": 0.58,
        "intent_bearish_override_chop_min_transition_risk": 0.40,
        "intent_unconfirmed_flip_reduce_only": 0.0,
        "intent_time_leverage_hold_enabled": 0.0,
        "intent_time_leverage_min_hold_bars": 0,
        "intent_time_leverage_flip_signal_threshold": 0.55,
        "intent_time_leverage_confidence_floor": 0.55,
        "intent_time_leverage_transition_risk_floor": 0.35,
        "intent_time_leverage_trailing_stop_atr_mult": 0.0,
        "intent_time_leverage_trailing_exit_hold_enabled": 0.0,
        "intent_time_leverage_trailing_exit_max_adverse_atr": 2.5,
        "intent_time_leverage_size_hysteresis_enabled": 0.0,
        "intent_time_leverage_add_deadband_pct": 0.0,
        "intent_time_leverage_reduce_deadband_pct": 0.0,
        "intent_time_leverage_long_bias_enabled": 0.0,
        "intent_time_leverage_long_bias_confidence_floor": 0.65,
        "intent_time_leverage_long_bias_transition_ceiling": 0.45,
        "intent_time_leverage_long_bias_block_short": 1.0,
        "intent_time_leverage_long_bias_hold_reductions": 1.0,
        "intent_time_leverage_dip_accumulation_enabled": 0.0,
        "intent_time_leverage_dip_min_adverse_atr": 0.75,
        "intent_time_leverage_dip_max_adverse_atr": 2.5,
        "intent_time_leverage_dip_add_pct": 0.15,
        "intent_time_leverage_dip_max_leverage": 3.0,
        "hostile_behavior_router_enabled": 0.0,
        "squeeze_flip_flatten_only_enabled": 0.0,
        "squeeze_flip_cooldown_enabled": 0.0,
        "squeeze_conditional_cooldown_enabled": 0.0,
        "squeeze_flip_cooldown_bars": 1,
        "squeeze_conditional_cooldown_drawdown_threshold": 0.06,
        "squeeze_conditional_cooldown_toxic_only": 0.0,
        "squeeze_cooldown_cancel_on_non_squeeze_enabled": 0.0,
        "liquidation_data_enabled": 0.0,
        "state_liquidation_overlay_enabled": 0.0,
        "liq_risk_guard_enabled": 0.0,
    }
    fee_shape = {
        **base,
        "range_time_decay_bars": 5,
        "preserve_capital_time_decay_bars": 2,
        "trade_directional_confidence_floor": 0.55,
        "bearish_override_confidence_floor": 0.55,
        "exit_transition_risk": 0.85,
    }
    mature_low_switch = {
        **fee_shape,
        "target_vol_4h": 0.50,
        "max_leverage": 3.0,
        "pyramid_short_add_pct": 0.10,
        "range_time_decay_bars": 8,
        "preserve_capital_time_decay_bars": 3,
        "range_trailing_stop_atr_mult": 3.0,
        "preserve_capital_trailing_stop_atr_mult": 2.0,
        "trade_directional_confidence_floor": 0.60,
        "bearish_override_confidence_floor": 0.60,
        "exit_transition_risk": 0.80,
        "direction_signal_threshold": 0.45,
        "intent_add_signal_threshold": 0.50,
        "intent_add_confirmation_bars": 1,
        "min_bars_before_trail": 1,
        "min_hours_before_trail": 0.0,
        "intent_entry_confirmation_bars": 4,
        "intent_flip_confirmation_bars": 6,
        "intent_flat_confirmation_bars": 8,
        "intent_unconfirmed_flip_reduce_only": 1.0,
        "intent_time_leverage_hold_enabled": 1.0,
        "intent_time_leverage_min_hold_bars": 168,
        "intent_time_leverage_flip_signal_threshold": 0.75,
        "intent_time_leverage_confidence_floor": 0.70,
        "intent_time_leverage_transition_risk_floor": 0.45,
        "intent_time_leverage_trailing_stop_atr_mult": 0.0,
        "intent_time_leverage_size_hysteresis_enabled": 1.0,
        "intent_time_leverage_add_deadband_pct": 0.25,
        "intent_time_leverage_reduce_deadband_pct": 0.80,
        "intent_time_leverage_long_bias_enabled": 1.0,
        "intent_time_leverage_long_bias_confidence_floor": 0.70,
        "intent_time_leverage_long_bias_transition_ceiling": 0.35,
        "intent_time_leverage_dip_accumulation_enabled": 1.0,
        "intent_time_leverage_dip_min_adverse_atr": 1.25,
        "intent_time_leverage_dip_max_adverse_atr": 3.0,
        "intent_time_leverage_dip_add_pct": 0.15,
        "intent_time_leverage_dip_max_leverage": 3.0,
    }
    fee_efficient_high_return = {
        **fee_shape,
        "target_vol_4h": 0.65,
        "max_leverage": 3.0,
        "direction_signal_threshold": 0.30,
        "intent_add_signal_threshold": 0.45,
        "intent_add_confirmation_bars": 1,
        "intent_entry_confirmation_bars": 3,
        "intent_flip_confirmation_bars": 5,
        "intent_flat_confirmation_bars": 4,
        "intent_unconfirmed_flip_reduce_only": 1.0,
        "intent_time_leverage_hold_enabled": 1.0,
        "intent_time_leverage_min_hold_bars": 120,
        "intent_time_leverage_flip_signal_threshold": 0.65,
        "intent_time_leverage_confidence_floor": 0.65,
        "intent_time_leverage_transition_risk_floor": 0.35,
        "intent_time_leverage_size_hysteresis_enabled": 1.0,
        "intent_time_leverage_add_deadband_pct": 0.15,
        "intent_time_leverage_reduce_deadband_pct": 0.40,
        "intent_time_leverage_long_bias_enabled": 1.0,
        "intent_time_leverage_long_bias_confidence_floor": 0.65,
        "intent_time_leverage_long_bias_transition_ceiling": 0.35,
        "intent_time_leverage_dip_accumulation_enabled": 1.0,
        "intent_time_leverage_dip_min_adverse_atr": 0.75,
        "intent_time_leverage_dip_max_adverse_atr": 2.5,
        "intent_time_leverage_dip_add_pct": 0.10,
        "intent_time_leverage_dip_max_leverage": 3.0,
    }
    fee_efficient_slow_flip = {
        **fee_efficient_high_return,
        "intent_flip_confirmation_bars": 8,
        "intent_time_leverage_min_hold_bars": 240,
        "intent_time_leverage_flip_signal_threshold": 0.75,
        "intent_time_leverage_confidence_floor": 0.70,
    }
    fee_efficient_wide_hysteresis = {
        **fee_efficient_slow_flip,
        "direction_signal_threshold": 0.35,
        "intent_time_leverage_add_deadband_pct": 0.25,
        "intent_time_leverage_reduce_deadband_pct": 0.80,
    }
    slow_exit_low_switch = {
        **mature_low_switch,
        "range_time_decay_bars": 12,
        "preserve_capital_time_decay_bars": 4,
        "range_trailing_stop_atr_mult": 3.5,
        "preserve_capital_trailing_stop_atr_mult": 2.0,
        "exit_transition_risk": 0.90,
        "intent_flat_confirmation_bars": 8,
    }
    paulwei_time_leverage_low_turnover = {
        **mature_low_switch,
        "target_vol_4h": 0.40,
        "max_leverage": 2.5,
        "range_time_decay_bars": 16,
        "preserve_capital_time_decay_bars": 6,
        "range_trailing_stop_atr_mult": 4.5,
        "preserve_capital_trailing_stop_atr_mult": 3.0,
        "exit_transition_risk": 0.90,
        "intent_add_confirmation_bars": 2,
        "min_bars_before_trail": 1,
        "min_hours_before_trail": 20.0,
        "intent_flip_confirmation_bars": 8,
        "intent_flat_confirmation_bars": 16,
        "intent_range_entry_confirmation_bars": 6,
        "intent_range_reentry_cooldown_bars": 24,
        "intent_bearish_override_confirmation_bars": 4,
        "intent_bearish_override_chop_guard_enabled": 0.0,
        "intent_bearish_override_chop_max_confidence": 0.50,
        "intent_bearish_override_chop_min_transition_risk": 0.55,
        "intent_unconfirmed_flip_reduce_only": 0.0,
        "intent_time_leverage_min_hold_bars": 240,
        "intent_time_leverage_add_deadband_pct": 0.40,
        "intent_time_leverage_reduce_deadband_pct": 1.00,
        "intent_time_leverage_dip_add_pct": 0.10,
    }
    paulwei_time_leverage_low_exposure = {
        **paulwei_time_leverage_low_turnover,
        "target_vol_4h": 0.35,
        "intent_flip_confirmation_bars": 12,
        "intent_flat_confirmation_bars": 24,
        "intent_range_entry_confirmation_bars": 8,
        "intent_range_reentry_cooldown_bars": 48,
        "intent_bearish_override_confirmation_bars": 8,
        "intent_bearish_override_chop_guard_enabled": 0.0,
        "intent_bearish_override_chop_max_confidence": 0.40,
        "intent_bearish_override_chop_min_transition_risk": 0.65,
        "intent_time_leverage_min_hold_bars": 336,
        "intent_time_leverage_add_deadband_pct": 0.60,
        "intent_time_leverage_reduce_deadband_pct": 1.25,
    }
    paulwei_time_leverage_fee_scaled = {
        **paulwei_time_leverage_low_exposure,
        "target_vol_4h": 0.30,
        "max_leverage": 2.0,
        "intent_time_leverage_dip_max_leverage": 2.0,
    }
    paulwei_time_leverage_signal_quality = {
        **paulwei_time_leverage_low_turnover,
        "direction_signal_threshold": 0.55,
        "intent_add_signal_threshold": 0.60,
    }
    return [
        paulwei_time_leverage_low_turnover,
        paulwei_time_leverage_low_exposure,
        paulwei_time_leverage_fee_scaled,
        paulwei_time_leverage_signal_quality,
        mature_low_switch,
        slow_exit_low_switch,
        {
            **mature_low_switch,
            "intent_add_signal_threshold": 0.55,
            "intent_add_confirmation_bars": 2,
            "intent_flat_confirmation_bars": 12,
        },
        fee_efficient_high_return,
        fee_efficient_slow_flip,
        fee_efficient_wide_hysteresis,
        {
            **mature_low_switch,
            "max_leverage": 2.5,
        },
        {
            **mature_low_switch,
            "target_vol_4h": 0.45,
        },
        {
            **mature_low_switch,
            "target_vol_4h": 0.65,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.55,
            "intent_flip_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 240,
            "exit_transition_risk": 0.90,
        },
        {
            **fee_shape,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.45,
            "intent_entry_confirmation_bars": 2,
            "intent_flip_confirmation_bars": 3,
            "intent_time_leverage_hold_enabled": 1.0,
            "intent_time_leverage_min_hold_bars": 24,
            "intent_time_leverage_flip_signal_threshold": 0.65,
            "intent_time_leverage_confidence_floor": 0.65,
            "intent_time_leverage_size_hysteresis_enabled": 1.0,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 0.30,
            "intent_time_leverage_long_bias_enabled": 1.0,
            "intent_time_leverage_long_bias_confidence_floor": 0.65,
            "intent_time_leverage_long_bias_transition_ceiling": 0.45,
            "intent_time_leverage_dip_accumulation_enabled": 0.0,
        },
        {
            **fee_shape,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.45,
            "intent_flip_confirmation_bars": 3,
            "intent_time_leverage_hold_enabled": 1.0,
            "intent_time_leverage_min_hold_bars": 12,
            "intent_time_leverage_flip_signal_threshold": 0.55,
            "intent_time_leverage_size_hysteresis_enabled": 1.0,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 0.20,
            "intent_time_leverage_long_bias_enabled": 1.0,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.45,
            "intent_time_leverage_dip_accumulation_enabled": 1.0,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 2.5,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
        },
        {
            **fee_shape,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.45,
            "intent_entry_confirmation_bars": 2,
            "intent_flip_confirmation_bars": 3,
            "intent_time_leverage_hold_enabled": 1.0,
            "intent_time_leverage_min_hold_bars": 24,
            "intent_time_leverage_flip_signal_threshold": 0.65,
            "intent_time_leverage_confidence_floor": 0.65,
            "intent_time_leverage_size_hysteresis_enabled": 1.0,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 0.30,
            "intent_time_leverage_long_bias_enabled": 1.0,
            "intent_time_leverage_long_bias_confidence_floor": 0.65,
            "intent_time_leverage_long_bias_transition_ceiling": 0.35,
            "intent_time_leverage_dip_accumulation_enabled": 1.0,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_max_adverse_atr": 2.5,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 2.5,
        },
        {
            **fee_shape,
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.40,
            "intent_flip_confirmation_bars": 3,
            "intent_time_leverage_hold_enabled": 1.0,
            "intent_time_leverage_min_hold_bars": 48,
            "intent_time_leverage_flip_signal_threshold": 0.65,
            "intent_time_leverage_confidence_floor": 0.65,
            "intent_time_leverage_transition_risk_floor": 0.45,
            "intent_time_leverage_size_hysteresis_enabled": 1.0,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 0.30,
            "intent_time_leverage_long_bias_enabled": 1.0,
            "intent_time_leverage_long_bias_confidence_floor": 0.70,
            "intent_time_leverage_long_bias_transition_ceiling": 0.35,
            "intent_time_leverage_dip_accumulation_enabled": 1.0,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 3.0,
        },
        {**fee_shape, "direction_signal_threshold": 0.25},
        {**fee_shape, "direction_signal_threshold": 0.25, "intent_add_signal_threshold": 0.30},
        {**fee_shape, "direction_signal_threshold": 0.25, "intent_add_signal_threshold": 0.35},
        {**fee_shape, "direction_signal_threshold": 0.25, "intent_add_signal_threshold": 0.40},
        {**fee_shape, "direction_signal_threshold": 0.25, "intent_add_signal_threshold": 0.45},
        {
            **fee_shape,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.45,
            "intent_flip_confirmation_bars": 2,
        },
        {
            **fee_shape,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.45,
            "intent_flip_confirmation_bars": 3,
        },
        {
            **fee_shape,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.45,
            "intent_entry_confirmation_bars": 2,
        },
        {
            **fee_shape,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.45,
            "intent_entry_confirmation_bars": 2,
            "intent_flip_confirmation_bars": 2,
        },
        {**fee_shape, "target_vol_4h": 0.65, "max_leverage": 3.0, "direction_signal_threshold": 0.25},
        {
            **fee_shape,
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.30,
        },
        {
            **fee_shape,
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.35,
        },
        {
            **fee_shape,
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.40,
        },
        {
            **fee_shape,
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.35,
        },
        {
            **fee_shape,
            "target_vol_4h": 0.50,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.35,
        },
    ]


def ralph_protective_reduce_seed_trials() -> list[dict[str, float]]:
    """Deterministic Ralph loop seed set for proving reduce-first behavior before dev review."""

    common_reduce_first = {
        "intent_unconfirmed_flip_reduce_only": 1.0,
        "flip_min_confidence": 0.80,
        "flip_flatten_only_enabled": 1.0,
        "flip_reverse_open_scale": 1.00,
        "loss_flip_reverse_open_confidence_floor": 1.00,
        "target_delta_deadband_qty": 0.0,
        "target_reduce_deadband_qty": 0.0,
        "target_reduce_deadband_pct": 0.0,
        "weak_signal_no_flip_enabled": 1.0,
        "tiny_reverse_reduce_only_enabled": 1.0,
        "tiny_reverse_deadband_qty": 0.0,
        "range_entry_block_enabled": 1.0,
        "degraded_entry_block_enabled": 1.0,
        "loss_dca_range_block_enabled": 1.0,
        "loss_dca_degraded_block_enabled": 1.0,
        "profit_protect_reduce_enabled": 1.0,
    }
    reduce_first_variants = [
        {
            "target_vol_4h": 0.35,
            "max_leverage": 2.5,
            "direction_signal_threshold": 0.50,
            "intent_add_signal_threshold": 0.60,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 4,
            "intent_time_leverage_min_hold_bars": 480,
            "intent_time_leverage_reduce_deadband_pct": 0.40,
            "target_delta_deadband_pct": 0.06,
            "min_hold_bars_before_flip": 12,
            "entry_cooldown_bars": 4,
            "weak_signal_no_flip_confidence": 0.85,
            "tiny_reverse_deadband_pct": 0.20,
            "entry_confidence_floor": 0.90,
            "reverse_open_confidence_floor": 1.00,
            "degraded_entry_confidence_floor": 0.90,
            "loss_dca_confidence_floor": 0.90,
            "profit_protect_min_move_atr": 0.75,
            "profit_protect_unwind_pct": 0.30,
            "profit_protect_confidence_ceiling": 0.85,
        },
        {
            "target_vol_4h": 0.40,
            "max_leverage": 2.5,
            "direction_signal_threshold": 0.45,
            "intent_add_signal_threshold": 0.55,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 16,
            "intent_flat_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 336,
            "intent_time_leverage_reduce_deadband_pct": 0.40,
            "target_delta_deadband_pct": 0.06,
            "min_hold_bars_before_flip": 8,
            "entry_cooldown_bars": 2,
            "weak_signal_no_flip_confidence": 0.85,
            "tiny_reverse_deadband_pct": 0.20,
            "entry_confidence_floor": 0.85,
            "reverse_open_confidence_floor": 1.00,
            "degraded_entry_confidence_floor": 0.85,
            "loss_dca_confidence_floor": 0.85,
            "profit_protect_min_move_atr": 1.00,
            "profit_protect_unwind_pct": 0.25,
            "profit_protect_confidence_ceiling": 0.80,
        },
        {
            "target_vol_4h": 0.50,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.45,
            "intent_add_signal_threshold": 0.55,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 12,
            "intent_flat_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 240,
            "intent_time_leverage_reduce_deadband_pct": 0.80,
            "target_delta_deadband_pct": 0.04,
            "min_hold_bars_before_flip": 8,
            "entry_cooldown_bars": 2,
            "weak_signal_no_flip_confidence": 0.75,
            "tiny_reverse_deadband_pct": 0.10,
            "entry_confidence_floor": 0.85,
            "reverse_open_confidence_floor": 0.95,
            "degraded_entry_confidence_floor": 0.85,
            "loss_dca_confidence_floor": 0.80,
            "profit_protect_min_move_atr": 1.00,
            "profit_protect_unwind_pct": 0.25,
            "profit_protect_confidence_ceiling": 0.75,
        },
        {
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.50,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 12,
            "intent_flat_confirmation_bars": 12,
            "intent_time_leverage_min_hold_bars": 240,
            "intent_time_leverage_reduce_deadband_pct": 0.80,
            "target_delta_deadband_pct": 0.04,
            "min_hold_bars_before_flip": 4,
            "entry_cooldown_bars": 1,
            "weak_signal_no_flip_confidence": 0.75,
            "tiny_reverse_deadband_pct": 0.10,
            "entry_confidence_floor": 0.80,
            "reverse_open_confidence_floor": 0.95,
            "degraded_entry_confidence_floor": 0.85,
            "loss_dca_confidence_floor": 0.80,
            "profit_protect_min_move_atr": 1.25,
            "profit_protect_unwind_pct": 0.20,
            "profit_protect_confidence_ceiling": 0.75,
        },
    ]
    seeds: list[dict[str, float]] = []
    for index, base_seed in enumerate(intent_churn_ablation_seed_trials()[:12]):
        seeds.append(
            {
                **base_seed,
                **common_reduce_first,
                **reduce_first_variants[index % len(reduce_first_variants)],
            }
        )
    return seeds


def ralph_protective_reduce_fee_repair_seed_trials() -> list[dict[str, float]]:
    """Second Ralph reduce-first seed set that tries to preserve behavior while reducing fee drag."""

    fee_repair_variants = [
        {
            "target_vol_4h": 0.35,
            "max_leverage": 2.0,
            "exit_unwind_pct": 0.55,
            "intent_add_signal_threshold": 0.65,
            "intent_add_confirmation_bars": 4,
            "intent_entry_confirmation_bars": 8,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 2,
            "intent_time_leverage_reduce_deadband_pct": 0.20,
            "target_delta_deadband_pct": 0.10,
            "entry_cooldown_bars": 8,
            "weak_signal_no_flip_confidence": 0.90,
            "profit_protect_min_move_atr": 2.25,
            "profit_protect_unwind_pct": 0.50,
            "profit_protect_confidence_ceiling": 0.65,
        },
        {
            "target_vol_4h": 0.40,
            "max_leverage": 2.5,
            "exit_unwind_pct": 0.55,
            "intent_add_signal_threshold": 0.60,
            "intent_add_confirmation_bars": 4,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 4,
            "intent_time_leverage_reduce_deadband_pct": 0.20,
            "target_delta_deadband_pct": 0.10,
            "entry_cooldown_bars": 4,
            "weak_signal_no_flip_confidence": 0.85,
            "profit_protect_min_move_atr": 1.75,
            "profit_protect_unwind_pct": 0.50,
            "profit_protect_confidence_ceiling": 0.75,
        },
        {
            "target_vol_4h": 0.50,
            "max_leverage": 2.5,
            "exit_unwind_pct": 0.45,
            "intent_add_signal_threshold": 0.60,
            "intent_add_confirmation_bars": 2,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 16,
            "intent_flat_confirmation_bars": 4,
            "intent_time_leverage_reduce_deadband_pct": 0.40,
            "target_delta_deadband_pct": 0.08,
            "entry_cooldown_bars": 4,
            "weak_signal_no_flip_confidence": 0.85,
            "profit_protect_min_move_atr": 1.75,
            "profit_protect_unwind_pct": 0.35,
            "profit_protect_confidence_ceiling": 0.75,
        },
        {
            "target_vol_4h": 0.40,
            "max_leverage": 2.0,
            "exit_unwind_pct": 0.55,
            "intent_add_signal_threshold": 0.65,
            "intent_add_confirmation_bars": 2,
            "intent_entry_confirmation_bars": 8,
            "intent_flip_confirmation_bars": 16,
            "intent_flat_confirmation_bars": 8,
            "intent_time_leverage_reduce_deadband_pct": 0.40,
            "target_delta_deadband_pct": 0.08,
            "entry_cooldown_bars": 8,
            "weak_signal_no_flip_confidence": 0.90,
            "profit_protect_min_move_atr": 1.25,
            "profit_protect_unwind_pct": 0.35,
            "profit_protect_confidence_ceiling": 0.65,
        },
    ]
    seeds: list[dict[str, float]] = []
    for index, base_seed in enumerate(ralph_protective_reduce_seed_trials()):
        seeds.append(
            {
                **base_seed,
                **fee_repair_variants[index % len(fee_repair_variants)],
            }
        )
    return seeds


def ralph_execution_fee_cooldown_seed_trials() -> list[dict[str, float]]:
    """Ralph seed set for testing cooldown-gated protective reductions without suppressing all trades."""

    cooldown_variants = [
        {
            "target_vol_4h": 0.50,
            "max_leverage": 3.0,
            "intent_add_signal_threshold": 0.55,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 12,
            "intent_flat_confirmation_bars": 4,
            "target_delta_deadband_pct": 0.04,
            "entry_cooldown_bars": 2,
            "profit_protect_min_move_atr": 0.75,
            "profit_protect_unwind_pct": 0.35,
            "profit_protect_confidence_ceiling": 0.80,
            "profit_protect_cooldown_bars": 6,
        },
        {
            "target_vol_4h": 0.50,
            "max_leverage": 3.0,
            "intent_add_signal_threshold": 0.55,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 12,
            "intent_flat_confirmation_bars": 8,
            "target_delta_deadband_pct": 0.06,
            "entry_cooldown_bars": 2,
            "profit_protect_min_move_atr": 1.00,
            "profit_protect_unwind_pct": 0.25,
            "profit_protect_confidence_ceiling": 0.75,
            "profit_protect_cooldown_bars": 12,
        },
        {
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "intent_add_signal_threshold": 0.50,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 12,
            "intent_flat_confirmation_bars": 8,
            "target_delta_deadband_pct": 0.04,
            "entry_cooldown_bars": 2,
            "profit_protect_min_move_atr": 1.00,
            "profit_protect_unwind_pct": 0.35,
            "profit_protect_confidence_ceiling": 0.80,
            "profit_protect_cooldown_bars": 12,
        },
        {
            "target_vol_4h": 0.40,
            "max_leverage": 2.5,
            "intent_add_signal_threshold": 0.60,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 16,
            "intent_flat_confirmation_bars": 4,
            "target_delta_deadband_pct": 0.08,
            "entry_cooldown_bars": 4,
            "profit_protect_min_move_atr": 1.25,
            "profit_protect_unwind_pct": 0.50,
            "profit_protect_confidence_ceiling": 0.75,
            "profit_protect_cooldown_bars": 24,
        },
    ]
    seeds: list[dict[str, float]] = []
    for index, base_seed in enumerate(ralph_protective_reduce_seed_trials()):
        seeds.append(
            {
                **base_seed,
                **cooldown_variants[index % len(cooldown_variants)],
            }
        )
    return seeds


def ralph_intent_reduce_cadence_seed_trials() -> list[dict[str, float]]:
    """Ralph seed set for confirming and aggregating strategy-level same-direction reductions."""

    cadence_variants = [
        {
            "target_vol_4h": 0.50,
            "max_leverage": 3.0,
            "intent_add_signal_threshold": 0.55,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 12,
            "intent_flat_confirmation_bars": 4,
            "intent_flat_reduce_step_pct": 0.0,
            "intent_flat_step_cadence_bars": 0,
            "intent_same_direction_reduce_confirmation_bars": 4,
            "intent_time_leverage_reduce_deadband_pct": 0.40,
            "target_delta_deadband_pct": 0.04,
            "entry_cooldown_bars": 2,
            "profit_protect_min_move_atr": 0.75,
            "profit_protect_unwind_pct": 0.30,
            "profit_protect_confidence_ceiling": 0.80,
            "profit_protect_cooldown_bars": 0,
        },
        {
            "target_vol_4h": 0.50,
            "max_leverage": 3.0,
            "intent_add_signal_threshold": 0.55,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 12,
            "intent_flat_confirmation_bars": 8,
            "intent_flat_reduce_step_pct": 0.15,
            "intent_flat_step_cadence_bars": 8,
            "intent_same_direction_reduce_confirmation_bars": 4,
            "intent_time_leverage_reduce_deadband_pct": 0.80,
            "target_delta_deadband_pct": 0.06,
            "entry_cooldown_bars": 2,
            "profit_protect_min_move_atr": 1.00,
            "profit_protect_unwind_pct": 0.25,
            "profit_protect_confidence_ceiling": 0.75,
            "profit_protect_cooldown_bars": 0,
        },
        {
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "intent_add_signal_threshold": 0.50,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 12,
            "intent_flat_confirmation_bars": 8,
            "intent_flat_reduce_step_pct": 0.25,
            "intent_flat_step_cadence_bars": 12,
            "intent_same_direction_reduce_confirmation_bars": 8,
            "intent_time_leverage_reduce_deadband_pct": 0.80,
            "target_delta_deadband_pct": 0.04,
            "entry_cooldown_bars": 2,
            "profit_protect_min_move_atr": 1.00,
            "profit_protect_unwind_pct": 0.35,
            "profit_protect_confidence_ceiling": 0.80,
            "profit_protect_cooldown_bars": 0,
        },
        {
            "target_vol_4h": 0.40,
            "max_leverage": 2.5,
            "intent_add_signal_threshold": 0.60,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 16,
            "intent_flat_confirmation_bars": 4,
            "intent_flat_reduce_step_pct": 0.35,
            "intent_flat_step_cadence_bars": 24,
            "intent_same_direction_reduce_confirmation_bars": 12,
            "intent_time_leverage_reduce_deadband_pct": 1.00,
            "target_delta_deadband_pct": 0.08,
            "entry_cooldown_bars": 4,
            "profit_protect_min_move_atr": 1.25,
            "profit_protect_unwind_pct": 0.25,
            "profit_protect_confidence_ceiling": 0.75,
            "profit_protect_cooldown_bars": 0,
        },
    ]
    seeds: list[dict[str, float]] = []
    for index, base_seed in enumerate(ralph_protective_reduce_seed_trials()):
        seeds.append(
            {
                **base_seed,
                **cadence_variants[index % len(cadence_variants)],
            }
        )
    return seeds


def paulwei_balanced_tranche_seed_trials() -> list[dict[str, float]]:
    """Deterministic seed set for low-switch intent with staged build and staged unwind."""

    balanced_variants = [
        {
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.35,
            "intent_add_confirmation_bars": 1,
            "intent_entry_confirmation_bars": 3,
            "intent_flip_confirmation_bars": 12,
            "intent_flat_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 240,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 0.60,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 48,
            "preserve_capital_time_decay_bars": 12,
            "time_decay_long_loss_threshold": -0.08,
            "time_decay_short_loss_threshold": -0.04,
            "intent_flat_reduce_step_pct": 0.15,
            "intent_flat_step_cadence_bars": 8,
            "intent_same_direction_reduce_confirmation_bars": 4,
        },
        {
            "target_vol_4h": 0.50,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.40,
            "intent_add_confirmation_bars": 1,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 16,
            "intent_flat_confirmation_bars": 12,
            "intent_time_leverage_min_hold_bars": 336,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 0.80,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 96,
            "preserve_capital_time_decay_bars": 24,
            "time_decay_long_loss_threshold": -0.08,
            "time_decay_short_loss_threshold": -0.06,
            "intent_flat_reduce_step_pct": 0.25,
            "intent_flat_step_cadence_bars": 12,
            "intent_same_direction_reduce_confirmation_bars": 8,
        },
        {
            "target_vol_4h": 0.40,
            "max_leverage": 2.5,
            "direction_signal_threshold": 0.45,
            "intent_add_signal_threshold": 0.45,
            "intent_add_confirmation_bars": 2,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 16,
            "intent_time_leverage_min_hold_bars": 480,
            "intent_time_leverage_add_deadband_pct": 0.25,
            "intent_time_leverage_reduce_deadband_pct": 1.00,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 2.5,
            "range_time_decay_bars": 168,
            "preserve_capital_time_decay_bars": 48,
            "time_decay_long_loss_threshold": -0.12,
            "time_decay_short_loss_threshold": -0.08,
            "intent_flat_reduce_step_pct": 0.35,
            "intent_flat_step_cadence_bars": 24,
            "intent_same_direction_reduce_confirmation_bars": 12,
        },
        {
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.30,
            "intent_add_confirmation_bars": 1,
            "intent_entry_confirmation_bars": 3,
            "intent_flip_confirmation_bars": 16,
            "intent_flat_confirmation_bars": 24,
            "intent_time_leverage_min_hold_bars": 336,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.00,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 96,
            "preserve_capital_time_decay_bars": 24,
            "time_decay_long_loss_threshold": -0.12,
            "time_decay_short_loss_threshold": -0.06,
            "intent_flat_reduce_step_pct": 0.25,
            "intent_flat_step_cadence_bars": 24,
            "intent_same_direction_reduce_confirmation_bars": 8,
        },
    ]
    execution_neutral = {
        "intent_unconfirmed_flip_reduce_only": 0.0,
        "intent_time_leverage_hold_enabled": 1.0,
        "intent_time_leverage_size_hysteresis_enabled": 1.0,
        "intent_time_leverage_dip_accumulation_enabled": 1.0,
        "intent_time_leverage_dip_max_adverse_atr": 3.0,
        "intent_time_leverage_long_bias_enabled": 1.0,
        "intent_time_leverage_long_bias_confidence_floor": 0.65,
        "intent_time_leverage_long_bias_transition_ceiling": 0.35,
        "time_decay_exit_enabled": 1.0,
        "flip_flatten_only_enabled": 0.0,
        "flip_reverse_open_scale": 1.00,
        "loss_flip_reverse_open_confidence_floor": 0.0,
        "target_delta_deadband_qty": 0.0,
        "target_delta_deadband_pct": 0.0,
        "target_reduce_deadband_qty": 0.0,
        "target_reduce_deadband_pct": 0.0,
        "min_hold_bars_before_flip": 0,
        "entry_cooldown_bars": 0,
        "weak_signal_no_flip_enabled": 0.0,
        "tiny_reverse_reduce_only_enabled": 0.0,
        "tiny_reverse_deadband_qty": 0.0,
        "tiny_reverse_deadband_pct": 0.10,
        "entry_confidence_floor": 0.0,
        "reverse_open_confidence_floor": 0.0,
        "range_entry_block_enabled": 0.0,
        "degraded_entry_block_enabled": 0.0,
        "loss_dca_confidence_floor": 0.0,
        "loss_dca_range_block_enabled": 0.0,
        "loss_dca_degraded_block_enabled": 0.0,
        "profit_protect_reduce_enabled": 0.0,
        "profit_protect_min_move_atr": 1.00,
        "profit_protect_unwind_pct": 0.25,
        "profit_protect_confidence_ceiling": 0.75,
        "profit_protect_cooldown_bars": 0,
    }
    seeds: list[dict[str, float]] = []
    for index, base_seed in enumerate(intent_churn_ablation_seed_trials()[:12]):
        seeds.append(
            {
                **base_seed,
                **execution_neutral,
                **balanced_variants[index % len(balanced_variants)],
            }
        )
    return seeds


def paulwei_dual_phase_seed_trials() -> list[dict[str, float]]:
    """Deterministic seed set for PaulWei early-expansion plus mature-control learning."""

    dual_phase_variants = [
        {
            "target_vol_4h": 0.65,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.35,
            "intent_entry_confirmation_bars": 3,
            "intent_flip_confirmation_bars": 16,
            "intent_flat_confirmation_bars": 12,
            "intent_time_leverage_min_hold_bars": 336,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 0.80,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 96,
            "preserve_capital_time_decay_bars": 24,
            "intent_flat_reduce_step_pct": 0.15,
            "intent_flat_step_cadence_bars": 12,
            "intent_same_direction_reduce_confirmation_bars": 8,
        },
        {
            "target_vol_4h": 0.50,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.40,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 16,
            "intent_time_leverage_min_hold_bars": 480,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.00,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 168,
            "preserve_capital_time_decay_bars": 48,
            "intent_flat_reduce_step_pct": 0.25,
            "intent_flat_step_cadence_bars": 24,
            "intent_same_direction_reduce_confirmation_bars": 12,
        },
        {
            "target_vol_4h": 0.80,
            "max_leverage": 2.5,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.30,
            "intent_entry_confirmation_bars": 3,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 24,
            "intent_time_leverage_min_hold_bars": 672,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.25,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 2.5,
            "range_time_decay_bars": 168,
            "preserve_capital_time_decay_bars": 48,
            "intent_flat_reduce_step_pct": 0.35,
            "intent_flat_step_cadence_bars": 24,
            "intent_same_direction_reduce_confirmation_bars": 12,
        },
    ]
    seeds: list[dict[str, float]] = []
    for index, base_seed in enumerate(paulwei_balanced_tranche_seed_trials()):
        seed = {
            **base_seed,
            **dual_phase_variants[index % len(dual_phase_variants)],
            "paulwei_dual_phase_objective_enabled": 1.0,
            "intent_time_leverage_hold_enabled": 1.0,
            "intent_time_leverage_size_hysteresis_enabled": 1.0,
            "intent_time_leverage_dip_accumulation_enabled": 1.0,
            "intent_time_leverage_long_bias_enabled": 1.0,
            "time_decay_exit_enabled": 1.0,
            "flip_flatten_only_enabled": 0.0,
            "flip_reverse_open_scale": 1.00,
            "weak_signal_no_flip_enabled": 0.0,
            "tiny_reverse_reduce_only_enabled": 0.0,
            "profit_protect_reduce_enabled": 0.0,
        }
        seeds.append(seed)
    return seeds


def paulwei_dual_phase_fee_repair_seed_trials() -> list[dict[str, float]]:
    """Deterministic seeds for small v102-anchor fee repairs."""

    repair_variants = [
        {
            "target_vol_4h": 0.45,
            "target_vol_5m": 0.65,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.40,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 16,
        },
        {
            "target_vol_4h": 0.50,
            "target_vol_5m": 0.70,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.40,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 16,
        },
        {
            "target_vol_4h": 0.45,
            "target_vol_5m": 0.60,
            "direction_signal_threshold": 0.45,
            "intent_add_signal_threshold": 0.40,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 16,
        },
        {
            "target_vol_4h": 0.50,
            "target_vol_5m": 0.65,
            "direction_signal_threshold": 0.45,
            "intent_add_signal_threshold": 0.40,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 36,
            "intent_flat_confirmation_bars": 16,
        },
        {
            "target_vol_4h": 0.45,
            "target_vol_5m": 0.65,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.35,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 24,
            "intent_flat_confirmation_bars": 24,
        },
    ]
    shared_repair = {
        "paulwei_dual_phase_objective_enabled": 1.0,
        "target_vol_4h": 0.45,
        "target_vol_5m": 0.65,
        "max_leverage": 3.0,
        "intent_time_leverage_hold_enabled": 1.0,
        "intent_time_leverage_size_hysteresis_enabled": 1.0,
        "intent_time_leverage_dip_accumulation_enabled": 1.0,
        "intent_time_leverage_dip_min_adverse_atr": 1.25,
        "intent_time_leverage_dip_max_adverse_atr": 3.0,
        "intent_time_leverage_dip_add_pct": 0.10,
        "intent_time_leverage_dip_max_leverage": 3.0,
        "intent_time_leverage_long_bias_enabled": 1.0,
        "intent_time_leverage_min_hold_bars": 480,
        "intent_time_leverage_add_deadband_pct": 0.15,
        "intent_time_leverage_reduce_deadband_pct": 1.00,
        "time_decay_exit_enabled": 1.0,
        "range_time_decay_bars": 168,
        "preserve_capital_time_decay_bars": 48,
        "time_decay_long_loss_threshold": -0.08,
        "time_decay_short_loss_threshold": -0.06,
        "intent_flat_step_cadence_bars": 24,
        "intent_flat_reduce_step_pct": 0.25,
        "intent_same_direction_reduce_confirmation_bars": 12,
        "intent_range_entry_confirmation_bars": 12,
        "intent_range_reentry_cooldown_bars": 48,
        "intent_bearish_override_confirmation_bars": 8,
        "flip_flatten_only_enabled": 0.0,
        "flip_reverse_open_scale": 1.00,
        "weak_signal_no_flip_enabled": 0.0,
        "tiny_reverse_reduce_only_enabled": 0.0,
        "profit_protect_reduce_enabled": 0.0,
        "target_delta_deadband_pct": 0.0,
        "target_reduce_deadband_pct": 0.0,
    }
    seeds: list[dict[str, float]] = []
    for index, base_seed in enumerate(paulwei_dual_phase_seed_trials()):
        seeds.append(
            {
                **base_seed,
                **shared_repair,
                **repair_variants[index % len(repair_variants)],
            }
        )
    return seeds


def paulwei_dual_phase_spot_context_seed_trials() -> list[dict[str, float]]:
    """Deterministic seeds for trend-holding spot-context learning."""

    fee_repair_anchor = paulwei_dual_phase_fee_repair_seed_trials()[0]
    shared_context = {
        "paulwei_dual_phase_objective_enabled": 1.0,
        "max_leverage": 3.0,
        "intent_time_leverage_hold_enabled": 1.0,
        "intent_time_leverage_size_hysteresis_enabled": 1.0,
        "intent_time_leverage_dip_accumulation_enabled": 1.0,
        "intent_time_leverage_long_bias_enabled": 1.0,
        "intent_time_leverage_long_bias_block_short": 1.0,
        "intent_time_leverage_long_bias_hold_reductions": 1.0,
        "intent_time_leverage_trailing_stop_atr_mult": 0.0,
        "intent_range_entry_confirmation_bars": 12,
        "intent_range_reentry_cooldown_bars": 48,
        "intent_bearish_override_confirmation_bars": 12,
        "intent_flat_step_cadence_bars": 24,
        "intent_flat_reduce_step_pct": 0.25,
        "intent_same_direction_reduce_confirmation_bars": 12,
        "time_decay_exit_enabled": 1.0,
        "time_decay_long_loss_threshold": -0.12,
        "time_decay_short_loss_threshold": -0.08,
        "flip_flatten_only_enabled": 0.0,
        "flip_reverse_open_scale": 1.0,
        "weak_signal_no_flip_enabled": 0.0,
        "tiny_reverse_reduce_only_enabled": 0.0,
        "profit_protect_reduce_enabled": 0.0,
        "target_delta_deadband_pct": 0.0,
        "target_reduce_deadband_pct": 0.0,
    }
    context_variants = [
        {
            "target_vol_4h": 0.50,
            "target_vol_5m": 0.65,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.40,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 36,
            "intent_flat_confirmation_bars": 24,
            "intent_bearish_override_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 672,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.00,
            "intent_time_leverage_long_bias_confidence_floor": 0.65,
            "intent_time_leverage_long_bias_transition_ceiling": 0.35,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 240,
            "preserve_capital_time_decay_bars": 48,
        },
        {
            "target_vol_4h": 0.50,
            "target_vol_5m": 0.70,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.40,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 36,
            "intent_flat_confirmation_bars": 24,
            "intent_bearish_override_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 672,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.00,
            "intent_time_leverage_long_bias_confidence_floor": 0.65,
            "intent_time_leverage_long_bias_transition_ceiling": 0.35,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 240,
            "preserve_capital_time_decay_bars": 48,
        },
        {
            "target_vol_4h": 0.50,
            "target_vol_5m": 0.65,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.35,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 48,
            "intent_flat_confirmation_bars": 36,
            "intent_bearish_override_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 960,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.25,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.45,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 336,
            "preserve_capital_time_decay_bars": 72,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
        {
            "target_vol_4h": 0.50,
            "target_vol_5m": 0.70,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.30,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 72,
            "intent_flat_confirmation_bars": 36,
            "intent_bearish_override_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 960,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.25,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.45,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 336,
            "preserve_capital_time_decay_bars": 72,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
        {
            "target_vol_4h": 0.50,
            "target_vol_5m": 0.65,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.30,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 72,
            "intent_flat_confirmation_bars": 48,
            "intent_bearish_override_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 1344,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.50,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.55,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 480,
            "preserve_capital_time_decay_bars": 96,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
        {
            "target_vol_4h": 0.95,
            "target_vol_5m": 0.75,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.30,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 72,
            "intent_flat_confirmation_bars": 48,
            "intent_time_leverage_min_hold_bars": 1344,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.50,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.55,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.20,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 480,
            "preserve_capital_time_decay_bars": 96,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
        {
            "target_vol_4h": 0.65,
            "target_vol_5m": 0.70,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.35,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 48,
            "intent_flat_confirmation_bars": 24,
            "intent_time_leverage_min_hold_bars": 672,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.25,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.45,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 240,
            "preserve_capital_time_decay_bars": 48,
        },
        {
            "target_vol_4h": 0.80,
            "target_vol_5m": 0.75,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.30,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 72,
            "intent_flat_confirmation_bars": 36,
            "intent_time_leverage_min_hold_bars": 960,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.50,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.45,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 336,
            "preserve_capital_time_decay_bars": 72,
        },
        {
            "target_vol_4h": 0.65,
            "target_vol_5m": 0.65,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.35,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 48,
            "intent_flat_confirmation_bars": 24,
            "intent_time_leverage_min_hold_bars": 672,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.25,
            "intent_time_leverage_long_bias_confidence_floor": 0.65,
            "intent_time_leverage_long_bias_transition_ceiling": 0.35,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 240,
            "preserve_capital_time_decay_bars": 48,
        },
        {
            "target_vol_4h": 0.65,
            "target_vol_5m": 0.70,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.35,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 48,
            "intent_flat_confirmation_bars": 36,
            "intent_time_leverage_min_hold_bars": 672,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.25,
            "intent_time_leverage_long_bias_confidence_floor": 0.65,
            "intent_time_leverage_long_bias_transition_ceiling": 0.35,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 336,
            "preserve_capital_time_decay_bars": 72,
        },
        {
            "target_vol_4h": 0.65,
            "target_vol_5m": 0.75,
            "direction_signal_threshold": 0.40,
            "intent_add_signal_threshold": 0.30,
            "intent_entry_confirmation_bars": 8,
            "intent_flip_confirmation_bars": 72,
            "intent_flat_confirmation_bars": 36,
            "intent_time_leverage_min_hold_bars": 960,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.50,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.45,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 336,
            "preserve_capital_time_decay_bars": 72,
        },
        {
            "target_vol_4h": 0.80,
            "target_vol_5m": 0.70,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.40,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 48,
            "intent_flat_confirmation_bars": 24,
            "intent_time_leverage_min_hold_bars": 672,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.00,
            "intent_time_leverage_long_bias_confidence_floor": 0.65,
            "intent_time_leverage_long_bias_transition_ceiling": 0.35,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.10,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 240,
            "preserve_capital_time_decay_bars": 48,
        },
        {
            "target_vol_4h": 0.95,
            "target_vol_5m": 0.80,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.25,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 96,
            "intent_flat_confirmation_bars": 48,
            "intent_time_leverage_min_hold_bars": 960,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.50,
            "intent_time_leverage_long_bias_confidence_floor": 0.55,
            "intent_time_leverage_long_bias_transition_ceiling": 0.55,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.20,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 480,
            "preserve_capital_time_decay_bars": 96,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
        {
            "target_vol_4h": 0.95,
            "target_vol_5m": 0.75,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.30,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 72,
            "intent_flat_confirmation_bars": 48,
            "intent_time_leverage_min_hold_bars": 1344,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.50,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.55,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.20,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 480,
            "preserve_capital_time_decay_bars": 96,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
        {
            "target_vol_4h": 0.80,
            "target_vol_5m": 0.80,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.30,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 96,
            "intent_flat_confirmation_bars": 36,
            "intent_time_leverage_min_hold_bars": 960,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.25,
            "intent_time_leverage_long_bias_confidence_floor": 0.55,
            "intent_time_leverage_long_bias_transition_ceiling": 0.55,
            "intent_time_leverage_dip_min_adverse_atr": 1.00,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.20,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 480,
            "preserve_capital_time_decay_bars": 96,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
        {
            "target_vol_4h": 0.95,
            "target_vol_5m": 0.70,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.30,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 96,
            "intent_flat_confirmation_bars": 48,
            "intent_time_leverage_min_hold_bars": 1344,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.50,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.45,
            "intent_time_leverage_dip_min_adverse_atr": 1.25,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.15,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 480,
            "preserve_capital_time_decay_bars": 96,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
        {
            "target_vol_4h": 0.80,
            "target_vol_5m": 0.75,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.25,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 72,
            "intent_flat_confirmation_bars": 48,
            "intent_time_leverage_min_hold_bars": 1344,
            "intent_time_leverage_add_deadband_pct": 0.10,
            "intent_time_leverage_reduce_deadband_pct": 1.75,
            "intent_time_leverage_long_bias_confidence_floor": 0.55,
            "intent_time_leverage_long_bias_transition_ceiling": 0.55,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.20,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 480,
            "preserve_capital_time_decay_bars": 96,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
        {
            "target_vol_4h": 0.95,
            "target_vol_5m": 0.75,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.35,
            "intent_entry_confirmation_bars": 6,
            "intent_flip_confirmation_bars": 120,
            "intent_flat_confirmation_bars": 48,
            "intent_time_leverage_min_hold_bars": 1344,
            "intent_time_leverage_add_deadband_pct": 0.15,
            "intent_time_leverage_reduce_deadband_pct": 1.75,
            "intent_time_leverage_long_bias_confidence_floor": 0.60,
            "intent_time_leverage_long_bias_transition_ceiling": 0.55,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_max_adverse_atr": 3.0,
            "intent_time_leverage_dip_add_pct": 0.20,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "range_time_decay_bars": 480,
            "preserve_capital_time_decay_bars": 96,
            "time_decay_long_loss_threshold": -0.16,
            "time_decay_short_loss_threshold": -0.10,
        },
    ]
    seeds = [
        {
            **fee_repair_anchor,
            **shared_context,
            **variant,
        }
        for variant in context_variants
    ]
    seeds.extend(paulwei_dual_phase_fee_repair_seed_trials()[:6])
    return seeds


def paulwei_dual_phase_spot_context_relaxed_fee_seed_trials() -> list[dict[str, float]]:
    """Deterministic seeds for the research-only relaxed fee upside probe."""

    relaxed_variants = [
        {
            "target_vol_4h": 1.25,
            "target_vol_5m": 1.00,
            "max_leverage": 4.0,
            "direction_signal_threshold": 0.20,
            "intent_add_signal_threshold": 0.20,
            "intent_entry_confirmation_bars": 2,
            "intent_flip_confirmation_bars": 36,
            "intent_flat_confirmation_bars": 24,
            "intent_bearish_override_confirmation_bars": 4,
            "intent_time_leverage_min_hold_bars": 672,
            "intent_time_leverage_reduce_deadband_pct": 1.00,
            "intent_time_leverage_long_bias_confidence_floor": 0.50,
            "intent_time_leverage_long_bias_transition_ceiling": 0.65,
            "intent_time_leverage_long_bias_block_short": 0.0,
            "intent_time_leverage_dip_min_adverse_atr": 0.50,
            "intent_time_leverage_dip_add_pct": 0.25,
            "intent_time_leverage_dip_max_leverage": 4.0,
            "multi_momentum_range_profit_run_enabled": 1.0,
            "multi_momentum_range_profit_run_threshold": 0.15,
            "multi_momentum_range_profit_run_value": 1.00,
            "multi_momentum_range_profit_run_confidence": 0.75,
            "multi_momentum_range_profit_run_max_transition_risk": 0.55,
            "multi_momentum_range_profit_run_min_cycle_position": 0.95,
            "multi_momentum_range_profit_run_block_distorted_volatility": 1.0,
            "multi_momentum_range_profit_run_block_unsafe_quality": 1.0,
            "time_decay_long_loss_threshold": -0.20,
        },
        {
            "target_vol_4h": 0.80,
            "target_vol_5m": 0.80,
            "max_leverage": 3.0,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.25,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 48,
            "intent_flat_confirmation_bars": 24,
            "intent_bearish_override_confirmation_bars": 4,
            "intent_time_leverage_min_hold_bars": 672,
            "intent_time_leverage_reduce_deadband_pct": 1.00,
            "intent_time_leverage_long_bias_confidence_floor": 0.55,
            "intent_time_leverage_long_bias_transition_ceiling": 0.55,
            "intent_time_leverage_long_bias_block_short": 0.0,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_add_pct": 0.20,
            "intent_time_leverage_dip_max_leverage": 3.0,
            "multi_momentum_range_profit_run_enabled": 1.0,
            "multi_momentum_range_profit_run_threshold": 0.20,
            "multi_momentum_range_profit_run_value": 0.85,
            "multi_momentum_range_profit_run_confidence": 0.65,
            "multi_momentum_range_profit_run_max_transition_risk": 0.45,
            "multi_momentum_range_profit_run_min_cycle_position": 1.00,
            "multi_momentum_range_profit_run_block_distorted_volatility": 1.0,
            "multi_momentum_range_profit_run_block_unsafe_quality": 1.0,
        },
        {
            "target_vol_4h": 1.10,
            "target_vol_5m": 0.90,
            "max_leverage": 3.5,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.25,
            "intent_entry_confirmation_bars": 4,
            "intent_flip_confirmation_bars": 72,
            "intent_flat_confirmation_bars": 48,
            "intent_bearish_override_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 960,
            "intent_time_leverage_reduce_deadband_pct": 1.25,
            "intent_time_leverage_long_bias_confidence_floor": 0.55,
            "intent_time_leverage_long_bias_transition_ceiling": 0.55,
            "intent_time_leverage_long_bias_block_short": 0.0,
            "intent_time_leverage_dip_min_adverse_atr": 0.75,
            "intent_time_leverage_dip_add_pct": 0.20,
            "intent_time_leverage_dip_max_leverage": 3.5,
            "multi_momentum_range_profit_run_enabled": 1.0,
            "multi_momentum_range_profit_run_threshold": 0.20,
            "multi_momentum_range_profit_run_value": 0.85,
            "multi_momentum_range_profit_run_confidence": 0.65,
            "multi_momentum_range_profit_run_max_transition_risk": 0.55,
            "multi_momentum_range_profit_run_min_cycle_position": 0.95,
            "multi_momentum_range_profit_run_block_distorted_volatility": 0.0,
            "multi_momentum_range_profit_run_block_unsafe_quality": 1.0,
        },
        {
            "target_vol_4h": 1.25,
            "target_vol_5m": 1.00,
            "max_leverage": 4.0,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.20,
            "intent_flip_confirmation_bars": 168,
            "intent_flat_confirmation_bars": 72,
            "intent_bearish_override_confirmation_bars": 16,
            "intent_time_leverage_min_hold_bars": 2016,
            "intent_time_leverage_reduce_deadband_pct": 2.00,
            "intent_time_leverage_long_bias_block_short": 1.0,
            "intent_time_leverage_dip_max_leverage": 4.0,
            "time_decay_long_loss_threshold": -0.24,
            "time_decay_short_loss_threshold": -0.16,
        },
        {
            "target_vol_4h": 1.10,
            "target_vol_5m": 1.00,
            "max_leverage": 4.0,
            "direction_signal_threshold": 0.20,
            "intent_add_signal_threshold": 0.20,
            "intent_flip_confirmation_bars": 168,
            "intent_flat_confirmation_bars": 72,
            "intent_bearish_override_confirmation_bars": 24,
            "intent_time_leverage_min_hold_bars": 2016,
            "intent_time_leverage_reduce_deadband_pct": 2.00,
            "intent_time_leverage_long_bias_block_short": 0.0,
            "intent_time_leverage_dip_max_leverage": 4.0,
            "time_decay_long_loss_threshold": -0.24,
            "time_decay_short_loss_threshold": -0.16,
        },
        {
            "target_vol_4h": 0.95,
            "target_vol_5m": 1.00,
            "max_leverage": 3.5,
            "direction_signal_threshold": 0.20,
            "intent_add_signal_threshold": 0.25,
            "intent_flip_confirmation_bars": 120,
            "intent_flat_confirmation_bars": 72,
            "intent_bearish_override_confirmation_bars": 16,
            "intent_time_leverage_min_hold_bars": 2016,
            "intent_time_leverage_reduce_deadband_pct": 1.75,
            "intent_time_leverage_long_bias_block_short": 0.0,
            "intent_time_leverage_dip_max_leverage": 3.5,
            "time_decay_long_loss_threshold": -0.20,
            "time_decay_short_loss_threshold": -0.12,
        },
        {
            "target_vol_4h": 1.25,
            "target_vol_5m": 0.90,
            "max_leverage": 4.0,
            "direction_signal_threshold": 0.30,
            "intent_add_signal_threshold": 0.20,
            "intent_flip_confirmation_bars": 120,
            "intent_flat_confirmation_bars": 48,
            "intent_bearish_override_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 1344,
            "intent_time_leverage_reduce_deadband_pct": 1.75,
            "intent_time_leverage_long_bias_block_short": 1.0,
            "intent_time_leverage_dip_max_leverage": 4.0,
        },
        {
            "target_vol_4h": 0.80,
            "target_vol_5m": 1.00,
            "max_leverage": 3.5,
            "direction_signal_threshold": 0.20,
            "intent_add_signal_threshold": 0.20,
            "intent_flip_confirmation_bars": 96,
            "intent_flat_confirmation_bars": 72,
            "intent_bearish_override_confirmation_bars": 24,
            "intent_time_leverage_min_hold_bars": 2016,
            "intent_time_leverage_reduce_deadband_pct": 2.00,
            "intent_time_leverage_long_bias_block_short": 0.0,
            "intent_time_leverage_dip_max_leverage": 3.5,
        },
        {
            "target_vol_4h": 1.10,
            "target_vol_5m": 0.80,
            "max_leverage": 4.0,
            "direction_signal_threshold": 0.35,
            "intent_add_signal_threshold": 0.30,
            "intent_flip_confirmation_bars": 168,
            "intent_flat_confirmation_bars": 48,
            "intent_bearish_override_confirmation_bars": 16,
            "intent_time_leverage_min_hold_bars": 1344,
            "intent_time_leverage_reduce_deadband_pct": 1.50,
            "intent_time_leverage_long_bias_block_short": 1.0,
            "intent_time_leverage_dip_max_leverage": 4.0,
        },
        {
            "target_vol_4h": 0.95,
            "target_vol_5m": 0.80,
            "max_leverage": 4.0,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.35,
            "intent_flip_confirmation_bars": 96,
            "intent_flat_confirmation_bars": 36,
            "intent_bearish_override_confirmation_bars": 8,
            "intent_time_leverage_min_hold_bars": 960,
            "intent_time_leverage_reduce_deadband_pct": 1.25,
            "intent_time_leverage_long_bias_block_short": 0.0,
            "intent_time_leverage_dip_max_leverage": 4.0,
        },
        {
            "target_vol_4h": 1.25,
            "target_vol_5m": 1.00,
            "max_leverage": 3.5,
            "direction_signal_threshold": 0.20,
            "intent_add_signal_threshold": 0.20,
            "intent_flip_confirmation_bars": 168,
            "intent_flat_confirmation_bars": 72,
            "intent_bearish_override_confirmation_bars": 24,
            "intent_time_leverage_min_hold_bars": 2016,
            "intent_time_leverage_reduce_deadband_pct": 2.00,
            "intent_time_leverage_long_bias_block_short": 1.0,
            "intent_time_leverage_dip_max_leverage": 3.5,
            "time_decay_long_loss_threshold": -0.24,
            "time_decay_short_loss_threshold": -0.16,
        },
        {
            "target_vol_4h": 0.65,
            "target_vol_5m": 0.90,
            "max_leverage": 3.5,
            "direction_signal_threshold": 0.25,
            "intent_add_signal_threshold": 0.20,
            "intent_flip_confirmation_bars": 120,
            "intent_flat_confirmation_bars": 72,
            "intent_bearish_override_confirmation_bars": 16,
            "intent_time_leverage_min_hold_bars": 2016,
            "intent_time_leverage_reduce_deadband_pct": 1.75,
            "intent_time_leverage_long_bias_block_short": 0.0,
            "intent_time_leverage_dip_max_leverage": 3.5,
        },
    ]
    seeds: list[dict[str, float]] = []
    for index, base_seed in enumerate(paulwei_dual_phase_spot_context_seed_trials()):
        seeds.append(
            {
                **base_seed,
                **relaxed_variants[index % len(relaxed_variants)],
                "research_relaxed_fee_probe_enabled": 1.0,
            }
        )
    return seeds


def binance_factor_weight_repair_seed_trials() -> list[dict[str, float]]:
    targeted_v22_fee_shape = {
        **baseline_anchor_params(),
        "dd_step_1_long": 0.18,
        "dd_step_1_short": 0.10,
        "vol_shock_threshold": 1.00,
        "target_vol_4h": 0.80,
        "max_leverage": 3.5,
        "pyramid_short_initial_pct": 0.15,
        "pyramid_short_add_pct": 0.12,
        "pyramid_spacing_atr": 1.00,
        "exit_unwind_pct": 0.35,
        "dca_max_adverse_atr": 2.5,
        "dca_spacing_atr": 1.75,
        "dca_size_decay": 0.90,
        "flip_min_confidence": 0.70,
        "flip_flatten_only_enabled": 0.0,
        "flip_reverse_open_scale": 1.00,
        "loss_flip_reverse_open_confidence_floor": 0.0,
        "range_time_decay_bars": 8,
        "preserve_capital_time_decay_bars": 4,
        "trade_directional_confidence_floor": 0.80,
        "bearish_override_confidence_floor": 0.75,
        "exit_transition_risk": 0.95,
        "hostile_behavior_router_enabled": 0.0,
        "squeeze_flip_flatten_only_enabled": 0.0,
        "squeeze_flip_cooldown_enabled": 0.0,
        "squeeze_conditional_cooldown_enabled": 0.0,
        "squeeze_flip_cooldown_bars": 1,
        "squeeze_conditional_cooldown_drawdown_threshold": 0.06,
        "squeeze_conditional_cooldown_toxic_only": 0.0,
        "squeeze_cooldown_cancel_on_non_squeeze_enabled": 0.0,
        "liquidation_data_enabled": 0.0,
        "state_liquidation_overlay_enabled": 0.0,
        "liq_risk_guard_enabled": 0.0,
        "w_binance_metrics_state": 0.07,
        "w_perp_carry": 0.04,
        "w_vol_structure": 0.04,
        "binance_metrics_state_enabled": 1.0,
        "binance_metrics_risk_sizer_enabled": 0.0,
        "binance_metrics_risk_edge_threshold": 0.15,
        "binance_metrics_risk_discount_floor": 0.60,
        "target_delta_deadband_qty": 0.0,
        "target_delta_deadband_pct": 0.0,
        "target_reduce_deadband_qty": 0.0,
        "target_reduce_deadband_pct": 0.0,
        "min_hold_bars_before_flip": 0,
        "entry_cooldown_bars": 0,
        "weak_signal_no_flip_enabled": 0.0,
        "weak_signal_no_flip_confidence": 0.70,
        "tiny_reverse_reduce_only_enabled": 0.0,
        "tiny_reverse_deadband_qty": 0.0,
        "tiny_reverse_deadband_pct": 0.05,
        "entry_confidence_floor": 0.0,
        "reverse_open_confidence_floor": 0.0,
        "range_entry_block_enabled": 0.0,
        "degraded_entry_block_enabled": 0.0,
        "degraded_entry_confidence_floor": 0.80,
        "loss_dca_confidence_floor": 0.0,
        "loss_dca_range_block_enabled": 0.0,
        "loss_dca_degraded_block_enabled": 0.0,
        "profit_protect_reduce_enabled": 0.0,
        "profit_protect_min_move_atr": 1.00,
        "profit_protect_unwind_pct": 0.25,
        "profit_protect_confidence_ceiling": 0.75,
    }
    targeted_seeds = [
        dict(targeted_v22_fee_shape),
        {
            **targeted_v22_fee_shape,
            "target_vol_4h": 0.65,
            "max_leverage": 2.5,
            "target_delta_deadband_pct": 0.08,
            "entry_confidence_floor": 0.85,
            "reverse_open_confidence_floor": 0.95,
            "flip_flatten_only_enabled": 0.0,
            "flip_reverse_open_scale": 1.00,
            "loss_flip_reverse_open_confidence_floor": 1.00,
        },
        {
            **targeted_v22_fee_shape,
            "flip_min_confidence": 0.80,
            "target_delta_deadband_pct": 0.10,
            "tiny_reverse_deadband_pct": 0.20,
            "entry_confidence_floor": 0.85,
            "reverse_open_confidence_floor": 0.95,
            "flip_flatten_only_enabled": 0.0,
            "flip_reverse_open_scale": 1.00,
            "loss_flip_reverse_open_confidence_floor": 0.95,
        },
        {
            **targeted_v22_fee_shape,
            "binance_metrics_risk_sizer_enabled": 1.0,
            "binance_metrics_risk_edge_threshold": 0.25,
            "binance_metrics_risk_discount_floor": 0.70,
            "target_delta_deadband_pct": 0.08,
            "entry_confidence_floor": 0.85,
            "reverse_open_confidence_floor": 0.95,
            "flip_flatten_only_enabled": 0.0,
            "flip_reverse_open_scale": 1.00,
            "loss_flip_reverse_open_confidence_floor": 1.00,
        },
    ]
    seeds: list[dict[str, float]] = []
    for base_seed in intent_churn_repair_seed_trials():
        seeds.extend(
            [
                {
                    **base_seed,
                    "target_vol_4h": 0.50,
                    "max_leverage": 2.5,
                    "range_time_decay_bars": max(float(base_seed["range_time_decay_bars"]), 12.0),
                    "preserve_capital_time_decay_bars": max(
                        float(base_seed["preserve_capital_time_decay_bars"]),
                        3.0,
                    ),
                    "trade_directional_confidence_floor": max(
                        float(base_seed["trade_directional_confidence_floor"]),
                        0.75,
                    ),
                    "bearish_override_confidence_floor": max(
                        float(base_seed["bearish_override_confidence_floor"]),
                        0.75,
                    ),
                    "exit_transition_risk": max(float(base_seed["exit_transition_risk"]), 0.90),
                    "w_binance_metrics_state": 0.18,
                    "w_perp_carry": 0.04,
                    "w_vol_structure": 0.08,
                    "binance_metrics_state_enabled": 1.0,
                    "binance_metrics_risk_sizer_enabled": 1.0,
                    "binance_metrics_risk_edge_threshold": 0.15,
                    "binance_metrics_risk_discount_floor": 0.60,
                    "target_delta_deadband_qty": 0.0,
                    "target_delta_deadband_pct": 0.06,
                    "target_reduce_deadband_qty": 0.0,
                    "target_reduce_deadband_pct": 0.05,
                    "flip_reverse_open_scale": 1.00,
                    "loss_flip_reverse_open_confidence_floor": 0.95,
                    "min_hold_bars_before_flip": 4,
                    "entry_cooldown_bars": 2,
                    "weak_signal_no_flip_enabled": 1.0,
                    "weak_signal_no_flip_confidence": 0.75,
                    "tiny_reverse_reduce_only_enabled": 1.0,
                    "tiny_reverse_deadband_qty": 0.0,
                    "tiny_reverse_deadband_pct": 0.10,
                    "entry_confidence_floor": 0.85,
                    "reverse_open_confidence_floor": 0.90,
                    "range_entry_block_enabled": 1.0,
                    "degraded_entry_block_enabled": 1.0,
                    "degraded_entry_confidence_floor": 0.85,
                    "loss_dca_confidence_floor": 0.85,
                    "loss_dca_range_block_enabled": 1.0,
                    "loss_dca_degraded_block_enabled": 1.0,
                    "profit_protect_reduce_enabled": 0.0,
                    "profit_protect_min_move_atr": 1.00,
                    "profit_protect_unwind_pct": 0.25,
                    "profit_protect_confidence_ceiling": 0.80,
                },
                {
                    **base_seed,
                    "target_vol_4h": 0.65,
                    "max_leverage": 3.5,
                    "range_time_decay_bars": max(float(base_seed["range_time_decay_bars"]), 12.0),
                    "preserve_capital_time_decay_bars": max(
                        float(base_seed["preserve_capital_time_decay_bars"]),
                        3.0,
                    ),
                    "trade_directional_confidence_floor": max(
                        float(base_seed["trade_directional_confidence_floor"]),
                        0.70,
                    ),
                    "bearish_override_confidence_floor": max(
                        float(base_seed["bearish_override_confidence_floor"]),
                        0.70,
                    ),
                    "exit_transition_risk": 0.95,
                    "w_binance_metrics_state": 0.12,
                    "w_perp_carry": 0.08,
                    "w_vol_structure": 0.08,
                    "binance_metrics_state_enabled": 1.0,
                    "binance_metrics_risk_sizer_enabled": 1.0,
                    "binance_metrics_risk_edge_threshold": 0.25,
                    "binance_metrics_risk_discount_floor": 0.70,
                    "target_delta_deadband_qty": 0.0,
                    "target_delta_deadband_pct": 0.04,
                    "target_reduce_deadband_qty": 0.0,
                    "target_reduce_deadband_pct": 0.0,
                    "flip_reverse_open_scale": 1.00,
                    "loss_flip_reverse_open_confidence_floor": 0.0,
                    "min_hold_bars_before_flip": 2,
                    "entry_cooldown_bars": 1,
                    "weak_signal_no_flip_enabled": 1.0,
                    "weak_signal_no_flip_confidence": 0.70,
                    "tiny_reverse_reduce_only_enabled": 1.0,
                    "tiny_reverse_deadband_qty": 0.0,
                    "tiny_reverse_deadband_pct": 0.05,
                    "entry_confidence_floor": 0.80,
                    "reverse_open_confidence_floor": 0.85,
                    "range_entry_block_enabled": 1.0,
                    "degraded_entry_block_enabled": 1.0,
                    "degraded_entry_confidence_floor": 0.80,
                    "loss_dca_confidence_floor": 0.80,
                    "loss_dca_range_block_enabled": 1.0,
                    "loss_dca_degraded_block_enabled": 1.0,
                    "profit_protect_reduce_enabled": 0.0,
                    "profit_protect_min_move_atr": 1.25,
                    "profit_protect_unwind_pct": 0.20,
                    "profit_protect_confidence_ceiling": 0.75,
                },
                {
                    **base_seed,
                    "w_binance_metrics_state": 0.0,
                    "w_perp_carry": 0.08,
                    "w_vol_structure": 0.08,
                    "binance_metrics_state_enabled": 0.0,
                    "binance_metrics_risk_sizer_enabled": 0.0,
                    "binance_metrics_risk_edge_threshold": 0.25,
                    "binance_metrics_risk_discount_floor": 0.70,
                    "target_delta_deadband_qty": 0.0,
                    "target_delta_deadband_pct": 0.06,
                    "target_reduce_deadband_qty": 0.0,
                    "target_reduce_deadband_pct": 0.05,
                    "flip_reverse_open_scale": 1.00,
                    "loss_flip_reverse_open_confidence_floor": 0.95,
                    "min_hold_bars_before_flip": 4,
                    "entry_cooldown_bars": 2,
                    "weak_signal_no_flip_enabled": 1.0,
                    "weak_signal_no_flip_confidence": 0.75,
                    "tiny_reverse_reduce_only_enabled": 1.0,
                    "tiny_reverse_deadband_qty": 0.0,
                    "tiny_reverse_deadband_pct": 0.10,
                    "entry_confidence_floor": 0.85,
                    "reverse_open_confidence_floor": 0.90,
                    "range_entry_block_enabled": 1.0,
                    "degraded_entry_block_enabled": 1.0,
                    "degraded_entry_confidence_floor": 0.85,
                    "loss_dca_confidence_floor": 0.85,
                    "loss_dca_range_block_enabled": 1.0,
                    "loss_dca_degraded_block_enabled": 1.0,
                    "profit_protect_reduce_enabled": 0.0,
                    "profit_protect_min_move_atr": 1.00,
                    "profit_protect_unwind_pct": 0.25,
                    "profit_protect_confidence_ceiling": 0.80,
                },
                {
                    **base_seed,
                    "target_vol_4h": 0.65,
                    "max_leverage": 3.5,
                    "w_binance_metrics_state": 0.18,
                    "w_perp_carry": 0.04,
                    "w_vol_structure": 0.08,
                    "binance_metrics_state_enabled": 1.0,
                    "binance_metrics_risk_sizer_enabled": 1.0,
                    "binance_metrics_risk_edge_threshold": 0.15,
                    "binance_metrics_risk_discount_floor": 0.60,
                    "target_delta_deadband_qty": 0.0,
                    "target_delta_deadband_pct": 0.06,
                    "target_reduce_deadband_qty": 0.0,
                    "target_reduce_deadband_pct": 0.05,
                    "flip_reverse_open_scale": 1.00,
                    "loss_flip_reverse_open_confidence_floor": 0.95,
                    "min_hold_bars_before_flip": 4,
                    "entry_cooldown_bars": 2,
                    "weak_signal_no_flip_enabled": 1.0,
                    "weak_signal_no_flip_confidence": 0.75,
                    "tiny_reverse_reduce_only_enabled": 1.0,
                    "tiny_reverse_deadband_qty": 0.0,
                    "tiny_reverse_deadband_pct": 0.10,
                    "entry_confidence_floor": 0.80,
                    "reverse_open_confidence_floor": 0.90,
                    "range_entry_block_enabled": 1.0,
                    "degraded_entry_block_enabled": 1.0,
                    "degraded_entry_confidence_floor": 0.85,
                    "loss_dca_confidence_floor": 0.85,
                    "loss_dca_range_block_enabled": 1.0,
                    "loss_dca_degraded_block_enabled": 1.0,
                    "profit_protect_reduce_enabled": 0.0,
                    "profit_protect_min_move_atr": 1.00,
                    "profit_protect_unwind_pct": 0.25,
                    "profit_protect_confidence_ceiling": 0.75,
                },
            ]
        )
    return targeted_seeds + seeds


def core_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    grid: dict[str, list[float]] = {
        "dd_step_1_long": [0.12, 0.15, 0.18],
        "dd_step_1_short": [0.06, 0.08, 0.10],
        "vpin_threshold": [0.08, 0.10],
        "vol_shock_threshold": [0.80, 0.90, 1.00],
        "target_vol_4h": [0.80, 0.95, 1.10],
        "max_leverage": [3.5, 4.5, 6.0],
        "pyramid_initial_pct": [0.30],
        "pyramid_add_pct": [0.30],
        "pyramid_short_initial_pct": [0.15, 0.20],
        "pyramid_short_add_pct": [0.10, 0.12, 0.16, 0.20],
        "pyramid_spacing_atr": [0.50, 0.75, 1.00],
        "exit_unwind_pct": [0.25, 0.30, 0.35],
        "dca_max_adverse_atr": [2.5, 3.0, 3.5],
        "dca_spacing_atr": [0.75, 1.25, 1.75],
        "dca_size_decay": [0.60, 0.75, 0.90],
        "flip_min_confidence": [0.55, 0.65, 0.75],
    }
    if include_liquidation_toggles:
        grid.update(
            {
                "liquidation_data_enabled": [0.0, 1.0],
                "state_liquidation_overlay_enabled": [0.0, 1.0],
                "liq_risk_guard_enabled": [0.0, 1.0],
                "liq_risk_guard_threshold": [1.8, 2.2, 2.6],
                "liq_risk_guard_mult": [0.65, 0.75, 0.85],
            }
        )
    return grid


def param_grid_for_mode(
    *,
    mode: str = "execution_first_v2_correlation_constrained",
    include_liquidation_toggles: bool = False,
) -> dict[str, list[float]]:
    if mode == "baseline_anchored_2023_08_hostile_repair":
        return baseline_anchored_2023_08_hostile_repair_param_grid(
            include_liquidation_toggles=include_liquidation_toggles
        )
    if mode == "baseline_anchored_defensive_candidate_existence":
        return baseline_anchored_defensive_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "baseline_anchored_train_only":
        return baseline_anchored_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "execution_inventory_rethink_train_only":
        return execution_inventory_rethink_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "intent_churn_ablation_train_only":
        return intent_churn_ablation_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "intent_churn_repair_train_only":
        return intent_churn_repair_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "binance_factor_weight_repair_train_only":
        return binance_factor_weight_repair_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "ralph_protective_reduce_train_only":
        return ralph_protective_reduce_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "ralph_protective_reduce_fee_repair_train_only":
        return ralph_protective_reduce_fee_repair_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "ralph_execution_fee_cooldown_train_only":
        return ralph_execution_fee_cooldown_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "ralph_intent_reduce_cadence_train_only":
        return ralph_intent_reduce_cadence_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "paulwei_balanced_tranche_train_only":
        return paulwei_balanced_tranche_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "paulwei_dual_phase_train_only":
        return paulwei_dual_phase_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "paulwei_dual_phase_fee_repair_train_only":
        return paulwei_dual_phase_fee_repair_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "paulwei_dual_phase_spot_context_train_only":
        return paulwei_dual_phase_spot_context_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    if mode == "paulwei_dual_phase_spot_context_relaxed_fee_train_only":
        return paulwei_dual_phase_spot_context_relaxed_fee_param_grid(
            include_liquidation_toggles=include_liquidation_toggles
        )
    return core_param_grid(include_liquidation_toggles=include_liquidation_toggles)


def search_space_profile(
    *,
    include_liquidation_toggles: bool = False,
    mode: str = "execution_first_v2_correlation_constrained",
) -> dict[str, Any]:
    baseline_anchored = mode in {
        "baseline_anchored_train_only",
        "baseline_anchored_defensive_candidate_existence",
        "baseline_anchored_2023_08_hostile_repair",
    }
    execution_inventory_rethink = mode == "execution_inventory_rethink_train_only"
    intent_churn_ablation = mode == "intent_churn_ablation_train_only"
    intent_churn_repair = mode == "intent_churn_repair_train_only"
    binance_factor_weight_repair = mode == "binance_factor_weight_repair_train_only"
    ralph_protective_reduce = mode == "ralph_protective_reduce_train_only"
    ralph_protective_reduce_fee_repair = mode == "ralph_protective_reduce_fee_repair_train_only"
    ralph_execution_fee_cooldown = mode == "ralph_execution_fee_cooldown_train_only"
    ralph_intent_reduce_cadence = mode == "ralph_intent_reduce_cadence_train_only"
    paulwei_balanced_tranche = mode == "paulwei_balanced_tranche_train_only"
    paulwei_dual_phase_fee_repair = mode == "paulwei_dual_phase_fee_repair_train_only"
    paulwei_dual_phase_spot_context_relaxed_fee = mode == "paulwei_dual_phase_spot_context_relaxed_fee_train_only"
    paulwei_dual_phase_spot_context = mode in {
        "paulwei_dual_phase_spot_context_train_only",
        "paulwei_dual_phase_spot_context_relaxed_fee_train_only",
    }
    paulwei_dual_phase = mode in {
        "paulwei_dual_phase_train_only",
        "paulwei_dual_phase_fee_repair_train_only",
        "paulwei_dual_phase_spot_context_train_only",
        "paulwei_dual_phase_spot_context_relaxed_fee_train_only",
    }
    return {
        "profile": (
            mode
            if (
                baseline_anchored
                or execution_inventory_rethink
                or intent_churn_ablation
                or intent_churn_repair
                or binance_factor_weight_repair
                or ralph_protective_reduce
                or ralph_protective_reduce_fee_repair
                or ralph_execution_fee_cooldown
                or ralph_intent_reduce_cadence
                or paulwei_balanced_tranche
                or paulwei_dual_phase
                or paulwei_dual_phase_fee_repair
                or paulwei_dual_phase_spot_context
            )
            else "execution_first_v2_correlation_constrained"
            if not include_liquidation_toggles
            else "execution_first_v2_correlation_constrained_liquidation"
        ),
        "search_space_mode": mode,
        "baseline_anchored_search": baseline_anchored,
        "baseline_anchor_variant": "paulwei_baseline",
        "execution_inventory_rethink_search": execution_inventory_rethink,
        "intent_churn_ablation_search": intent_churn_ablation,
        "intent_churn_repair_search": intent_churn_repair,
        "binance_factor_weight_repair_search": binance_factor_weight_repair,
        "ralph_protective_reduce_search": ralph_protective_reduce,
        "ralph_protective_reduce_fee_repair_search": ralph_protective_reduce_fee_repair,
        "ralph_execution_fee_cooldown_search": ralph_execution_fee_cooldown,
        "ralph_intent_reduce_cadence_search": ralph_intent_reduce_cadence,
        "paulwei_balanced_tranche_search": paulwei_balanced_tranche,
        "paulwei_dual_phase_search": paulwei_dual_phase,
        "paulwei_dual_phase_fee_repair_search": paulwei_dual_phase_fee_repair,
        "paulwei_dual_phase_spot_context_search": paulwei_dual_phase_spot_context,
        "paulwei_dual_phase_spot_context_relaxed_fee_search": paulwei_dual_phase_spot_context_relaxed_fee,
        "research_relaxed_fee_probe": paulwei_dual_phase_spot_context_relaxed_fee,
        "paulwei_phase_learning_contract": {
            "enabled": paulwei_dual_phase,
            "templates": [
                "early_convexity_usdt_projection",
                "mature_low_switch_control",
                *(
                    ["dual_phase_fee_repair"]
                    if paulwei_dual_phase_fee_repair or paulwei_dual_phase_spot_context
                    else []
                ),
                *(["spot_era_context_profit_run"] if paulwei_dual_phase_spot_context else []),
                *(["research_relaxed_fee_probe"] if paulwei_dual_phase_spot_context_relaxed_fee else []),
            ]
            if paulwei_dual_phase
            else [],
            "forbidden_transfers": [
                "BTC-collateral convexity as direct USDT return target",
                "public-ledger hindsight as promotion truth",
                "early-public-ledger drawdown tolerance without USDT risk caps",
            ]
            if paulwei_dual_phase
            else [],
        },
        "hostile_window_focus": "weak_window_2023_08" if mode == "baseline_anchored_2023_08_hostile_repair" else None,
        "include_liquidation_toggles": include_liquidation_toggles,
        "direction_weight_policy": {
            "correlation_constrained": True,
            "primary_direction_signal": "multi_momentum_signal",
            "narrow_direction_signals": ["crash_reversal_signal", "cvd_signal"],
            "direction_axes": [
                "multi_momentum_signal",
                "crash_reversal_signal",
                "cvd_signal",
                "crowding_signal",
            ],
            "capped_direction_signal": "crowding_signal",
            "demoted_signal": "funding_alpha_signal",
            "demoted_signal_role": "state",
        },
        "hostile_window_guardrails": {
            "enabled": True,
            "constraints": [
                "high_target_vol_with_excess_leverage",
                "aggressive_long_and_short_pyramiding",
                "tight_dca_spacing_with_heavy_inventory_reloading",
                "low_flip_confidence_with_fast_inventory_expansion",
            ],
        },
        "param_count": len(param_grid_for_mode(mode=mode, include_liquidation_toggles=include_liquidation_toggles)),
    }
