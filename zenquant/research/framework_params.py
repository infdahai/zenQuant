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


def search_space_profile(*, include_liquidation_toggles: bool = False) -> dict[str, Any]:
    return {
        "profile": (
            "execution_first_v1"
            if not include_liquidation_toggles
            else "execution_first_v1_liquidation"
        ),
        "include_liquidation_toggles": include_liquidation_toggles,
        "param_count": len(core_param_grid(include_liquidation_toggles=include_liquidation_toggles)),
    }
