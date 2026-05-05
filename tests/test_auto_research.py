"""Tests for the AutoResearch harness: experiment tracker, promotion gate, campaign."""

import tempfile
import unittest
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from unittest.mock import patch

from zenquant.research.auto_research import (
    _WORKER_REPLAY_CONTEXT_CACHE,
    Campaign,
    CampaignConfig,
    SearchSpace,
    SearchStageTimeoutError,
    ValidationResult,
    WalkForwardSplit,
    _build_param_grid,
    _build_strategy_config,
    _hostile_proxy_diagnostics,
    _run_backtest_worker,
    _stage1_sort_metric,
    run_dev_robustness_stage,
    run_dev_validation_stage,
    run_search_stage,
)
from zenquant.research.experiment import ExperimentTracker, params_hash
from zenquant.research.framework_params import hostile_constraint_violations
from zenquant.research.promoter import (
    BaselineMetrics,
    CandidateMetrics,
    PromotionCriteria,
    PromotionGate,
    RobustnessResult,
)
from zenquant.research.splits import default_btc_split_spec


class _ImmediateExecutor:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:  # pragma: no cover
            future.set_exception(exc)
        return future

    def shutdown(self, *args, **kwargs) -> None:
        del args, kwargs


def _wait_done_immediately(fs, timeout=None, return_when=None):
    del timeout, return_when
    return set(fs), set()


class TestParamsHash(unittest.TestCase):
    def test_deterministic(self) -> None:
        p = {"a": 1, "b": 2.0}
        self.assertEqual(params_hash(p), params_hash(p))

    def test_order_independent(self) -> None:
        self.assertEqual(
            params_hash({"a": 1, "b": 2}),
            params_hash({"b": 2, "a": 1}),
        )

    def test_different_values_differ(self) -> None:
        self.assertNotEqual(
            params_hash({"a": 1}),
            params_hash({"a": 2}),
        )


class TestExperimentTracker(unittest.TestCase):
    def test_record_and_rank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ExperimentTracker(Path(tmp))
            tracker.record({"x": 1}, {"sharpe": 1.5, "pnl": 100})
            tracker.record({"x": 2}, {"sharpe": 2.0, "pnl": 200})
            tracker.record({"x": 3}, {"sharpe": 0.5, "pnl": 50})

            self.assertEqual(tracker.count(), 3)

            top = tracker.rank(metric="sharpe", top_n=2)
            self.assertEqual(len(top), 2)
            self.assertEqual(top[0].params, {"x": 2})
            self.assertEqual(top[1].params, {"x": 1})

    def test_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ExperimentTracker(Path(tmp))
            tracker.record({"x": 1}, {"sharpe": 1.0})
            tracker.record({"x": 1}, {"sharpe": 2.0})  # same params, should overwrite
            self.assertEqual(tracker.count(), 1)
            self.assertEqual(tracker.best("sharpe").metrics["sharpe"], 2.0)

    def test_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker1 = ExperimentTracker(Path(tmp))
            tracker1.record({"x": 1}, {"sharpe": 1.5})
            tracker1.flush()

            # Reload from disk
            tracker2 = ExperimentTracker(Path(tmp))
            self.assertEqual(tracker2.count(), 1)
            self.assertEqual(tracker2.best("sharpe").metrics["sharpe"], 1.5)

    def test_compare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ExperimentTracker(Path(tmp))
            e1 = tracker.record({"x": 1}, {"sharpe": 1.0, "pnl": 100})
            e2 = tracker.record({"x": 2}, {"sharpe": 2.0, "pnl": 200})

            rows = tracker.compare([e1.experiment_id, e2.experiment_id], metrics=["sharpe"])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["sharpe"], 1.0)
            self.assertEqual(rows[1]["sharpe"], 2.0)

    def test_tag_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ExperimentTracker(Path(tmp))
            tracker.record({"x": 1}, {"sharpe": 1.0}, tags=["sweep"])
            tracker.record({"x": 2}, {"sharpe": 2.0}, tags=["walk_forward"])

            sweep_only = tracker.rank(metric="sharpe", tag_filter="sweep")
            self.assertEqual(len(sweep_only), 1)
            self.assertEqual(sweep_only[0].params, {"x": 1})


