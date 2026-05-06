import importlib.util
import json
import multiprocessing as mp
import sys
import tempfile
import unittest
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktestConfig
from zenquant.historical.storage import HistoricalKlineRow, write_parquet_rows
from zenquant.research.auto_research import _build_strategy_config, _hostile_proxy_diagnostics
from zenquant.research.comparison import VariantSpec
from zenquant.research.framework_params import (
    BASE_TIMEFRAME_SMOKE_MODES,
    DEFAULT_DEV_FEE_STABILITY_LIMITS,
    RELAXED_DEV_FEE_STABILITY_LIMITS,
    SEARCH_SPACE_MODES,
    STRICT_TRAIN_FEE_STOP_PROFILE_FLAGS,
    STRICT_TRAIN_TRUTH_PROFILE_FLAGS,
    baseline_anchor_params,
    baseline_anchor_seed_trials,
    baseline_anchored_2023_08_hostile_repair_param_grid,
    baseline_anchored_defensive_param_grid,
    baseline_anchored_param_grid,
    binance_factor_weight_repair_param_grid,
    binance_factor_weight_repair_seed_trials,
    build_execution_config,
    build_plugin_config_overrides,
    build_replay_toggles,
    build_signal_weight_overrides,
    build_strategy_config,
    core_param_grid,
    execution_inventory_rethink_param_grid,
    execution_inventory_rethink_seed_trials,
    hostile_constraint_violations,
    intent_churn_ablation_param_grid,
    intent_churn_ablation_seed_trials,
    intent_churn_repair_param_grid,
    intent_churn_repair_seed_trials,
    is_hostile_aware_param_combo,
    paulwei_balanced_tranche_param_grid,
    paulwei_balanced_tranche_seed_trials,
    paulwei_dual_phase_fee_repair_param_grid,
    paulwei_dual_phase_fee_repair_seed_trials,
    paulwei_dual_phase_param_grid,
    paulwei_dual_phase_seed_trials,
    paulwei_dual_phase_spot_context_param_grid,
    paulwei_dual_phase_spot_context_relaxed_fee_param_grid,
    paulwei_dual_phase_spot_context_relaxed_fee_seed_trials,
    paulwei_dual_phase_spot_context_seed_trials,
    ralph_execution_fee_cooldown_param_grid,
    ralph_execution_fee_cooldown_seed_trials,
    ralph_intent_reduce_cadence_param_grid,
    ralph_intent_reduce_cadence_seed_trials,
    ralph_protective_reduce_fee_repair_param_grid,
    ralph_protective_reduce_fee_repair_seed_trials,
    ralph_protective_reduce_param_grid,
    ralph_protective_reduce_seed_trials,
    search_space_profile,
    smoke_uses_base_timeframe,
)
from zenquant.research.framework_search import (
    BacktestSnapshot,
    CandidateSummary,
    FrameworkEvaluator,
    PrepareVariantTimeoutError,
    SearchReport,
    SearchStageTimeoutError,
    ValidationBundleTimeoutError,
    _assess_full_training_readiness,
    _candidate_score,
    _classify_post_smoke_scenario,
    _dev_fee_stability_summary,
    _hostile_regression_summary,
    _late_dev_regime_window,
    _ns_from_utc,
    _run_validation_bundle,
    _run_variant_backtest_with_timeout,
    _spot_context_score,
    _stage1_fail_fast_reason,
    _temporal_robustness_audit,
    _train_temporal_robustness_windows,
    build_markdown_report,
    run_optuna_search,
    write_search_report,
)
from zenquant.research.rl_tuner import train_tuner
from zenquant.research.splits import default_btc_split_spec, split_summary_json


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


IMPORT_LIQ = _load_module("import_liq_history_module", "scripts/import_liquidation_history.py")


