import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.framework_params import build_replay_toggles, core_param_grid, search_space_profile
from zenquant.research.framework_search import (
    CandidateSummary,
    FrameworkEvaluator,
    SearchReport,
    _candidate_score,
    run_optuna_search,
    write_search_report,
)
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
        self.assertEqual(profile["direction_weight_policy"]["frozen_or_capped_signal"], "crowding_signal")


class FrameworkEvaluatorTests(unittest.TestCase):
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
            readiness={"data_ready": False, "promotion_ready": False, "blocked_by": "missing_liquidation_history", "detail": "missing"},
            deribit_status={"enabled": True, "observation_ready": False, "research_ready": False},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={"fast_windows": ["overall_recent"]},
            training_input_contract={
                "primary_truth_source": "binance",
                "execution_framework": {"five_min_feature_aggregation_enabled": True},
                "state_feature_sources": {"deribit_in_state_features": True, "hyperliquid_in_state_features": True},
                "signal_stack_contract": {"direction_state_execution_split_enabled": True},
            },
            search_status={
                "optuna_available": False,
                "optuna_trials_requested": 2,
                "rl_episodes_requested": 2,
                "liquidation_in_search": False,
                "search_space_profile": {"profile": "reduced_core_v2"},
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
        self.assertIn("training_input_contract", payload)
        self.assertTrue(payload["training_input_contract"]["signal_stack_contract"]["direction_state_execution_split_enabled"])
        self.assertEqual(payload["final_verdict"]["best_variant"], "current")
        self.assertIn("promotion_failure_reason", payload["final_verdict"])
        self.assertIn("search_space_profile", payload["final_verdict"])
        self.assertIn("comparison_to_paulwei_baseline", payload["final_verdict"])

    def test_run_optuna_search_uses_train_only_autoresearch_candidates(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float

        summary_a = CandidateSummary(
            variant="optuna_candidate",
            robust_score=1.0,
            selection_split="dev",
            dev_sharpe=1.0,
            dev_pnl=10.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.4,
            weak_window_sharpe=0.5,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
            promotion_failure_reason="avg_test_sharpe",
        )
        summary_b = CandidateSummary(
            variant="optuna_candidate",
            robust_score=1.5,
            selection_split="dev",
            dev_sharpe=1.2,
            dev_pnl=12.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.1,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.6,
            weak_window_sharpe=0.7,
            negative_dev_worst_windows=0,
            promotion_candidate=False,
            promotion_failure_reason="avg_test_sharpe",
        )

        with (
            patch("zenquant.research.framework_search.run_search_stage", return_value=FakeSweep(
                total_combinations=2,
                results=[],
                top_n=[
                    {"params": {"w_momentum": 0.3}, "metrics": {"sharpe": 1.0}},
                    {"params": {"w_momentum": 0.45}, "metrics": {"sharpe": 1.2}},
                ],
                runtime_s=0.1,
            )) as run_stage,
            patch.object(FrameworkEvaluator, "evaluate_params", side_effect=[summary_a, summary_b]),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            best, ranked = run_optuna_search(evaluator, n_trials=5)

        self.assertEqual(best, {"w_momentum": 0.45})
        self.assertEqual(ranked[0]["candidate_source"], "auto_research_train_only")
        self.assertEqual(ranked[0]["split_contract_version"], "btc_temporal_v1")
        run_stage.assert_called_once()


if __name__ == "__main__":
    unittest.main()