class TestPromotionGate(unittest.TestCase):
    def test_promote_on_good_metrics(self) -> None:
        gate = PromotionGate(
            baseline=BaselineMetrics(sharpe=1.0, max_drawdown=0.10),
            criteria=PromotionCriteria(min_val_sharpe=0.5, max_val_drawdown=0.20),
        )
        candidate = CandidateMetrics(
            avg_val_sharpe=1.5,
            avg_val_drawdown=0.08,
            avg_val_pnl=5000,
            total_trade_count=20,
        )
        verdict = gate.evaluate(candidate)
        self.assertEqual(verdict.decision, "PROMOTE")
        self.assertIn("walk_forward", verdict.passed_gates)

    def test_reject_low_sharpe(self) -> None:
        gate = PromotionGate(
            criteria=PromotionCriteria(min_val_sharpe=1.0),
        )
        candidate = CandidateMetrics(
            avg_val_sharpe=0.3,
            avg_val_drawdown=0.05,
            avg_val_pnl=100,
            total_trade_count=10,
        )
        verdict = gate.evaluate(candidate)
        self.assertEqual(verdict.decision, "REJECT")
        self.assertIn("walk_forward", verdict.failed_gates)

    def test_reject_high_drawdown(self) -> None:
        gate = PromotionGate(
            criteria=PromotionCriteria(min_val_sharpe=0.5, max_val_drawdown=0.10),
        )
        candidate = CandidateMetrics(
            avg_val_sharpe=2.0,
            avg_val_drawdown=0.15,
            avg_val_pnl=5000,
            total_trade_count=20,
        )
        verdict = gate.evaluate(candidate)
        self.assertEqual(verdict.decision, "REJECT")

    def test_reject_too_few_trades(self) -> None:
        gate = PromotionGate(
            criteria=PromotionCriteria(min_trade_count=10),
        )
        candidate = CandidateMetrics(
            avg_val_sharpe=2.0,
            avg_val_drawdown=0.05,
            avg_val_pnl=1000,
            total_trade_count=3,
        )
        verdict = gate.evaluate(candidate)
        self.assertEqual(verdict.decision, "REJECT")

    def test_robustness_gate_pass(self) -> None:
        gate = PromotionGate(
            baseline=BaselineMetrics(sharpe=1.0, max_drawdown=0.10),
            criteria=PromotionCriteria(
                min_val_sharpe=0.5,
                min_seeds_passing=4,
                robustness_sharpe_floor=0.0,
                max_sharpe_degradation=0.5,
            ),
        )
        candidate = CandidateMetrics(
            avg_val_sharpe=1.5,
            avg_val_drawdown=0.08,
            avg_val_pnl=5000,
            total_trade_count=20,
        )
        robustness = [
            RobustnessResult(seed=s, sharpe=1.2, max_drawdown=0.09, net_pnl=4000, trade_count=18)
            for s in [42, 123, 456, 789, 1024]
        ]
        verdict = gate.evaluate(candidate, robustness)
        self.assertEqual(verdict.decision, "PROMOTE")
        self.assertIn("robustness", verdict.passed_gates)

    def test_robustness_gate_fail(self) -> None:
        gate = PromotionGate(
            baseline=BaselineMetrics(sharpe=1.0, max_drawdown=0.10),
            criteria=PromotionCriteria(
                min_val_sharpe=0.5,
                min_seeds_passing=4,
                robustness_sharpe_floor=0.5,
            ),
        )
        candidate = CandidateMetrics(
            avg_val_sharpe=1.5,
            avg_val_drawdown=0.08,
            avg_val_pnl=5000,
            total_trade_count=20,
        )
        # 3 of 5 seeds fail (sharpe below floor)
        robustness = [
            RobustnessResult(seed=42, sharpe=0.8, max_drawdown=0.09, net_pnl=4000, trade_count=18),
            RobustnessResult(seed=123, sharpe=0.1, max_drawdown=0.15, net_pnl=100, trade_count=5),
            RobustnessResult(seed=456, sharpe=0.3, max_drawdown=0.12, net_pnl=500, trade_count=8),
            RobustnessResult(seed=789, sharpe=0.2, max_drawdown=0.14, net_pnl=200, trade_count=6),
            RobustnessResult(seed=1024, sharpe=0.9, max_drawdown=0.08, net_pnl=3500, trade_count=16),
        ]
        verdict = gate.evaluate(candidate, robustness)
        self.assertIn("robustness", verdict.failed_gates)
        # WF passes, so decision is REVIEW (not REJECT)
        self.assertEqual(verdict.decision, "REVIEW")