class FrameworkParamTests(unittest.TestCase):
    def test_build_replay_toggles_disable_overlay_without_data_toggle(self) -> None:
        liquidation_data_enabled, state_overlay = build_replay_toggles(
            {
                "liquidation_data_enabled": 0.0,
                "state_liquidation_overlay_enabled": 1.0,
            }
        )

        self.assertFalse(liquidation_data_enabled)
        self.assertFalse(state_overlay)

    def test_core_param_grid_excludes_liquidation_toggles_by_default(self) -> None:
        grid = core_param_grid()
        self.assertIn("pyramid_initial_pct", grid)
        self.assertIn("pyramid_spacing_atr", grid)
        self.assertNotIn("liquidation_data_enabled", grid)

    def test_search_space_profile_marks_correlation_constraints(self) -> None:
        profile = search_space_profile()
        self.assertEqual(profile["profile"], "execution_first_v2_correlation_constrained")
        self.assertTrue(profile["direction_weight_policy"]["correlation_constrained"])
        self.assertEqual(profile["direction_weight_policy"]["capped_direction_signal"], "crowding_signal")
        self.assertEqual(profile["direction_weight_policy"]["demoted_signal"], "funding_alpha_signal")
        self.assertEqual(profile["direction_weight_policy"]["demoted_signal_role"], "state")
        self.assertTrue(profile["hostile_window_guardrails"]["enabled"])

    def test_framework_research_config_registry_contains_campaign_modes_and_limits(self) -> None:
        self.assertIn("intent_churn_ablation_train_only", SEARCH_SPACE_MODES)
        self.assertIn("ralph_intent_reduce_cadence_train_only", SEARCH_SPACE_MODES)
        self.assertIn("paulwei_balanced_tranche_train_only", SEARCH_SPACE_MODES)
        self.assertIn("paulwei_dual_phase_train_only", SEARCH_SPACE_MODES)
        self.assertIn("paulwei_dual_phase_fee_repair_train_only", SEARCH_SPACE_MODES)
        self.assertIn("paulwei_dual_phase_spot_context_train_only", SEARCH_SPACE_MODES)
        self.assertIn("paulwei_dual_phase_spot_context_relaxed_fee_train_only", SEARCH_SPACE_MODES)
        self.assertIn("intent_churn_ablation_train_only", BASE_TIMEFRAME_SMOKE_MODES)
        self.assertTrue(
            smoke_uses_base_timeframe(budget="smoke", search_space_mode="ralph_intent_reduce_cadence_train_only")
        )
        self.assertTrue(
            smoke_uses_base_timeframe(budget="smoke", search_space_mode="paulwei_balanced_tranche_train_only")
        )
        self.assertTrue(smoke_uses_base_timeframe(budget="smoke", search_space_mode="paulwei_dual_phase_train_only"))
        self.assertTrue(
            smoke_uses_base_timeframe(budget="smoke", search_space_mode="paulwei_dual_phase_fee_repair_train_only")
        )
        self.assertTrue(
            smoke_uses_base_timeframe(
                budget="smoke",
                search_space_mode="paulwei_dual_phase_spot_context_train_only",
            )
        )
        self.assertTrue(
            smoke_uses_base_timeframe(
                budget="smoke",
                search_space_mode="paulwei_dual_phase_spot_context_relaxed_fee_train_only",
            )
        )
        self.assertFalse(smoke_uses_base_timeframe(budget="smoke", search_space_mode="baseline_anchored_train_only"))
        self.assertIn("intent_churn_ablation_search", STRICT_TRAIN_FEE_STOP_PROFILE_FLAGS)
        self.assertIn("ralph_intent_reduce_cadence_search", STRICT_TRAIN_TRUTH_PROFILE_FLAGS)
        self.assertIn("paulwei_balanced_tranche_search", STRICT_TRAIN_FEE_STOP_PROFILE_FLAGS)
        self.assertIn("paulwei_balanced_tranche_search", STRICT_TRAIN_TRUTH_PROFILE_FLAGS)
        self.assertIn("paulwei_dual_phase_search", STRICT_TRAIN_FEE_STOP_PROFILE_FLAGS)
        self.assertIn("paulwei_dual_phase_search", STRICT_TRAIN_TRUTH_PROFILE_FLAGS)
        self.assertIn("paulwei_dual_phase_fee_repair_search", STRICT_TRAIN_FEE_STOP_PROFILE_FLAGS)
        self.assertIn("paulwei_dual_phase_fee_repair_search", STRICT_TRAIN_TRUTH_PROFILE_FLAGS)
        self.assertIn("paulwei_dual_phase_spot_context_search", STRICT_TRAIN_FEE_STOP_PROFILE_FLAGS)
        self.assertIn("paulwei_dual_phase_spot_context_search", STRICT_TRAIN_TRUTH_PROFILE_FLAGS)
        self.assertEqual(DEFAULT_DEV_FEE_STABILITY_LIMITS.dev_fee_drag_fail_fast_cap, 0.075)
        self.assertEqual(RELAXED_DEV_FEE_STABILITY_LIMITS.dev_fee_drag_fail_fast_cap, 0.25)

    def test_hostile_constraint_violations_reject_aggressive_combo(self) -> None:
        params = {
            "target_vol_4h": 1.10,
            "max_leverage": 6.0,
            "pyramid_add_pct": 0.30,
            "pyramid_short_add_pct": 0.16,
            "dca_spacing_atr": 0.75,
            "dca_size_decay": 0.60,
            "flip_min_confidence": 0.55,
        }

        violations = hostile_constraint_violations(params)

        self.assertIn("high_target_vol_with_excess_leverage", violations)
        self.assertIn("aggressive_long_and_short_pyramiding", violations)
        self.assertIn("tight_dca_spacing_with_heavy_inventory_reloading", violations)
        self.assertFalse(is_hostile_aware_param_combo(params))

    def test_hostile_constraint_violations_allow_moderate_combo(self) -> None:
        params = {
            "target_vol_4h": 0.95,
            "max_leverage": 4.5,
            "pyramid_add_pct": 0.20,
            "pyramid_short_add_pct": 0.12,
            "dca_spacing_atr": 1.25,
            "dca_size_decay": 0.75,
            "flip_min_confidence": 0.65,
        }

        self.assertEqual(hostile_constraint_violations(params), [])
        self.assertTrue(is_hostile_aware_param_combo(params))

    def test_baseline_anchored_grid_contains_paulwei_anchor_values(self) -> None:
        grid = baseline_anchored_param_grid()
        self.assertIn(0.95, grid["vol_shock_threshold"])
        self.assertEqual(grid["pyramid_initial_pct"], [0.30])
        self.assertEqual(grid["pyramid_add_pct"], [0.30])
        self.assertIn(0.20, grid["pyramid_short_add_pct"])
        self.assertIn(0.50, grid["flip_min_confidence"])
        self.assertIn(1.00, grid["pyramid_spacing_atr"])
        self.assertIn(1.75, grid["dca_spacing_atr"])

    def test_search_space_profile_marks_baseline_anchored_mode(self) -> None:
        profile = search_space_profile(mode="baseline_anchored_train_only")
        self.assertEqual(profile["search_space_mode"], "baseline_anchored_train_only")
        self.assertTrue(profile["baseline_anchored_search"])
        self.assertFalse(profile["execution_inventory_rethink_search"])
        self.assertEqual(profile["baseline_anchor_variant"], "paulwei_baseline")

    def test_execution_inventory_rethink_grid_trains_churn_reduction_controls(self) -> None:
        grid = execution_inventory_rethink_param_grid()

        self.assertEqual(grid["pyramid_initial_pct"], [0.30])
        self.assertEqual(grid["pyramid_add_pct"], [0.30])
        self.assertEqual(grid["max_leverage"], [4.5])
        self.assertEqual(grid["pyramid_short_initial_pct"], [0.15])
        self.assertEqual(grid["dca_size_decay"], [0.90])
        self.assertTrue(all(value >= 1.00 for value in grid["pyramid_spacing_atr"]))
        self.assertTrue(all(value >= 1.25 for value in grid["dca_spacing_atr"]))
        self.assertTrue(all(value >= 0.70 for value in grid["flip_min_confidence"]))
        self.assertEqual(grid["hostile_behavior_router_enabled"], [0.0, 1.0])
        self.assertEqual(grid["squeeze_conditional_cooldown_enabled"], [0.0, 1.0])
        self.assertEqual(grid["liquidation_data_enabled"], [0.0])

    def test_search_space_profile_marks_execution_inventory_rethink_mode(self) -> None:
        profile = search_space_profile(mode="execution_inventory_rethink_train_only")

        self.assertEqual(profile["profile"], "execution_inventory_rethink_train_only")
        self.assertEqual(profile["search_space_mode"], "execution_inventory_rethink_train_only")
        self.assertFalse(profile["baseline_anchored_search"])
        self.assertTrue(profile["execution_inventory_rethink_search"])
        self.assertFalse(profile["include_liquidation_toggles"])

    def test_intent_churn_repair_grid_trains_strategy_intent_controls(self) -> None:
        grid = intent_churn_repair_param_grid()

        self.assertEqual(grid["max_leverage"], [4.5])
        self.assertEqual(grid["pyramid_initial_pct"], [0.30])
        self.assertEqual(grid["pyramid_add_pct"], [0.30])
        self.assertEqual(grid["pyramid_short_initial_pct"], [0.15])
        self.assertTrue(all(value <= 0.12 for value in grid["pyramid_short_add_pct"]))
        self.assertTrue(all(value >= 1.25 for value in grid["dca_spacing_atr"]))
        self.assertTrue(all(value >= 0.70 for value in grid["flip_min_confidence"]))
        self.assertEqual(grid["range_time_decay_bars"], [5, 8, 12])
        self.assertEqual(grid["preserve_capital_time_decay_bars"], [2, 3])
        self.assertEqual(grid["trade_directional_confidence_floor"], [0.55, 0.60, 0.65])
        self.assertEqual(grid["bearish_override_confidence_floor"], [0.55, 0.60, 0.65])
        self.assertEqual(grid["exit_transition_risk"], [0.85, 0.90])
        self.assertEqual(grid["liquidation_data_enabled"], [0.0])

    def test_search_space_profile_marks_intent_churn_repair_mode(self) -> None:
        profile = search_space_profile(mode="intent_churn_repair_train_only")

        self.assertEqual(profile["profile"], "intent_churn_repair_train_only")
        self.assertEqual(profile["search_space_mode"], "intent_churn_repair_train_only")
        self.assertFalse(profile["baseline_anchored_search"])
        self.assertFalse(profile["execution_inventory_rethink_search"])
        self.assertTrue(profile["intent_churn_repair_search"])
        self.assertFalse(profile["include_liquidation_toggles"])

    def test_intent_churn_ablation_grid_prioritizes_low_switch_intent(self) -> None:
        grid = intent_churn_ablation_param_grid()

        self.assertEqual(grid["target_vol_4h"], [0.30, 0.35, 0.40, 0.45, 0.50, 0.65])
        self.assertEqual(grid["max_leverage"], [2.0, 2.5, 3.0])
        self.assertEqual(grid["pyramid_add_pct"], [0.30])
        self.assertEqual(grid["pyramid_short_add_pct"], [0.10, 0.12])
        self.assertEqual(grid["range_time_decay_bars"], [8, 12, 16, 24])
        self.assertEqual(grid["preserve_capital_time_decay_bars"], [3, 4, 6])
        self.assertEqual(grid["range_trailing_stop_atr_mult"], [3.0, 3.5, 4.5])
        self.assertEqual(grid["preserve_capital_trailing_stop_atr_mult"], [2.0, 2.5, 3.0])
        self.assertEqual(grid["trade_directional_confidence_floor"][0], 0.50)
        self.assertEqual(grid["bearish_override_confidence_floor"][0], 0.50)
        self.assertEqual(grid["exit_transition_risk"][0], 0.80)
        self.assertEqual(grid["direction_signal_threshold"], [0.30, 0.35, 0.40, 0.45, 0.50, 0.55])
        self.assertEqual(grid["intent_add_signal_threshold"], [0.45, 0.50, 0.55, 0.60])
        self.assertEqual(grid["intent_add_confirmation_bars"], [1, 2, 4])
        self.assertEqual(grid["min_bars_before_trail"], [1])
        self.assertEqual(grid["min_hours_before_trail"], [0.0, 12.0, 20.0, 36.0])
        self.assertEqual(grid["intent_entry_confirmation_bars"], [3, 4])
        self.assertEqual(grid["intent_flip_confirmation_bars"], [6, 8, 12, 16])
        self.assertEqual(grid["intent_flat_confirmation_bars"], [8, 12, 16, 24])
        self.assertEqual(grid["intent_range_entry_confirmation_bars"], [4, 6, 8, 12])
        self.assertEqual(grid["intent_range_reentry_cooldown_bars"], [0, 12, 24, 48])
        self.assertEqual(grid["intent_bearish_override_confirmation_bars"], [0, 4, 8, 12])
        self.assertEqual(grid["intent_bearish_override_chop_guard_enabled"], [0.0, 1.0])
        self.assertEqual(grid["intent_bearish_override_chop_max_confidence"], [0.40, 0.50, 0.58])
        self.assertEqual(grid["intent_bearish_override_chop_min_transition_risk"], [0.40, 0.55, 0.65])
        self.assertEqual(grid["intent_unconfirmed_flip_reduce_only"], [0.0, 1.0])
        self.assertEqual(grid["intent_time_leverage_hold_enabled"], [1.0])
        self.assertEqual(grid["intent_time_leverage_min_hold_bars"], [168, 240, 336, 480])
        self.assertEqual(grid["intent_time_leverage_flip_signal_threshold"], [0.65, 0.75])
        self.assertEqual(grid["intent_time_leverage_trailing_stop_atr_mult"], [0.0])
        self.assertEqual(grid["intent_time_leverage_size_hysteresis_enabled"], [1.0])
        self.assertEqual(grid["intent_time_leverage_add_deadband_pct"], [0.25, 0.40, 0.60])
        self.assertEqual(grid["intent_time_leverage_reduce_deadband_pct"], [0.80, 1.00, 1.25])
        self.assertEqual(grid["intent_time_leverage_long_bias_enabled"], [1.0])
        self.assertEqual(grid["intent_time_leverage_long_bias_confidence_floor"], [0.65, 0.70])
        self.assertEqual(grid["intent_time_leverage_dip_accumulation_enabled"], [0.0, 1.0])
        self.assertEqual(grid["intent_time_leverage_dip_add_pct"], [0.10, 0.15])
        self.assertEqual(grid["hostile_behavior_router_enabled"], [0.0])
        self.assertEqual(grid["liquidation_data_enabled"], [0.0])

    def test_search_space_profile_marks_intent_churn_ablation_mode(self) -> None:
        profile = search_space_profile(mode="intent_churn_ablation_train_only")

        self.assertEqual(profile["profile"], "intent_churn_ablation_train_only")
        self.assertEqual(profile["search_space_mode"], "intent_churn_ablation_train_only")
        self.assertTrue(profile["intent_churn_ablation_search"])
        self.assertFalse(profile["intent_churn_repair_search"])
        self.assertFalse(profile["include_liquidation_toggles"])

    def test_binance_factor_weight_repair_grid_trains_audited_state_weights(self) -> None:
        grid = binance_factor_weight_repair_param_grid()

        self.assertEqual(grid["liquidation_data_enabled"], [0.0])
        self.assertEqual(grid["target_vol_4h"], [0.50, 0.65, 0.80])
        self.assertEqual(grid["max_leverage"], [2.5, 3.5, 4.5])
        self.assertEqual(grid["range_time_decay_bars"], [8, 12, 16])
        self.assertEqual(grid["trade_directional_confidence_floor"], [0.65, 0.70, 0.75, 0.80])
        self.assertEqual(grid["w_binance_metrics_state"], [0.0, 0.07, 0.12, 0.18, 0.25])
        self.assertEqual(grid["w_perp_carry"], [0.04, 0.08])
        self.assertEqual(grid["w_vol_structure"], [0.04, 0.08])
        self.assertEqual(grid["binance_metrics_state_enabled"], [0.0, 1.0])
        self.assertEqual(grid["binance_metrics_risk_sizer_enabled"], [0.0, 1.0])
        self.assertEqual(grid["binance_metrics_risk_edge_threshold"], [0.15, 0.25])
        self.assertEqual(grid["flip_min_confidence"], [0.70, 0.75, 0.80])
        self.assertEqual(grid["flip_flatten_only_enabled"], [0.0])
        self.assertEqual(grid["flip_reverse_open_scale"], [1.00])
        self.assertEqual(grid["loss_flip_reverse_open_confidence_floor"], [0.0])
        self.assertEqual(grid["target_delta_deadband_pct"], [0.0, 0.04, 0.06, 0.08])
        self.assertEqual(grid["target_reduce_deadband_pct"], [0.0])
        self.assertEqual(grid["min_hold_bars_before_flip"], [0, 2, 4])
        self.assertEqual(grid["entry_cooldown_bars"], [0, 1, 2])
        self.assertEqual(grid["weak_signal_no_flip_enabled"], [0.0, 1.0])
        self.assertEqual(grid["tiny_reverse_reduce_only_enabled"], [0.0, 1.0])
        self.assertEqual(grid["entry_confidence_floor"], [0.0, 0.75, 0.80, 0.85])
        self.assertEqual(grid["tiny_reverse_deadband_pct"], [0.05, 0.10, 0.20])
        self.assertEqual(grid["reverse_open_confidence_floor"], [0.0, 0.85, 0.90, 0.95])
        self.assertEqual(grid["range_entry_block_enabled"], [0.0, 1.0])
        self.assertEqual(grid["degraded_entry_block_enabled"], [0.0, 1.0])
        self.assertEqual(grid["loss_dca_confidence_floor"], [0.0, 0.80, 0.85])
        self.assertEqual(grid["loss_dca_range_block_enabled"], [0.0, 1.0])
        self.assertEqual(grid["loss_dca_degraded_block_enabled"], [0.0, 1.0])
        self.assertEqual(grid["profit_protect_reduce_enabled"], [0.0])
        self.assertEqual(grid["profit_protect_min_move_atr"], [0.75, 1.00, 1.25])
        self.assertEqual(grid["profit_protect_unwind_pct"], [0.20, 0.25, 0.30])
        self.assertEqual(grid["profit_protect_confidence_ceiling"], [0.70, 0.75, 0.80])

    def test_search_space_profile_marks_binance_factor_weight_repair_mode(self) -> None:
        profile = search_space_profile(mode="binance_factor_weight_repair_train_only")

        self.assertEqual(profile["profile"], "binance_factor_weight_repair_train_only")
        self.assertEqual(profile["search_space_mode"], "binance_factor_weight_repair_train_only")
        self.assertFalse(profile["baseline_anchored_search"])
        self.assertFalse(profile["intent_churn_repair_search"])
        self.assertTrue(profile["binance_factor_weight_repair_search"])
        self.assertFalse(profile["include_liquidation_toggles"])

    def test_ralph_protective_reduce_grid_targets_flatten_first_behavior(self) -> None:
        grid = ralph_protective_reduce_param_grid()

        self.assertEqual(grid["target_vol_4h"], [0.35, 0.40, 0.50, 0.65])
        self.assertEqual(grid["max_leverage"], [2.5, 3.0])
        self.assertEqual(grid["intent_unconfirmed_flip_reduce_only"], [1.0])
        self.assertEqual(grid["intent_flip_confirmation_bars"], [12, 16, 24])
        self.assertEqual(grid["intent_flat_confirmation_bars"], [4, 8, 12])
        self.assertEqual(grid["flip_flatten_only_enabled"], [1.0])
        self.assertEqual(grid["target_reduce_deadband_pct"], [0.0])
        self.assertEqual(grid["min_hold_bars_before_flip"], [4, 8, 12])
        self.assertEqual(grid["weak_signal_no_flip_enabled"], [1.0])
        self.assertEqual(grid["tiny_reverse_reduce_only_enabled"], [1.0])
        self.assertEqual(grid["range_entry_block_enabled"], [1.0])
        self.assertEqual(grid["degraded_entry_block_enabled"], [1.0])
        self.assertEqual(grid["loss_dca_range_block_enabled"], [1.0])
        self.assertEqual(grid["loss_dca_degraded_block_enabled"], [1.0])
        self.assertEqual(grid["profit_protect_reduce_enabled"], [1.0])

    def test_search_space_profile_marks_ralph_protective_reduce_mode(self) -> None:
        profile = search_space_profile(mode="ralph_protective_reduce_train_only")

        self.assertEqual(profile["profile"], "ralph_protective_reduce_train_only")
        self.assertEqual(profile["search_space_mode"], "ralph_protective_reduce_train_only")
        self.assertFalse(profile["baseline_anchored_search"])
        self.assertFalse(profile["intent_churn_ablation_search"])
        self.assertFalse(profile["binance_factor_weight_repair_search"])
        self.assertTrue(profile["ralph_protective_reduce_search"])
        self.assertFalse(profile["include_liquidation_toggles"])

    def test_ralph_protective_reduce_fee_repair_grid_tightens_fee_controls(self) -> None:
        grid = ralph_protective_reduce_fee_repair_param_grid()

        self.assertEqual(grid["target_vol_4h"], [0.35, 0.40, 0.50])
        self.assertEqual(grid["max_leverage"], [2.0, 2.5])
        self.assertEqual(grid["exit_unwind_pct"], [0.45, 0.55])
        self.assertEqual(grid["intent_add_signal_threshold"], [0.60, 0.65])
        self.assertEqual(grid["intent_add_confirmation_bars"], [2, 4])
        self.assertEqual(grid["intent_entry_confirmation_bars"], [6, 8])
        self.assertEqual(grid["intent_flip_confirmation_bars"], [16, 24])
        self.assertEqual(grid["intent_unconfirmed_flip_reduce_only"], [1.0])
        self.assertEqual(grid["flip_flatten_only_enabled"], [1.0])
        self.assertEqual(grid["target_delta_deadband_pct"], [0.08, 0.10])
        self.assertEqual(grid["target_reduce_deadband_pct"], [0.0])
        self.assertEqual(grid["entry_cooldown_bars"], [4, 8])
        self.assertEqual(grid["profit_protect_min_move_atr"], [1.25, 1.75, 2.25])
        self.assertEqual(grid["profit_protect_unwind_pct"], [0.35, 0.50])
        self.assertEqual(grid["profit_protect_confidence_ceiling"], [0.65, 0.75])

    def test_search_space_profile_marks_ralph_protective_reduce_fee_repair_mode(self) -> None:
        profile = search_space_profile(mode="ralph_protective_reduce_fee_repair_train_only")

        self.assertEqual(profile["profile"], "ralph_protective_reduce_fee_repair_train_only")
        self.assertEqual(profile["search_space_mode"], "ralph_protective_reduce_fee_repair_train_only")
        self.assertFalse(profile["ralph_protective_reduce_search"])
        self.assertTrue(profile["ralph_protective_reduce_fee_repair_search"])
        self.assertFalse(profile["binance_factor_weight_repair_search"])

    def test_ralph_execution_fee_cooldown_grid_adds_protective_reduce_throttle(self) -> None:
        grid = ralph_execution_fee_cooldown_param_grid()

        self.assertEqual(grid["target_vol_4h"], [0.40, 0.50, 0.65])
        self.assertEqual(grid["max_leverage"], [2.5, 3.0])
        self.assertEqual(grid["intent_unconfirmed_flip_reduce_only"], [1.0])
        self.assertEqual(grid["flip_flatten_only_enabled"], [1.0])
        self.assertEqual(grid["profit_protect_reduce_enabled"], [1.0])
        self.assertEqual(grid["profit_protect_cooldown_bars"], [6, 12, 24])
        self.assertEqual(grid["profit_protect_unwind_pct"], [0.25, 0.35, 0.50])
        self.assertEqual(grid["target_delta_deadband_pct"], [0.04, 0.06, 0.08])

    def test_search_space_profile_marks_ralph_execution_fee_cooldown_mode(self) -> None:
        profile = search_space_profile(mode="ralph_execution_fee_cooldown_train_only")

        self.assertEqual(profile["profile"], "ralph_execution_fee_cooldown_train_only")
        self.assertEqual(profile["search_space_mode"], "ralph_execution_fee_cooldown_train_only")
        self.assertFalse(profile["ralph_protective_reduce_search"])
        self.assertFalse(profile["ralph_protective_reduce_fee_repair_search"])
        self.assertTrue(profile["ralph_execution_fee_cooldown_search"])
        self.assertFalse(profile["ralph_intent_reduce_cadence_search"])

    def test_ralph_intent_reduce_cadence_grid_targets_strategy_reduce_confirmation(self) -> None:
        grid = ralph_intent_reduce_cadence_param_grid()

        self.assertEqual(grid["target_vol_4h"], [0.40, 0.50, 0.65])
        self.assertEqual(grid["max_leverage"], [2.5, 3.0])
        self.assertEqual(grid["intent_unconfirmed_flip_reduce_only"], [1.0])
        self.assertEqual(grid["flip_flatten_only_enabled"], [1.0])
        self.assertEqual(grid["profit_protect_reduce_enabled"], [1.0])
        self.assertEqual(grid["profit_protect_cooldown_bars"], [0])
        self.assertEqual(grid["intent_flat_reduce_step_pct"], [0.0, 0.15, 0.25, 0.35, 0.50])
        self.assertEqual(grid["intent_flat_step_cadence_bars"], [0, 8, 12, 24])
        self.assertEqual(grid["intent_same_direction_reduce_confirmation_bars"], [2, 4, 8, 12])
        self.assertEqual(grid["intent_time_leverage_reduce_deadband_pct"], [0.40, 0.80, 1.00])
        self.assertEqual(grid["target_delta_deadband_pct"], [0.04, 0.06, 0.08])

    def test_search_space_profile_marks_ralph_intent_reduce_cadence_mode(self) -> None:
        profile = search_space_profile(mode="ralph_intent_reduce_cadence_train_only")

        self.assertEqual(profile["profile"], "ralph_intent_reduce_cadence_train_only")
        self.assertEqual(profile["search_space_mode"], "ralph_intent_reduce_cadence_train_only")
        self.assertFalse(profile["ralph_protective_reduce_search"])
        self.assertFalse(profile["ralph_protective_reduce_fee_repair_search"])
        self.assertFalse(profile["ralph_execution_fee_cooldown_search"])
        self.assertTrue(profile["ralph_intent_reduce_cadence_search"])

    def test_paulwei_balanced_tranche_grid_targets_strategy_tranche_intent(self) -> None:
        grid = paulwei_balanced_tranche_param_grid()

        self.assertEqual(grid["target_vol_4h"], [0.40, 0.50, 0.65])
        self.assertEqual(grid["max_leverage"], [2.5, 3.0])
        self.assertEqual(grid["intent_add_signal_threshold"], [0.30, 0.35, 0.40, 0.45, 0.50])
        self.assertEqual(grid["intent_add_confirmation_bars"], [1, 2])
        self.assertEqual(grid["intent_flip_confirmation_bars"], [12, 16, 24])
        self.assertEqual(grid["intent_time_leverage_add_deadband_pct"], [0.10, 0.15, 0.25])
        self.assertEqual(grid["intent_time_leverage_reduce_deadband_pct"], [0.60, 0.80, 1.00])
        self.assertEqual(grid["intent_time_leverage_dip_accumulation_enabled"], [1.0])
        self.assertEqual(grid["intent_flat_reduce_step_pct"], [0.15, 0.25, 0.35])
        self.assertEqual(grid["intent_flat_step_cadence_bars"], [8, 12, 24])
        self.assertEqual(grid["intent_same_direction_reduce_confirmation_bars"], [4, 8, 12])
        self.assertEqual(grid["range_time_decay_bars"], [48, 96, 168])
        self.assertEqual(grid["preserve_capital_time_decay_bars"], [12, 24, 48])
        self.assertEqual(grid["time_decay_exit_enabled"], [1.0])
        self.assertEqual(grid["time_decay_long_loss_threshold"], [-0.08, -0.12])
        self.assertEqual(grid["time_decay_short_loss_threshold"], [-0.04, -0.06, -0.08])
        self.assertEqual(grid["intent_unconfirmed_flip_reduce_only"], [0.0])
        self.assertEqual(grid["flip_flatten_only_enabled"], [0.0])
        self.assertEqual(grid["target_delta_deadband_pct"], [0.0])
        self.assertEqual(grid["weak_signal_no_flip_enabled"], [0.0])
        self.assertEqual(grid["profit_protect_reduce_enabled"], [0.0])

    def test_search_space_profile_marks_paulwei_balanced_tranche_mode(self) -> None:
        profile = search_space_profile(mode="paulwei_balanced_tranche_train_only")

        self.assertEqual(profile["profile"], "paulwei_balanced_tranche_train_only")
        self.assertEqual(profile["search_space_mode"], "paulwei_balanced_tranche_train_only")
        self.assertFalse(profile["intent_churn_ablation_search"])
        self.assertFalse(profile["ralph_intent_reduce_cadence_search"])
        self.assertTrue(profile["paulwei_balanced_tranche_search"])

    def test_paulwei_dual_phase_grid_targets_expansion_and_control_templates(self) -> None:
        grid = paulwei_dual_phase_param_grid()
        seeds = paulwei_dual_phase_seed_trials()

        self.assertEqual(grid["paulwei_dual_phase_objective_enabled"], [1.0])
        self.assertEqual(grid["max_leverage"], [2.5, 3.0])
        self.assertEqual(grid["intent_time_leverage_min_hold_bars"], [336, 480, 672])
        self.assertEqual(grid["intent_time_leverage_dip_accumulation_enabled"], [1.0])
        self.assertEqual(grid["intent_time_leverage_reduce_deadband_pct"], [0.80, 1.00, 1.25])
        self.assertEqual(grid["intent_flip_confirmation_bars"], [16, 24, 36])
        self.assertEqual(grid["target_delta_deadband_pct"], [0.0])
        self.assertEqual(grid["profit_protect_reduce_enabled"], [0.0])
        self.assertGreaterEqual(len(seeds), 4)
        self.assertTrue(all(seed["paulwei_dual_phase_objective_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(all(seed["max_leverage"] <= 3.0 for seed in seeds))

    def test_search_space_profile_marks_paulwei_dual_phase_mode(self) -> None:
        profile = search_space_profile(mode="paulwei_dual_phase_train_only")

        self.assertEqual(profile["profile"], "paulwei_dual_phase_train_only")
        self.assertEqual(profile["search_space_mode"], "paulwei_dual_phase_train_only")
        self.assertFalse(profile["paulwei_balanced_tranche_search"])
        self.assertTrue(profile["paulwei_dual_phase_search"])
        self.assertTrue(profile["paulwei_phase_learning_contract"]["enabled"])
        self.assertEqual(
            profile["paulwei_phase_learning_contract"]["templates"],
            ["early_convexity_usdt_projection", "mature_low_switch_control"],
        )

    def test_paulwei_dual_phase_fee_repair_grid_targets_entry_exit_fee_drag(self) -> None:
        grid = paulwei_dual_phase_fee_repair_param_grid()
        seeds = paulwei_dual_phase_fee_repair_seed_trials()

        self.assertEqual(grid["paulwei_dual_phase_objective_enabled"], [1.0])
        self.assertEqual(grid["target_vol_4h"], [0.45, 0.50])
        self.assertEqual(grid["target_vol_5m"], [0.60, 0.65, 0.70])
        self.assertEqual(grid["max_leverage"], [3.0])
        self.assertEqual(grid["direction_signal_threshold"], [0.40, 0.45])
        self.assertEqual(grid["intent_add_signal_threshold"], [0.35, 0.40])
        self.assertEqual(grid["intent_entry_confirmation_bars"], [4, 6])
        self.assertEqual(grid["intent_flat_confirmation_bars"], [16, 24])
        self.assertEqual(grid["intent_time_leverage_min_hold_bars"], [480])
        self.assertEqual(grid["intent_time_leverage_reduce_deadband_pct"], [1.00])
        self.assertEqual(grid["intent_flat_step_cadence_bars"], [24])
        self.assertEqual(grid["range_time_decay_bars"], [168])
        self.assertEqual(grid["target_delta_deadband_pct"], [0.0])
        self.assertEqual(grid["profit_protect_reduce_enabled"], [0.0])
        self.assertGreaterEqual(len(seeds), 4)
        self.assertTrue(all(seed["paulwei_dual_phase_objective_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(all(seed["target_vol_4h"] in {0.45, 0.50} for seed in seeds))
        self.assertTrue(all(seed["target_vol_5m"] in {0.60, 0.65, 0.70} for seed in seeds))
        self.assertTrue(all(seed["max_leverage"] == 3.0 for seed in seeds))
        self.assertTrue(all(seed["intent_add_signal_threshold"] <= 0.40 for seed in seeds))
        self.assertTrue(all(seed["intent_flat_step_cadence_bars"] == 24 for seed in seeds))

    def test_search_space_profile_marks_paulwei_dual_phase_fee_repair_mode(self) -> None:
        profile = search_space_profile(mode="paulwei_dual_phase_fee_repair_train_only")

        self.assertEqual(profile["profile"], "paulwei_dual_phase_fee_repair_train_only")
        self.assertEqual(profile["search_space_mode"], "paulwei_dual_phase_fee_repair_train_only")
        self.assertFalse(profile["paulwei_balanced_tranche_search"])
        self.assertTrue(profile["paulwei_dual_phase_search"])
        self.assertTrue(profile["paulwei_dual_phase_fee_repair_search"])
        self.assertTrue(profile["paulwei_phase_learning_contract"]["enabled"])
        self.assertEqual(
            profile["paulwei_phase_learning_contract"]["templates"],
            ["early_convexity_usdt_projection", "mature_low_switch_control", "dual_phase_fee_repair"],
        )

    def test_paulwei_dual_phase_spot_context_grid_targets_profit_running(self) -> None:
        grid = paulwei_dual_phase_spot_context_param_grid()
        seeds = paulwei_dual_phase_spot_context_seed_trials()

        self.assertEqual(grid["paulwei_dual_phase_objective_enabled"], [1.0])
        self.assertEqual(grid["target_vol_4h"], [0.50, 0.65, 0.80, 0.95])
        self.assertEqual(grid["target_vol_5m"], [0.65, 0.70, 0.75, 0.80])
        self.assertEqual(grid["max_leverage"], [3.0])
        self.assertEqual(grid["direction_signal_threshold"], [0.25, 0.30, 0.35, 0.40])
        self.assertEqual(grid["intent_add_signal_threshold"], [0.25, 0.30, 0.35, 0.40])
        self.assertEqual(grid["intent_flip_confirmation_bars"], [36, 48, 72, 96, 120])
        self.assertEqual(grid["intent_time_leverage_min_hold_bars"], [672, 960, 1344])
        self.assertEqual(grid["intent_time_leverage_reduce_deadband_pct"], [1.00, 1.25, 1.50, 1.75])
        self.assertEqual(grid["time_decay_long_loss_threshold"], [-0.12, -0.16])
        self.assertGreaterEqual(len(seeds), 24)
        self.assertTrue(all(seed["paulwei_dual_phase_objective_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(all(seed["max_leverage"] == 3.0 for seed in seeds))
        self.assertGreater(max(seed["intent_time_leverage_min_hold_bars"] for seed in seeds), 480)
        self.assertTrue(all(seed.get("intent_time_leverage_long_bias_enabled") == 1.0 for seed in seeds[:12]))
        self.assertTrue(any(seed["target_vol_4h"] == 0.50 for seed in seeds[:12]))
        self.assertTrue(any(seed["target_vol_4h"] == 0.95 for seed in seeds[:12]))
        self.assertTrue(any(seed["intent_bearish_override_confirmation_bars"] == 8 for seed in seeds[:6]))

    def test_search_space_profile_marks_paulwei_dual_phase_spot_context_mode(self) -> None:
        profile = search_space_profile(mode="paulwei_dual_phase_spot_context_train_only")

        self.assertEqual(profile["profile"], "paulwei_dual_phase_spot_context_train_only")
        self.assertEqual(profile["search_space_mode"], "paulwei_dual_phase_spot_context_train_only")
        self.assertTrue(profile["paulwei_dual_phase_search"])
        self.assertFalse(profile["paulwei_dual_phase_fee_repair_search"])
        self.assertTrue(profile["paulwei_dual_phase_spot_context_search"])
        self.assertEqual(
            profile["paulwei_phase_learning_contract"]["templates"],
            [
                "early_convexity_usdt_projection",
                "mature_low_switch_control",
                "dual_phase_fee_repair",
                "spot_era_context_profit_run",
            ],
        )

    def test_search_space_profile_marks_paulwei_dual_phase_relaxed_fee_probe_mode(self) -> None:
        profile = search_space_profile(mode="paulwei_dual_phase_spot_context_relaxed_fee_train_only")
        grid = paulwei_dual_phase_spot_context_relaxed_fee_param_grid()
        seeds = paulwei_dual_phase_spot_context_relaxed_fee_seed_trials()

        self.assertEqual(profile["profile"], "paulwei_dual_phase_spot_context_relaxed_fee_train_only")
        self.assertTrue(profile["paulwei_dual_phase_search"])
        self.assertTrue(profile["paulwei_dual_phase_spot_context_search"])
        self.assertTrue(profile["paulwei_dual_phase_spot_context_relaxed_fee_search"])
        self.assertTrue(profile["research_relaxed_fee_probe"])
        self.assertIn("research_relaxed_fee_probe", profile["paulwei_phase_learning_contract"]["templates"])
        self.assertEqual(grid["research_relaxed_fee_probe_enabled"], [1.0])
        self.assertIn(1.25, grid["target_vol_4h"])
        self.assertIn(4.0, grid["max_leverage"])
        self.assertIn(36, grid["intent_flip_confirmation_bars"])
        self.assertIn(672, grid["intent_time_leverage_min_hold_bars"])
        self.assertIn(4, grid["intent_bearish_override_confirmation_bars"])
        self.assertIn(0.50, grid["intent_time_leverage_long_bias_confidence_floor"])
        self.assertIn(0.65, grid["intent_time_leverage_long_bias_transition_ceiling"])
        self.assertIn(0.25, grid["intent_time_leverage_dip_add_pct"])
        self.assertIn(1.0, grid["multi_momentum_range_profit_run_enabled"])
        self.assertIn(0.15, grid["multi_momentum_range_profit_run_threshold"])
        self.assertIn(0.45, grid["multi_momentum_range_profit_run_max_transition_risk"])
        self.assertIn(1.00, grid["multi_momentum_range_profit_run_min_cycle_position"])
        self.assertIn(1.0, grid["multi_momentum_range_profit_run_cycle_fallback_enabled"])
        self.assertIn(0.10, grid["multi_momentum_range_profit_run_cycle_momentum_threshold"])
        self.assertIn(-0.25, grid["multi_momentum_range_profit_run_min_state_direction_signal"])
        self.assertIn(0.0, grid["intent_time_leverage_long_bias_block_short"])
        self.assertTrue(all(seed["research_relaxed_fee_probe_enabled"] == 1.0 for seed in seeds[:12]))
        self.assertTrue(any(seed["max_leverage"] == 4.0 for seed in seeds[:12]))
        self.assertTrue(any(seed["intent_flip_confirmation_bars"] == 36 for seed in seeds[:12]))
        self.assertTrue(any(seed["intent_time_leverage_min_hold_bars"] == 672 for seed in seeds[:12]))
        self.assertTrue(any(seed["intent_time_leverage_long_bias_confidence_floor"] == 0.50 for seed in seeds[:12]))
        self.assertTrue(any(seed["intent_time_leverage_dip_add_pct"] == 0.25 for seed in seeds[:12]))
        self.assertTrue(any(seed["multi_momentum_range_profit_run_enabled"] == 1.0 for seed in seeds[:12]))
        self.assertTrue(any(seed["multi_momentum_range_profit_run_threshold"] == 0.15 for seed in seeds[:12]))
        self.assertTrue(any(seed["multi_momentum_range_profit_run_max_transition_risk"] == 0.45 for seed in seeds[:12]))
        self.assertTrue(any(seed["multi_momentum_range_profit_run_min_cycle_position"] == 1.00 for seed in seeds[:12]))
        self.assertTrue(
            any(seed["multi_momentum_range_profit_run_cycle_fallback_enabled"] == 1.0 for seed in seeds[:12])
        )
        self.assertTrue(
            any(seed["multi_momentum_range_profit_run_cycle_momentum_threshold"] == 0.10 for seed in seeds[:12])
        )
        self.assertTrue(
            any(seed["multi_momentum_range_profit_run_min_state_direction_signal"] == -0.25 for seed in seeds[:12])
        )
        self.assertTrue(any(seed["intent_time_leverage_long_bias_block_short"] == 0.0 for seed in seeds[:12]))

    def test_spot_context_score_penalizes_bull_undercapture(self) -> None:
        weak_bull = {
            "spot_context_full_2017_2019": {
                "equity_multiple": 1.21,
                "max_drawdown": 0.05,
                "sharpe": 2.4,
            },
            "spot_context_bull_expansion_2017": {
                "equity_multiple": 1.01,
                "fee_drag_pct": 0.028,
            },
            "spot_context_bear_crash_2018": {
                "max_drawdown": 0.03,
                "sharpe": 0.4,
                "fee_drag_pct": 0.078,
            },
            "spot_context_recovery_2019": {
                "equity_multiple": 1.19,
            },
        }
        captured_bull = {
            **weak_bull,
            "spot_context_bull_expansion_2017": {
                "equity_multiple": 1.12,
                "fee_drag_pct": 0.012,
            },
            "spot_context_bear_crash_2018": {
                "max_drawdown": 0.03,
                "sharpe": 0.4,
                "fee_drag_pct": 0.030,
            },
        }

        self.assertLess(_spot_context_score(weak_bull), 0.0)
        self.assertGreater(_spot_context_score(captured_bull), _spot_context_score(weak_bull))
        self.assertGreater(_spot_context_score(captured_bull), 0.0)

    def test_build_signal_weight_overrides_maps_audited_state_factor_weights(self) -> None:
        weights = build_signal_weight_overrides(
            {
                "w_binance_metrics_state": 0.18,
                "w_onchain_activity": 0.07,
                "w_alt_risk_context": 0.05,
                "w_cross_asset_flow": 0.04,
            }
        )

        self.assertEqual(weights["binance_metrics_state_signal"], 0.18)
        self.assertEqual(weights["onchain_activity_signal"], 0.07)
        self.assertEqual(weights["alt_risk_context_signal"], 0.05)
        self.assertEqual(weights["cross_asset_flow_signal"], 0.04)

    def test_build_plugin_config_overrides_enables_binance_factor_research_plugins(self) -> None:
        overrides = build_plugin_config_overrides(
            {
                "target_vol_5m": 0.65,
                "target_vol_4h": 0.45,
                "w_binance_metrics_state": 0.18,
                "binance_metrics_state_enabled": 1.0,
                "binance_metrics_risk_sizer_enabled": 1.0,
                "binance_metrics_risk_edge_threshold": 0.15,
                "binance_metrics_risk_discount_floor": 0.60,
                "exit_transition_risk": 0.90,
                "time_decay_exit_enabled": 1.0,
                "time_decay_long_loss_threshold": -0.08,
                "time_decay_short_loss_threshold": -0.06,
                "multi_momentum_range_profit_run_enabled": 1.0,
                "multi_momentum_range_profit_run_threshold": 0.20,
                "multi_momentum_range_profit_run_value": 0.85,
                "multi_momentum_range_profit_run_confidence": 0.65,
                "multi_momentum_range_profit_run_max_transition_risk": 0.45,
                "multi_momentum_range_profit_run_min_cycle_position": 1.00,
                "multi_momentum_range_profit_run_block_distorted_volatility": 1.0,
                "multi_momentum_range_profit_run_block_unsafe_quality": 1.0,
                "multi_momentum_range_profit_run_cycle_fallback_enabled": 1.0,
                "multi_momentum_range_profit_run_cycle_threshold": 1.00,
                "multi_momentum_range_profit_run_cycle_momentum_threshold": 0.10,
                "multi_momentum_range_profit_run_block_confirmed_downtrend": 1.0,
                "multi_momentum_range_profit_run_min_state_direction_signal": -0.25,
            }
        )

        self.assertTrue(overrides["signals"]["binance_metrics_state_signal"]["enabled"])
        self.assertEqual(overrides["signals"]["binance_metrics_state_signal"]["weight"], 0.18)
        self.assertEqual(overrides["exits"]["regime_change_exit"]["transition_risk_threshold"], 0.90)
        self.assertTrue(overrides["exits"]["time_decay_exit"]["enabled"])
        self.assertEqual(overrides["exits"]["time_decay_exit"]["long_loss_threshold"], -0.08)
        self.assertEqual(overrides["exits"]["time_decay_exit"]["short_loss_threshold"], -0.06)
        self.assertEqual(overrides["sizers"]["vol_target_sizer"]["target_vol_5m"], 0.65)
        self.assertEqual(overrides["sizers"]["vol_target_sizer"]["target_vol_4h"], 0.45)
        self.assertTrue(overrides["sizers"]["binance_metrics_risk_sizer"]["enabled"])
        self.assertEqual(overrides["sizers"]["binance_metrics_risk_sizer"]["edge_threshold"], 0.15)
        self.assertEqual(overrides["sizers"]["binance_metrics_risk_sizer"]["discount_floor"], 0.60)
        self.assertTrue(overrides["signals"]["multi_momentum_signal"]["range_profit_run_enabled"])
        self.assertEqual(overrides["signals"]["multi_momentum_signal"]["range_profit_run_threshold"], 0.20)
        self.assertEqual(overrides["signals"]["multi_momentum_signal"]["range_profit_run_value"], 0.85)
        self.assertEqual(overrides["signals"]["multi_momentum_signal"]["range_profit_run_confidence"], 0.65)
        self.assertEqual(overrides["signals"]["multi_momentum_signal"]["range_profit_run_max_transition_risk"], 0.45)
        self.assertEqual(overrides["signals"]["multi_momentum_signal"]["range_profit_run_min_cycle_position"], 1.00)
        self.assertTrue(overrides["signals"]["multi_momentum_signal"]["range_profit_run_block_distorted_volatility"])
        self.assertTrue(overrides["signals"]["multi_momentum_signal"]["range_profit_run_block_unsafe_quality"])
        self.assertTrue(overrides["signals"]["multi_momentum_signal"]["range_profit_run_cycle_fallback_enabled"])
        self.assertEqual(overrides["signals"]["multi_momentum_signal"]["range_profit_run_cycle_threshold"], 1.00)
        self.assertEqual(
            overrides["signals"]["multi_momentum_signal"]["range_profit_run_cycle_momentum_threshold"], 0.10
        )
        self.assertTrue(overrides["signals"]["multi_momentum_signal"]["range_profit_run_block_confirmed_downtrend"])
        self.assertEqual(
            overrides["signals"]["multi_momentum_signal"]["range_profit_run_min_state_direction_signal"], -0.25
        )

    def test_build_strategy_config_maps_intent_churn_controls(self) -> None:
        config = build_strategy_config(
            {
                "range_time_decay_bars": 8,
                "preserve_capital_time_decay_bars": 3,
                "range_trailing_stop_atr_mult": 3.5,
                "preserve_capital_trailing_stop_atr_mult": 2.0,
                "trade_directional_confidence_floor": 0.60,
                "bearish_override_confidence_floor": 0.65,
                "exit_transition_risk": 0.90,
                "intent_add_confirmation_bars": 4,
                "min_bars_before_trail": 72,
                "min_hours_before_trail": 20.0,
                "intent_flat_reduce_step_pct": 0.50,
                "intent_flat_step_cadence_bars": 8,
                "intent_range_entry_confirmation_bars": 36,
                "intent_range_reentry_cooldown_bars": 72,
                "intent_bearish_override_confirmation_bars": 48,
                "intent_time_leverage_hold_enabled": 1.0,
                "intent_time_leverage_min_hold_bars": 24,
                "intent_time_leverage_flip_signal_threshold": 0.65,
                "intent_time_leverage_confidence_floor": 0.65,
                "intent_time_leverage_transition_risk_floor": 0.45,
                "intent_time_leverage_trailing_stop_atr_mult": 9.0,
                "intent_time_leverage_trailing_exit_hold_enabled": 1.0,
                "intent_time_leverage_trailing_exit_max_adverse_atr": 2.0,
                "intent_same_direction_reduce_confirmation_bars": 4,
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
            }
        )

        self.assertIsNotNone(config.playbook_config)
        self.assertIsNotNone(config.exit_config)
        assert config.playbook_config is not None
        assert config.exit_config is not None
        self.assertEqual(config.playbook_config.range_time_decay_bars, 8)
        self.assertEqual(config.playbook_config.preserve_capital_time_decay_bars, 3)
        self.assertEqual(config.playbook_config.range_trailing_stop_atr_mult, 3.5)
        self.assertEqual(config.playbook_config.preserve_capital_trailing_stop_atr_mult, 2.0)
        self.assertEqual(config.playbook_config.trade_directional_confidence_floor, 0.60)
        self.assertEqual(config.playbook_config.bearish_override_confidence_floor, 0.65)
        self.assertEqual(config.exit_config.transition_risk_exit_threshold, 0.90)
        self.assertEqual(config.intent_add_confirmation_bars, 4)
        self.assertEqual(config.min_bars_before_trail, 72)
        self.assertEqual(config.min_hours_before_trail, 20.0)
        self.assertEqual(config.intent_flat_reduce_step_pct, 0.50)
        self.assertEqual(config.intent_flat_step_cadence_bars, 8)
        self.assertEqual(config.intent_range_entry_confirmation_bars, 36)
        self.assertEqual(config.intent_range_reentry_cooldown_bars, 72)
        self.assertEqual(config.intent_bearish_override_confirmation_bars, 48)
        self.assertTrue(config.intent_time_leverage_hold_enabled)
        self.assertEqual(config.intent_time_leverage_min_hold_bars, 24)
        self.assertEqual(config.intent_time_leverage_flip_signal_threshold, 0.65)
        self.assertEqual(config.intent_time_leverage_confidence_floor, 0.65)
        self.assertEqual(config.intent_time_leverage_transition_risk_floor, 0.45)
        self.assertEqual(config.intent_time_leverage_trailing_stop_atr_mult, 9.0)
        self.assertTrue(config.intent_time_leverage_trailing_exit_hold_enabled)
        self.assertEqual(config.intent_time_leverage_trailing_exit_max_adverse_atr, 2.0)
        self.assertEqual(config.intent_same_direction_reduce_confirmation_bars, 4)
        self.assertTrue(config.intent_time_leverage_size_hysteresis_enabled)
        self.assertEqual(config.intent_time_leverage_add_deadband_pct, 0.15)
        self.assertEqual(config.intent_time_leverage_reduce_deadband_pct, 0.30)
        self.assertTrue(config.intent_time_leverage_long_bias_enabled)
        self.assertEqual(config.intent_time_leverage_long_bias_confidence_floor, 0.70)
        self.assertEqual(config.intent_time_leverage_long_bias_transition_ceiling, 0.35)
        self.assertTrue(config.intent_time_leverage_dip_accumulation_enabled)
        self.assertEqual(config.intent_time_leverage_dip_min_adverse_atr, 1.25)
        self.assertEqual(config.intent_time_leverage_dip_max_adverse_atr, 3.0)
        self.assertEqual(config.intent_time_leverage_dip_add_pct, 0.15)
        self.assertEqual(config.intent_time_leverage_dip_max_leverage, 3.0)

    def test_autoresearch_strategy_config_accepts_shared_intent_churn_config(self) -> None:
        config = _build_strategy_config(
            {
                "range_time_decay_bars": 8,
                "preserve_capital_time_decay_bars": 3,
                "trade_directional_confidence_floor": 0.60,
                "bearish_override_confidence_floor": 0.65,
                "exit_transition_risk": 0.90,
                "intent_add_confirmation_bars": 4,
                "min_bars_before_trail": 24,
                "min_hours_before_trail": 12.0,
                "intent_flat_reduce_step_pct": 0.35,
                "intent_flat_step_cadence_bars": 6,
                "intent_range_entry_confirmation_bars": 18,
                "intent_range_reentry_cooldown_bars": 36,
                "intent_bearish_override_confirmation_bars": 24,
                "intent_time_leverage_hold_enabled": 1.0,
                "intent_time_leverage_min_hold_bars": 12,
                "intent_time_leverage_trailing_stop_atr_mult": 6.0,
                "intent_time_leverage_trailing_exit_hold_enabled": 1.0,
                "intent_time_leverage_trailing_exit_max_adverse_atr": 1.5,
                "intent_same_direction_reduce_confirmation_bars": 3,
                "intent_time_leverage_size_hysteresis_enabled": 1.0,
                "intent_time_leverage_reduce_deadband_pct": 0.20,
                "intent_time_leverage_long_bias_enabled": 1.0,
                "intent_time_leverage_dip_accumulation_enabled": 1.0,
            },
            execution_timeframe=Timeframe.TF_4H,
        )

        self.assertEqual(config.execution_timeframe, Timeframe.TF_4H)
        self.assertEqual(config.intent_add_confirmation_bars, 4)
        self.assertEqual(config.min_bars_before_trail, 24)
        self.assertEqual(config.min_hours_before_trail, 12.0)
        self.assertEqual(config.intent_flat_reduce_step_pct, 0.35)
        self.assertEqual(config.intent_flat_step_cadence_bars, 6)
        self.assertEqual(config.intent_range_entry_confirmation_bars, 18)
        self.assertEqual(config.intent_range_reentry_cooldown_bars, 36)
        self.assertEqual(config.intent_bearish_override_confirmation_bars, 24)
        self.assertTrue(config.intent_time_leverage_hold_enabled)
        self.assertEqual(config.intent_time_leverage_min_hold_bars, 12)
        self.assertEqual(config.intent_time_leverage_trailing_stop_atr_mult, 6.0)
        self.assertTrue(config.intent_time_leverage_trailing_exit_hold_enabled)
        self.assertEqual(config.intent_time_leverage_trailing_exit_max_adverse_atr, 1.5)
        self.assertEqual(config.intent_same_direction_reduce_confirmation_bars, 3)
        self.assertTrue(config.intent_time_leverage_size_hysteresis_enabled)
        self.assertEqual(config.intent_time_leverage_reduce_deadband_pct, 0.20)
        self.assertTrue(config.intent_time_leverage_long_bias_enabled)
        self.assertTrue(config.intent_time_leverage_dip_accumulation_enabled)
        self.assertIsNotNone(config.playbook_config)
        self.assertIsNotNone(config.exit_config)
        assert config.playbook_config is not None
        assert config.exit_config is not None
        self.assertEqual(config.playbook_config.range_time_decay_bars, 8)
        self.assertEqual(config.playbook_config.preserve_capital_time_decay_bars, 3)
        self.assertEqual(config.playbook_config.trade_directional_confidence_floor, 0.60)
        self.assertEqual(config.playbook_config.bearish_override_confidence_floor, 0.65)
        self.assertEqual(config.exit_config.transition_risk_exit_threshold, 0.90)

    def test_defensive_anchored_grid_freezes_long_side_and_only_widens_inventory_path(self) -> None:
        grid = baseline_anchored_defensive_param_grid()
        self.assertEqual(grid["pyramid_initial_pct"], [0.30])
        self.assertEqual(grid["pyramid_add_pct"], [0.30])
        self.assertTrue(all(value >= baseline_anchor_params()["dca_spacing_atr"] for value in grid["dca_spacing_atr"]))
        self.assertTrue(all(value >= baseline_anchor_params()["dca_size_decay"] for value in grid["dca_size_decay"]))
        self.assertTrue(
            all(value >= baseline_anchor_params()["flip_min_confidence"] for value in grid["flip_min_confidence"])
        )

    def test_search_space_profile_marks_defensive_anchored_mode(self) -> None:
        profile = search_space_profile(mode="baseline_anchored_defensive_candidate_existence")
        self.assertEqual(profile["search_space_mode"], "baseline_anchored_defensive_candidate_existence")
        self.assertTrue(profile["baseline_anchored_search"])

    def test_targeted_hostile_repair_grid_is_more_defensive_than_baseline_anchor(self) -> None:
        grid = baseline_anchored_2023_08_hostile_repair_param_grid()
        self.assertEqual(grid["pyramid_initial_pct"], [0.30])
        self.assertEqual(grid["pyramid_add_pct"], [0.30])
        self.assertEqual(grid["pyramid_short_initial_pct"], [0.15])
        self.assertEqual(grid["pyramid_short_add_pct"], [0.10, 0.12])
        self.assertEqual(grid["pyramid_spacing_atr"], [1.00])
        self.assertEqual(grid["exit_unwind_pct"], [0.35])
        self.assertEqual(grid["max_leverage"], [4.5])
        self.assertTrue(all(value >= baseline_anchor_params()["dca_spacing_atr"] for value in grid["dca_spacing_atr"]))
        self.assertTrue(all(value >= baseline_anchor_params()["dca_size_decay"] for value in grid["dca_size_decay"]))
        self.assertTrue(all(value >= 0.60 for value in grid["flip_min_confidence"]))

    def test_search_space_profile_marks_targeted_hostile_repair_mode(self) -> None:
        profile = search_space_profile(mode="baseline_anchored_2023_08_hostile_repair")
        self.assertEqual(profile["search_space_mode"], "baseline_anchored_2023_08_hostile_repair")
        self.assertTrue(profile["baseline_anchored_search"])
        self.assertEqual(profile["hostile_window_focus"], "weak_window_2023_08")

    def test_baseline_anchor_seed_trials_start_with_exact_baseline(self) -> None:
        seeds = baseline_anchor_seed_trials()
        self.assertGreaterEqual(len(seeds), 1)
        self.assertEqual(seeds[0], baseline_anchor_params())

    def test_baseline_anchor_seed_trials_include_execution_contract_safe_churn_repair_seeds(self) -> None:
        seeds = baseline_anchor_seed_trials()
        baseline = baseline_anchor_params()

        self.assertIn(
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
            seeds,
        )
        self.assertIn(
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
            seeds,
        )

    def test_baseline_anchor_seed_trials_satisfy_execution_contract(self) -> None:
        for seed in baseline_anchor_seed_trials():
            build_execution_config(seed)

    def test_execution_inventory_rethink_seed_trials_satisfy_execution_contract(self) -> None:
        seeds = execution_inventory_rethink_seed_trials()

        self.assertGreaterEqual(len(seeds), 3)
        self.assertTrue(any(seed["hostile_behavior_router_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["squeeze_conditional_cooldown_enabled"] == 1.0 for seed in seeds))
        for seed in seeds:
            config = build_execution_config(seed)
            self.assertEqual(config.pyramid_initial_pct, 0.30)
            self.assertEqual(config.pyramid_add_pct, 0.30)
            self.assertLessEqual(config.pyramid_short_add_pct, 0.12)
            self.assertGreaterEqual(config.flip_min_confidence, 0.70)

    def test_intent_churn_repair_seed_trials_satisfy_strategy_and_execution_contracts(self) -> None:
        seeds = intent_churn_repair_seed_trials()

        self.assertEqual(len(seeds), 3)
        self.assertTrue(any(seed["range_time_decay_bars"] == 8 for seed in seeds))
        self.assertTrue(any(seed["exit_transition_risk"] == 0.90 for seed in seeds))
        self.assertTrue(any(seed["trade_directional_confidence_floor"] == 0.65 for seed in seeds))
        for seed in seeds:
            execution_config = build_execution_config(seed)
            strategy_config = build_strategy_config(seed)
            self.assertEqual(execution_config.pyramid_initial_pct, 0.30)
            self.assertEqual(execution_config.pyramid_add_pct, 0.30)
            self.assertLessEqual(execution_config.pyramid_short_add_pct, 0.12)
            self.assertGreaterEqual(execution_config.flip_min_confidence, 0.70)
            self.assertEqual(seed["liquidation_data_enabled"], 0.0)
            self.assertEqual(seed["state_liquidation_overlay_enabled"], 0.0)
            self.assertEqual(seed["liq_risk_guard_enabled"], 0.0)
            self.assertIsNotNone(strategy_config.playbook_config)
            self.assertIsNotNone(strategy_config.exit_config)

    def test_intent_churn_ablation_seed_trials_start_with_mature_low_switch_anchor(self) -> None:
        seeds = intent_churn_ablation_seed_trials()

        self.assertGreaterEqual(len(seeds), 12)
        self.assertEqual(seeds[0]["range_time_decay_bars"], 16)
        self.assertEqual(seeds[0]["preserve_capital_time_decay_bars"], 6)
        self.assertEqual(seeds[0]["trade_directional_confidence_floor"], 0.60)
        self.assertEqual(seeds[0]["bearish_override_confidence_floor"], 0.60)
        self.assertEqual(seeds[0]["target_vol_4h"], 0.40)
        self.assertEqual(seeds[0]["max_leverage"], 2.5)
        self.assertEqual(seeds[0]["pyramid_add_pct"], 0.30)
        self.assertEqual(seeds[0]["pyramid_short_add_pct"], 0.10)
        self.assertEqual(seeds[0]["range_trailing_stop_atr_mult"], 4.5)
        self.assertEqual(seeds[0]["preserve_capital_trailing_stop_atr_mult"], 3.0)
        self.assertEqual(seeds[0]["exit_transition_risk"], 0.90)
        self.assertEqual(seeds[0]["direction_signal_threshold"], 0.45)
        self.assertEqual(seeds[0]["intent_add_signal_threshold"], 0.50)
        self.assertEqual(seeds[0]["intent_add_confirmation_bars"], 2)
        self.assertEqual(seeds[0]["min_bars_before_trail"], 1)
        self.assertEqual(seeds[0]["min_hours_before_trail"], 20.0)
        self.assertEqual(seeds[0]["intent_entry_confirmation_bars"], 4)
        self.assertEqual(seeds[0]["intent_flip_confirmation_bars"], 8)
        self.assertEqual(seeds[0]["intent_flat_confirmation_bars"], 16)
        self.assertEqual(seeds[0]["intent_range_entry_confirmation_bars"], 6)
        self.assertEqual(seeds[0]["intent_range_reentry_cooldown_bars"], 24)
        self.assertEqual(seeds[0]["intent_bearish_override_confirmation_bars"], 4)
        self.assertEqual(seeds[0]["intent_bearish_override_chop_guard_enabled"], 0.0)
        self.assertEqual(seeds[0]["intent_bearish_override_chop_max_confidence"], 0.50)
        self.assertEqual(seeds[0]["intent_bearish_override_chop_min_transition_risk"], 0.55)
        self.assertEqual(seeds[0]["intent_unconfirmed_flip_reduce_only"], 0.0)
        self.assertEqual(seeds[0]["intent_time_leverage_hold_enabled"], 1.0)
        self.assertEqual(seeds[0]["intent_time_leverage_min_hold_bars"], 240)
        self.assertEqual(seeds[0]["intent_time_leverage_flip_signal_threshold"], 0.75)
        self.assertEqual(seeds[0]["intent_time_leverage_confidence_floor"], 0.70)
        self.assertEqual(seeds[0]["intent_time_leverage_trailing_stop_atr_mult"], 0.0)
        self.assertEqual(seeds[0]["intent_time_leverage_size_hysteresis_enabled"], 1.0)
        self.assertEqual(seeds[0]["intent_time_leverage_add_deadband_pct"], 0.40)
        self.assertEqual(seeds[0]["intent_time_leverage_reduce_deadband_pct"], 1.00)
        self.assertEqual(seeds[0]["intent_time_leverage_long_bias_enabled"], 1.0)
        self.assertEqual(seeds[0]["intent_time_leverage_dip_accumulation_enabled"], 1.0)
        self.assertEqual(seeds[1]["target_vol_4h"], 0.35)
        self.assertEqual(seeds[1]["range_time_decay_bars"], 16)
        self.assertEqual(seeds[1]["preserve_capital_time_decay_bars"], 6)
        self.assertEqual(seeds[1]["range_trailing_stop_atr_mult"], 4.5)
        self.assertEqual(seeds[1]["preserve_capital_trailing_stop_atr_mult"], 3.0)
        self.assertEqual(seeds[1]["exit_transition_risk"], 0.90)
        self.assertEqual(seeds[1]["intent_flip_confirmation_bars"], 12)
        self.assertEqual(seeds[1]["intent_range_entry_confirmation_bars"], 8)
        self.assertEqual(seeds[1]["intent_range_reentry_cooldown_bars"], 48)
        self.assertEqual(seeds[1]["intent_bearish_override_confirmation_bars"], 8)
        self.assertEqual(seeds[1]["intent_bearish_override_chop_guard_enabled"], 0.0)
        self.assertEqual(seeds[1]["intent_bearish_override_chop_max_confidence"], 0.40)
        self.assertEqual(seeds[1]["intent_bearish_override_chop_min_transition_risk"], 0.65)
        self.assertEqual(seeds[1]["intent_unconfirmed_flip_reduce_only"], 0.0)
        self.assertEqual(seeds[2]["target_vol_4h"], 0.30)
        self.assertEqual(seeds[2]["max_leverage"], 2.0)
        self.assertEqual(seeds[2]["intent_time_leverage_dip_max_leverage"], 2.0)
        self.assertEqual(seeds[3]["direction_signal_threshold"], 0.55)
        self.assertEqual(seeds[3]["intent_add_signal_threshold"], 0.60)
        self.assertEqual(seeds[1]["intent_time_leverage_min_hold_bars"], 336)
        self.assertTrue(any(seed["trade_directional_confidence_floor"] == 0.55 for seed in seeds))
        self.assertTrue(any(seed["bearish_override_confidence_floor"] == 0.55 for seed in seeds))
        self.assertTrue(any(seed["direction_signal_threshold"] == 0.35 for seed in seeds))
        self.assertTrue(any(seed["direction_signal_threshold"] == 0.55 for seed in seeds))
        self.assertTrue(any(seed["intent_add_signal_threshold"] == 0.55 for seed in seeds))
        self.assertTrue(any(seed["intent_add_signal_threshold"] == 0.60 for seed in seeds))
        self.assertTrue(any(seed["intent_add_confirmation_bars"] == 2 for seed in seeds))
        self.assertTrue(any(seed["intent_entry_confirmation_bars"] == 4 for seed in seeds))
        self.assertTrue(any(seed["intent_flip_confirmation_bars"] == 12 for seed in seeds))
        self.assertTrue(any(seed["intent_flat_confirmation_bars"] == 24 for seed in seeds))
        self.assertTrue(any(seed["intent_range_entry_confirmation_bars"] == 8 for seed in seeds))
        self.assertTrue(any(seed["intent_range_reentry_cooldown_bars"] == 48 for seed in seeds))
        self.assertTrue(any(seed["intent_bearish_override_confirmation_bars"] == 8 for seed in seeds))
        self.assertTrue(all("intent_bearish_override_chop_guard_enabled" in seed for seed in seeds))
        self.assertTrue(
            any(
                seed["target_vol_4h"] == 0.65
                and seed["intent_flat_confirmation_bars"] == 4
                and seed["intent_time_leverage_dip_add_pct"] == 0.10
                for seed in seeds
            )
        )
        self.assertTrue(any(seed["intent_time_leverage_hold_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["intent_time_leverage_min_hold_bars"] == 336 for seed in seeds))
        self.assertTrue(any(seed["intent_unconfirmed_flip_reduce_only"] == 0.0 for seed in seeds))
        self.assertTrue(any(seed["intent_time_leverage_size_hysteresis_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["intent_time_leverage_reduce_deadband_pct"] == 1.25 for seed in seeds))
        self.assertTrue(any(seed["intent_time_leverage_long_bias_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["intent_time_leverage_dip_accumulation_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(
            any(
                seed["intent_time_leverage_long_bias_enabled"] == 1.0
                and seed["intent_time_leverage_dip_accumulation_enabled"] == 0.0
                for seed in seeds[1:]
            )
        )
        self.assertTrue(any(seed["intent_time_leverage_dip_add_pct"] == 0.15 for seed in seeds))
        self.assertTrue(any(seed["target_vol_4h"] == 0.65 for seed in seeds))
        self.assertTrue(any(seed["target_vol_4h"] == 0.30 for seed in seeds))
        self.assertTrue(any(seed["target_vol_4h"] == 0.35 for seed in seeds))
        self.assertTrue(any(seed["target_vol_4h"] == 0.45 for seed in seeds))
        self.assertTrue(any(seed["max_leverage"] == 2.0 for seed in seeds))
        self.assertTrue(any(seed["max_leverage"] == 3.0 for seed in seeds))
        for seed in seeds:
            build_execution_config(seed)
            strategy_config = build_strategy_config(seed)
            self.assertEqual(seed["hostile_behavior_router_enabled"], 0.0)
            self.assertEqual(seed["liquidation_data_enabled"], 0.0)
            self.assertIsNotNone(strategy_config.playbook_config)
            self.assertIsNotNone(strategy_config.exit_config)
            self.assertGreaterEqual(strategy_config.direction_signal_threshold, 0.15)
            self.assertGreaterEqual(strategy_config.intent_add_signal_threshold, 0.0)
            self.assertGreaterEqual(strategy_config.intent_add_confirmation_bars, 1)
            self.assertGreaterEqual(strategy_config.intent_entry_confirmation_bars, 1)
            self.assertGreaterEqual(strategy_config.intent_flip_confirmation_bars, 1)
            self.assertGreaterEqual(strategy_config.intent_flat_confirmation_bars, 1)
            self.assertGreaterEqual(strategy_config.intent_range_entry_confirmation_bars, 0)
            self.assertGreaterEqual(strategy_config.intent_range_reentry_cooldown_bars, 0)
            self.assertGreaterEqual(strategy_config.intent_bearish_override_confirmation_bars, 0)
            self.assertGreaterEqual(strategy_config.intent_bearish_override_chop_max_confidence, 0.0)
            self.assertGreaterEqual(strategy_config.intent_bearish_override_chop_min_transition_risk, 0.0)
            self.assertGreaterEqual(strategy_config.intent_time_leverage_min_hold_bars, 0)
            self.assertGreaterEqual(strategy_config.intent_time_leverage_reduce_deadband_pct, 0.0)
            self.assertGreaterEqual(strategy_config.intent_time_leverage_long_bias_confidence_floor, 0.0)
            self.assertGreaterEqual(strategy_config.intent_time_leverage_dip_add_pct, 0.0)

    def test_binance_factor_weight_repair_seed_trials_include_neutral_and_active_weight_arms(self) -> None:
        seeds = binance_factor_weight_repair_seed_trials()

        self.assertGreaterEqual(len(seeds), 6)
        self.assertTrue(any(seed["w_binance_metrics_state"] == 0.0 for seed in seeds))
        self.assertTrue(any(seed["w_binance_metrics_state"] >= 0.18 for seed in seeds))
        self.assertTrue(any(seed["binance_metrics_risk_sizer_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["target_vol_4h"] == 0.50 for seed in seeds))
        self.assertTrue(any(seed["max_leverage"] == 2.5 for seed in seeds))
        self.assertTrue(any(seed["trade_directional_confidence_floor"] >= 0.75 for seed in seeds))
        self.assertTrue(any(seed["target_delta_deadband_pct"] == 0.06 for seed in seeds))
        self.assertTrue(any(seed["min_hold_bars_before_flip"] == 4 for seed in seeds))
        self.assertTrue(any(seed["weak_signal_no_flip_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["tiny_reverse_reduce_only_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["entry_confidence_floor"] == 0.85 for seed in seeds))
        self.assertTrue(any(seed["reverse_open_confidence_floor"] == 0.95 for seed in seeds))
        self.assertTrue(all(seed.get("flip_flatten_only_enabled", 0.0) == 0.0 for seed in seeds[:4]))
        self.assertTrue(all(seed["flip_reverse_open_scale"] == 1.0 for seed in seeds))
        self.assertEqual(seeds[0]["target_delta_deadband_pct"], 0.0)
        self.assertEqual(seeds[0]["target_reduce_deadband_pct"], 0.0)
        self.assertEqual(seeds[0]["min_hold_bars_before_flip"], 0)
        self.assertEqual(seeds[0]["entry_cooldown_bars"], 0)
        self.assertEqual(seeds[0]["reverse_open_confidence_floor"], 0.0)
        self.assertTrue(any(seed["range_entry_block_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["degraded_entry_block_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["loss_dca_confidence_floor"] == 0.85 for seed in seeds))
        self.assertTrue(any(seed["loss_dca_range_block_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(any(seed["loss_dca_degraded_block_enabled"] == 1.0 for seed in seeds))
        self.assertTrue(all(seed["profit_protect_reduce_enabled"] == 0.0 for seed in seeds))
        for seed in seeds:
            self.assertEqual(seed["liquidation_data_enabled"], 0.0)
            weights = build_signal_weight_overrides(seed)
            self.assertIn("binance_metrics_state_signal", weights)
            execution_config = build_execution_config(seed)
            self.assertIn(seed.get("flip_flatten_only_enabled", 0.0), {0.0, 1.0})
            self.assertGreater(execution_config.flip_reverse_open_scale, 0.0)
            self.assertGreaterEqual(execution_config.loss_flip_reverse_open_confidence_floor, 0.0)
            self.assertGreaterEqual(execution_config.target_reduce_deadband_pct, 0.0)
            self.assertGreaterEqual(execution_config.target_delta_deadband_pct, 0.0)
            self.assertGreaterEqual(execution_config.entry_confidence_floor, 0.0)
            self.assertGreaterEqual(execution_config.loss_dca_confidence_floor, 0.0)
            self.assertGreater(execution_config.profit_protect_min_move_atr, 0.0)

    def test_ralph_protective_reduce_seed_trials_force_reduce_first_execution(self) -> None:
        seeds = ralph_protective_reduce_seed_trials()

        self.assertEqual(len(seeds), 12)
        self.assertEqual(seeds[0]["target_vol_4h"], 0.35)
        self.assertEqual(seeds[0]["intent_flip_confirmation_bars"], 24)
        self.assertEqual(seeds[0]["intent_flat_confirmation_bars"], 4)
        self.assertEqual(seeds[0]["entry_cooldown_bars"], 4)
        self.assertEqual(seeds[0]["profit_protect_unwind_pct"], 0.30)
        self.assertTrue(any(seed["target_vol_4h"] == 0.65 for seed in seeds))
        self.assertTrue(any(seed["intent_flat_confirmation_bars"] == 12 for seed in seeds))
        for seed in seeds:
            execution_config = build_execution_config(seed)
            strategy_config = build_strategy_config(seed)
            self.assertEqual(seed["intent_unconfirmed_flip_reduce_only"], 1.0)
            self.assertEqual(seed["flip_flatten_only_enabled"], 1.0)
            self.assertEqual(seed["target_reduce_deadband_pct"], 0.0)
            self.assertEqual(seed["weak_signal_no_flip_enabled"], 1.0)
            self.assertEqual(seed["tiny_reverse_reduce_only_enabled"], 1.0)
            self.assertEqual(seed["range_entry_block_enabled"], 1.0)
            self.assertEqual(seed["degraded_entry_block_enabled"], 1.0)
            self.assertEqual(seed["loss_dca_range_block_enabled"], 1.0)
            self.assertEqual(seed["loss_dca_degraded_block_enabled"], 1.0)
            self.assertEqual(seed["profit_protect_reduce_enabled"], 1.0)
            self.assertTrue(execution_config.flip_flatten_only_enabled)
            self.assertTrue(execution_config.profit_protect_reduce_enabled)
            self.assertTrue(strategy_config.intent_unconfirmed_flip_reduce_only)
            self.assertGreaterEqual(strategy_config.intent_flip_confirmation_bars, 12)
            self.assertGreaterEqual(strategy_config.intent_time_leverage_min_hold_bars, 240)

    def test_ralph_protective_reduce_fee_repair_seed_trials_reduce_fee_pressure(self) -> None:
        seeds = ralph_protective_reduce_fee_repair_seed_trials()

        self.assertEqual(len(seeds), 12)
        self.assertEqual(seeds[0]["max_leverage"], 2.0)
        self.assertEqual(seeds[0]["exit_unwind_pct"], 0.55)
        self.assertEqual(seeds[0]["profit_protect_min_move_atr"], 2.25)
        self.assertEqual(seeds[0]["profit_protect_unwind_pct"], 0.50)
        self.assertTrue(any(seed["target_vol_4h"] == 0.50 for seed in seeds))
        for seed in seeds:
            execution_config = build_execution_config(seed)
            strategy_config = build_strategy_config(seed)
            self.assertEqual(seed["intent_unconfirmed_flip_reduce_only"], 1.0)
            self.assertEqual(seed["flip_flatten_only_enabled"], 1.0)
            self.assertEqual(seed["target_reduce_deadband_pct"], 0.0)
            self.assertEqual(seed["profit_protect_reduce_enabled"], 1.0)
            self.assertGreaterEqual(seed["profit_protect_min_move_atr"], 1.25)
            self.assertGreaterEqual(seed["profit_protect_unwind_pct"], 0.35)
            self.assertGreaterEqual(seed["target_delta_deadband_pct"], 0.08)
            self.assertGreaterEqual(seed["entry_cooldown_bars"], 4)
            self.assertTrue(execution_config.flip_flatten_only_enabled)
            self.assertTrue(execution_config.profit_protect_reduce_enabled)
            self.assertTrue(strategy_config.intent_unconfirmed_flip_reduce_only)
            self.assertGreaterEqual(strategy_config.intent_entry_confirmation_bars, 6)

    def test_ralph_execution_fee_cooldown_seed_trials_keep_activity_while_throttling_reduces(self) -> None:
        seeds = ralph_execution_fee_cooldown_seed_trials()

        self.assertEqual(len(seeds), 12)
        self.assertEqual(seeds[0]["profit_protect_cooldown_bars"], 6)
        self.assertEqual(seeds[0]["profit_protect_unwind_pct"], 0.35)
        self.assertTrue(any(seed["profit_protect_cooldown_bars"] == 24 for seed in seeds))
        self.assertTrue(any(seed["target_vol_4h"] == 0.65 for seed in seeds))
        for seed in seeds:
            execution_config = build_execution_config(seed)
            strategy_config = build_strategy_config(seed)
            self.assertEqual(seed["intent_unconfirmed_flip_reduce_only"], 1.0)
            self.assertEqual(seed["flip_flatten_only_enabled"], 1.0)
            self.assertEqual(seed["profit_protect_reduce_enabled"], 1.0)
            self.assertGreaterEqual(seed["profit_protect_cooldown_bars"], 6)
            self.assertGreaterEqual(seed["profit_protect_unwind_pct"], 0.25)
            self.assertGreaterEqual(seed["target_delta_deadband_pct"], 0.04)
            self.assertTrue(execution_config.flip_flatten_only_enabled)
            self.assertTrue(execution_config.profit_protect_reduce_enabled)
            self.assertGreaterEqual(execution_config.profit_protect_cooldown_bars, 6)
            self.assertTrue(strategy_config.intent_unconfirmed_flip_reduce_only)

    def test_ralph_intent_reduce_cadence_seed_trials_confirm_strategy_reduces(self) -> None:
        seeds = ralph_intent_reduce_cadence_seed_trials()

        self.assertEqual(len(seeds), 12)
        self.assertEqual(seeds[0]["intent_same_direction_reduce_confirmation_bars"], 4)
        self.assertEqual(seeds[0]["intent_flat_reduce_step_pct"], 0.0)
        self.assertEqual(seeds[0]["intent_flat_step_cadence_bars"], 0)
        self.assertEqual(seeds[0]["profit_protect_cooldown_bars"], 0)
        self.assertTrue(any(seed["intent_same_direction_reduce_confirmation_bars"] == 12 for seed in seeds))
        self.assertTrue(any(seed["intent_flat_reduce_step_pct"] == 0.35 for seed in seeds))
        self.assertTrue(any(seed["target_vol_4h"] == 0.65 for seed in seeds))
        for seed in seeds:
            execution_config = build_execution_config(seed)
            strategy_config = build_strategy_config(seed)
            self.assertEqual(seed["intent_unconfirmed_flip_reduce_only"], 1.0)
            self.assertEqual(seed["flip_flatten_only_enabled"], 1.0)
            self.assertEqual(seed["profit_protect_reduce_enabled"], 1.0)
            self.assertEqual(seed["profit_protect_cooldown_bars"], 0)
            self.assertGreaterEqual(seed["intent_flat_reduce_step_pct"], 0.0)
            self.assertGreaterEqual(seed["intent_flat_step_cadence_bars"], 0)
            self.assertGreaterEqual(seed["intent_same_direction_reduce_confirmation_bars"], 2)
            self.assertGreaterEqual(seed["target_delta_deadband_pct"], 0.04)
            self.assertTrue(execution_config.flip_flatten_only_enabled)
            self.assertTrue(execution_config.profit_protect_reduce_enabled)
            self.assertEqual(execution_config.profit_protect_cooldown_bars, 0)
            self.assertTrue(strategy_config.intent_unconfirmed_flip_reduce_only)
            self.assertGreaterEqual(strategy_config.intent_flat_reduce_step_pct, 0.0)
            self.assertGreaterEqual(strategy_config.intent_flat_step_cadence_bars, 0)
            self.assertGreaterEqual(strategy_config.intent_same_direction_reduce_confirmation_bars, 2)

    def test_paulwei_balanced_tranche_seed_trials_keep_execution_neutral(self) -> None:
        seeds = paulwei_balanced_tranche_seed_trials()

        self.assertEqual(len(seeds), 12)
        self.assertEqual(seeds[0]["target_vol_4h"], 0.65)
        self.assertEqual(seeds[0]["intent_add_signal_threshold"], 0.35)
        self.assertEqual(seeds[0]["intent_flat_reduce_step_pct"], 0.15)
        self.assertEqual(seeds[0]["intent_flat_step_cadence_bars"], 8)
        self.assertTrue(any(seed["intent_flat_reduce_step_pct"] == 0.35 for seed in seeds))
        self.assertTrue(any(seed["intent_same_direction_reduce_confirmation_bars"] == 12 for seed in seeds))
        self.assertTrue(any(seed["intent_time_leverage_min_hold_bars"] == 480 for seed in seeds))
        self.assertTrue(any(seed["intent_add_signal_threshold"] == 0.30 for seed in seeds))
        for seed in seeds:
            execution_config = build_execution_config(seed)
            strategy_config = build_strategy_config(seed)
            self.assertEqual(seed["intent_unconfirmed_flip_reduce_only"], 0.0)
            self.assertEqual(seed["intent_time_leverage_dip_accumulation_enabled"], 1.0)
            self.assertEqual(seed["time_decay_exit_enabled"], 1.0)
            self.assertEqual(seed["flip_flatten_only_enabled"], 0.0)
            self.assertEqual(seed["target_delta_deadband_pct"], 0.0)
            self.assertEqual(seed["target_reduce_deadband_pct"], 0.0)
            self.assertEqual(seed["weak_signal_no_flip_enabled"], 0.0)
            self.assertEqual(seed["tiny_reverse_reduce_only_enabled"], 0.0)
            self.assertEqual(seed["profit_protect_reduce_enabled"], 0.0)
            self.assertFalse(execution_config.flip_flatten_only_enabled)
            self.assertFalse(execution_config.profit_protect_reduce_enabled)
            self.assertFalse(strategy_config.intent_unconfirmed_flip_reduce_only)
            self.assertTrue(strategy_config.intent_time_leverage_dip_accumulation_enabled)
            self.assertLessEqual(strategy_config.intent_add_signal_threshold, 0.45)
            self.assertLessEqual(strategy_config.intent_time_leverage_add_deadband_pct, 0.25)
            self.assertGreaterEqual(seed["range_time_decay_bars"], 48)
            self.assertGreaterEqual(seed["preserve_capital_time_decay_bars"], 12)
            self.assertLessEqual(seed["time_decay_long_loss_threshold"], -0.08)
            self.assertLessEqual(seed["time_decay_short_loss_threshold"], -0.04)
            self.assertGreaterEqual(strategy_config.intent_flat_reduce_step_pct, 0.15)
            self.assertGreaterEqual(strategy_config.intent_flat_step_cadence_bars, 8)
            self.assertGreaterEqual(strategy_config.intent_same_direction_reduce_confirmation_bars, 4)


class FrameworkEvaluatorTests(unittest.TestCase):
    def test_temporal_robustness_windows_are_train_dev_only(self) -> None:
        split = default_btc_split_spec()

        train_windows = _train_temporal_robustness_windows(split)
        late_dev_window = _late_dev_regime_window(split)

        self.assertEqual(
            [window.label for window in train_windows], [f"train_temporal_{year}" for year in range(2020, 2024)]
        )
        self.assertTrue(all(window.start_ns >= split.train.start_ns for window in train_windows))
        self.assertTrue(
            all(window.end_ns is not None and window.end_ns <= split.train.end_ns for window in train_windows)
        )
        self.assertIsNotNone(late_dev_window)
        assert late_dev_window is not None
        self.assertGreaterEqual(late_dev_window.start_ns or 0, split.dev.start_ns)
        self.assertEqual(late_dev_window.end_ns, split.dev.end_ns)
        self.assertLessEqual(late_dev_window.end_ns or 0, split.test.start_ns)

    def test_temporal_robustness_audit_blocks_unstable_train_and_late_dev(self) -> None:
        split = default_btc_split_spec()
        base_config = HistoricalBacktestConfig(profile_path=None)
        evaluator = SimpleNamespace(
            search_space_profile={"paulwei_dual_phase_spot_context_relaxed_fee_search": True},
            _split_spec=split,
            _base_config=base_config,
            candidate_variant=lambda _params, name, label: VariantSpec(name=name, label=label),
        )

        def fake_snapshot(_base_config, _variant, *, start_ns, end_ns):
            del end_ns
            if start_ns == split.train.start_ns:
                return BacktestSnapshot(
                    sharpe=-0.75,
                    net_pnl=-1_000.0,
                    max_drawdown=0.08,
                    win_rate=0.4,
                    profit_factor=0.8,
                )
            if start_ns == _ns_from_utc(2022, 1, 1):
                return BacktestSnapshot(
                    sharpe=-0.60,
                    net_pnl=-1_500.0,
                    max_drawdown=0.18,
                    win_rate=0.42,
                    profit_factor=0.85,
                )
            if start_ns and start_ns >= (split.dev.end_ns or 0) - 200 * 86_400 * 1_000_000_000:
                return BacktestSnapshot(
                    sharpe=-0.40,
                    net_pnl=-500.0,
                    max_drawdown=0.05,
                    win_rate=0.45,
                    profit_factor=0.9,
                )
            return BacktestSnapshot(
                sharpe=1.20,
                net_pnl=2_000.0,
                max_drawdown=0.04,
                win_rate=0.55,
                profit_factor=1.4,
            )

        with patch("zenquant.research.framework_search._run_variant_backtest_snapshot", side_effect=fake_snapshot):
            audit = _temporal_robustness_audit(evaluator, {"target_vol_4h": 0.65})

        self.assertIsNotNone(audit)
        assert audit is not None
        self.assertFalse(audit["test_evaluated"])
        self.assertEqual(audit["scope"], "train_dev_only_temporal_robustness")
        self.assertIn("paulwei_2020_profit_run_fail_fast", audit["blocked_by"])
        self.assertIn("paulwei_2022_bear_survival_fail_fast", audit["blocked_by"])
        self.assertIn("temporal_train_min_sharpe_fail_fast", audit["blocked_by"])
        self.assertIn("late_dev_regime_fail_fast", audit["blocked_by"])
        self.assertEqual(audit["primary_blocker"], "paulwei_2020_profit_run_fail_fast")
        self.assertEqual(
            audit["paulwei_temporal_target_audit"]["scope"],
            "paulwei_2020_2022_temporal_behavior_target",
        )
        self.assertLess(audit["score_adjustment"], 0.0)

    def test_training_input_contract_treats_missing_train_1h_as_derived_from_5m(self) -> None:
        @dataclass
        class Readiness:
            total_bars: int = 100
            nonzero_bars: int = 10
            nonstale_bars: int = 10
            extreme_bars: int = 1
            max_total_liq_usd: float = 1_000_000.0
            data_ready: bool = True
            promotion_ready: bool = False
            blocked_by: str = "not_promotion_ready"
            detail: str = "ok"

        @dataclass
        class DeribitStatus:
            enabled: bool = True
            observation_ready: bool = False
            research_ready: bool = False
            snapshot_count_7d: int = 0

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "binance"
            split = default_btc_split_spec()
            train_start = split.train.start_ns

            write_parquet_rows(
                root / "klines" / "BTCUSDT" / "5m" / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="5m",
                        open_time_ns=train_start,
                        close_time_ns=train_start + Timeframe.TF_5M.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.5,
                        volume=1.0,
                        trade_count=1,
                    )
                ],
            )
            write_parquet_rows(
                root / "klines" / "BTCUSDT" / "4h" / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="4h",
                        open_time_ns=train_start,
                        close_time_ns=train_start + Timeframe.TF_4H.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.5,
                        volume=1.0,
                        trade_count=1,
                    )
                ],
            )
            write_parquet_rows(
                root / "klines" / "BTCUSDT" / "1h" / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="1h",
                        open_time_ns=split.dev.start_ns,
                        close_time_ns=split.dev.start_ns + Timeframe.TF_1H.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.5,
                        volume=1.0,
                        trade_count=1,
                    )
                ],
            )

            with (
                patch("zenquant.research.framework_search.liquidation_readiness") as readiness_mock,
                patch("zenquant.research.framework_search.collect_deribit_status") as deribit_mock,
                patch("zenquant.research.framework_search.load_source_admission_review", return_value={}),
            ):
                readiness_mock.return_value = Readiness()
                deribit_mock.return_value = DeribitStatus()
                evaluator = FrameworkEvaluator(
                    HistoricalBacktestConfig(
                        root_dir=root,
                        symbol="BTCUSDT",
                        execution_timeframe=Timeframe.TF_5M,
                        direction_timeframe=Timeframe.TF_4H,
                        context_timeframe=Timeframe.TF_1H,
                    )
                )

        execution_contract = evaluator.training_input_contract["execution_framework"]
        self.assertTrue(execution_contract["five_min_feature_aggregation_enabled"])
        self.assertTrue(execution_contract["one_hour_bars_available"])
        self.assertEqual(execution_contract["one_hour_bars_source"], "derived_from_5m")

    def test_training_input_contract_only_admits_mvrv_with_review(self) -> None:
        @dataclass
        class Readiness:
            total_bars: int = 100
            nonzero_bars: int = 10
            nonstale_bars: int = 10
            extreme_bars: int = 1
            max_total_liq_usd: float = 1_000_000.0
            data_ready: bool = True
            promotion_ready: bool = False
            blocked_by: str = "not_promotion_ready"
            detail: str = "ok"

        @dataclass
        class DeribitStatus:
            enabled: bool = True
            observation_ready: bool = False
            research_ready: bool = False
            snapshot_count_7d: int = 0

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "binance"
            macro_root = Path(tmp) / "macro"
            split = default_btc_split_spec()
            train_start = split.train.start_ns
            write_parquet_rows(
                root / "klines" / "BTCUSDT" / "5m" / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="5m",
                        open_time_ns=train_start,
                        close_time_ns=train_start + Timeframe.TF_5M.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.5,
                        volume=1.0,
                        trade_count=1,
                    )
                ],
            )
            write_parquet_rows(
                root / "klines" / "BTCUSDT" / "4h" / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="4h",
                        open_time_ns=train_start,
                        close_time_ns=train_start + Timeframe.TF_4H.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.5,
                        volume=1.0,
                        trade_count=1,
                    )
                ],
            )
            (macro_root / "btc_mvrv").mkdir(parents=True, exist_ok=True)
            (macro_root / "btc_mvrv" / "data.parquet").write_text("placeholder", encoding="utf-8")
            (macro_root / "btc_mvrv" / "metadata.json").write_text(
                json.dumps(
                    {
                        "btc_mvrv_source": "local_csv",
                        "btc_mvrv_source_confidence": "high",
                        "btc_mvrv_last_success_at": "2026-04-18T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            def _review(name: str):
                if name == "mvrv":
                    return {
                        "tier_decision": "tier2_research_only_feature",
                        "time_granularity": "1d",
                        "research_ready": True,
                        "use_for_research_only": True,
                        "exclude_from_promote_truth": True,
                        "blocked_by": None,
                    }
                if name == "deribit_large_option_flow":
                    return {
                        "tier_decision": "tier2_research_candidate",
                        "time_granularity": "4h",
                        "research_ready": True,
                        "use_for_research_only": True,
                        "exclude_from_promote_truth": True,
                        "strategy_wiring_allowed": False,
                        "risk_overlay_wiring_allowed": False,
                        "direction_vote_allowed": False,
                        "intent_aggregation_allowed": False,
                        "position_sizing_allowed": False,
                        "allowed_in_training_observation": True,
                        "allowed_in_candidate_explanation": True,
                        "feature_freeze_candidate": True,
                        "blocked_by": None,
                    }
                return {}

            def _independence(name: str):
                if name == "btc_mvrv":
                    return {
                        "allowed_in_state_features": True,
                        "use_for_research_only": True,
                        "blocked_by": None,
                        "max_abs_correlation": 0.41,
                    }
                if name == "deribit_large_option_flow":
                    return {
                        "allowed_in_state_features": False,
                        "allowed_in_pressure_context": True,
                        "allowed_in_candidate_explanation": True,
                        "direction_vote_allowed": False,
                        "intent_aggregation_allowed": False,
                        "risk_overlay_wiring_allowed": False,
                        "use_for_research_only": True,
                        "blocked_by": None,
                        "max_abs_correlation": 0.32,
                    }
                return {}

            with (
                patch("zenquant.research.framework_search.liquidation_readiness") as readiness_mock,
                patch("zenquant.research.framework_search.collect_deribit_status") as deribit_mock,
                patch("zenquant.research.framework_search.load_source_admission_review", side_effect=_review),
                patch("zenquant.research.framework_search.load_source_independence_audit", side_effect=_independence),
                patch(
                    "zenquant.research.framework_search._active_signal_independence_contract",
                    return_value={
                        "audit_present": False,
                        "redundant_pairs": [],
                        "role_overlap_pairs": [],
                        "direction_layer_clean": False,
                        "state_layer_clean": False,
                        "blocked_by": "active_signal_independence_missing",
                    },
                ),
                patch(
                    "zenquant.research.framework_search.load_historical_extension_candidate_contract",
                    return_value={
                        "candidate_source_type": "spot",
                        "candidate_name": "binance_spot_2017_plus",
                        "ready": False,
                        "layered_train_enabled": False,
                        "historical_extension_mode": "spot_context_only",
                        "formal_train_truth": False,
                        "train_truth_legs": [
                            {"name": "post_2020_futures_train", "source": "futures"},
                        ],
                        "dev_truth_source": "futures_only",
                        "test_truth_source": "futures_only",
                        "mixed_era_declared": False,
                        "full_unified_truth": False,
                        "blocked_by": ["spot_perp_truth_boundary"],
                        "spot_train_leg_blocked_by": [],
                        "candidate_window_start": "2017-08-17T00:00:00+00:00",
                        "candidate_window_end": "2026-04-17T00:00:00+00:00",
                        "source_boundary": "spot candidate only",
                    },
                ),
            ):
                readiness_mock.return_value = Readiness()
                deribit_mock.return_value = DeribitStatus()
                evaluator = FrameworkEvaluator(
                    HistoricalBacktestConfig(
                        root_dir=root,
                        macro_dir=macro_root,
                        symbol="BTCUSDT",
                        execution_timeframe=Timeframe.TF_5M,
                        direction_timeframe=Timeframe.TF_4H,
                        context_timeframe=Timeframe.TF_1H,
                    )
                )

        state_sources = evaluator.training_input_contract["state_feature_sources"]
        signal_contract = evaluator.training_input_contract["signal_independence_contract"]
        self.assertTrue(state_sources["mvrv_in_state_features"])
        self.assertTrue(state_sources["mvrv_admission_review_present"])
        self.assertTrue(state_sources["mvrv_independence_review_present"])
        self.assertEqual(state_sources["mvrv_tier_decision"], "tier2_research_only_feature")
        self.assertEqual(state_sources["mvrv_time_granularity"], "1d")
        self.assertTrue(state_sources["deribit_large_option_flow_research_candidate"])
        self.assertFalse(state_sources["deribit_large_option_flow_in_state_features"])
        self.assertEqual(state_sources["deribit_large_option_flow_tier_decision"], "tier2_research_candidate")
        self.assertEqual(state_sources["deribit_large_option_flow_time_granularity"], "4h")
        self.assertFalse(state_sources["deribit_large_option_flow_strategy_wiring_allowed"])
        self.assertFalse(state_sources["deribit_large_option_flow_risk_overlay_wiring_allowed"])
        self.assertFalse(state_sources["deribit_large_option_flow_direction_vote_allowed"])
        self.assertFalse(state_sources["deribit_large_option_flow_intent_aggregation_allowed"])
        self.assertFalse(state_sources["deribit_large_option_flow_position_sizing_allowed"])
        self.assertTrue(state_sources["deribit_large_option_flow_training_observation_allowed"])
        self.assertTrue(state_sources["deribit_large_option_flow_candidate_explanation_allowed"])
        self.assertTrue(state_sources["deribit_large_option_flow_independence_review_present"])
        self.assertFalse(state_sources["deribit_large_option_flow_independence_allowed"])
        self.assertTrue(state_sources["deribit_large_option_flow_pressure_context_allowed"])
        self.assertEqual(state_sources["deribit_large_option_flow_max_abs_correlation"], 0.32)
        self.assertFalse(signal_contract["audit_present"])
        self.assertEqual(signal_contract["blocked_by"], "active_signal_independence_missing")
        historical_contract = evaluator.training_input_contract["historical_extension_candidate_contract"]
        self.assertTrue(historical_contract["present"])
        self.assertEqual(historical_contract["source"], "spot")
        self.assertFalse(historical_contract["ready"])
        self.assertIn("spot_perp_truth_boundary", historical_contract["blocked_by"])
        self.assertFalse(evaluator.training_input_contract["historical_extension_train_enabled"])
        self.assertEqual(evaluator.training_input_contract["historical_extension_mode"], "spot_context_only")
        self.assertFalse(evaluator.training_input_contract["mixed_era_declared"])
        self.assertEqual(len(evaluator.training_input_contract["train_truth_legs"]), 1)

    def test_liquidation_candidate_is_blocked_when_history_missing(self) -> None:
        fake_summary = CandidateSummary(
            variant="candidate",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=10.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.5,
            weak_window_sharpe=0.5,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            blocked_by=None,
        )

        class FakeReadiness:
            total_bars = 100
            nonzero_bars = 0
            nonstale_bars = 0
            extreme_bars = 0
            max_total_liq_usd = 0.0
            data_ready = False
            promotion_ready = False
            blocked_by = "missing_liquidation_history"
            detail = "missing"

        with (
            patch("zenquant.research.framework_search.liquidation_readiness", return_value=FakeReadiness()),
            patch.object(FrameworkEvaluator, "_evaluate_variant", return_value=fake_summary),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig())
            self.assertNotIn("liquidation_data_enabled", evaluator.search_param_grid)
            summary = evaluator.evaluate_params(
                {
                    "liquidation_data_enabled": 1.0,
                    "state_liquidation_overlay_enabled": 1.0,
                    "liq_risk_guard_enabled": 1.0,
                }
            )

        self.assertFalse(summary.promotion_candidate)
        self.assertEqual(summary.blocked_by, "missing_liquidation_history")

    def test_backtest_cache_stores_lightweight_snapshots(self) -> None:
        @dataclass
        class FakeMetrics:
            sharpe: float = 1.0
            net_pnl: float = 100.0
            max_drawdown: float = 0.1
            win_rate: float = 0.5

        class FakeResult:
            metrics = FakeMetrics()

        class FakeRunner:
            pass

        evaluator = FrameworkEvaluator(HistoricalBacktestConfig())
        with patch(
            "zenquant.research.framework_search._run_variant_backtest", return_value=(FakeRunner(), FakeResult())
        ):
            snapshot = evaluator._run_variant_backtest_cached(evaluator._current_variant, start_ns=None, end_ns=None)

        self.assertIsInstance(snapshot, BacktestSnapshot)
        self.assertEqual(snapshot.sharpe, 1.0)
        self.assertIsInstance(next(iter(evaluator._backtest_cache.values())), BacktestSnapshot)


class ImportLiquidationHistoryTests(unittest.TestCase):
    def test_import_csv_aggregates_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "liq.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "ts_ns,symbol,side,price,qty,usd_value",
                        "1690848000000000000,BTCUSDT,SELL,29000,0.5,14500",
                        "1690848600000000000,BTCUSDT,BUY,29100,0.2,5820",
                    ]
                ),
                encoding="utf-8",
            )

            events = IMPORT_LIQ._load_events(csv_path, "BTCUSDT")

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].symbol, "BTCUSDT")
        self.assertEqual(events[0].side, "SELL")
        self.assertEqual(events[0].source, "external_csv")


class SearchReportSerializationTests(unittest.TestCase):
    def test_candidate_score_penalizes_drawdown_and_worst_window_regressions(self) -> None:
        baseline = CandidateSummary(
            variant="baseline",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=2.0,
            dev_pnl=100.0,
            dev_drawdown=0.15,
            avg_dev_sharpe=2.0,
            avg_dev_ratio=1.1,
            worst_dev_sharpe=1.0,
            weak_window_sharpe=1.2,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            blocked_by=None,
            failed_gate_metrics=None,
            promotion_failure_reason=None,
        )
        stable = _candidate_score(
            avg_test_sharpe=2.1,
            worst_test_sharpe=1.0,
            avg_oos_ratio=1.0,
            overall_sharpe=2.0,
            overall_pnl=200.0,
            weak_window_sharpe=1.1,
            overall_drawdown=0.16,
            negative_worst_windows=0,
            baseline=baseline,
        )
        aggressive = _candidate_score(
            avg_test_sharpe=2.3,
            worst_test_sharpe=0.2,
            avg_oos_ratio=1.0,
            overall_sharpe=2.2,
            overall_pnl=2_000_000.0,
            weak_window_sharpe=2.5,
            overall_drawdown=0.40,
            negative_worst_windows=2,
            baseline=baseline,
        )
        self.assertGreater(stable, aggressive)

    def test_candidate_score_prefers_hard_cap_compliant_drawdown(self) -> None:
        baseline = CandidateSummary(
            variant="baseline",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=2.0,
            dev_pnl=100.0,
            dev_drawdown=0.083,
            avg_dev_sharpe=2.0,
            avg_dev_ratio=1.1,
            worst_dev_sharpe=1.0,
            weak_window_sharpe=1.2,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            blocked_by=None,
            failed_gate_metrics=None,
            promotion_failure_reason=None,
        )
        within_cap = _candidate_score(
            avg_test_sharpe=2.0,
            worst_test_sharpe=1.0,
            avg_oos_ratio=1.0,
            overall_sharpe=2.0,
            overall_pnl=1_000_000.0,
            weak_window_sharpe=1.0,
            overall_drawdown=0.145,
            negative_worst_windows=0,
            baseline=baseline,
        )
        above_cap = _candidate_score(
            avg_test_sharpe=2.0,
            worst_test_sharpe=1.0,
            avg_oos_ratio=1.0,
            overall_sharpe=2.0,
            overall_pnl=1_000_000.0,
            weak_window_sharpe=1.0,
            overall_drawdown=0.17,
            negative_worst_windows=0,
            baseline=baseline,
        )
        self.assertGreater(within_cap, above_cap)

    def test_candidate_score_caps_extreme_avg_dev_ratio_influence(self) -> None:
        baseline = CandidateSummary(
            variant="baseline",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=100.0,
            dev_drawdown=0.20,
            avg_dev_sharpe=0.9,
            avg_dev_ratio=0.9,
            worst_dev_sharpe=-0.5,
            weak_window_sharpe=0.1,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            blocked_by=None,
            failed_gate_metrics=None,
            promotion_failure_reason=None,
        )
        normal_ratio = _candidate_score(
            avg_test_sharpe=1.0,
            worst_test_sharpe=-0.4,
            avg_oos_ratio=1.2,
            overall_sharpe=1.0,
            overall_pnl=100_000.0,
            weak_window_sharpe=0.2,
            overall_drawdown=0.20,
            negative_worst_windows=0,
            baseline=baseline,
        )
        absurd_ratio = _candidate_score(
            avg_test_sharpe=1.0,
            worst_test_sharpe=-0.4,
            avg_oos_ratio=20.0,
            overall_sharpe=1.0,
            overall_pnl=100_000.0,
            weak_window_sharpe=0.2,
            overall_drawdown=0.20,
            negative_worst_windows=0,
            baseline=baseline,
        )
        self.assertLess(abs(absurd_ratio - normal_ratio), 0.2)

    def test_candidate_score_penalizes_turnover_and_fee_drag(self) -> None:
        baseline = CandidateSummary(
            variant="baseline",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=2.0,
            dev_pnl=100.0,
            dev_drawdown=0.10,
            avg_dev_sharpe=2.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=1.0,
            weak_window_sharpe=1.0,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            sortino=2.5,
            profit_factor=1.5,
            turnover_per_day=1000.0,
            fee_drag_pct=0.01,
        )
        cleaner = _candidate_score(
            avg_test_sharpe=2.1,
            worst_test_sharpe=1.1,
            avg_oos_ratio=1.0,
            overall_sharpe=2.05,
            overall_pnl=200_000.0,
            weak_window_sharpe=1.0,
            overall_drawdown=0.10,
            negative_worst_windows=0,
            baseline=baseline,
            overall_sortino=2.6,
            overall_profit_factor=1.6,
            turnover_per_day=1050.0,
            fee_drag_pct=0.012,
        )
        expensive = _candidate_score(
            avg_test_sharpe=2.1,
            worst_test_sharpe=1.1,
            avg_oos_ratio=1.0,
            overall_sharpe=2.05,
            overall_pnl=200_000.0,
            weak_window_sharpe=1.0,
            overall_drawdown=0.10,
            negative_worst_windows=0,
            baseline=baseline,
            overall_sortino=2.6,
            overall_profit_factor=1.6,
            turnover_per_day=2500.0,
            fee_drag_pct=0.05,
        )
        self.assertGreater(cleaner, expensive)

    def test_stage1_hostile_proxy_penalizes_train_churn_stress(self) -> None:
        params = {
            "target_vol_4h": 1.1,
            "max_leverage": 4.5,
            "pyramid_initial_pct": 0.3,
            "pyramid_add_pct": 0.3,
            "pyramid_short_initial_pct": 0.2,
            "pyramid_short_add_pct": 0.1,
            "dca_spacing_atr": 0.75,
            "dca_size_decay": 0.75,
            "flip_min_confidence": 0.7,
        }
        base_metrics = {
            "sharpe": 2.9,
            "max_drawdown": 0.18,
            "profit_factor": 1.2,
            "fee_drag_pct": 0.015,
            "trade_count": 3600,
        }
        clean = _hostile_proxy_diagnostics(params, base_metrics)
        churny = _hostile_proxy_diagnostics(
            params,
            {
                **base_metrics,
                "fee_drag_pct": 0.045,
                "trade_count": 5200,
            },
        )

        self.assertGreater(churny["train_churn_stress_penalty"], clean["train_churn_stress_penalty"])
        self.assertGreater(churny["train_trade_count_penalty"], 0.0)
        self.assertGreater(churny["train_fee_drag_proxy_penalty"], 0.0)
        self.assertLess(churny["stage1_score"], clean["stage1_score"])

    def test_stage1_fail_fast_rejects_fragile_hostile_candidate(self) -> None:
        baseline = CandidateSummary(
            variant="current",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=100.0,
            dev_drawdown=0.10,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.4,
            weak_window_sharpe=0.5,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            profit_factor=1.3,
            turnover_per_day=1000.0,
            fee_drag_pct=0.01,
        )
        current = CandidateSummary(
            variant="current",
            robust_score=baseline.robust_score,
            selection_split=baseline.selection_split,
            dev_sharpe=baseline.dev_sharpe,
            dev_pnl=baseline.dev_pnl,
            dev_drawdown=baseline.dev_drawdown,
            avg_dev_sharpe=baseline.avg_dev_sharpe,
            avg_dev_ratio=baseline.avg_dev_ratio,
            worst_dev_sharpe=0.3,
            weak_window_sharpe=baseline.weak_window_sharpe,
            negative_dev_worst_windows=baseline.negative_dev_worst_windows,
            promotion_candidate=baseline.promotion_candidate,
            profit_factor=baseline.profit_factor,
            turnover_per_day=baseline.turnover_per_day,
            fee_drag_pct=baseline.fee_drag_pct,
        )
        fragile = CandidateSummary(
            variant="optuna_tuned",
            robust_score=baseline.robust_score,
            selection_split=baseline.selection_split,
            dev_sharpe=baseline.dev_sharpe,
            dev_pnl=baseline.dev_pnl,
            dev_drawdown=baseline.dev_drawdown,
            avg_dev_sharpe=baseline.avg_dev_sharpe,
            avg_dev_ratio=baseline.avg_dev_ratio,
            worst_dev_sharpe=-0.2,
            weak_window_sharpe=baseline.weak_window_sharpe,
            negative_dev_worst_windows=baseline.negative_dev_worst_windows,
            promotion_candidate=baseline.promotion_candidate,
            profit_factor=0.8,
            turnover_per_day=1800.0,
            fee_drag_pct=0.03,
        )

        self.assertEqual(
            _stage1_fail_fast_reason(fragile, baseline=baseline, current=current),
            "worst_dev_sharpe_fail_fast",
        )

    def test_stage1_fail_fast_rejects_weak_window_2023_08_regression_first(self) -> None:
        baseline = CandidateSummary(
            variant="paulwei_baseline",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=100.0,
            dev_drawdown=0.10,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.4,
            weak_window_sharpe=0.5,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            profit_factor=1.3,
            turnover_per_day=1000.0,
            fee_drag_pct=0.01,
        )
        current = CandidateSummary(
            variant="current",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=100.0,
            dev_drawdown=0.10,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.5,
            weak_window_sharpe=0.55,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            profit_factor=1.3,
            turnover_per_day=1000.0,
            fee_drag_pct=0.01,
        )
        fragile = CandidateSummary(
            variant="optuna_tuned",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.5,
            dev_pnl=120.0,
            dev_drawdown=0.08,
            avg_dev_sharpe=1.2,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.4,
            weak_window_sharpe=0.2,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
            profit_factor=1.1,
            turnover_per_day=1000.0,
            fee_drag_pct=0.01,
        )

        self.assertEqual(
            _stage1_fail_fast_reason(fragile, baseline=baseline, current=current),
            "weak_window_2023_08_fail_fast",
        )

    def test_stage1_fail_fast_rejects_catastrophic_dev_fee_drag(self) -> None:
        baseline = CandidateSummary(
            variant="paulwei_baseline",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=100.0,
            dev_drawdown=0.10,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.4,
            weak_window_sharpe=0.5,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            profit_factor=1.3,
            turnover_per_day=1000.0,
            fee_drag_pct=0.01,
        )
        current = CandidateSummary(
            variant="current",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=100.0,
            dev_drawdown=0.10,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.5,
            weak_window_sharpe=0.55,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            profit_factor=1.3,
            turnover_per_day=1000.0,
            fee_drag_pct=0.01,
        )
        expensive = CandidateSummary(
            variant="optuna_tuned",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=2.0,
            dev_pnl=120.0,
            dev_drawdown=0.08,
            avg_dev_sharpe=1.2,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.6,
            weak_window_sharpe=0.8,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            profit_factor=1.5,
            turnover_per_day=1200.0,
            fee_drag_pct=0.08,
        )

        self.assertEqual(
            _stage1_fail_fast_reason(expensive, baseline=baseline, current=current),
            "dev_fee_drag_fail_fast",
        )

        close_miss = CandidateSummary(
            variant="optuna_tuned",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=2.0,
            dev_pnl=120.0,
            dev_drawdown=0.08,
            avg_dev_sharpe=1.2,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.6,
            weak_window_sharpe=0.8,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            profit_factor=1.5,
            turnover_per_day=1200.0,
            fee_drag_pct=0.057,
        )
        self.assertIsNone(_stage1_fail_fast_reason(close_miss, baseline=baseline, current=current))

    def test_post_smoke_scenario_classifies_hostile_window_failure(self) -> None:
        report = SearchReport(
            created_at="2026-04-10T00:00:00+00:00",
            symbol="BTCUSDT",
            readiness={"promotion_ready": False, "blocked_by": "not_promotion_ready"},
            deribit_status={"observation_ready": True, "enabled": True, "research_ready": True},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={},
            training_input_contract={
                "execution_framework": {
                    "five_min_direct_execution_enabled": True,
                    "five_min_feature_aggregation_enabled": True,
                    "one_hour_bars_available": True,
                    "four_hour_bars_available": True,
                },
                "state_feature_sources": {
                    "hyperliquid_in_state_features": True,
                    "etf_flow_in_state_features": True,
                },
                "signal_independence_contract": {
                    "audit_present": True,
                    "direction_layer_clean": True,
                    "state_layer_clean": True,
                    "blocked_by": None,
                },
            },
            search_status={
                "search_space_profile": search_space_profile(),
                "liquidation_in_search": False,
            },
            current=CandidateSummary(
                variant="current",
                robust_score=0.9,
                selection_split="dev",
                dev_sharpe=0.8,
                dev_pnl=100.0,
                dev_drawdown=0.18,
                avg_dev_sharpe=0.8,
                avg_dev_ratio=0.8,
                worst_dev_sharpe=-1.0,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=1,
                promotion_candidate=False,
            ),
            paulwei_baseline=CandidateSummary(
                variant="paulwei_baseline",
                robust_score=1.0,
                selection_split="dev",
                dev_sharpe=0.9,
                dev_pnl=120.0,
                dev_drawdown=0.20,
                avg_dev_sharpe=0.85,
                avg_dev_ratio=0.9,
                worst_dev_sharpe=-1.0,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=1,
                promotion_candidate=True,
            ),
            optuna_best=CandidateSummary(
                variant="optuna_tuned",
                robust_score=0.5,
                selection_split="dev",
                dev_sharpe=0.7,
                dev_pnl=90.0,
                dev_drawdown=0.26,
                avg_dev_sharpe=0.7,
                avg_dev_ratio=0.9,
                worst_dev_sharpe=-1.5,
                weak_window_sharpe=0.1,
                negative_dev_worst_windows=3,
                promotion_candidate=False,
                promotion_failure_reason="worst_test_sharpe",
            ),
            rl_best=None,
            best_variant="optuna_tuned",
            best_score=0.5,
            best_params={"x": 1},
            comparison_payload={},
            smoke_completed=True,
        )

        assessment = _assess_full_training_readiness(report, smoke_completed=True)
        scenario = _classify_post_smoke_scenario(report, full_training_assessment=assessment)

        self.assertEqual(scenario["scenario"], "scenario_b_hostile_window_failure")
        self.assertEqual(scenario["next_action"], "continue_execution_first_hostile_window_optimization")

    def test_hostile_regression_summary_identifies_weak_window_driver(self) -> None:
        report = SearchReport(
            created_at="2026-04-10T00:00:00+00:00",
            symbol="BTCUSDT",
            readiness={"promotion_ready": False, "blocked_by": "not_promotion_ready"},
            deribit_status={"observation_ready": True, "enabled": True, "research_ready": True},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={},
            training_input_contract={},
            search_status={},
            current=CandidateSummary(
                variant="current",
                robust_score=0.8,
                selection_split="dev",
                dev_sharpe=2.0,
                dev_pnl=100.0,
                dev_drawdown=0.20,
                avg_dev_sharpe=2.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=-0.4,
                weak_window_sharpe=-0.4,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            paulwei_baseline=CandidateSummary(
                variant="paulwei_baseline",
                robust_score=0.8,
                selection_split="dev",
                dev_sharpe=2.0,
                dev_pnl=100.0,
                dev_drawdown=0.20,
                avg_dev_sharpe=2.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=-0.4,
                weak_window_sharpe=-0.4,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            optuna_best=CandidateSummary(
                variant="optuna_tuned",
                robust_score=0.7,
                selection_split="dev",
                dev_sharpe=2.8,
                dev_pnl=110.0,
                dev_drawdown=0.15,
                avg_dev_sharpe=-1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=-6.0,
                weak_window_sharpe=-6.0,
                negative_dev_worst_windows=0,
                promotion_candidate=False,
            ),
            rl_best=None,
            best_variant="current",
            best_score=0.8,
            best_params=None,
            comparison_payload={},
        )

        summary = _hostile_regression_summary(report)

        self.assertTrue(summary["dev_primary_improved"])
        self.assertTrue(summary["hostile_window_regressed"])
        self.assertEqual(summary["hostile_regression_driver"], "weak_window_2023_08")

    def test_build_markdown_report_includes_hostile_regression_fields(self) -> None:
        report = SearchReport(
            created_at="2026-04-10T00:00:00+00:00",
            symbol="BTCUSDT",
            readiness={
                "promotion_ready": False,
                "blocked_by": "not_promotion_ready",
                "data_ready": True,
                "detail": "ok",
            },
            deribit_status={"observation_ready": True, "enabled": True, "research_ready": True},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={},
            training_input_contract={
                "state_feature_sources": {},
                "signal_stack_contract": {},
                "signal_independence_contract": {},
            },
            search_status={"search_space_profile": search_space_profile()},
            current=CandidateSummary(
                variant="current",
                robust_score=0.8,
                selection_split="dev",
                dev_sharpe=2.0,
                dev_pnl=100.0,
                dev_drawdown=0.20,
                avg_dev_sharpe=2.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=-0.4,
                weak_window_sharpe=-0.4,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            paulwei_baseline=CandidateSummary(
                variant="paulwei_baseline",
                robust_score=0.8,
                selection_split="dev",
                dev_sharpe=2.0,
                dev_pnl=100.0,
                dev_drawdown=0.20,
                avg_dev_sharpe=2.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=-0.4,
                weak_window_sharpe=-0.4,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            optuna_best=CandidateSummary(
                variant="optuna_tuned",
                robust_score=0.7,
                selection_split="dev",
                dev_sharpe=2.8,
                dev_pnl=110.0,
                dev_drawdown=0.15,
                avg_dev_sharpe=-1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=-6.0,
                weak_window_sharpe=-6.0,
                negative_dev_worst_windows=0,
                promotion_candidate=False,
                promotion_failure_reason="worst_test_sharpe",
            ),
            rl_best=None,
            best_variant="current",
            best_score=0.8,
            best_params=None,
            comparison_payload={},
        )

        markdown = build_markdown_report(report)

        self.assertIn("dev_primary_improved", markdown)
        self.assertIn("hostile_window_regressed", markdown)
        self.assertIn("hostile_regression_driver", markdown)

    def test_full_training_readiness_treats_deribit_as_warning_not_hard_blocker(self) -> None:
        report = SearchReport(
            created_at="2026-04-10T00:00:00+00:00",
            symbol="BTCUSDT",
            readiness={"promotion_ready": False, "blocked_by": "not_promotion_ready"},
            deribit_status={"enabled": True, "observation_ready": False, "research_ready": False},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={},
            training_input_contract={
                "execution_framework": {
                    "five_min_direct_execution_enabled": True,
                    "five_min_feature_aggregation_enabled": True,
                    "one_hour_bars_available": True,
                    "four_hour_bars_available": True,
                },
                "state_feature_sources": {
                    "hyperliquid_in_state_features": True,
                    "deribit_in_state_features": True,
                    "etf_flow_in_state_features": True,
                },
                "signal_independence_contract": {
                    "audit_present": True,
                    "direction_layer_clean": True,
                    "state_layer_clean": True,
                    "blocked_by": None,
                },
            },
            search_status={
                "search_space_profile": {
                    "direction_weight_policy": {
                        "correlation_constrained": True,
                        "capped_direction_signal": "crowding_signal",
                        "demoted_signal": "funding_alpha_signal",
                        "demoted_signal_role": "state",
                    }
                }
            },
            current=CandidateSummary(
                variant="current",
                robust_score=1.0,
                selection_split="dev",
                dev_sharpe=1.0,
                dev_pnl=10.0,
                dev_drawdown=0.1,
                avg_dev_sharpe=1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=0.5,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            paulwei_baseline=CandidateSummary(
                variant="paulwei_baseline",
                robust_score=1.0,
                selection_split="dev",
                dev_sharpe=1.0,
                dev_pnl=10.0,
                dev_drawdown=0.1,
                avg_dev_sharpe=1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=0.5,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            optuna_best=CandidateSummary(
                variant="optuna_tuned",
                robust_score=1.2,
                selection_split="dev",
                dev_sharpe=1.1,
                dev_pnl=12.0,
                dev_drawdown=0.1,
                avg_dev_sharpe=1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=0.5,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            rl_best=None,
            best_variant="optuna_tuned",
            best_score=1.2,
            best_params={"x": 1},
            comparison_payload={},
            smoke_completed=True,
        )

        assessment = _assess_full_training_readiness(report, smoke_completed=True)

        self.assertTrue(assessment["ready"])
        self.assertIn("deribit_observation_not_ready", assessment["warnings"])
        self.assertNotIn("deribit_observation_not_ready", assessment["blockers"])

    def test_dev_fee_stability_gate_blocks_train_dev_fee_mismatch(self) -> None:
        best_params = {"target_vol_4h": 0.35, "intent_time_leverage_min_hold_bars": 336.0}
        report = SearchReport(
            created_at="2026-05-05T00:00:00+00:00",
            symbol="BTCUSDT",
            readiness={"promotion_ready": False, "blocked_by": "not_promotion_ready"},
            deribit_status={"enabled": True, "observation_ready": True, "research_ready": True},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={},
            training_input_contract={
                "execution_framework": {
                    "five_min_direct_execution_enabled": True,
                    "five_min_feature_aggregation_enabled": True,
                    "one_hour_bars_available": True,
                    "four_hour_bars_available": True,
                },
                "state_feature_sources": {
                    "hyperliquid_in_state_features": True,
                    "etf_flow_in_state_features": True,
                    "mvrv_in_state_features": True,
                    "deribit_block_flow_in_state_features": True,
                },
                "signal_independence_contract": {
                    "audit_present": True,
                    "direction_layer_clean": True,
                    "state_layer_clean": True,
                    "blocked_by": None,
                },
            },
            search_status={
                "search_space_profile": {
                    "direction_weight_policy": {
                        "correlation_constrained": True,
                        "capped_direction_signal": "crowding_signal",
                        "demoted_signal": "funding_alpha_signal",
                        "demoted_signal_role": "state",
                    }
                },
                "train_candidate_diagnostics": {
                    "top_train_trial_diagnostics": [
                        {
                            "rank": 1,
                            "params": best_params,
                            "metrics": {"fee_drag_pct": 0.011215, "sharpe": 1.7271},
                        }
                    ]
                },
            },
            current=CandidateSummary(
                variant="current",
                robust_score=1.0,
                selection_split="dev",
                dev_sharpe=1.0,
                dev_pnl=10.0,
                dev_drawdown=0.1,
                avg_dev_sharpe=1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=0.5,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            paulwei_baseline=CandidateSummary(
                variant="paulwei_baseline",
                robust_score=1.0,
                selection_split="dev",
                dev_sharpe=1.0,
                dev_pnl=10.0,
                dev_drawdown=0.1,
                avg_dev_sharpe=1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=0.5,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            optuna_best=CandidateSummary(
                variant="optuna_tuned",
                robust_score=1.2,
                selection_split="dev",
                dev_sharpe=4.5647,
                dev_pnl=24_807.49,
                dev_drawdown=0.058481,
                avg_dev_sharpe=9.6997,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=4.5647,
                weak_window_sharpe=10.1243,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
                turnover_per_day=5141.09,
                fee_drag_pct=0.057135,
            ),
            rl_best=None,
            best_variant="optuna_tuned",
            best_score=1.2,
            best_params=best_params,
            comparison_payload={
                "window_reports": [
                    {"variant": "optuna_tuned", "window_label": "dev", "metrics": {"fee_drag_pct": 0.057135}},
                    {"variant": "optuna_tuned", "window_label": "2023-08", "metrics": {"fee_drag_pct": 0.0726}},
                ]
            },
            smoke_completed=True,
        )

        fee_summary = _dev_fee_stability_summary(report)
        assessment = _assess_full_training_readiness(report, smoke_completed=True)
        scenario = _classify_post_smoke_scenario(report, full_training_assessment=assessment)

        self.assertFalse(fee_summary["passed"])
        self.assertIn("dev_fee_drag_cap_exceeded", fee_summary["failures"])
        self.assertIn("dev_train_fee_drag_mismatch", fee_summary["failures"])
        self.assertIn("hostile_window_fee_drag_cap_exceeded", fee_summary["failures"])
        self.assertFalse(assessment["ready"])
        self.assertIn("dev_train_fee_drag_mismatch", assessment["blockers"])
        self.assertEqual(scenario["scenario"], "scenario_b_fee_efficiency_failure")

    def test_classify_post_smoke_scenario_no_tuned_candidate_is_not_data_gap(self) -> None:
        report = SearchReport(
            created_at="2026-04-10T00:00:00+00:00",
            symbol="BTCUSDT",
            readiness={"promotion_ready": False, "blocked_by": "not_promotion_ready"},
            deribit_status={"enabled": True, "observation_ready": False, "research_ready": False},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={},
            training_input_contract={
                "execution_framework": {
                    "five_min_direct_execution_enabled": True,
                    "five_min_feature_aggregation_enabled": True,
                    "one_hour_bars_available": True,
                    "four_hour_bars_available": True,
                },
                "state_feature_sources": {
                    "hyperliquid_in_state_features": True,
                    "deribit_in_state_features": True,
                    "etf_flow_in_state_features": True,
                },
                "signal_independence_contract": {
                    "audit_present": True,
                    "direction_layer_clean": True,
                    "state_layer_clean": True,
                    "blocked_by": None,
                },
            },
            search_status={
                "search_space_profile": {
                    "direction_weight_policy": {
                        "correlation_constrained": True,
                        "capped_direction_signal": "crowding_signal",
                        "demoted_signal": "funding_alpha_signal",
                        "demoted_signal_role": "state",
                    }
                }
            },
            current=CandidateSummary(
                variant="current",
                robust_score=1.0,
                selection_split="dev",
                dev_sharpe=1.0,
                dev_pnl=10.0,
                dev_drawdown=0.1,
                avg_dev_sharpe=1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=0.5,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            paulwei_baseline=CandidateSummary(
                variant="paulwei_baseline",
                robust_score=1.0,
                selection_split="dev",
                dev_sharpe=1.0,
                dev_pnl=10.0,
                dev_drawdown=0.1,
                avg_dev_sharpe=1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=0.5,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=0,
                promotion_candidate=True,
            ),
            optuna_best=None,
            rl_best=None,
            best_variant="paulwei_baseline",
            best_score=1.0,
            best_params=None,
            comparison_payload={},
            smoke_completed=True,
        )

        assessment = _assess_full_training_readiness(report, smoke_completed=True)
        scenario = _classify_post_smoke_scenario(report, full_training_assessment=assessment)

        self.assertEqual(scenario["scenario"], "scenario_b_no_tuned_candidate")
        self.assertEqual(scenario["next_action"], "tighten_train_search_and_rerun_smoke")

    def test_write_search_report_emits_delivery_fields(self) -> None:
        summary = CandidateSummary(
            variant="current",
            robust_score=1.2,
            selection_split="dev",
            dev_sharpe=1.1,
            dev_pnl=100.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=0.9,
            worst_dev_sharpe=0.4,
            weak_window_sharpe=0.5,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
            blocked_by=None,
            failed_gate_metrics={"avg_test_sharpe": True},
            promotion_failure_reason=None,
        )
        report = SearchReport(
            created_at="2026-04-08T00:00:00+00:00",
            symbol="BTCUSDT",
            readiness={
                "data_ready": False,
                "promotion_ready": False,
                "blocked_by": "missing_liquidation_history",
                "detail": "missing",
            },
            deribit_status={"enabled": True, "observation_ready": False, "research_ready": False},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={"fast_windows": ["overall_recent"]},
            training_input_contract={
                "primary_truth_source": "binance",
                "historical_extension_train_enabled": False,
                "historical_extension_mode": "spot_context_only",
                "train_truth_legs": [
                    {"name": "post_2020_futures_train", "source": "futures"},
                ],
                "dev_truth_source": "futures_only",
                "test_truth_source": "futures_only",
                "mixed_era_declared": False,
                "historical_extension_candidate_contract": {
                    "full_unified_truth": False,
                    "spot_train_leg_blocked_by": ["execution_parity_not_verified_pre_2020"],
                },
                "execution_framework": {
                    "five_min_feature_aggregation_enabled": True,
                    "five_min_direct_execution_enabled": True,
                    "final_execution_timeframe": "5m",
                },
                "state_feature_sources": {
                    "deribit_in_state_features": True,
                    "hyperliquid_in_state_features": True,
                    "etf_flow_in_state_features": True,
                    "etf_flow_source": "bitbo_etf_flows",
                    "etf_flow_source_confidence": "medium",
                    "etf_flow_last_success_at": "2026-04-10T00:00:00+00:00",
                },
                "signal_stack_contract": {"direction_state_execution_split_enabled": True},
                "signal_independence_contract": {
                    "audit_present": True,
                    "direction_layer_clean": True,
                    "state_layer_clean": True,
                    "blocked_by": None,
                },
            },
            search_status={
                "optuna_available": False,
                "optuna_trials_requested": 2,
                "rl_episodes_requested": 2,
                "liquidation_in_search": False,
                "search_space_profile": {
                    "profile": "reduced_core_v2",
                    "search_space_mode": "baseline_anchored_train_only",
                    "baseline_anchored_search": True,
                    "baseline_anchor_variant": "paulwei_baseline",
                    "direction_weight_policy": {
                        "correlation_constrained": True,
                        "capped_direction_signal": "crowding_signal",
                        "demoted_signal": "funding_alpha_signal",
                        "demoted_signal_role": "state",
                    },
                },
                "etf_flow_source": "bitbo_etf_flows",
                "etf_flow_source_confidence": "medium",
                "etf_flow_last_success_at": "2026-04-10T00:00:00+00:00",
                "stage1_execution_mode": "1h_context_only_with_5m_fallback",
                "train_candidate_diagnostics": {"raw_trial_count": 5, "raw_nonzero_trade_trials": 1},
                "empty_shortlist_reason": None,
                "no_tuned_candidate_reason": "proxy_penalized_out",
                "historical_extension_train_enabled": False,
                "historical_extension_mode": "spot_context_only",
                "mixed_era_declared": False,
                "train_truth_legs": [
                    {"name": "post_2020_futures_train", "source": "futures"},
                ],
                "spot_train_leg_blocked_by": ["execution_parity_not_verified_pre_2020"],
                "full_unified_truth": False,
            },
            current=summary,
            paulwei_baseline=summary,
            optuna_best=None,
            rl_best=None,
            best_variant="current",
            best_score=1.2,
            best_params=None,
            comparison_payload={},
            test_comparison_payload=None,
            test_evaluated=False,
            report_mode="fast",
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "framework_optimization.json"
            write_search_report(path, report)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertIn("data_sufficiency", payload)
        self.assertIn("historical_leaderboard", payload)
        self.assertIn("final_verdict", payload)
        self.assertIn("search_status", payload)
        self.assertIn("signal_combination_audit", payload)
        self.assertIn("full_training_assessment", payload)
        self.assertIn("framework_diagnosis", payload)
        self.assertIn("training_input_contract", payload)
        self.assertIn("full_training_allowed", payload)
        self.assertIn("full_training_blockers", payload)
        self.assertIn("full_training_decision", payload)
        self.assertIn("signal_independence_contract", payload["training_input_contract"])
        self.assertEqual(payload["search_space_mode"], "baseline_anchored_train_only")
        self.assertTrue(payload["baseline_anchored_search"])
        self.assertEqual(payload["baseline_anchor_variant"], "paulwei_baseline")
        self.assertEqual(payload["stage1_execution_mode"], "1h_context_only_with_5m_fallback")
        self.assertEqual(payload["train_candidate_diagnostics"]["raw_trial_count"], 5)
        self.assertIsNone(payload["empty_shortlist_reason"])
        self.assertEqual(payload["no_tuned_candidate_reason"], "proxy_penalized_out")
        self.assertFalse(payload["historical_extension_train_enabled"])
        self.assertEqual(payload["historical_extension_mode"], "spot_context_only")
        self.assertFalse(payload["mixed_era_declared"])
        self.assertFalse(payload["full_unified_truth"])
        self.assertIsNone(payload["optuna_tuned"])
        self.assertIsNone(payload["rl_tuned"])
        self.assertTrue(
            payload["training_input_contract"]["signal_stack_contract"]["direction_state_execution_split_enabled"]
        )
        self.assertTrue(payload["training_input_contract"]["execution_framework"]["five_min_direct_execution_enabled"])
        self.assertEqual(payload["final_verdict"]["best_variant"], "current")
        self.assertTrue(payload["final_verdict"]["candidate_generation_failed"])
        self.assertEqual(
            payload["final_verdict"]["candidate_generation_failure_driver"],
            "proxy_penalized_out",
        )
        self.assertEqual(payload["final_verdict"]["no_tuned_candidate_reason"], "proxy_penalized_out")
        self.assertIn("promotion_failure_reason", payload["final_verdict"])
        self.assertIn("search_space_profile", payload["final_verdict"])
        self.assertIn("comparison_to_paulwei_baseline", payload["final_verdict"])
        self.assertIn("comparison_to_paulwei_public", payload["final_verdict"])
        self.assertIn("full_training_ready", payload["final_verdict"])
        self.assertIn("baseline_gate_pass_current", payload["final_verdict"])
        self.assertIn("gap_to_current", payload["final_verdict"])
        self.assertIn("candidate_score", payload["final_verdict"])
        self.assertIn("weakest_window_penalty", payload["final_verdict"])
        self.assertIn("hostile_fragility_penalty", payload["final_verdict"])
        self.assertEqual(payload["final_verdict"]["etf_flow_source"], "bitbo_etf_flows")
        self.assertEqual(payload["campaign_execution_contract"]["etf_flow_source_confidence"], "medium")
        self.assertEqual(payload["final_verdict"]["historical_extension_mode"], "spot_context_only")
        self.assertFalse(payload["final_verdict"]["full_unified_truth"])
        self.assertEqual(payload["full_training_assessment"]["decision"], "defer_handoff")
        self.assertIn("handoff_blockers", payload)

    def test_run_optuna_search_uses_train_only_autoresearch_candidates(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float

        with patch(
            "zenquant.research.framework_search.run_search_stage",
            return_value=FakeSweep(
                total_combinations=2,
                results=[],
                top_n=[
                    {"params": {"w_momentum": 0.3}, "metrics": {"sharpe": 1.0, "score": 1.0}},
                    {"params": {"w_momentum": 0.45}, "metrics": {"sharpe": 1.2, "score": 1.2}},
                ],
                runtime_s=0.1,
            ),
        ) as run_stage:
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            best, ranked, diagnostics = run_optuna_search(evaluator, n_trials=5)

        self.assertEqual(best, {"w_momentum": 0.45})
        self.assertEqual(ranked[0]["candidate_source"], "auto_research_train_only")
        self.assertEqual(ranked[0]["split_contract_version"], "btc_temporal_v2_train_selection_only")
        self.assertEqual(ranked[0]["summary"]["selection_split"], "train")
        self.assertIn("regime_objective", ranked[0]["combined_train_diagnostics"])
        self.assertIn("regime_objective_score", ranked[0]["train_metrics"])
        self.assertTrue(diagnostics["train_candidate_diagnostics"]["regime_objective_enabled"])
        self.assertIn("train_candidate_diagnostics", diagnostics)
        run_stage.assert_called_once()

    def test_run_optuna_search_attaches_bull_window_attribution_to_temporal_rejections(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float
            train_candidate_diagnostics: dict

        temporal_audit = {
            "primary_blocker": "paulwei_2020_profit_run_fail_fast",
            "blocked_by": ["paulwei_2020_profit_run_fail_fast"],
            "score_adjustment": -1.0,
        }
        bull_attribution = {
            "scope": "train_dev_only_bull_window_direction_exposure_attribution",
            "promotion_allowed": False,
            "test_evaluated": False,
            "windows": {
                "train_temporal_2020": {"diagnosis": {"primary_failure_mode": "uptrend_not_converted_to_long_intent"}}
            },
        }

        with (
            patch(
                "zenquant.research.framework_search.run_search_stage",
                return_value=FakeSweep(
                    total_combinations=1,
                    results=[],
                    top_n=[
                        {
                            "params": {"w_momentum": 0.3},
                            "metrics": {"sharpe": 2.0, "score": 2.0, "net_pnl": 50_000.0, "max_drawdown": 0.08},
                            "baseline_train_metrics": {},
                        },
                    ],
                    runtime_s=0.1,
                    train_candidate_diagnostics={"raw_trial_count": 1},
                ),
            ),
            patch(
                "zenquant.research.framework_search.score_regime_objective_candidate_generation",
                return_value={"regime_objective_score": 2.0, "allow_dev_diagnostic_review": True},
            ),
            patch("zenquant.research.framework_search._spot_context_replay_audit", return_value=None),
            patch("zenquant.research.framework_search._temporal_robustness_audit", return_value=temporal_audit),
            patch(
                "zenquant.research.framework_search._bull_window_direction_exposure_attribution",
                return_value=bull_attribution,
            ) as attribution,
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            best, ranked, diagnostics = run_optuna_search(evaluator, n_trials=1)

        self.assertIsNone(best)
        self.assertEqual(ranked, [])
        rejected = diagnostics["train_candidate_diagnostics"]["rejected_candidate_diagnostics"]
        self.assertEqual(rejected[0]["bull_window_direction_exposure_attribution"], bull_attribution)
        self.assertTrue(
            diagnostics["train_candidate_diagnostics"]["bull_window_direction_exposure_attribution_enabled"]
        )
        self.assertEqual(
            diagnostics["train_candidate_diagnostics"]["best_bull_window_direction_exposure_attribution"],
            bull_attribution,
        )
        attribution.assert_called_once()

    def test_run_optuna_search_rejects_regime_bucket_failure_before_dev_screen(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float

        top_n = [
            {
                "params": {"w_momentum": 0.3},
                "metrics": {
                    "sharpe": 2.0,
                    "score": 2.0,
                    "hostile_proxy_score": 0.5,
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.1}},
                },
                "baseline_train_metrics": {
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.4}},
                },
            },
        ]
        baseline = CandidateSummary(
            variant="paulwei_baseline",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=1.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.1,
            weak_window_sharpe=0.1,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
        )
        with (
            patch(
                "zenquant.research.framework_search.run_search_stage",
                return_value=FakeSweep(total_combinations=2, results=[], top_n=top_n, runtime_s=0.1),
            ),
            patch.object(FrameworkEvaluator, "_get_current_fast_summary", return_value=baseline) as current_summary,
            patch.object(FrameworkEvaluator, "_get_paulwei_fast_summary", return_value=baseline) as paulwei_summary,
            patch.object(FrameworkEvaluator, "_evaluate_variant") as evaluate_variant,
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            best, ranked, diagnostics = run_optuna_search(evaluator, n_trials=5)

        current_summary.assert_not_called()
        paulwei_summary.assert_not_called()
        evaluate_variant.assert_not_called()
        self.assertIsNone(best)
        self.assertEqual(ranked, [])
        self.assertEqual(diagnostics["empty_shortlist_reason"], "stage1_fail_fast_rejected")
        self.assertEqual(
            diagnostics["train_candidate_diagnostics"]["stage1_fail_fast_reasons"],
            {"regime_objective_train_bucket_fail_fast": 1},
        )
        self.assertEqual(diagnostics["train_candidate_diagnostics"]["regime_objective_rejection_count"], 1)
        rejected = diagnostics["train_candidate_diagnostics"]["rejected_candidate_diagnostics"]
        self.assertEqual(rejected[0]["params"], {"w_momentum": 0.3})
        self.assertEqual(rejected[0]["stage1_fail_fast_reason"], "regime_objective_train_bucket_fail_fast")
        self.assertFalse(rejected[0]["regime_objective"]["allow_dev_diagnostic_review"])

    def test_run_optuna_search_passes_stage1_target_inflight(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float

        with patch(
            "zenquant.research.framework_search.run_search_stage",
            return_value=FakeSweep(
                total_combinations=1,
                results=[],
                top_n=[{"params": {"w_momentum": 0.45}, "metrics": {"sharpe": 1.2, "score": 1.2}}],
                runtime_s=0.1,
            ),
        ) as run_stage:
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            run_optuna_search(
                evaluator,
                n_trials=5,
                search_execution_timeframe=Timeframe.TF_5M,
                stage1_parallel_mode="cpu_parallel",
                stage1_max_workers=6,
                stage1_target_inflight=6,
                stage1_batch_size=4,
            )

        config = run_stage.call_args.args[0]
        self.assertEqual(config.stage1_parallel_mode, "cpu_parallel")
        self.assertEqual(config.stage1_max_workers, 6)
        self.assertEqual(config.stage1_target_inflight, 6)
        self.assertEqual(config.stage1_batch_size, 4)
        self.assertEqual(config.min_completed_trials, 5)
        self.assertEqual(config.early_stop_fee_drag_buffer, 2.0)

    def test_run_optuna_search_tightens_fee_drag_stop_for_fee_repair_modes(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float

        with patch(
            "zenquant.research.framework_search.run_search_stage",
            return_value=FakeSweep(
                total_combinations=1,
                results=[],
                top_n=[{"params": {"w_momentum": 0.45}, "metrics": {"sharpe": 1.2, "score": 1.2}}],
                runtime_s=0.1,
            ),
        ) as run_stage:
            evaluator = FrameworkEvaluator(
                HistoricalBacktestConfig(),
                split_spec=default_btc_split_spec(),
                search_space_mode="intent_churn_ablation_train_only",
            )
            run_optuna_search(evaluator, n_trials=5)

        config = run_stage.call_args.args[0]
        self.assertEqual(config.early_stop_fee_drag_buffer, 1.25)
        self.assertFalse(config.enable_early_stop)
        self.assertIsNone(config.trial_runtime_budget_s)

    def test_run_optuna_search_treats_pre_2020_spot_as_context_only(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float
            train_candidate_diagnostics: dict
            empty_shortlist_reason: str | None = None
            budget_mode: str = "trial_count_primary"
            stop_reason: str = "trial_budget_reached"

        with (
            patch(
                "zenquant.research.framework_search.run_search_stage",
                return_value=FakeSweep(
                    total_combinations=2,
                    results=[],
                    top_n=[
                        {
                            "params": {"w_momentum": 0.3},
                            "metrics": {"sharpe": 2.0, "score": 2.0, "net_pnl": 5.0, "max_drawdown": 0.1},
                        },
                        {
                            "params": {"w_momentum": 0.45},
                            "metrics": {"sharpe": 1.6, "score": 1.6, "net_pnl": 5.0, "max_drawdown": 0.1},
                        },
                    ],
                    runtime_s=0.1,
                    train_candidate_diagnostics={"raw_trial_count": 2},
                ),
            ),
            patch(
                "zenquant.research.framework_search.load_historical_extension_candidate_contract",
                return_value={
                    "candidate_source_type": "spot",
                    "candidate_name": "binance_spot_2017_plus",
                    "ready": True,
                    "layered_train_enabled": False,
                    "historical_extension_mode": "spot_context_only",
                    "formal_train_truth": False,
                    "train_truth_legs": [
                        {"name": "post_2020_futures_train", "source": "futures"},
                    ],
                    "dev_truth_source": "futures_only",
                    "test_truth_source": "futures_only",
                    "mixed_era_declared": False,
                    "full_unified_truth": False,
                    "blocked_by": ["spot_perp_truth_boundary"],
                    "spot_train_leg_blocked_by": ["spot_context_only_not_formal_train_truth"],
                    "candidate_window_start": "2017-08-17T00:00:00+00:00",
                    "candidate_window_end": "2026-04-17T00:00:00+00:00",
                    "allowed_uses": [
                        "pre_2020_regime_context",
                        "train_side_regime_context_objective",
                    ],
                    "source_boundary": "spot context only",
                },
            ),
            patch(
                "zenquant.research.framework_search._spot_context_replay_audit",
                return_value={
                    "scope": "formal_auxiliary_train_regime_direction_profit_run",
                    "formal_train_truth": False,
                    "formal_auxiliary_train_leg": True,
                    "execution_truth": False,
                    "promotion_allowed": False,
                    "test_evaluated": False,
                    "windows": {
                        "spot_context_full_2017_2019": {
                            "sharpe": 1.5,
                            "net_pnl": 50_000.0,
                            "equity_multiple": 1.5,
                            "max_drawdown": 0.2,
                        }
                    },
                    "context_score_adjustment": 0.4,
                },
            ),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            best, ranked, diagnostics = run_optuna_search(evaluator, n_trials=5)

        self.assertEqual(best, {"w_momentum": 0.3})
        self.assertEqual(ranked[0]["params"], {"w_momentum": 0.3})
        self.assertIsNone(ranked[0]["spot_train_leg_metrics"])
        self.assertIsNotNone(ranked[0]["spot_context_metrics"])
        self.assertEqual(ranked[0]["spot_context_metrics"]["formal_train_truth"], False)
        self.assertEqual(ranked[0]["spot_context_metrics"]["formal_auxiliary_train_leg"], True)
        self.assertEqual(ranked[0]["train_metrics"]["spot_context_score_adjustment"], 0.4)
        self.assertEqual(ranked[0]["train_metrics"]["spot_context_formal_auxiliary_train_leg"], True)
        self.assertTrue(diagnostics["spot_era_context_replay_audit"]["enabled"])
        self.assertTrue(diagnostics["spot_era_context_replay_audit"]["formal_auxiliary_train_leg"])
        self.assertFalse(diagnostics["spot_era_context_replay_audit"]["execution_truth"])
        self.assertEqual(diagnostics["historical_extension_mode"], "spot_context_only")

    def test_final_report_passes_smoke_minimal_comparison_mode_and_heartbeat(self) -> None:
        summary = CandidateSummary(
            variant="current",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=10.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.5,
            weak_window_sharpe=0.2,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
            promotion_failure_reason="avg_test_sharpe",
        )
        heartbeats: list[dict[str, object]] = []
        build_calls: list[dict[str, object]] = []

        def _status_callback(**payload: object) -> None:
            heartbeats.append(payload)

        def _fake_build(*_args, **kwargs):
            build_calls.append(kwargs)
            callback = kwargs.get("status_callback")
            if callback is not None:
                callback(
                    event="comparison_window_completed",
                    variant="current",
                    window_label="dev",
                    completed_units=1,
                    total_units=1,
                )
            return {
                "report_mode": kwargs["report_mode"],
                "comparison_compute_mode": kwargs["comparison_compute_mode"],
                "window_reports": [],
                "validation_reports": [],
            }

        def _fake_evaluate_variant(_variant, *, fast, lightweight=False, progress_callback=None):
            del fast
            del lightweight
            if progress_callback is not None:
                progress_callback(1, "dev")
                progress_callback(2, "2023-08")
                progress_callback("baseline_summary_started", "lightweight")
                progress_callback("baseline_summary_completed", "lightweight")
                progress_callback(3, "worst_windows")
                progress_callback("summary_scoring_started", "lightweight")
                progress_callback("summary_scoring_completed", "lightweight")
                progress_callback(4, "lightweight_summary")
            return summary

        with (
            patch.object(FrameworkEvaluator, "_evaluate_variant", side_effect=_fake_evaluate_variant),
            patch("zenquant.research.framework_search._build_comparison_payload", side_effect=_fake_build),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            report = evaluator.final_report(
                optuna_params={"w_momentum": 0.3},
                rl_params=None,
                search_status={"stage1_execution_mode": "1h_context_only"},
                full=False,
                evaluate_test=False,
                include_comparison_payload=True,
                status_callback=_status_callback,
                comparison_compute_mode="smoke_minimal",
                evaluation_role="development",
                test_access_allowed=False,
                dev_gate_used_this_round=True,
            )

        self.assertEqual(len(build_calls), 1)
        self.assertEqual(build_calls[0]["comparison_compute_mode"], "smoke_minimal")
        self.assertEqual(report.comparison_payload["comparison_compute_mode"], "smoke_minimal")
        self.assertEqual(report.comparison_payload["report_mode"], "summary_only")
        events = [str(item["event"]) for item in heartbeats]
        self.assertIn("stage2_prepare_variants_started", events)
        self.assertIn("stage2_prepare_variant_unit_completed", events)
        self.assertIn("stage2_prepare_variant_validation_progress", events)
        self.assertIn("stage2_compare_windows_started", events)
        self.assertIn("comparison_window_completed", events)
        self.assertIn("stage2_rank_variants_completed", events)
        prepare_units = [
            int(item["completed_units"])
            for item in heartbeats
            if item.get("event") == "stage2_prepare_variant_unit_completed"
        ]
        self.assertIn(4, prepare_units)
        self.assertTrue(any(unit > 4 for unit in prepare_units))
        prepare_detail_units = [
            str(item["unit"])
            for item in heartbeats
            if item.get("event") == "stage2_prepare_variant_validation_progress"
        ]
        self.assertTrue(any(unit.startswith("baseline_summary_started:") for unit in prepare_detail_units))
        self.assertTrue(any(unit.startswith("summary_scoring_completed:") for unit in prepare_detail_units))
        self.assertEqual(report.evaluation_role, "development")

    def test_evaluate_variant_reports_cache_reuse_progress(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        cached_summary = CandidateSummary(
            variant="current",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=10.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.5,
            weak_window_sharpe=0.2,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
        )
        variant = evaluator._current_variant
        evaluator._summary_cache[(evaluator._variant_key(variant), True, True)] = cached_summary
        progress: list[tuple[object, str]] = []

        summary = evaluator._evaluate_variant(
            variant,
            fast=True,
            lightweight=True,
            progress_callback=lambda step_completed, unit: progress.append((step_completed, unit)),
        )

        self.assertIs(summary, cached_summary)
        self.assertIn(("cache_hit", "summary_cache"), progress)
        self.assertIn(("replay_reused", "summary_cache"), progress)
        self.assertIn((4, "cached"), progress)

    def test_evaluate_variant_reports_cache_miss_progress(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        variant = evaluator._current_variant
        progress: list[tuple[object, str]] = []

        with patch.object(
            evaluator,
            "_run_prepare_backtest_cached",
            return_value=BacktestSnapshot(
                sharpe=1.0,
                net_pnl=10.0,
                max_drawdown=0.1,
                win_rate=0.5,
                sortino=1.2,
                profit_factor=1.1,
                turnover_per_day=0.5,
                fee_drag_pct=0.05,
            ),
        ):
            summary = evaluator._evaluate_variant(
                variant,
                fast=True,
                lightweight=True,
                progress_callback=lambda step_completed, unit: progress.append((step_completed, unit)),
            )

        self.assertEqual(summary.variant, "current")
        self.assertIn(("cache_miss", "summary_cache"), progress)
        self.assertIn(("cache_miss", "window_snapshot:dev"), progress)
        self.assertIn(("cache_miss", "window_snapshot:2023-08"), progress)

    def test_stage2_runtime_contract_uses_spawn_on_darwin(self) -> None:
        fake_variants = [
            type("Variant", (), {"name": "current"})(),
            type("Variant", (), {"name": "paulwei_baseline"})(),
        ]
        with (
            patch("zenquant.research.framework_search.sys.platform", "darwin"),
            patch("zenquant.research.framework_search.default_variant_specs", return_value=fake_variants),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())

        self.assertEqual(
            evaluator.stage2_runtime_contract,
            {
                "stage2_mp_start_method": "spawn",
                "validation_pool_mode": "process_pool_spawn",
            },
        )

    def test_get_validation_pool_uses_explicit_mp_context(self) -> None:
        fake_variants = [
            type("Variant", (), {"name": "current"})(),
            type("Variant", (), {"name": "paulwei_baseline"})(),
        ]
        executor_calls: list[dict[str, object]] = []

        class FakeExecutor:
            def __init__(self, **kwargs) -> None:
                executor_calls.append(kwargs)

            def shutdown(self, **_kwargs) -> None:
                return None

        with (
            patch("zenquant.research.framework_search.default_variant_specs", return_value=fake_variants),
            patch("zenquant.research.framework_search.ProcessPoolExecutor", FakeExecutor),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            evaluator._stage2_mp_start_method = "spawn"
            evaluator._validation_pool = None
            evaluator._get_validation_pool(workers=2)

        self.assertEqual(executor_calls[0]["max_workers"], 2)
        self.assertEqual(executor_calls[0]["mp_context"].get_start_method(), mp.get_context("spawn").get_start_method())

    def test_variant_backtest_timeout_does_not_wait_for_local_pool_shutdown(self) -> None:
        shutdown_calls: list[tuple[bool, bool]] = []
        cancel_calls: list[bool] = []

        class FakeFuture:
            def result(self, timeout=None):
                del timeout
                raise FutureTimeoutError()

            def cancel(self) -> bool:
                cancel_calls.append(True)
                return True

        class FakeExecutor:
            def __init__(self, **_kwargs) -> None:
                return None

            def submit(self, *_args, **_kwargs) -> FakeFuture:
                return FakeFuture()

            def shutdown(self, *, wait=True, cancel_futures=False) -> None:
                shutdown_calls.append((wait, cancel_futures))

        with (
            patch("zenquant.research.framework_search.ProcessPoolExecutor", FakeExecutor),
            self.assertRaisesRegex(TimeoutError, "variant_backtest_timeout:1s"),
        ):
            _run_variant_backtest_with_timeout(
                HistoricalBacktestConfig(),
                FrameworkEvaluator(HistoricalBacktestConfig())._current_variant,
                start_ns=None,
                end_ns=None,
                timeout_s=1.0,
            )

        self.assertEqual(cancel_calls, [True])
        self.assertEqual(shutdown_calls, [(False, True)])

    def test_prepare_backtest_timeout_resets_validation_pool(self) -> None:
        shutdown_calls: list[tuple[bool, bool]] = []

        class FakePool:
            def shutdown(self, *, wait=False, cancel_futures=False) -> None:
                shutdown_calls.append((wait, cancel_futures))

        window = type("Window", (), {"label": "dev", "start_ns": None, "end_ns": None})()
        with patch(
            "zenquant.research.framework_search._run_variant_backtest_with_timeout",
            side_effect=TimeoutError("variant_backtest_timeout:1s"),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            fake_pool = FakePool()
            evaluator._validation_pool = fake_pool

            with self.assertRaises(PrepareVariantTimeoutError):
                evaluator._run_prepare_backtest_cached(
                    evaluator._current_variant,
                    window,
                    timeout_s=1.0,
                    replay_pool=fake_pool,
                )

        self.assertIsNone(evaluator._validation_pool)
        self.assertEqual(shutdown_calls, [(False, True)])

    def test_final_report_uses_fast_compare_window_profile_and_workers(self) -> None:
        summary = CandidateSummary(
            variant="current",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=10.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.5,
            weak_window_sharpe=0.2,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
            promotion_failure_reason="avg_test_sharpe",
        )
        build_calls: list[dict[str, object]] = []

        def _fake_build(*_args, **kwargs):
            build_calls.append(kwargs)
            return {
                "report_mode": kwargs["report_mode"],
                "comparison_compute_mode": kwargs["comparison_compute_mode"],
                "window_reports": [],
                "validation_reports": [],
            }

        with (
            patch.object(FrameworkEvaluator, "_evaluate_variant", return_value=summary),
            patch("zenquant.research.framework_search._build_comparison_payload", side_effect=_fake_build),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            self.assertEqual(
                [window.label for window in evaluator._fast_dev_compare_windows[:2]],
                ["dev", "2023-08"],
            )
            evaluator.final_report(
                optuna_params={"w_momentum": 0.3},
                rl_params=None,
                search_status={"stage1_execution_mode": "1h_context_only"},
                full=False,
                evaluate_test=False,
                include_comparison_payload=True,
                comparison_compute_mode="full",
                evaluation_role="development",
                test_access_allowed=False,
                dev_gate_used_this_round=True,
            )

        self.assertEqual(len(build_calls), 1)
        self.assertEqual(len(build_calls[0]["windows"]), len(evaluator._fast_dev_compare_windows))
        self.assertEqual(build_calls[0]["window_workers"], evaluator._compute_profiles["fast"]["compare_workers"])
        self.assertEqual(
            build_calls[0]["validation_workers"],
            evaluator._compute_profiles["fast"]["compare_validation_workers"],
        )

    def test_final_report_uses_lightweight_prepare_for_fast_but_not_high(self) -> None:
        captured: list[tuple[bool, bool, str]] = []

        def _fake_evaluate_variant(self, variant, *, fast, lightweight=False, progress_callback=None):
            del progress_callback
            captured.append((fast, lightweight, variant.name))
            return CandidateSummary(
                variant=variant.name,
                robust_score=1.0 if variant.name == "current" else 0.5,
                selection_split="dev",
                dev_sharpe=1.0,
                dev_pnl=10.0,
                dev_drawdown=0.1,
                avg_dev_sharpe=1.0,
                avg_dev_ratio=1.0,
                worst_dev_sharpe=0.5,
                weak_window_sharpe=0.2,
                negative_dev_worst_windows=0,
                promotion_candidate=False,
            )

        with patch.object(FrameworkEvaluator, "_evaluate_variant", new=_fake_evaluate_variant):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            evaluator.final_report(
                optuna_params=None,
                rl_params=None,
                search_status={"stage1_execution_mode": "5m_lightweight_train_search"},
                full=False,
                evaluate_test=False,
                include_comparison_payload=False,
                comparison_compute_mode="full",
                evaluation_role="development",
                test_access_allowed=False,
                dev_gate_used_this_round=True,
            )
            fast_calls = list(captured)
            captured.clear()
            evaluator.final_report(
                optuna_params=None,
                rl_params=None,
                search_status={"stage1_execution_mode": "5m_lightweight_train_search"},
                full=True,
                evaluate_test=False,
                include_comparison_payload=False,
                comparison_compute_mode="full",
                evaluation_role="development",
                test_access_allowed=False,
                dev_gate_used_this_round=True,
            )
            high_calls = list(captured)

        self.assertTrue(all(fast and lightweight for fast, lightweight, _ in fast_calls))
        self.assertTrue(all((not fast) and (not lightweight) for fast, lightweight, _ in high_calls))

    def test_compute_profiles_raise_smoke_compare_workers_without_expanding_windows(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())

        self.assertEqual(evaluator._compute_profiles["smoke"]["compare_workers"], 2)
        self.assertEqual(
            evaluator._compute_profiles["smoke"]["compare_windows"],
            len(evaluator._fast_dev_compare_windows),
        )
        self.assertEqual([window.label for window in evaluator._fast_dev_compare_windows[:2]], ["dev", "2023-08"])

    def test_development_report_rejects_test_evaluation(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        with self.assertRaisesRegex(ValueError, "development path must not evaluate test"):
            evaluator.final_report(
                optuna_params=None,
                rl_params=None,
                search_status={},
                full=False,
                evaluate_test=True,
                evaluation_role="development",
                test_access_allowed=False,
            )

    def test_train_tuner_caps_episode_progress_to_requested_budget(self) -> None:
        heartbeats: list[dict[str, object]] = []

        class FakeEnv:
            def __init__(self, *_args, **_kwargs) -> None:
                self._results = [
                    type(
                        "Result",
                        (),
                        {
                            "params": {"w_momentum": 0.3},
                            "candidate_score": 1.0,
                            "sharpe": 1.0,
                            "pnl": 10.0,
                            "max_dd": 0.1,
                            "trades": 5,
                        },
                    )()
                    for _ in range(7)
                ]

        class FakePPO:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def learn(self, total_timesteps: int, callback) -> None:
                callback.num_timesteps = total_timesteps + 2
                callback._on_step()

        with (
            patch("zenquant.research.rl_tuner.ParamTunerEnv", FakeEnv),
            patch("stable_baselines3.PPO", FakePPO),
        ):
            result = train_tuner(
                total_timesteps=5,
                verbose=0,
                status_callback=lambda **payload: heartbeats.append(payload),
            )

        progress_events = [item for item in heartbeats if item["event"] == "rl_progress"]
        self.assertTrue(progress_events)
        self.assertEqual(progress_events[-1]["completed_steps"], 5)
        self.assertEqual(progress_events[-1]["actual_steps"], 7)
        self.assertEqual(progress_events[-1]["requested_steps"], 5)
        self.assertEqual(result["search_status"]["episodes_completed"], 5)
        self.assertEqual(result["search_status"]["selection_split"], "train")
        self.assertFalse(result["search_status"]["dev_only_ranking"])
        self.assertEqual(result["candidate_source"], "rl_train_shortlist")

    def test_run_optuna_search_uses_smoke_candidate_timeout(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float

        with patch(
            "zenquant.research.framework_search.run_search_stage",
            return_value=FakeSweep(
                total_combinations=1,
                results=[],
                top_n=[{"params": {"w_momentum": 0.3}, "metrics": {"sharpe": 1.0, "score": 1.0}}],
                runtime_s=0.1,
            ),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())

            with self.assertRaisesRegex(Exception, "stage1_dev_validation_no_progress_timeout"):
                run_optuna_search(
                    evaluator,
                    n_trials=5,
                    ranking_top_n=1,
                    ranking_candidate_timeout_s=0.0,
                )

    def test_evaluate_variant_lightweight_skips_validation_bundle(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        variant = evaluator._current_variant

        def _snapshot(sharpe: float, pnl: float, dd: float):
            return type(
                "Snapshot",
                (),
                {
                    "sharpe": sharpe,
                    "net_pnl": pnl,
                    "max_drawdown": dd,
                    "win_rate": 0.5,
                },
            )()

        with (
            patch.object(
                evaluator,
                "_run_prepare_backtest_cached",
                side_effect=[
                    _snapshot(1.0, 10.0, 0.1),
                    _snapshot(0.4, 5.0, 0.08),
                    _snapshot(-0.2, -1.0, 0.12),
                ],
            ),
            patch.object(evaluator, "_run_validation_bundle_cached", side_effect=AssertionError("should_not_run")),
            patch.object(
                evaluator,
                "_get_current_fast_summary",
                return_value=CandidateSummary(
                    variant="current",
                    robust_score=0.5,
                    selection_split="dev",
                    dev_sharpe=0.5,
                    dev_pnl=5.0,
                    dev_drawdown=0.1,
                    avg_dev_sharpe=0.5,
                    avg_dev_ratio=1.0,
                    worst_dev_sharpe=0.1,
                    weak_window_sharpe=0.1,
                    negative_dev_worst_windows=0,
                    promotion_candidate=False,
                ),
            ),
        ):
            summary = evaluator._evaluate_variant(variant, fast=True, lightweight=True)

        self.assertEqual(summary.selection_split, "dev")
        self.assertAlmostEqual(summary.avg_dev_sharpe, 0.4)
        self.assertAlmostEqual(summary.worst_dev_sharpe, -0.2)
        self.assertEqual(
            [window.label for window in evaluator._prepare_windows(fast=True, lightweight=True)],
            ["dev", "2023-08", "2024-11 DD"],
        )

    def test_evaluate_variant_lightweight_emits_worst_window_progress(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        variant = evaluator._current_variant
        heartbeats: list[tuple[object, str]] = []

        def _snapshot(sharpe: float):
            return type(
                "Snapshot",
                (),
                {
                    "sharpe": sharpe,
                    "net_pnl": sharpe * 10.0,
                    "max_drawdown": 0.1,
                    "win_rate": 0.5,
                },
            )()

        with (
            patch.object(
                evaluator,
                "_run_prepare_backtest_cached",
                side_effect=[
                    _snapshot(1.0),
                    _snapshot(0.4),
                    _snapshot(-0.2),
                ],
            ),
            patch.object(
                evaluator,
                "_get_current_fast_summary",
                return_value=CandidateSummary(
                    variant="current",
                    robust_score=0.5,
                    selection_split="dev",
                    dev_sharpe=0.5,
                    dev_pnl=5.0,
                    dev_drawdown=0.1,
                    avg_dev_sharpe=0.5,
                    avg_dev_ratio=1.0,
                    worst_dev_sharpe=0.1,
                    weak_window_sharpe=0.1,
                    negative_dev_worst_windows=0,
                    promotion_candidate=False,
                ),
            ),
        ):
            evaluator._evaluate_variant(
                variant,
                fast=True,
                lightweight=True,
                progress_callback=lambda step, unit: heartbeats.append((step, unit)),
            )

        self.assertIn(("worst_window", "2024-11 DD"), heartbeats)
        self.assertIn((3, "worst_windows_completed"), heartbeats)

    def test_evaluate_variant_lightweight_timeout_degrades_tuned_candidate(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        variant = evaluator.candidate_variant({"w_momentum": 0.3}, name="optuna_tuned", label="Optuna Tuned")

        def _snapshot(sharpe: float):
            return type(
                "Snapshot",
                (),
                {
                    "sharpe": sharpe,
                    "net_pnl": sharpe * 10.0,
                    "max_drawdown": 0.1,
                    "win_rate": 0.5,
                },
            )()

        with (
            patch.object(
                evaluator,
                "_run_prepare_backtest_cached",
                side_effect=[
                    _snapshot(1.0),
                    _snapshot(0.4),
                    PrepareVariantTimeoutError(
                        variant="optuna_tuned",
                        window_label="2024-11 DD",
                        timeout_s=180.0,
                    ),
                ],
            ),
            patch.object(
                evaluator,
                "_get_current_fast_summary",
                return_value=CandidateSummary(
                    variant="current",
                    robust_score=0.5,
                    selection_split="dev",
                    dev_sharpe=0.5,
                    dev_pnl=5.0,
                    dev_drawdown=0.1,
                    avg_dev_sharpe=0.5,
                    avg_dev_ratio=1.0,
                    worst_dev_sharpe=0.1,
                    weak_window_sharpe=0.1,
                    negative_dev_worst_windows=0,
                    promotion_candidate=False,
                ),
            ),
        ):
            summary = evaluator._evaluate_variant(variant, fast=True, lightweight=True)

        self.assertEqual(summary.blocked_by, "prepare_timeout")
        self.assertFalse(summary.promotion_candidate)
        self.assertEqual(summary.promotion_failure_reason, "prepare_timeout")

    def test_evaluate_variant_lightweight_timeout_fails_current_with_precise_reason(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())

        def _snapshot(sharpe: float):
            return type(
                "Snapshot",
                (),
                {
                    "sharpe": sharpe,
                    "net_pnl": sharpe * 10.0,
                    "max_drawdown": 0.1,
                    "win_rate": 0.5,
                },
            )()

        with (
            patch.object(
                evaluator,
                "_run_prepare_backtest_cached",
                side_effect=[
                    _snapshot(1.0),
                    _snapshot(0.4),
                    PrepareVariantTimeoutError(
                        variant="current",
                        window_label="2024-11 DD",
                        timeout_s=180.0,
                    ),
                ],
            ),
            self.assertRaisesRegex(
                SearchStageTimeoutError,
                r"stage2_prepare_variant_timeout: variant=current window=2024-11 DD",
            ),
        ):
            evaluator._evaluate_variant(evaluator._current_variant, fast=True, lightweight=True)

    def test_evaluate_variant_degrades_tuned_candidate_on_validation_timeout(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        variant = evaluator.candidate_variant({"w_momentum": 0.3}, name="optuna_tuned", label="Optuna Tuned")

        def _snapshot(sharpe: float, pnl: float, dd: float):
            return type(
                "Snapshot",
                (),
                {
                    "sharpe": sharpe,
                    "net_pnl": pnl,
                    "max_drawdown": dd,
                    "win_rate": 0.5,
                },
            )()

        with (
            patch.object(
                evaluator,
                "_run_variant_backtest_cached",
                side_effect=[
                    _snapshot(1.1, 10.0, 0.09),
                    _snapshot(0.6, 5.0, 0.08),
                    _snapshot(0.2, -1.0, 0.09),
                    _snapshot(0.1, 1.0, 0.09),
                ],
            ),
            patch.object(
                evaluator,
                "_get_current_fast_summary",
                return_value=CandidateSummary(
                    variant="current",
                    robust_score=0.5,
                    selection_split="dev",
                    dev_sharpe=0.5,
                    dev_pnl=5.0,
                    dev_drawdown=0.12,
                    avg_dev_sharpe=0.5,
                    avg_dev_ratio=1.0,
                    worst_dev_sharpe=0.1,
                    weak_window_sharpe=0.1,
                    negative_dev_worst_windows=0,
                    promotion_candidate=False,
                ),
            ),
            patch.object(
                evaluator,
                "_run_validation_bundle_cached",
                side_effect=ValidationBundleTimeoutError(
                    variant="optuna_tuned",
                    window_label="wf_1",
                    leg="train",
                    timeout_s=240.0,
                ),
            ),
        ):
            summary = evaluator._evaluate_variant(variant, fast=True, lightweight=False)

        self.assertEqual(summary.blocked_by, "validation_timeout")
        self.assertFalse(summary.promotion_candidate)
        self.assertEqual(summary.promotion_failure_reason, "validation_timeout")
        self.assertEqual(summary.robust_score, -10.0)

    def test_evaluate_variant_prevalidation_rejection_skips_validation_bundle(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        variant = evaluator.candidate_variant({"w_momentum": 0.3}, name="optuna_tuned", label="Optuna Tuned")

        def _snapshot(sharpe: float, pnl: float, dd: float):
            return type(
                "Snapshot",
                (),
                {
                    "sharpe": sharpe,
                    "net_pnl": pnl,
                    "max_drawdown": dd,
                    "win_rate": 0.5,
                },
            )()

        baseline = CandidateSummary(
            variant="paulwei_baseline",
            robust_score=0.8,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=5.0,
            dev_drawdown=0.08,
            avg_dev_sharpe=0.9,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.2,
            weak_window_sharpe=0.1,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
        )

        with (
            patch.object(
                evaluator,
                "_run_variant_backtest_cached",
                side_effect=[
                    _snapshot(0.3, 10.0, 0.14),
                    _snapshot(0.1, 5.0, 0.10),
                    _snapshot(-0.2, -1.0, 0.12),
                    _snapshot(-0.1, 1.0, 0.09),
                ],
            ),
            patch.object(evaluator, "_get_current_fast_summary", return_value=baseline),
            patch.object(evaluator, "_run_validation_bundle_cached", side_effect=AssertionError("should_not_run")),
        ):
            summary = evaluator._evaluate_variant(variant, fast=True, lightweight=False)

        self.assertEqual(summary.blocked_by, "pre_validation_rejection")
        self.assertFalse(summary.promotion_candidate)
        self.assertEqual(summary.promotion_failure_reason, "pre_validation_rejection")
        self.assertEqual(summary.robust_score, -8.0)

    def test_run_validation_bundle_emits_leg_progress(self) -> None:
        windows = [
            type("Window", (), {"label": "wf_a", "start_ns": 100, "end_ns": 200})(),
            type("Window", (), {"label": "wf_b", "start_ns": 200, "end_ns": 300})(),
        ]
        variant = type("Variant", (), {"name": "optuna_tuned"})()
        events: list[tuple[str, str]] = []

        def _fake_run(_variant, *, start_ns, end_ns):
            del start_ns, end_ns
            return BacktestSnapshot(sharpe=1.0, net_pnl=10.0, max_drawdown=0.1, win_rate=0.5)

        metrics = _run_validation_bundle(
            HistoricalBacktestConfig(),
            variant,
            windows,
            run_backtest=_fake_run,
            progress_callback=lambda event, label: events.append((event, label)),
        )

        self.assertEqual(metrics["avg_test_sharpe"], 1.0)
        self.assertIn(("validation_train_started", "wf_a"), events)
        self.assertIn(("validation_train_completed", "wf_a"), events)
        self.assertIn(("validation_test_started", "wf_a"), events)
        self.assertIn(("validation_test_completed", "wf_b"), events)

    def test_evaluate_variant_lightweight_uses_lightweight_paulwei_baseline(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        current_variant = evaluator._current_variant
        heartbeats: list[tuple[int, str]] = []

        def _snapshot(sharpe: float, pnl: float, dd: float):
            return type(
                "Snapshot",
                (),
                {
                    "sharpe": sharpe,
                    "net_pnl": pnl,
                    "max_drawdown": dd,
                    "win_rate": 0.5,
                },
            )()

        with (
            patch.object(
                evaluator,
                "_run_prepare_backtest_cached",
                side_effect=[
                    _snapshot(1.0, 10.0, 0.1),
                    _snapshot(0.4, 5.0, 0.08),
                    _snapshot(-0.2, -1.0, 0.12),
                    _snapshot(0.8, 8.0, 0.11),
                    _snapshot(0.3, 3.0, 0.07),
                    _snapshot(-0.1, -1.0, 0.10),
                ],
            ),
            patch.object(evaluator, "_run_validation_bundle_cached", side_effect=AssertionError("should_not_run")),
        ):
            summary = evaluator._evaluate_variant(
                current_variant,
                fast=True,
                lightweight=True,
                progress_callback=lambda step, unit: heartbeats.append((step, unit)),
            )

        self.assertEqual(summary.selection_split, "dev")
        self.assertEqual(heartbeats[-1], (4, "lightweight_summary"))
        self.assertIsNotNone(evaluator._paulwei_fast_lightweight)
        self.assertIsNone(evaluator._paulwei_fast_full)

    def test_current_fast_summary_evaluates_current_without_paulwei_recursion(self) -> None:
        evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
        summary = CandidateSummary(
            variant="current",
            robust_score=0.5,
            selection_split="dev",
            dev_sharpe=0.5,
            dev_pnl=5.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=0.5,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.1,
            weak_window_sharpe=0.1,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
        )

        with (
            patch.object(
                evaluator,
                "_get_paulwei_fast_summary",
                side_effect=AssertionError("current summary must not recurse through PaulWei"),
            ) as paulwei_summary,
            patch.object(evaluator, "_evaluate_variant", return_value=summary) as evaluate_variant,
        ):
            result = evaluator._get_current_fast_summary(lightweight=False)

        self.assertIs(result, summary)
        paulwei_summary.assert_not_called()
        evaluate_variant.assert_called_once()
        self.assertIs(evaluate_variant.call_args.args[0], evaluator._current_variant)
        self.assertTrue(evaluate_variant.call_args.kwargs["fast"])
        self.assertFalse(evaluate_variant.call_args.kwargs["lightweight"])

    def test_acceptance_readiness_blocks_failed_test_candidate(self) -> None:
        baseline = CandidateSummary(
            variant="current",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=10.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.5,
            weak_window_sharpe=0.4,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
        )
        tuned = CandidateSummary(
            variant="optuna_tuned",
            robust_score=1.2,
            selection_split="dev",
            dev_sharpe=1.2,
            dev_pnl=12.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.1,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.6,
            weak_window_sharpe=0.5,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
            test_sharpe=-0.1,
            test_drawdown=0.08,
            test_adversarial_failures=0,
            test_evaluated=True,
            promotion_failure_reason="test_sharpe",
        )
        report = SearchReport(
            created_at="2026-05-04T00:00:00+00:00",
            symbol="BTCUSDT",
            readiness={"promotion_ready": True, "blocked_by": None},
            deribit_status={"observation_ready": True, "enabled": True, "research_ready": True},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={},
            training_input_contract={
                "execution_framework": {
                    "five_min_direct_execution_enabled": True,
                    "five_min_feature_aggregation_enabled": True,
                    "one_hour_bars_available": True,
                    "four_hour_bars_available": True,
                },
                "state_feature_sources": {
                    "hyperliquid_in_state_features": True,
                    "etf_flow_in_state_features": True,
                    "mvrv_in_state_features": True,
                    "deribit_block_flow_in_state_features": True,
                },
                "signal_independence_contract": {"audit_present": True, "direction_layer_clean": True},
            },
            search_status={
                "search_space_profile": {
                    "direction_weight_policy": {
                        "correlation_constrained": True,
                        "capped_direction_signal": "crowding_signal",
                        "demoted_signal": "funding_alpha_signal",
                        "demoted_signal_role": "state",
                    }
                }
            },
            current=baseline,
            paulwei_baseline=baseline,
            optuna_best=tuned,
            rl_best=None,
            best_variant="optuna_tuned",
            best_score=1.2,
            best_params={"x": 1},
            comparison_payload={},
            test_evaluated=True,
            smoke_completed=True,
            evaluation_role="acceptance",
            test_access_allowed=True,
        )

        assessment = _assess_full_training_readiness(report, smoke_completed=True)

        self.assertFalse(assessment["ready"])
        self.assertIn("test_sharpe", assessment["blockers"])


if __name__ == "__main__":
    unittest.main()
