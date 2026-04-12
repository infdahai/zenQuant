import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.auto_research import Campaign, CampaignConfig, SearchSpace
from zenquant.research.comparison import compare_variants, default_walk_forward_windows
from zenquant.research.framework_search import FrameworkEvaluator
from zenquant.research.splits import default_btc_split_spec, split_summary_json


class SplitContractTests(unittest.TestCase):
    def test_default_btc_split_spec_is_non_overlapping_and_serializable(self) -> None:
        spec = default_btc_split_spec()

        self.assertLess(spec.train.start_ns, spec.train.end_ns)
        self.assertLess(spec.train.end_ns, spec.dev.start_ns + 1)
        self.assertLess(spec.dev.start_ns, spec.dev.end_ns)
        self.assertLess(spec.dev.end_ns, spec.test.start_ns + 1)

        payload = split_summary_json(spec)
        self.assertEqual(payload["version"], "btc_temporal_v2_train_selection_only")
        self.assertEqual(payload["selection_policy"], "train_only_search_dev_diagnostics_test_acceptance")
        self.assertIn("windows", payload)

    def test_default_walk_forward_windows_stay_inside_dev(self) -> None:
        spec = default_btc_split_spec()
        windows = default_walk_forward_windows(split_spec=spec, comparison_split="dev")

        self.assertGreater(len(windows), 0)
        for window in windows:
            assert window.start_ns is not None and window.end_ns is not None
            self.assertGreaterEqual(window.start_ns, spec.dev.start_ns)
            self.assertLessEqual(window.end_ns, spec.dev.end_ns)


class ResearchDisciplineTests(unittest.TestCase):
    def test_campaign_requires_explicit_split_spec(self) -> None:
        with self.assertRaisesRegex(ValueError, "split_spec is required"):
            Campaign(CampaignConfig(search_space=SearchSpace(strategy_params={"bull_leverage": [2.5]})))

    def test_compare_variants_defaults_to_dev_split_without_overall(self) -> None:
        @dataclass
        class FakeMetrics:
            sharpe: float = 1.0
            net_pnl: float = 1.0
            max_drawdown: float = 0.1
            profit_factor: float = 1.0
            trade_count: int = 1
            win_rate: float = 1.0

        @dataclass
        class FakeDiagnostics:
            playbook_counts: dict[str, int]
            risk_flag_counts: dict[str, int]
            actionable_exit_counts: dict[str, int]

        @dataclass
        class FakeCycle:
            cycle_position: float = 0.0
            cycle_peak_distance: float = 0.0
            cycle_trough_distance: float = 0.0
            cycle_momentum: float = 0.0
            cycle_confidence: float = 0.0

        @dataclass
        class FakeState:
            timeframe: str = "4h"
            directional_state: str = "bull"
            regime: str = "trend"

        @dataclass
        class FakePlaybook:
            timeframe: str = "4h"
            playbook_id: str = "pb"

        class FakeResult:
            metrics = FakeMetrics()
            diagnostics = FakeDiagnostics({"pb": 1}, {"OK": 1}, {"NONE": 1})
            cycle_context = FakeCycle()

        class FakeRunner:
            def __init__(self) -> None:
                self._market_states = [FakeState()]
                self._playbooks = [FakePlaybook()]

        with patch("zenquant.research.comparison._run_variant_backtest", return_value=(FakeRunner(), FakeResult())):
            payload = compare_variants(
                HistoricalBacktestConfig(root_dir=Path("data/raw/binance"), symbol="BTCUSDT"),
                walk_forward_windows=[],
                split_spec=default_btc_split_spec(),
                comparison_split="dev",
            )
        labels = [item["label"] for item in payload["windows"]]
        self.assertIn("dev", labels)
        self.assertNotIn("overall", labels)
        self.assertEqual(payload["split_scope"], "train_dev_only")

    def test_framework_report_records_test_evaluated_false_until_acceptance(self) -> None:
        from zenquant.research.framework_search import CandidateSummary

        candidate = CandidateSummary(
            variant="current",
            robust_score=1.0,
            selection_split="train",
            dev_sharpe=1.0,
            dev_pnl=10.0,
            dev_drawdown=0.1,
            avg_dev_sharpe=1.0,
            avg_dev_ratio=1.0,
            worst_dev_sharpe=0.5,
            weak_window_sharpe=0.5,
            negative_dev_worst_windows=0,
            promotion_candidate=True,
        )
        with (
            patch.object(FrameworkEvaluator, "_evaluate_variant", return_value=candidate),
            patch("zenquant.research.framework_search._build_comparison_payload", return_value={}),
        ):
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            report = evaluator.final_report(optuna_params=None, rl_params=None, search_status={}, full=False, evaluate_test=False)

        self.assertFalse(report.test_evaluated)
        self.assertEqual(report.split_contract["version"], "btc_temporal_v2_train_selection_only")
        self.assertIn("train_window", report.training_window_coverage)


if __name__ == "__main__":
    unittest.main()