class TestStage1HostileProxy(unittest.TestCase):
    def test_hostile_proxy_penalizes_aggressive_execution_profile(self) -> None:
        stable = _hostile_proxy_diagnostics(
            {
                "target_vol_4h": 0.95,
                "max_leverage": 4.5,
                "pyramid_initial_pct": 0.25,
                "pyramid_add_pct": 0.20,
                "pyramid_short_initial_pct": 0.15,
                "pyramid_short_add_pct": 0.12,
                "dca_spacing_atr": 1.25,
                "dca_size_decay": 0.75,
                "flip_min_confidence": 0.65,
            },
            {
                "sharpe": 2.0,
                "max_drawdown": 0.10,
                "profit_factor": 1.1,
                "fee_drag_pct": 0.2,
                "trade_count": 2800,
                "terminated_early": 0.0,
            },
        )
        aggressive = _hostile_proxy_diagnostics(
            {
                "target_vol_4h": 1.10,
                "max_leverage": 6.0,
                "pyramid_initial_pct": 0.30,
                "pyramid_add_pct": 0.30,
                "pyramid_short_initial_pct": 0.20,
                "pyramid_short_add_pct": 0.16,
                "dca_spacing_atr": 0.75,
                "dca_size_decay": 0.60,
                "flip_min_confidence": 0.55,
            },
            {
                "sharpe": 2.0,
                "max_drawdown": 0.10,
                "profit_factor": 1.1,
                "fee_drag_pct": 0.2,
                "trade_count": 2800,
                "terminated_early": 0.0,
            },
        )

        self.assertLess(aggressive["hostile_proxy_score"], stable["hostile_proxy_score"])
        self.assertGreater(aggressive["execution_fragility_score"], stable["execution_fragility_score"])
        self.assertGreater(
            aggressive["inventory_path_dependence_score"],
            stable["inventory_path_dependence_score"],
        )
        self.assertGreater(
            aggressive["hostile_proxy_penalty_applied"],
            stable["hostile_proxy_penalty_applied"],
        )

    def test_hostile_proxy_penalizes_flip_reduce_cadence_over_time_leverage(self) -> None:
        efficient = _hostile_proxy_diagnostics(
            {
                "target_vol_4h": 0.95,
                "max_leverage": 4.5,
                "pyramid_initial_pct": 0.25,
                "pyramid_add_pct": 0.20,
                "pyramid_short_initial_pct": 0.15,
                "pyramid_short_add_pct": 0.12,
                "dca_spacing_atr": 1.25,
                "dca_size_decay": 0.75,
                "flip_min_confidence": 0.65,
            },
            {
                "sharpe": 2.0,
                "max_drawdown": 0.08,
                "profit_factor": 1.2,
                "fee_drag_pct": 0.015,
                "trade_count": 180,
                "net_pnl": 4500.0,
                "net_pnl_per_trade": 25.0,
                "position_switches_per_30d": 2.0,
                "flip_reduce_fee_drag_pct": 0.008,
                "net_pnl_per_position_switch": 300.0,
                "terminated_early": 0.0,
            },
        )
        churny = _hostile_proxy_diagnostics(
            {
                "target_vol_4h": 0.95,
                "max_leverage": 4.5,
                "pyramid_initial_pct": 0.25,
                "pyramid_add_pct": 0.20,
                "pyramid_short_initial_pct": 0.15,
                "pyramid_short_add_pct": 0.12,
                "dca_spacing_atr": 1.25,
                "dca_size_decay": 0.75,
                "flip_min_confidence": 0.65,
            },
            {
                "sharpe": 2.0,
                "max_drawdown": 0.08,
                "profit_factor": 1.2,
                "fee_drag_pct": 0.015,
                "trade_count": 180,
                "net_pnl": 4500.0,
                "net_pnl_per_trade": 25.0,
                "position_switches_per_30d": 8.0,
                "flip_reduce_fee_drag_pct": 0.025,
                "net_pnl_per_position_switch": 40.0,
                "terminated_early": 0.0,
            },
        )

        self.assertGreater(efficient["time_leverage_bonus"], churny["time_leverage_bonus"])
        self.assertGreater(
            churny["mature_position_switch_penalty"],
            efficient["mature_position_switch_penalty"],
        )
        self.assertGreater(churny["position_switch_penalty"], efficient["position_switch_penalty"])
        self.assertGreater(churny["flip_reduce_fee_drag_penalty"], efficient["flip_reduce_fee_drag_penalty"])
        self.assertLess(churny["hostile_proxy_score"], efficient["hostile_proxy_score"])

    def test_hostile_proxy_soft_targets_paulwei_mature_switch_cadence(self) -> None:
        params = {
            "target_vol_4h": 0.95,
            "max_leverage": 4.5,
            "pyramid_initial_pct": 0.25,
            "pyramid_add_pct": 0.20,
            "pyramid_short_initial_pct": 0.15,
            "pyramid_short_add_pct": 0.12,
            "dca_spacing_atr": 1.25,
            "dca_size_decay": 0.75,
            "flip_min_confidence": 0.65,
        }
        below_mature_cadence = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 2.0,
                "max_drawdown": 0.08,
                "profit_factor": 1.2,
                "fee_drag_pct": 0.015,
                "trade_count": 180,
                "net_pnl": 4500.0,
                "net_pnl_per_trade": 25.0,
                "position_switches_per_30d": 1.6,
                "flip_reduce_fee_drag_pct": 0.008,
                "net_pnl_per_position_switch": 300.0,
                "terminated_early": 0.0,
            },
        )
        above_mature_cadence = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 2.0,
                "max_drawdown": 0.08,
                "profit_factor": 1.2,
                "fee_drag_pct": 0.015,
                "trade_count": 180,
                "net_pnl": 4500.0,
                "net_pnl_per_trade": 25.0,
                "position_switches_per_30d": 2.6,
                "flip_reduce_fee_drag_pct": 0.008,
                "net_pnl_per_position_switch": 300.0,
                "terminated_early": 0.0,
            },
        )

        self.assertEqual(below_mature_cadence["mature_position_switch_penalty"], 0.0)
        self.assertEqual(below_mature_cadence["target_position_switches_per_30d"], 1.6807)
        self.assertGreater(above_mature_cadence["mature_position_switch_penalty"], 0.0)
        self.assertEqual(above_mature_cadence["position_switch_penalty"], 0.0)
        self.assertGreater(
            below_mature_cadence["hostile_proxy_score"],
            above_mature_cadence["hostile_proxy_score"],
        )

    def test_hostile_proxy_penalizes_flip_dominant_exits(self) -> None:
        params = {
            "target_vol_4h": 0.95,
            "max_leverage": 4.5,
            "pyramid_initial_pct": 0.25,
            "pyramid_add_pct": 0.20,
            "pyramid_short_initial_pct": 0.15,
            "pyramid_short_add_pct": 0.12,
            "dca_spacing_atr": 1.25,
            "dca_size_decay": 0.75,
            "flip_min_confidence": 0.65,
        }
        protective = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 2.0,
                "max_drawdown": 0.05,
                "profit_factor": 1.3,
                "fee_drag_pct": 0.012,
                "trade_count": 220,
                "net_pnl": 8000.0,
                "net_pnl_per_trade": 36.36,
                "position_switches_per_30d": 1.4,
                "flip_reduce_fee_drag_pct": 0.008,
                "net_pnl_per_position_switch": 220.0,
                "action_trade_counts": {"flip": 30, "reduce": 90},
            },
        )
        flip_dominant = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 2.0,
                "max_drawdown": 0.05,
                "profit_factor": 1.3,
                "fee_drag_pct": 0.012,
                "trade_count": 220,
                "net_pnl": 8000.0,
                "net_pnl_per_trade": 36.36,
                "position_switches_per_30d": 1.4,
                "flip_reduce_fee_drag_pct": 0.008,
                "net_pnl_per_position_switch": 220.0,
                "action_trade_counts": {"flip": 90, "reduce": 30},
            },
        )

        self.assertEqual(protective["protective_reduce_share"], 0.75)
        self.assertEqual(flip_dominant["protective_reduce_share"], 0.25)
        self.assertGreater(
            flip_dominant["protective_reduce_bias_penalty"],
            protective["protective_reduce_bias_penalty"],
        )
        self.assertLess(flip_dominant["hostile_proxy_score"], protective["hostile_proxy_score"])

    def test_hostile_proxy_penalizes_expensive_exit_and_weak_reduce_quality(self) -> None:
        params = {
            "target_vol_4h": 0.95,
            "max_leverage": 4.5,
            "pyramid_initial_pct": 0.25,
            "pyramid_add_pct": 0.20,
            "pyramid_short_initial_pct": 0.15,
            "pyramid_short_add_pct": 0.12,
            "dca_spacing_atr": 1.25,
            "dca_size_decay": 0.75,
            "flip_min_confidence": 0.65,
        }
        clean = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 2.0,
                "max_drawdown": 0.05,
                "profit_factor": 1.3,
                "fee_drag_pct": 0.012,
                "trade_count": 220,
                "net_pnl": 8000.0,
                "net_pnl_per_trade": 36.36,
                "position_switches_per_30d": 1.4,
                "flip_reduce_fee_drag_pct": 0.008,
                "net_pnl_per_position_switch": 220.0,
                "action_trade_counts": {"flip": 20, "reduce": 80},
                "action_fee_drag_pct": {"exit": 0.010, "flip": 0.010, "reduce": 0.006},
                "action_profit_factor": {"reduce": 1.20},
            },
        )
        expensive = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 2.0,
                "max_drawdown": 0.05,
                "profit_factor": 1.3,
                "fee_drag_pct": 0.012,
                "trade_count": 220,
                "net_pnl": 8000.0,
                "net_pnl_per_trade": 36.36,
                "position_switches_per_30d": 1.4,
                "flip_reduce_fee_drag_pct": 0.008,
                "net_pnl_per_position_switch": 220.0,
                "action_trade_counts": {"flip": 20, "reduce": 80},
                "action_fee_drag_pct": {"exit": 0.033, "flip": 0.045, "reduce": 0.016},
                "action_profit_factor": {"reduce": 0.94},
            },
        )

        self.assertGreater(expensive["exit_fee_drag_penalty"], 0.0)
        self.assertGreater(expensive["flip_action_fee_drag_penalty"], 0.0)
        self.assertGreater(expensive["reduce_profit_factor_penalty"], 0.0)
        self.assertLess(expensive["hostile_proxy_score"], clean["hostile_proxy_score"])

    def test_hostile_proxy_rewards_balanced_tranche_build_and_unwind(self) -> None:
        params = {
            "target_vol_4h": 0.95,
            "max_leverage": 4.5,
            "pyramid_initial_pct": 0.25,
            "pyramid_add_pct": 0.20,
            "pyramid_short_initial_pct": 0.15,
            "pyramid_short_add_pct": 0.12,
            "dca_spacing_atr": 1.25,
            "dca_size_decay": 0.75,
            "flip_min_confidence": 0.65,
        }
        balanced = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 2.1,
                "max_drawdown": 0.04,
                "profit_factor": 1.6,
                "fee_drag_pct": 0.014,
                "trade_count": 180,
                "net_pnl": 7200.0,
                "net_pnl_per_trade": 40.0,
                "position_switches_per_30d": 1.2,
                "flip_reduce_fee_drag_pct": 0.010,
                "net_pnl_per_position_switch": 260.0,
                "action_trade_counts": {"add": 35, "entry": 18, "exit": 18, "flip": 12, "reduce": 40},
                "action_fee_drag_pct": {"exit": 0.004, "flip": 0.004, "reduce": 0.006},
                "action_profit_factor": {"reduce": 1.25},
            },
        )
        one_sided_reduce = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 2.1,
                "max_drawdown": 0.04,
                "profit_factor": 1.6,
                "fee_drag_pct": 0.014,
                "trade_count": 180,
                "net_pnl": 7200.0,
                "net_pnl_per_trade": 40.0,
                "position_switches_per_30d": 1.2,
                "flip_reduce_fee_drag_pct": 0.010,
                "net_pnl_per_position_switch": 260.0,
                "action_trade_counts": {"add": 2, "entry": 10, "exit": 18, "flip": 12, "reduce": 90},
                "action_fee_drag_pct": {"exit": 0.004, "flip": 0.004, "reduce": 0.006},
                "action_profit_factor": {"reduce": 1.25},
            },
        )

        self.assertEqual(balanced["staged_inventory_balance_penalty"], 0.0)
        self.assertGreater(balanced["tranche_alignment_bonus"], 0.0)
        self.assertGreater(one_sided_reduce["staged_inventory_balance_penalty"], 0.0)
        self.assertLess(one_sided_reduce["hostile_proxy_score"], balanced["hostile_proxy_score"])

    def test_hostile_proxy_penalizes_wide_train_drawdown_band(self) -> None:
        params = {
            "target_vol_4h": 0.95,
            "max_leverage": 4.5,
            "pyramid_initial_pct": 0.25,
            "pyramid_add_pct": 0.20,
            "pyramid_short_initial_pct": 0.15,
            "pyramid_short_add_pct": 0.12,
            "dca_spacing_atr": 1.25,
            "dca_size_decay": 0.75,
            "flip_min_confidence": 0.65,
        }
        stable = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 1.6,
                "max_drawdown": 0.05,
                "profit_factor": 1.4,
                "fee_drag_pct": 0.01,
                "trade_count": 120,
                "net_pnl": 100_000.0,
                "terminated_early": 0.0,
            },
        )
        wide = _hostile_proxy_diagnostics(
            params,
            {
                "sharpe": 1.6,
                "max_drawdown": 0.09,
                "profit_factor": 1.4,
                "fee_drag_pct": 0.01,
                "trade_count": 120,
                "net_pnl": 100_000.0,
                "terminated_early": 0.0,
            },
        )

        self.assertEqual(stable["train_drawdown_preferred_band_penalty"], 0.0)
        self.assertGreater(wide["train_drawdown_preferred_band_penalty"], 0.0)
        self.assertLess(wide["hostile_proxy_score"], stable["hostile_proxy_score"])

    def test_stage1_sort_metric_prefers_hostile_proxy_score_over_raw_sharpe(self) -> None:
        strong_sharpe = {"sharpe": 3.0, "hostile_proxy_score": 0.2}
        safer_profile = {"sharpe": 2.0, "hostile_proxy_score": 1.5}

        self.assertLess(
            _stage1_sort_metric(strong_sharpe, "sharpe"),
            _stage1_sort_metric(safer_profile, "sharpe"),
        )

    def test_hostile_guardrails_flag_new_execution_fragility_combos(self) -> None:
        violations = hostile_constraint_violations(
            {
                "target_vol_4h": 0.95,
                "max_leverage": 6.0,
                "pyramid_initial_pct": 0.30,
                "pyramid_add_pct": 0.20,
                "pyramid_short_add_pct": 0.16,
                "dca_spacing_atr": 0.75,
                "dca_size_decay": 0.60,
                "flip_min_confidence": 0.60,
            }
        )

        self.assertIn("high_target_vol_with_excess_leverage", violations)
        self.assertIn("aggressive_long_and_short_pyramiding", violations)
        self.assertIn("tight_dca_spacing_with_heavy_inventory_reloading", violations)
        self.assertIn("low_flip_confidence_with_fast_inventory_expansion", violations)

    def test_drawdown_regression_gate(self) -> None:
        gate = PromotionGate(
            baseline=BaselineMetrics(sharpe=1.0, max_drawdown=0.05),
            criteria=PromotionCriteria(
                min_val_sharpe=0.5,
                max_drawdown_regression=0.03,
            ),
        )
        # Candidate drawdown is 0.12, baseline is 0.05 → regression = 0.07 > 0.03
        candidate = CandidateMetrics(
            avg_val_sharpe=1.5,
            avg_val_drawdown=0.12,
            avg_val_pnl=5000,
            total_trade_count=20,
        )
        verdict = gate.evaluate(candidate)
        self.assertIn("drawdown_regression", verdict.failed_gates)


