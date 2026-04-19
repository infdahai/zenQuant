import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktestConfig
from zenquant.historical.storage import HistoricalKlineRow, write_parquet_rows
from zenquant.research.framework_params import (
    baseline_anchor_params,
    baseline_anchor_seed_trials,
    baseline_anchored_param_grid,
    build_replay_toggles,
    core_param_grid,
    hostile_constraint_violations,
    is_hostile_aware_param_combo,
    search_space_profile,
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
    _hostile_regression_summary,
    _run_validation_bundle,
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
        self.assertIn(0.20, grid["pyramid_short_add_pct"])
        self.assertIn(0.50, grid["flip_min_confidence"])
        self.assertIn(1.00, grid["pyramid_spacing_atr"])
        self.assertIn(1.75, grid["dca_spacing_atr"])

    def test_search_space_profile_marks_baseline_anchored_mode(self) -> None:
        profile = search_space_profile(mode="baseline_anchored_train_only")
        self.assertEqual(profile["search_space_mode"], "baseline_anchored_train_only")
        self.assertTrue(profile["baseline_anchored_search"])
        self.assertEqual(profile["baseline_anchor_variant"], "paulwei_baseline")

    def test_baseline_anchor_seed_trials_start_with_exact_baseline(self) -> None:
        seeds = baseline_anchor_seed_trials()
        self.assertGreaterEqual(len(seeds), 1)
        self.assertEqual(seeds[0], baseline_anchor_params())


class FrameworkEvaluatorTests(unittest.TestCase):
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
                return {}

            def _independence(name: str):
                if name == "btc_mvrv":
                    return {
                        "allowed_in_state_features": True,
                        "use_for_research_only": True,
                        "blocked_by": None,
                        "max_abs_correlation": 0.41,
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
                        "formal_train_truth": False,
                        "blocked_by": ["spot_perp_truth_boundary"],
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
        self.assertFalse(signal_contract["audit_present"])
        self.assertEqual(signal_contract["blocked_by"], "active_signal_independence_missing")
        historical_contract = evaluator.training_input_contract["historical_extension_candidate_contract"]
        self.assertTrue(historical_contract["present"])
        self.assertEqual(historical_contract["source"], "spot")
        self.assertFalse(historical_contract["ready"])
        self.assertIn("spot_perp_truth_boundary", historical_contract["blocked_by"])

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
        with patch("zenquant.research.framework_search._run_variant_backtest", return_value=(FakeRunner(), FakeResult())):
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
            readiness={"promotion_ready": False, "blocked_by": "not_promotion_ready", "data_ready": True, "detail": "ok"},
            deribit_status={"observation_ready": True, "enabled": True, "research_ready": True},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={},
            training_input_contract={"state_feature_sources": {}, "signal_stack_contract": {}, "signal_independence_contract": {}},
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
            search_status={"search_space_profile": {"direction_weight_policy": {"correlation_constrained": True, "capped_direction_signal": "crowding_signal", "demoted_signal": "funding_alpha_signal", "demoted_signal_role": "state"}}},
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
            search_status={"search_space_profile": {"direction_weight_policy": {"correlation_constrained": True, "capped_direction_signal": "crowding_signal", "demoted_signal": "funding_alpha_signal", "demoted_signal_role": "state"}}},
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
            readiness={"data_ready": False, "promotion_ready": False, "blocked_by": "missing_liquidation_history", "detail": "missing"},
            deribit_status={"enabled": True, "observation_ready": False, "research_ready": False},
            split_contract=split_summary_json(default_btc_split_spec()),
            training_window_coverage={"fast_windows": ["overall_recent"]},
            training_input_contract={
                "primary_truth_source": "binance",
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
        self.assertIsNone(payload["optuna_tuned"])
        self.assertIsNone(payload["rl_tuned"])
        self.assertTrue(payload["training_input_contract"]["signal_stack_contract"]["direction_state_execution_split_enabled"])
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
        self.assertIn("full_training_ready", payload["final_verdict"])
        self.assertEqual(payload["final_verdict"]["etf_flow_source"], "bitbo_etf_flows")
        self.assertEqual(payload["campaign_execution_contract"]["etf_flow_source_confidence"], "medium")
        self.assertEqual(payload["full_training_assessment"]["decision"], "defer_handoff")
        self.assertIn("handoff_blockers", payload)

    def test_run_optuna_search_uses_train_only_autoresearch_candidates(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float

        with patch("zenquant.research.framework_search.run_search_stage", return_value=FakeSweep(
            total_combinations=2,
            results=[],
            top_n=[
                {"params": {"w_momentum": 0.3}, "metrics": {"sharpe": 1.0, "score": 1.0}},
                {"params": {"w_momentum": 0.45}, "metrics": {"sharpe": 1.2, "score": 1.2}},
            ],
            runtime_s=0.1,
        )) as run_stage:
            evaluator = FrameworkEvaluator(HistoricalBacktestConfig(), split_spec=default_btc_split_spec())
            best, ranked, diagnostics = run_optuna_search(evaluator, n_trials=5)

        self.assertEqual(best, {"w_momentum": 0.45})
        self.assertEqual(ranked[0]["candidate_source"], "auto_research_train_only")
        self.assertEqual(ranked[0]["split_contract_version"], "btc_temporal_v2_train_selection_only")
        self.assertEqual(ranked[0]["summary"]["selection_split"], "train")
        self.assertIn("train_candidate_diagnostics", diagnostics)
        run_stage.assert_called_once()

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
                    type("Result", (), {
                        "params": {"w_momentum": 0.3},
                        "candidate_score": 1.0,
                        "sharpe": 1.0,
                        "pnl": 10.0,
                        "max_dd": 0.1,
                        "trades": 5,
                    })()
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
                "_get_paulwei_fast_summary",
                return_value=CandidateSummary(
                    variant="paulwei_baseline",
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
        self.assertEqual([window.label for window in evaluator._prepare_windows(fast=True, lightweight=True)], ["dev", "2023-08", "2024-11 DD"])

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
                "_get_paulwei_fast_summary",
                return_value=CandidateSummary(
                    variant="paulwei_baseline",
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
                "_get_paulwei_fast_summary",
                return_value=CandidateSummary(
                    variant="paulwei_baseline",
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
                "_get_paulwei_fast_summary",
                return_value=CandidateSummary(
                    variant="paulwei_baseline",
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
            patch.object(evaluator, "_get_paulwei_fast_summary", return_value=baseline),
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


if __name__ == "__main__":
    unittest.main()
