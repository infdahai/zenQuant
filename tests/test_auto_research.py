"""Tests for the AutoResearch harness: experiment tracker, promotion gate, campaign."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenResearch.research.auto_research import (
    Campaign,
    CampaignConfig,
    SearchSpace,
    ValidationResult,
    WalkForwardSplit,
    _build_param_grid,
    _build_strategy_config,
    run_dev_robustness_stage,
    run_dev_validation_stage,
    run_search_stage,
)
from zenResearch.research.experiment import ExperimentTracker, params_hash
from zenResearch.research.promoter import (
    BaselineMetrics,
    CandidateMetrics,
    PromotionCriteria,
    PromotionGate,
    RobustnessResult,
)
from zenResearch.research.splits import default_btc_split_spec


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
            }
        )
        self.assertEqual(config.playbook_config.preserve_capital_transition_risk, 0.65)
        self.assertEqual(config.playbook_config.bull_max_leverage, 3.0)
        self.assertEqual(config.exit_config.transition_risk_exit_threshold, 0.85)

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

        def fake_worker(args):
            captured.append((args[4], args[5]))
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
            patch("zenResearch.research.auto_research._run_backtest_worker", fake_worker),
            patch("zenResearch.research.auto_research._max_workers", return_value=1),
        ):
            sweep = run_search_stage(config)

        self.assertEqual(sweep.total_combinations, 2)
        self.assertTrue(captured)
        for start_ns, end_ns in captured:
            self.assertEqual(start_ns, config.split_spec.train.start_ns)
            self.assertEqual(end_ns, config.split_spec.train.end_ns)

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
            captured.append((args[4], args[5]))
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
            patch("zenResearch.research.auto_research._run_backtest_worker", fake_worker),
            patch("zenResearch.research.auto_research._max_workers", return_value=1),
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
            captured.append((args[4], args[5]))
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
            patch("zenResearch.research.auto_research._run_backtest_worker", fake_worker),
            patch("zenResearch.research.auto_research._max_workers", return_value=1),
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

        with patch("zenResearch.research.auto_research._run_backtest_worker", fake_worker):
            campaign = Campaign(config)
            campaign._workers = 1
            report = campaign.run()

        self.assertFalse(report.authoritative_promote_path)
        self.assertEqual(report.split_contract_version, "btc_temporal_v1")
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


if __name__ == "__main__":
    unittest.main()