class TestTimeoutCleanup(unittest.TestCase):
    def test_bayesian_serial_timeout_shuts_down_pool_without_waiting(self) -> None:
        spec = default_btc_split_spec()
        shutdown_calls: list[tuple[bool, bool]] = []

        class _TimeoutFuture:
            def result(self, timeout=None):
                del timeout
                raise FutureTimeoutError()

            def cancel(self):
                return True

        class _TimeoutExecutor:
            def submit(self, fn, *args, **kwargs):
                del fn, args, kwargs
                return _TimeoutFuture()

            def shutdown(self, wait=False, cancel_futures=False):
                shutdown_calls.append((wait, cancel_futures))

        config = CampaignConfig(
            split_spec=spec,
            search_space=SearchSpace(strategy_params={"bull_leverage": [2.5]}),
            sweep_mode="bayesian",
            n_trials=1,
            wf_splits=[],
            robustness_seeds=[],
            verbose=False,
            trial_timeout_s=1.0,
        )

        with (
            patch("zenquant.research.auto_research.ProcessPoolExecutor", return_value=_TimeoutExecutor()),
            self.assertRaises(SearchStageTimeoutError),
        ):
            run_search_stage(config)

        self.assertTrue(shutdown_calls)
        self.assertTrue(all(wait is False for wait, _ in shutdown_calls))
        self.assertTrue(all(cancel_futures is True for _, cancel_futures in shutdown_calls))

    def test_bayesian_cpu_parallel_respects_wall_clock_budget(self) -> None:
        spec = default_btc_split_spec()
        clock_values = iter([0.0, 1.0, 2.0, 12.0, 13.0, 14.0, 15.0])

        def fake_time():
            return next(clock_values)

        seen_fee_buffers: list[float] = []

        def fake_worker(args):
            seen_fee_buffers.append(float(args[14]))
            return args[0], {
                "sharpe": 1.0,
                "sortino": 1.0,
                "net_pnl": 1.0,
                "max_drawdown": 0.1,
                "win_rate": 0.5,
                "profit_factor": 1.0,
                "trade_count": 25,
                "fee_drag_pct": 0.01,
                "trial_runtime_s": 3.0,
                "replay_context_build_s": 0.2,
                "event_publish_run_s": 2.7,
                "cache_hit": 1.0,
                "event_loop_total_s": 2.5,
                "indicator_step_total_s": 0.5,
                "strategy_step_total_s": 0.6,
                "risk_step_total_s": 0.4,
                "execution_step_total_s": 0.7,
                "accounting_step_total_s": 0.1,
                "metrics_finalize_s": 0.1,
                "diagnostics_finalize_s": 0.0,
                "terminated_early": 1.0,
                "processed_event_ratio": 0.55,
            }

        config = CampaignConfig(
            split_spec=spec,
            search_space=SearchSpace(strategy_params={"bull_leverage": [2.5, 3.0, 3.5]}),
            sweep_mode="bayesian",
            n_trials=3,
            wf_splits=[],
            robustness_seeds=[],
            verbose=False,
            stage1_parallel_mode="cpu_parallel",
            stage1_max_workers=1,
            stage1_target_inflight=1,
            stage_wall_clock_budget_s=10.0,
            min_completed_trials=1,
            early_stop_fee_drag_buffer=1.25,
        )

        with (
            patch("zenquant.research.auto_research.ProcessPoolExecutor", _ImmediateExecutor),
            patch("zenquant.research.auto_research.wait", _wait_done_immediately),
            patch("zenquant.research.auto_research.time.time", side_effect=fake_time),
            patch("zenquant.research.auto_research._run_backtest_worker", side_effect=fake_worker),
        ):
            sweep = run_search_stage(config)

        self.assertEqual(sweep.stop_reason, "wall_clock_budget_reached")
        self.assertEqual(sweep.budget_mode, "wall_clock_primary")
        self.assertEqual(sweep.total_combinations, 3)
        self.assertGreaterEqual(len(sweep.results), 1)
        self.assertTrue(seen_fee_buffers)
        self.assertTrue(all(buffer == 1.25 for buffer in seen_fee_buffers))

    def test_bayesian_stage_handles_all_pruned_trials_without_crashing(self) -> None:
        spec = default_btc_split_spec()

        config = CampaignConfig(
            split_spec=spec,
            search_space=SearchSpace(strategy_params={"bull_leverage": [2.5]}),
            sweep_mode="bayesian",
            n_trials=3,
            wf_splits=[],
            robustness_seeds=[],
            verbose=False,
        )

        def fake_worker(args):
            del args
            return {}, {
                "sharpe": 1.0,
                "sortino": 1.0,
                "net_pnl": 1.0,
                "max_drawdown": 0.1,
                "win_rate": 0.5,
                "profit_factor": 1.0,
                "trade_count": 0,
                "fee_drag_pct": 0.01,
            }

        with (
            patch("zenquant.research.auto_research._run_backtest_worker", side_effect=fake_worker),
            patch("zenquant.research.auto_research._max_workers", return_value=1),
        ):
            sweep = run_search_stage(config)

        self.assertEqual(sweep.total_combinations, 1)
        self.assertEqual(sweep.results, [])
        self.assertEqual(sweep.top_n, [])
        self.assertEqual(sweep.train_candidate_diagnostics["raw_trial_count"], 1)
        trial_diagnostics = sweep.train_candidate_diagnostics["top_train_trial_diagnostics"]
        self.assertEqual(len(trial_diagnostics), 1)
        self.assertEqual(trial_diagnostics[0]["metrics"]["fee_drag_pct"], 0.01)
        self.assertEqual(trial_diagnostics[0]["metrics"]["trade_count"], 0)


