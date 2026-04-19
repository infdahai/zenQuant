"""Shared reduced-core framework parameter mapping for clean tuning."""

from __future__ import annotations

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


def build_strategy_config(params: dict[str, Any]):
    return _build_strategy_config(params)


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
    return weights


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
    if dca_spacing_atr is not None and dca_size_decay is not None and dca_spacing_atr <= 0.75 and dca_size_decay <= 0.60:
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
        "pyramid_initial_pct": [0.25, 0.30],
        "pyramid_add_pct": [0.15, 0.20, 0.30],
        "pyramid_short_initial_pct": [0.15, 0.20],
        "pyramid_short_add_pct": [0.10, 0.16, 0.20],
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
        "dca_max_adverse_atr": 3.0,
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
            "pyramid_add_pct": 0.15,
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
            "pyramid_add_pct": 0.15,
            "pyramid_short_add_pct": 0.10,
            "exit_unwind_pct": 0.40,
            "flip_min_confidence": 0.70,
        },
    ]


def core_param_grid(*, include_liquidation_toggles: bool = False) -> dict[str, list[float]]:
    grid: dict[str, list[float]] = {
        "dd_step_1_long": [0.12, 0.15, 0.18],
        "dd_step_1_short": [0.06, 0.08, 0.10],
        "vpin_threshold": [0.08, 0.10],
        "vol_shock_threshold": [0.80, 0.90, 1.00],
        "target_vol_4h": [0.80, 0.95, 1.10],
        "max_leverage": [3.5, 4.5, 6.0],
        "pyramid_initial_pct": [0.20, 0.25, 0.30],
        "pyramid_add_pct": [0.10, 0.20, 0.30],
        "pyramid_short_initial_pct": [0.10, 0.15, 0.20],
        "pyramid_short_add_pct": [0.08, 0.12, 0.16],
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
    if mode == "baseline_anchored_train_only":
        return baseline_anchored_param_grid(include_liquidation_toggles=include_liquidation_toggles)
    return core_param_grid(include_liquidation_toggles=include_liquidation_toggles)


def search_space_profile(
    *,
    include_liquidation_toggles: bool = False,
    mode: str = "execution_first_v2_correlation_constrained",
) -> dict[str, Any]:
    baseline_anchored = mode == "baseline_anchored_train_only"
    return {
        "profile": (
            "baseline_anchored_train_only"
            if baseline_anchored
            else "execution_first_v2_correlation_constrained"
            if not include_liquidation_toggles
            else "execution_first_v2_correlation_constrained_liquidation"
        ),
        "search_space_mode": mode,
        "baseline_anchored_search": baseline_anchored,
        "baseline_anchor_variant": "paulwei_baseline",
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