class TestSearchSpace(unittest.TestCase):
    def test_empty_grid(self) -> None:
        grid = _build_param_grid(SearchSpace())
        self.assertEqual(grid, [{}])

    def test_single_param(self) -> None:
        grid = _build_param_grid(
            SearchSpace(
                strategy_params={"bull_leverage": [2.5, 3.0, 3.5]},
            )
        )
        self.assertEqual(len(grid), 3)
        self.assertEqual(grid[0], {"bull_leverage": 2.5})

    def test_cartesian_product(self) -> None:
        grid = _build_param_grid(
            SearchSpace(
                strategy_params={
                    "bull_leverage": [2.5, 3.0],
                    "preserve_capital_risk": [0.60, 0.65],
                },
            )
        )
        self.assertEqual(len(grid), 4)

    def test_build_strategy_config(self) -> None:
        config = _build_strategy_config(
            {
                "preserve_capital_risk": 0.65,
                "bull_leverage": 3.0,
                "exit_transition_risk": 0.85,
                "intent_add_confirmation_bars": 4,
            }
        )
        self.assertEqual(config.playbook_config.preserve_capital_transition_risk, 0.65)
        self.assertEqual(config.playbook_config.bull_max_leverage, 3.0)
        self.assertEqual(config.exit_config.transition_risk_exit_threshold, 0.85)
        self.assertEqual(config.intent_add_confirmation_bars, 4)

    def test_build_strategy_config_partial(self) -> None:
        config = _build_strategy_config({"bull_leverage": 2.5})
        self.assertEqual(config.playbook_config.bull_max_leverage, 2.5)
        self.assertIsNone(config.exit_config)

    def test_run_search_stage_uses_train_split_only(self) -> None:
        config = CampaignConfig(
            split_spec=default_btc_split_spec(),
            search_space=SearchSpace(strategy_params={"bull_leverage": [2.5, 3.0]}),
            sweep_mode="grid",
            wf_splits=[],
            robustness_seeds=[],
            verbose=False,
        )
        captured: list[tuple[int | None, int | None]] = []
        captured_modes: list[tuple[str | None, bool | None]] = []

        def fake_worker(args):
            captured.append((args[8], args[9]))
            captured_modes.append((args[11], args[12]))
            return args[0], {
                "sharpe": 1.0,
                "sortino": 1.0,
                "net_pnl": 1.0,
                "max_drawdown": 0.1,
                "win_rate": 0.5,
                "profit_factor": 1.0,
                "trade_count": 10,
                "fee_drag_pct": 0.01,
            }

        with (
            patch("zenquant.research.auto_research._run_backtest_worker", fake_worker),
            patch("zenquant.research.auto_research._max_workers", return_value=1),
        ):
            sweep = run_search_stage(config)

        self.assertEqual(sweep.total_combinations, 2)
        self.assertTrue(captured)
        for start_ns, end_ns in captured:
            self.assertEqual(start_ns, config.split_spec.train.start_ns)
            self.assertEqual(end_ns, config.split_spec.train.end_ns)
        self.assertTrue(captured_modes)
        self.assertEqual(captured_modes[0], ("authoritative", False))

    def test_run_dev_validation_stage_uses_dev_windows_only(self) -> None:
        spec = default_btc_split_spec()
        wf_splits = [
            WalkForwardSplit(
                train_start_ns=spec.train.start_ns,
                train_end_ns=spec.train.end_ns,
                val_start_ns=spec.dev.start_ns,
                val_end_ns=spec.dev.start_ns + 10,
            ),
            WalkForwardSplit(
                train_start_ns=spec.train.start_ns,
                train_end_ns=spec.train.end_ns,
                val_start_ns=spec.dev.start_ns + 10,
                val_end_ns=spec.dev.start_ns + 20,
            ),
        ]
        config = CampaignConfig(
            split_spec=spec,
            search_space=SearchSpace(strategy_params={"bull_leverage": [2.5]}),
            wf_splits=wf_splits,
            robustness_seeds=[],
            verbose=False,
        )
        candidates = [{"params": {"bull_leverage": 2.5}, "metrics": {"sharpe": 1.0}}]
        captured: list[tuple[int | None, int | None]] = []

        def fake_worker(args):
            captured.append((args[8], args[9]))
            return args[0], {
                "sharpe": 1.0,
                "sortino": 1.0,
                "net_pnl": 1.0,
                "max_drawdown": 0.1,
                "win_rate": 0.5,
                "profit_factor": 1.0,
                "trade_count": 10,
                "fee_drag_pct": 0.01,
            }

        with (
            patch("zenquant.research.auto_research._run_backtest_worker", fake_worker),
            patch("zenquant.research.auto_research._max_workers", return_value=1),
        ):
            validations = run_dev_validation_stage(config, candidates)

        self.assertEqual(len(validations), 1)
        self.assertEqual(
            captured,
            [
                (wf_splits[0].val_start_ns, wf_splits[0].val_end_ns),
                (wf_splits[1].val_start_ns, wf_splits[1].val_end_ns),
            ],
        )

    def test_run_dev_robustness_stage_uses_dev_split_only(self) -> None:
        spec = default_btc_split_spec()
        config = CampaignConfig(
            split_spec=spec,
            search_space=SearchSpace(strategy_params={"bull_leverage": [2.5]}),
            wf_splits=[],
            robustness_seeds=[11, 22],
            verbose=False,
        )
        validations = [
            ValidationResult(
                params={"bull_leverage": 2.5},
                split_sharpes=[1.0],
                split_drawdowns=[0.1],
                split_pnls=[100.0],
                split_trade_counts=[5],
                avg_val_sharpe=1.0,
                avg_val_drawdown=0.1,
                total_trade_count=5,
            )
        ]
        captured: list[tuple[int | None, int | None]] = []

        def fake_worker(args):
            captured.append((args[8], args[9]))
            return args[0], {
                "sharpe": 1.0,
                "sortino": 1.0,
                "net_pnl": 1.0,
                "max_drawdown": 0.1,
                "win_rate": 0.5,
                "profit_factor": 1.0,
                "trade_count": 10,
                "fee_drag_pct": 0.01,
            }

        with (
            patch("zenquant.research.auto_research._run_backtest_worker", fake_worker),
            patch("zenquant.research.auto_research._max_workers", return_value=1),
        ):
            robustness = run_dev_robustness_stage(config, validations)

        self.assertEqual(len(robustness), 1)
        self.assertEqual(
            captured,
            [
                (spec.dev.start_ns, spec.dev.end_ns),
                (spec.dev.start_ns, spec.dev.end_ns),
            ],
        )

    def test_campaign_report_is_internal_dev_only(self) -> None:
        spec = default_btc_split_spec()
        config = CampaignConfig(
            split_spec=spec,
            search_space=SearchSpace(strategy_params={"bull_leverage": [2.5]}),
            wf_splits=[
                WalkForwardSplit(
                    train_start_ns=spec.train.start_ns,
                    train_end_ns=spec.train.end_ns,
                    val_start_ns=spec.dev.start_ns,
                    val_end_ns=spec.dev.end_ns,
                )
            ],
            robustness_seeds=[],
            verbose=False,
        )

        def fake_worker(args):
            params = args[0]
            bull_lev = params.get("bull_leverage", 2.5)
            return params, {
                "sharpe": bull_lev * 1.0,
                "sortino": bull_lev * 0.8,
                "net_pnl": bull_lev * 10000,
                "max_drawdown": 0.1,
                "win_rate": 0.55,
                "profit_factor": 1.5,
                "trade_count": 100,
                "fee_drag_pct": 0.02,
            }

        with patch("zenquant.research.auto_research._run_backtest_worker", fake_worker):
            campaign = Campaign(config)
            campaign._workers = 1
            report = campaign.run()

        self.assertFalse(report.authoritative_promote_path)
        self.assertEqual(report.split_contract_version, "btc_temporal_v2_train_selection_only")
        self.assertFalse(report.test_evaluated)

    def test_bayesian_search_is_not_rejected_by_theoretical_grid_size(self) -> None:
        spec = default_btc_split_spec()
        config = CampaignConfig(
            split_spec=spec,
            search_space=SearchSpace(
                strategy_params={
                    "a": list(range(101)),
                    "b": list(range(1000)),
                }
            ),
            sweep_mode="bayesian",
            n_trials=5,
            wf_splits=[
                WalkForwardSplit(
                    train_start_ns=spec.train.start_ns,
                    train_end_ns=spec.train.end_ns,
                    val_start_ns=spec.dev.start_ns,
                    val_end_ns=spec.dev.end_ns,
                )
            ],
            robustness_seeds=[],
            verbose=False,
        )

        campaign = Campaign(config)
        self.assertEqual(campaign._config.sweep_mode, "bayesian")

    def test_bayesian_parallel_stage_reports_monotonic_completed_trials(self) -> None:
        spec = default_btc_split_spec()
        heartbeats: list[dict[str, object]] = []

        def fake_worker(args):
            params = args[0]
            score = float(params["bull_leverage"])
            return params, {
                "sharpe": score,
                "sortino": score,
                "net_pnl": score * 1000.0,
                "max_drawdown": 0.1,
                "win_rate": 0.5,
                "profit_factor": 1.0,
                "trade_count": 25,
                "fee_drag_pct": 0.01,
            }

        config = CampaignConfig(
            split_spec=spec,
            search_space=SearchSpace(strategy_params={"bull_leverage": [2.5, 3.0, 3.5, 4.0]}),
            sweep_mode="bayesian",
            n_trials=4,
            wf_splits=[],
            robustness_seeds=[],
            verbose=False,
            status_callback=lambda **payload: heartbeats.append(payload),
            stage1_parallel_mode="cpu_parallel",
            stage1_max_workers=2,
            stage1_batch_size=2,
            seed_trials=[{"bull_leverage": 2.5}, {"bull_leverage": 3.0}],
        )

        with (
            patch("zenquant.research.auto_research._run_backtest_worker", fake_worker),
            patch("zenquant.research.auto_research.ProcessPoolExecutor", _ImmediateExecutor),
            patch("zenquant.research.auto_research._max_workers", return_value=2),
        ):
            sweep = run_search_stage(config)

        completed = [int(item["completed_trials"]) for item in heartbeats if item.get("event") == "trial_completed"]
        self.assertEqual(completed, sorted(completed))
        self.assertEqual(completed[-1], 4)
        self.assertEqual(sweep.total_combinations, 4)

    def test_bayesian_parallel_stage_uses_target_inflight_not_batch_size(self) -> None:
        spec = default_btc_split_spec()
        heartbeats: list[dict[str, object]] = []

        def fake_worker(args):
            params = args[0]
            score = float(params["bull_leverage"])
            return params, {
                "sharpe": score,
                "sortino": score,
                "net_pnl": score * 1000.0,
                "max_drawdown": 0.1,
                "win_rate": 0.5,
                "profit_factor": 1.0,
                "trade_count": 25,
                "fee_drag_pct": 0.01,
            }

        config = CampaignConfig(
            split_spec=spec,
            search_space=SearchSpace(strategy_params={"bull_leverage": [2.5, 3.0, 3.5, 4.0]}),
            sweep_mode="bayesian",
            n_trials=4,
            wf_splits=[],
            robustness_seeds=[],
            verbose=False,
            status_callback=lambda **payload: heartbeats.append(payload),
            stage1_parallel_mode="cpu_parallel",
            stage1_max_workers=4,
            stage1_target_inflight=4,
            stage1_batch_size=2,
            seed_trials=[{"bull_leverage": 2.5}, {"bull_leverage": 3.0}],
        )

        with (
            patch("zenquant.research.auto_research._run_backtest_worker", fake_worker),
            patch("zenquant.research.auto_research.ProcessPoolExecutor", _ImmediateExecutor),
            patch("zenquant.research.auto_research._max_workers", return_value=4),
        ):
            sweep = run_search_stage(config)

        trial_started = [item for item in heartbeats if item.get("event") == "trial_started"]
        self.assertEqual(len(trial_started), 4)
        self.assertTrue(all(int(item["target_inflight"]) == 4 for item in trial_started))
        self.assertTrue(any(int(item["pending_trials"]) >= 2 for item in trial_started))
        self.assertTrue(all(not bool(item["underfilled"]) for item in trial_started))
        trial_completed = [item for item in heartbeats if item.get("event") == "trial_completed"]
        self.assertTrue(all(not bool(item["underfilled"]) for item in trial_completed))
        self.assertTrue(all("trial_runtime_s" in item for item in trial_completed))
        self.assertTrue(all("replay_context_build_s" in item for item in trial_completed))
        self.assertTrue(all("event_publish_run_s" in item for item in trial_completed))
        self.assertTrue(all("cache_hit" in item for item in trial_completed))
        self.assertEqual(sweep.total_combinations, 4)

    def test_run_backtest_worker_reuses_worker_local_replay_context(self) -> None:
        _WORKER_REPLAY_CONTEXT_CACHE.clear()

        class FakeMetrics:
            sharpe = 1.0
            sortino = 1.0
            net_pnl = 10.0
            max_drawdown = 0.1
            win_rate = 0.5
            profit_factor = 1.0
            trade_count = 5
            fee_drag_pct = 0.01

        class FakeResult:
            metrics = FakeMetrics()
            terminated_early = True
            termination_reason = "trade_count_too_low_for_processed_ratio"
            processed_event_ratio = 0.75

            def __init__(self) -> None:
                self.profiling = {
                    "event_loop_total_s": 1.1,
                    "metrics_finalize_s": 0.2,
                    "diagnostics_finalize_s": 0.0,
                }

        class FakeBacktest:
            def __init__(self, _config) -> None:
                self._config = _config

            def run(self):
                return FakeResult()

        class FakeContext:
            def __init__(self) -> None:
                self.funding_rows = []
                self.open_interest_rows = []
                self.aggtrades = {}
                self.events = []
                self.macro_store = type("Store", (), {"loaded": False})()
                self.options_store = type("Store", (), {"loaded": False})()
                self.hf_store = type("Store", (), {"loaded": False})()
                self.cot_store = type("Store", (), {"loaded": False})()
                self.liquidation_store = type("Store", (), {"loaded": False})()
                self.eth_store = type("Store", (), {"loaded": False})()
                self.basis_store = type("Store", (), {"loaded": False})()

        args = (
            {"bull_leverage": 2.5},
            "data/raw/binance",
            "BTCUSDT",
            100000.0,
            "5m",
            "1d",
            "4h",
            "1h",
            None,
            None,
            None,
        )

        with (
            patch("zenquant.research.auto_research._build_replay_context", return_value=FakeContext()) as build_context,
            patch("zenquant.research.auto_research.HistoricalBacktest", FakeBacktest),
        ):
            _, first = _run_backtest_worker(args)
            _, second = _run_backtest_worker(args)

        self.assertEqual(build_context.call_count, 1)
        self.assertEqual(first["cache_hit"], 0.0)
        self.assertEqual(second["cache_hit"], 1.0)
        self.assertEqual(second["terminated_early"], 1.0)
        self.assertEqual(second["termination_reason"], "trade_count_too_low_for_processed_ratio")
        self.assertEqual(second["processed_event_ratio"], 0.75)
        self.assertEqual(second["event_loop_total_s"], 1.1)


if __name__ == "__main__":
    unittest.main()
