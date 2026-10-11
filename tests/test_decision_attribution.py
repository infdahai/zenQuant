import unittest
from types import SimpleNamespace

from zenquant.domain import BacktestMetrics, ExecutionReport
from zenquant.research.attribution import (
    build_bear_window_direction_exposure_attribution,
    build_bull_window_direction_exposure_attribution,
    build_decision_attribution_ledger,
    classify_execution_action_source,
    classify_hostile_micro_state,
)


class _FakeRunner:
    def __init__(self) -> None:
        self._reports = [
            ExecutionReport(
                client_order_id="entry",
                symbol="BTCUSDT_PERP",
                side=-1,
                status="FILLED",
                filled_qty=0.2,
                avg_price=100.0,
                fee=0.01,
                transaction_time_ns=1,
            ),
            ExecutionReport(
                client_order_id="exit",
                symbol="BTCUSDT_PERP",
                side=1,
                status="FILLED",
                filled_qty=0.2,
                avg_price=95.0,
                fee=0.01,
                transaction_time_ns=2,
            ),
        ]

    def build_analysis_rows(self):
        return [
            {
                "cutoff_time_ns": 1,
                "target_qty": -1.0,
                "equity": 100000.0,
                "regime": "STRESS",
                "playbook_id": "BEAR_DEFENSE",
                "regime_confidence": 0.55,
                "directional_state": "DOWNTREND",
                "directional_confidence": 0.62,
                "volatility_state": "HIGH",
                "positioning_state": "HEALTHY",
                "quality_state": "NOISY",
                "realized_vol": 1.1,
                "transition_risk": 0.5,
                "target_reason_codes": "REGIME_STRESS|TREND_SHORT",
            },
            {
                "cutoff_time_ns": 2,
                "target_qty": 0.0,
                "equity": 100010.0,
                "regime": "TREND",
                "playbook_id": "RANGE_LIGHT",
                "regime_confidence": 0.9,
                "directional_state": "UPTREND",
                "volatility_state": "HIGH",
                "realized_vol": 0.9,
                "momentum_roc_z": 0.7,
                "exit_type": "TRAILING_STOP",
                "exit_reason_codes": "EXIT_TRAILING_STOP",
                "target_reason_codes": "EXIT_TRAILING_STOP",
            },
        ]


class DecisionAttributionTests(unittest.TestCase):
    def test_classifies_hostile_micro_states_from_point_in_time_fields(self) -> None:
        row = {
            "target_qty": -1.0,
            "regime": "STRESS",
            "directional_state": "DOWNTREND",
            "volatility_state": "HIGH",
            "realized_vol": 1.2,
            "transition_risk": 0.5,
        }

        self.assertEqual(classify_hostile_micro_state(row), "panic_reversal")

    def test_ledger_attributes_actions_fees_and_pnl_by_micro_state(self) -> None:
        metrics = BacktestMetrics(
            symbol="BTCUSDT_PERP",
            start_ns=1,
            end_ns=2,
            total_bars=2,
            net_pnl=10.0,
            gross_pnl=10.02,
            total_fees=0.02,
            total_funding=0.0,
            sharpe=1.0,
            sortino=1.0,
            max_drawdown=0.01,
            max_dd_duration_bars=1,
            win_rate=1.0,
            profit_factor=2.0,
            avg_win=10.0,
            avg_loss=0.0,
            trade_count=2,
            turnover_per_day=100.0,
            fee_drag_pct=0.002,
        )

        ledger = build_decision_attribution_ledger(
            _FakeRunner(),
            SimpleNamespace(metrics=metrics),
            variant="candidate",
            window_label="2023-08",
        )

        self.assertEqual(ledger["summary"]["bar_count"], 2)
        self.assertIn("panic_reversal", ledger["micro_state_summary"])
        self.assertIn("squeeze_continuation", ledger["micro_state_summary"])
        self.assertEqual(ledger["decision_rows"][0]["playbook_id"], "BEAR_DEFENSE")
        self.assertEqual(ledger["decision_rows"][0]["quality_state"], "NOISY")
        self.assertEqual(ledger["decision_rows"][0]["transition_risk"], 0.5)
        self.assertEqual(ledger["decision_rows"][0]["directional_confidence"], 0.62)
        self.assertIn("TREND_SHORT", ledger["decision_rows"][0]["reason_codes"])
        self.assertEqual(ledger["playbook_summary"]["BEAR_DEFENSE"]["execution_actions"]["entry"], 1)
        self.assertEqual(ledger["micro_state_summary"]["panic_reversal"]["execution_actions"]["entry"], 1)
        self.assertEqual(ledger["micro_state_summary"]["squeeze_continuation"]["execution_actions"]["exit"], 1)
        self.assertEqual(ledger["micro_state_summary"]["squeeze_continuation"]["estimated_equity_pnl"], 10.0)
        self.assertEqual(ledger["action_attribution_summary"]["exit"]["source_counts"]["exit_plugin"], 1)

    def test_ledger_attributes_risk_overlay_reduce_fees(self) -> None:
        metrics = BacktestMetrics(
            symbol="BTCUSDT_PERP",
            start_ns=1,
            end_ns=2,
            total_bars=2,
            net_pnl=1.0,
            gross_pnl=1.03,
            total_fees=0.03,
            total_funding=0.0,
            sharpe=1.0,
            sortino=1.0,
            max_drawdown=0.01,
            max_dd_duration_bars=1,
            win_rate=1.0,
            profit_factor=2.0,
            avg_win=1.0,
            avg_loss=0.0,
            trade_count=2,
            turnover_per_day=100.0,
            fee_drag_pct=0.03,
        )
        runner = SimpleNamespace(
            _reports=[
                ExecutionReport("entry", "BTCUSDT_PERP", 1, "FILLED", 0.5, 100.0, 0.01, 1),
                ExecutionReport("risk-reduce", "BTCUSDT_PERP", -1, "FILLED", 0.2, 105.0, 0.02, 2),
            ],
            build_analysis_rows=lambda: [
                {
                    "cutoff_time_ns": 1,
                    "target_qty": 0.5,
                    "equity": 100000.0,
                    "regime": "TREND",
                    "playbook_id": "BULL_TREND",
                    "target_reason_codes": "TREND_LONG",
                    "risk_decision_flags": "OK",
                },
                {
                    "cutoff_time_ns": 2,
                    "target_qty": 0.3,
                    "equity": 100001.0,
                    "regime": "TREND",
                    "playbook_id": "BULL_TREND",
                    "target_reason_codes": "TREND_LONG",
                    "risk_original_target_qty": 0.5,
                    "risk_revised_target_qty": 0.3,
                    "risk_decision_flags": "DD_STEP_1",
                },
            ],
        )

        ledger = build_decision_attribution_ledger(
            runner,
            SimpleNamespace(metrics=metrics),
            variant="candidate",
            window_label="train",
        )

        reduce_summary = ledger["action_attribution_summary"]["reduce"]
        self.assertEqual(reduce_summary["source_counts"]["risk_overlay"], 1)
        self.assertEqual(reduce_summary["source_fees"]["risk_overlay"], 0.02)

    def test_flip_from_new_entry_target_is_strategy_target(self) -> None:
        source = classify_execution_action_source(
            "flip",
            {"target_reason_codes": "TREND_SHORT", "risk_decision_flags": "OK"},
            target_action="entry",
        )

        self.assertEqual(source, "strategy_target")

    def test_bull_window_attribution_flags_missing_long_intent(self) -> None:
        metrics = BacktestMetrics(
            symbol="BTCUSDT_PERP",
            start_ns=1,
            end_ns=3,
            total_bars=3,
            net_pnl=-5.0,
            gross_pnl=-5.0,
            total_fees=0.0,
            total_funding=0.0,
            sharpe=-0.5,
            sortino=-0.5,
            max_drawdown=0.02,
            max_dd_duration_bars=1,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=-5.0,
            trade_count=0,
            turnover_per_day=0.0,
            fee_drag_pct=0.0,
        )
        runner = SimpleNamespace(
            _reports=[],
            build_analysis_rows=lambda: [
                {
                    "cutoff_time_ns": 1,
                    "close_price": 100.0,
                    "target_qty": 0.0,
                    "target_leverage": 0.0,
                    "equity": 100000.0,
                    "regime": "TREND",
                    "directional_state": "UPTREND",
                    "momentum_roc_z": 0.4,
                    "multi_momentum_z": 0.4,
                    "cycle_position": 1.1,
                    "playbook_id": "RANGE_LIGHT",
                },
                {
                    "cutoff_time_ns": 2,
                    "close_price": 125.0,
                    "target_qty": 0.0,
                    "target_leverage": 0.0,
                    "equity": 99998.0,
                    "regime": "TREND",
                    "directional_state": "UPTREND",
                    "momentum_roc_z": 0.4,
                    "multi_momentum_z": 0.4,
                    "cycle_position": 1.1,
                    "playbook_id": "RANGE_LIGHT",
                },
                {
                    "cutoff_time_ns": 3,
                    "close_price": 140.0,
                    "target_qty": 0.0,
                    "target_leverage": 0.0,
                    "equity": 99995.0,
                    "regime": "TREND",
                    "directional_state": "UPTREND",
                    "momentum_roc_z": 0.4,
                    "multi_momentum_z": 0.4,
                    "cycle_position": 1.1,
                    "playbook_id": "RANGE_LIGHT",
                },
            ],
        )

        attribution = build_bull_window_direction_exposure_attribution(
            runner,
            SimpleNamespace(metrics=metrics),
            variant="candidate",
            window_label="train_temporal_2020",
            source="post_2020_futures_train",
            role="bull_expansion_profit_run",
            formal_train_truth=True,
            execution_truth=True,
            starting_equity=100000.0,
        )

        self.assertEqual(attribution["scope"], "train_dev_only_bull_window_direction_exposure_attribution")
        self.assertFalse(attribution["test_evaluated"])
        self.assertTrue(attribution["formal_train_truth"])
        self.assertEqual(attribution["summary"]["price_return"], 0.4)
        self.assertEqual(attribution["summary"]["long_when_uptrend_pct"], 0.0)
        self.assertEqual(attribution["summary"]["cycle_bull_bar_pct"], 1.0)
        self.assertEqual(attribution["summary"]["long_when_cycle_bull_pct"], 0.0)
        self.assertEqual(attribution["summary"]["positive_momentum_bar_pct"], 1.0)
        self.assertEqual(attribution["diagnosis"]["primary_failure_mode"], "uptrend_not_converted_to_long_intent")
        self.assertIn(
            "cycle_bull_context_not_converted_to_long_intent",
            attribution["diagnosis"]["failure_flags"],
        )
        self.assertIn("insufficient_long_time_in_market", attribution["diagnosis"]["failure_flags"])

    def test_bull_window_attribution_summarizes_range_cycle_promotion_blockers(self) -> None:
        metrics = BacktestMetrics(
            symbol="BTCUSDT_PERP",
            start_ns=1,
            end_ns=3,
            total_bars=3,
            net_pnl=-5.0,
            gross_pnl=-5.0,
            total_fees=0.0,
            total_funding=0.0,
            sharpe=-0.5,
            sortino=-0.5,
            max_drawdown=0.02,
            max_dd_duration_bars=1,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=-5.0,
            trade_count=0,
            turnover_per_day=0.0,
            fee_drag_pct=0.0,
        )
        common = {
            "close_price": 100.0,
            "target_leverage": 0.0,
            "equity": 100000.0,
            "regime": "RANGE",
            "directional_state": "TRANSITION",
            "direction_signal": 0.4,
            "cycle_position": 1.1,
            "momentum_roc_z": 0.4,
            "multi_momentum_z": 0.5,
            "playbook_id": "RANGE_LIGHT",
        }
        runner = SimpleNamespace(
            _reports=[],
            build_analysis_rows=lambda: [
                {
                    **common,
                    "cutoff_time_ns": 1,
                    "target_qty": 0.0,
                    "target_reason_codes": "REGIME_RANGE|NO_DIRECTION",
                    "intent_promotion_latest_reason": "4H_PENDING_NO_DIRECTION",
                    "intent_promotion_latest_stage": "4h_pending",
                    "intent_promotion_latest_pending_direction": 0.0,
                    "intent_promotion_latest_pending_reason_codes": "REGIME_RANGE|NO_DIRECTION",
                    "intent_promotion_latest_diagnostic_reason_codes": "RANGE_BULL_PROFIT_MOMENTUM_TOO_WEAK",
                },
                {
                    **common,
                    "cutoff_time_ns": 2,
                    "target_qty": 0.0,
                    "target_reason_codes": "REGIME_RANGE|HOLD_EXECUTION_POSITION",
                    "intent_promotion_latest_reason": "1H_CONTEXT_SCORE_TOO_WEAK",
                    "intent_promotion_latest_stage": "1h_context",
                    "intent_promotion_latest_pending_direction": 1.0,
                    "intent_promotion_latest_pending_reason_codes": (
                        "multi_momentum_signal:MULTI_MOM_RANGE_BULL_PROFIT_RUN|TREND_LONG"
                    ),
                    "intent_promotion_latest_entry_score": 0.32,
                    "intent_promotion_latest_entry_score_threshold": 0.5,
                },
                {
                    **common,
                    "cutoff_time_ns": 3,
                    "target_qty": 1.0,
                    "target_leverage": 0.4,
                    "target_reason_codes": "REGIME_RANGE|TREND_LONG",
                    "intent_promotion_reasons": "5M_TIMING_CONFIRMED",
                    "intent_promotion_stages": "5m_timing",
                    "intent_promotion_pending_direction": 1.0,
                    "intent_promotion_pending_reason_codes": (
                        "multi_momentum_signal:MULTI_MOM_RANGE_BULL_PROFIT_RUN|TREND_LONG"
                    ),
                },
            ],
        )

        attribution = build_bull_window_direction_exposure_attribution(
            runner,
            SimpleNamespace(metrics=metrics),
            variant="candidate",
            window_label="train_temporal_2020",
            source="post_2020_futures_train",
            role="bull_expansion_profit_run",
            formal_train_truth=True,
            execution_truth=True,
            starting_equity=100000.0,
        )

        self.assertEqual(attribution["summary"]["range_cycle_profit_candidate_bar_pct"], 1.0)
        self.assertEqual(attribution["summary"]["long_when_range_cycle_profit_candidate_pct"], 0.333333)
        self.assertEqual(
            attribution["summary"]["confirmed_long_promotion_when_range_cycle_profit_candidate_pct"],
            0.333333,
        )
        self.assertEqual(attribution["summary"]["long_when_confirmed_long_promotion_pct"], 1.0)
        self.assertEqual(attribution["counts"]["range_cycle_profit_candidate_playbooks"]["RANGE_LIGHT"], 3)
        self.assertEqual(attribution["counts"]["range_cycle_profit_candidate_blockers"]["no_long_target"], 2)
        self.assertEqual(
            attribution["counts"]["range_cycle_profit_candidate_promotion_reasons"]["1H_CONTEXT_SCORE_TOO_WEAK"],
            1,
        )
        self.assertEqual(
            attribution["counts"]["range_cycle_profit_candidate_promotion_context_sources"]["latest_prior"],
            2,
        )
        self.assertEqual(
            attribution["counts"]["range_cycle_profit_candidate_promotion_context_sources"]["exact_bar"],
            1,
        )
        self.assertEqual(
            attribution["counts"]["range_cycle_profit_candidate_no_direction_diagnostic_reason_codes"][
                "RANGE_BULL_PROFIT_MOMENTUM_TOO_WEAK"
            ],
            1,
        )
        self.assertEqual(
            attribution["counts"]["range_cycle_profit_candidate_promotion_entry_score_buckets"]["0_30_0_35"],
            1,
        )
        self.assertEqual(
            attribution["counts"]["range_cycle_profit_candidate_entry_score_gap_buckets"]["neg_0_20_neg_0_10"],
            1,
        )
        self.assertEqual(
            attribution["counts"]["range_cycle_profit_candidate_confirmed_long_target_directions"]["long"],
            1,
        )

    def test_bear_window_attribution_flags_overlong_and_missing_short(self) -> None:
        metrics = BacktestMetrics(
            symbol="BTCUSDT_PERP",
            start_ns=1,
            end_ns=3,
            total_bars=3,
            net_pnl=-12.0,
            gross_pnl=-12.0,
            total_fees=0.0,
            total_funding=0.0,
            sharpe=-1.0,
            sortino=-1.0,
            max_drawdown=0.08,
            max_dd_duration_bars=2,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=-12.0,
            trade_count=1,
            turnover_per_day=0.0,
            fee_drag_pct=0.0,
        )
        runner = SimpleNamespace(
            _reports=[],
            build_analysis_rows=lambda: [
                {
                    "cutoff_time_ns": 1,
                    "close_price": 100.0,
                    "target_qty": 1.0,
                    "target_leverage": 0.8,
                    "equity": 100000.0,
                    "regime": "RANGE",
                    "directional_state": "DOWNTREND",
                    "direction_signal": -0.6,
                    "cycle_position": 1.1,
                    "momentum_roc_z": 0.3,
                    "playbook_id": "RANGE_LIGHT",
                },
                {
                    "cutoff_time_ns": 2,
                    "close_price": 90.0,
                    "target_qty": 1.0,
                    "target_leverage": 0.8,
                    "equity": 99994.0,
                    "regime": "RANGE",
                    "directional_state": "DOWNTREND",
                    "direction_signal": -0.6,
                    "cycle_position": 1.1,
                    "momentum_roc_z": 0.3,
                    "playbook_id": "RANGE_LIGHT",
                },
                {
                    "cutoff_time_ns": 3,
                    "close_price": 80.0,
                    "target_qty": 1.0,
                    "target_leverage": 0.8,
                    "equity": 99988.0,
                    "regime": "RANGE",
                    "directional_state": "DOWNTREND",
                    "direction_signal": -0.6,
                    "cycle_position": 1.1,
                    "momentum_roc_z": 0.3,
                    "playbook_id": "RANGE_LIGHT",
                },
            ],
        )

        attribution = build_bear_window_direction_exposure_attribution(
            runner,
            SimpleNamespace(metrics=metrics),
            variant="candidate",
            window_label="train_temporal_2022",
            source="post_2020_futures_train",
            role="bear_survival_direction_exposure",
            formal_train_truth=True,
            execution_truth=True,
            starting_equity=100000.0,
        )

        self.assertEqual(attribution["scope"], "train_dev_only_bear_window_direction_exposure_attribution")
        self.assertFalse(attribution["test_evaluated"])
        self.assertEqual(attribution["summary"]["price_return"], -0.2)
        self.assertEqual(attribution["summary"]["long_when_bearish_state_pct"], 1.0)
        self.assertEqual(attribution["summary"]["short_when_bearish_state_pct"], 0.0)
        self.assertEqual(attribution["diagnosis"]["primary_failure_mode"], "cycle_profit_run_overlong_in_bear_window")
        self.assertIn(
            "bearish_state_not_converted_to_short_intent",
            attribution["diagnosis"]["failure_flags"],
        )
        self.assertIn("risk_overlay_not_reducing_bear_exposure", attribution["diagnosis"]["failure_flags"])

    def test_bear_window_attribution_summarizes_bear_carry_base(self) -> None:
        metrics = BacktestMetrics(
            symbol="BTCUSDT_PERP",
            start_ns=1,
            end_ns=2,
            total_bars=2,
            net_pnl=-2.0,
            gross_pnl=-2.0,
            total_fees=0.0,
            total_funding=0.0,
            sharpe=-0.5,
            sortino=-0.5,
            max_drawdown=0.02,
            max_dd_duration_bars=1,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=-2.0,
            trade_count=0,
            turnover_per_day=0.0,
            fee_drag_pct=0.0,
        )
        runner = SimpleNamespace(
            _reports=[],
            build_analysis_rows=lambda: [
                {
                    "cutoff_time_ns": 1,
                    "close_price": 100.0,
                    "target_qty": 0.25,
                    "target_leverage": 0.25,
                    "target_reason_codes": ("TIME_LEVERAGE_LONG_BIAS_ACTIVE|TIME_LEVERAGE_BEAR_CARRY_BASE|TREND_LONG"),
                    "equity": 99999.0,
                    "regime": "TREND",
                    "directional_state": "DOWNTREND",
                    "direction_signal": -0.6,
                    "cycle_position": 0.90,
                    "momentum_roc_z": -0.4,
                    "playbook_id": "BEAR_DEFENSE",
                },
                {
                    "cutoff_time_ns": 2,
                    "close_price": 98.0,
                    "target_qty": 0.25,
                    "target_leverage": 0.25,
                    "target_reason_codes": ("TIME_LEVERAGE_LONG_BIAS_ACTIVE|TIME_LEVERAGE_BEAR_CARRY_CAP|TREND_LONG"),
                    "equity": 99998.0,
                    "regime": "TREND",
                    "directional_state": "DOWNTREND",
                    "direction_signal": -0.6,
                    "cycle_position": 0.90,
                    "momentum_roc_z": -0.4,
                    "playbook_id": "BEAR_DEFENSE",
                },
            ],
        )

        attribution = build_bear_window_direction_exposure_attribution(
            runner,
            SimpleNamespace(metrics=metrics),
            variant="candidate",
            window_label="train_temporal_2022",
            source="post_2020_futures_train",
            role="bear_survival_direction_exposure",
            formal_train_truth=True,
            execution_truth=True,
            starting_equity=100000.0,
        )

        self.assertEqual(attribution["summary"]["bear_carry_bar_pct"], 1.0)
        self.assertEqual(attribution["summary"]["long_when_bear_carry_pct"], 1.0)
        self.assertEqual(attribution["summary"]["avg_bear_carry_target_leverage"], 0.25)
        self.assertEqual(attribution["counts"]["bear_carry_reason_codes"]["TIME_LEVERAGE_BEAR_CARRY_BASE"], 1)
        self.assertEqual(attribution["counts"]["bear_carry_reason_codes"]["TIME_LEVERAGE_BEAR_CARRY_CAP"], 1)

    def test_bear_window_attribution_flags_range_bear_profit_context_without_short(self) -> None:
        metrics = BacktestMetrics(
            symbol="BTCUSDT_PERP",
            start_ns=1,
            end_ns=3,
            total_bars=3,
            net_pnl=-6.0,
            gross_pnl=-6.0,
            total_fees=0.0,
            total_funding=0.0,
            sharpe=-1.0,
            sortino=-1.0,
            max_drawdown=0.04,
            max_dd_duration_bars=2,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=-6.0,
            trade_count=0,
            turnover_per_day=0.0,
            fee_drag_pct=0.0,
        )
        runner = SimpleNamespace(
            _reports=[],
            build_analysis_rows=lambda: [
                {
                    "cutoff_time_ns": idx,
                    "close_price": price,
                    "target_qty": 0.0,
                    "target_leverage": 0.0,
                    "equity": 100000.0 - idx,
                    "regime": "RANGE",
                    "directional_state": "DOWNTREND",
                    "direction_signal": -0.4,
                    "cycle_position": 0.92,
                    "momentum_roc_z": -0.4,
                    "multi_momentum_z": -0.5,
                    "playbook_id": "RANGE_LIGHT",
                    "intent_promotion_latest_reason": "1H_CONTEXT_SCORE_TOO_WEAK",
                    "intent_promotion_latest_stage": "1h_context",
                    "intent_promotion_latest_pending_reason_codes": (
                        "STATE_RANGE_BEAR_PROFIT_RUN_OVERRIDE|TREND_SHORT"
                    ),
                    "intent_promotion_latest_entry_score": 0.32,
                    "intent_promotion_latest_entry_score_threshold": 0.50,
                    "intent_promotion_latest_diagnostic_reason_codes": "RANGE_BEAR_PROFIT_MOMENTUM_TOO_WEAK",
                }
                for idx, price in enumerate((100.0, 90.0, 82.0), start=1)
            ],
        )

        attribution = build_bear_window_direction_exposure_attribution(
            runner,
            SimpleNamespace(metrics=metrics),
            variant="candidate",
            window_label="train_temporal_2022",
            source="post_2020_futures_train",
            role="bear_survival_direction_exposure",
            formal_train_truth=True,
            execution_truth=True,
            starting_equity=100000.0,
        )

        self.assertEqual(attribution["summary"]["range_bear_profit_candidate_bar_pct"], 1.0)
        self.assertEqual(attribution["summary"]["short_when_range_bear_profit_candidate_pct"], 0.0)
        self.assertEqual(attribution["counts"]["range_bear_profit_candidate_playbooks"]["RANGE_LIGHT"], 3)
        self.assertEqual(attribution["counts"]["range_bear_profit_candidate_blockers"]["no_short_target"], 3)
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_promotion_reasons"]["1H_CONTEXT_SCORE_TOO_WEAK"],
            3,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_pending_reason_codes"][
                "STATE_RANGE_BEAR_PROFIT_RUN_OVERRIDE"
            ],
            3,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_promotion_context_sources"]["latest_prior"],
            3,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_promotion_entry_score_buckets"]["0_30_0_35"],
            3,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_context_entry_score_buckets"]["0_30_0_35"],
            3,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_entry_score_gap_buckets"]["neg_0_20_neg_0_10"],
            3,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_no_direction_diagnostic_reason_codes"],
            {},
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_blockers"]["lower_timeframe_score_too_weak"],
            3,
        )
        self.assertNotIn(
            "playbook_not_bear_profit_allowed",
            attribution["counts"]["range_bear_profit_candidate_blockers"],
        )
        self.assertEqual(
            attribution["diagnosis"]["primary_failure_mode"],
            "range_bear_profit_context_not_converted_to_short_intent",
        )

    def test_bear_window_attribution_counts_4h_no_direction_diagnostics(self) -> None:
        metrics = BacktestMetrics(
            symbol="BTCUSDT_PERP",
            start_ns=1,
            end_ns=2,
            total_bars=2,
            net_pnl=-4.0,
            gross_pnl=-4.0,
            total_fees=0.0,
            total_funding=0.0,
            sharpe=-1.0,
            sortino=-1.0,
            max_drawdown=0.04,
            max_dd_duration_bars=2,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=-4.0,
            trade_count=0,
            turnover_per_day=0.0,
            fee_drag_pct=0.0,
        )
        runner = SimpleNamespace(
            _reports=[],
            build_analysis_rows=lambda: [
                {
                    "cutoff_time_ns": idx,
                    "close_price": price,
                    "target_qty": 0.0,
                    "target_leverage": 0.0,
                    "target_reason_codes": "REGIME_RANGE|NO_DIRECTION",
                    "equity": 100000.0 - idx,
                    "regime": "RANGE",
                    "directional_state": "DOWNTREND",
                    "direction_signal": -0.4,
                    "cycle_position": 0.92,
                    "momentum_roc_z": -0.4,
                    "multi_momentum_z": -0.5,
                    "playbook_id": "RANGE_LIGHT",
                    "intent_promotion_latest_reason": "4H_PENDING_NO_DIRECTION",
                    "intent_promotion_latest_stage": "4h_pending",
                    "intent_promotion_latest_pending_direction": 0.0,
                    "intent_promotion_latest_pending_reason_codes": "REGIME_RANGE|NO_DIRECTION",
                    "intent_promotion_latest_diagnostic_reason_codes": (
                        "RANGE_BEAR_PROFIT_MOMENTUM_TOO_WEAK|RANGE_BEAR_PROFIT_STATE_NOT_BEARISH"
                    ),
                }
                for idx, price in enumerate((100.0, 92.0), start=1)
            ],
        )

        attribution = build_bear_window_direction_exposure_attribution(
            runner,
            SimpleNamespace(metrics=metrics),
            variant="candidate",
            window_label="train_temporal_2022",
            source="post_2020_futures_train",
            role="bear_survival_direction_exposure",
            formal_train_truth=True,
            execution_truth=True,
            starting_equity=100000.0,
        )

        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_blockers"]["4h_pending_no_direction"],
            2,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_no_direction_diagnostic_reason_codes"][
                "RANGE_BEAR_PROFIT_MOMENTUM_TOO_WEAK"
            ],
            2,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_no_direction_diagnostic_reason_codes"][
                "RANGE_BEAR_PROFIT_STATE_NOT_BEARISH"
            ],
            2,
        )

    def test_bear_window_attribution_counts_confirmed_short_promotion_target_conversion(self) -> None:
        metrics = BacktestMetrics(
            symbol="BTCUSDT_PERP",
            start_ns=1,
            end_ns=3,
            total_bars=3,
            net_pnl=-6.0,
            gross_pnl=-6.0,
            total_fees=0.0,
            total_funding=0.0,
            sharpe=-1.0,
            sortino=-1.0,
            max_drawdown=0.04,
            max_dd_duration_bars=2,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=-6.0,
            trade_count=0,
            turnover_per_day=0.0,
            fee_drag_pct=0.0,
        )
        common = {
            "close_price": 100.0,
            "target_leverage": 0.0,
            "equity": 100000.0,
            "regime": "RANGE",
            "directional_state": "DOWNTREND",
            "direction_signal": -0.4,
            "cycle_position": 0.92,
            "momentum_roc_z": -0.4,
            "multi_momentum_z": -0.5,
            "playbook_id": "RANGE_LIGHT",
        }
        rows = [
            {
                **common,
                "cutoff_time_ns": 1,
                "target_qty": 0.0,
                "target_reason_codes": "REGIME_RANGE|NO_DIRECTION",
                "intent_promotion_reasons": "5M_TIMING_CONFIRMED",
                "intent_promotion_stages": "5m_timing",
                "intent_promotion_pending_direction": -1.0,
                "intent_promotion_pending_reason_codes": "STATE_RANGE_BEAR_PROFIT_RUN_OVERRIDE|TREND_SHORT",
            },
            {
                **common,
                "cutoff_time_ns": 2,
                "target_qty": 0.0,
                "target_reason_codes": "REGIME_RANGE|HOLD_EXECUTION_POSITION",
                "intent_promotion_latest_reason": "5M_TIMING_CONFIRMED",
                "intent_promotion_latest_stage": "5m_timing",
                "intent_promotion_latest_pending_direction": -1.0,
                "intent_promotion_latest_age_ns": 10 * 60 * 1_000_000_000,
                "intent_promotion_latest_pending_reason_codes": ("STATE_RANGE_BEAR_PROFIT_RUN_OVERRIDE|TREND_SHORT"),
            },
            {
                **common,
                "cutoff_time_ns": 3,
                "target_qty": -1.0,
                "target_reason_codes": "REGIME_RANGE|TREND_SHORT",
                "intent_promotion_reasons": "1H_CONTEXT_CONFIRMED",
                "intent_promotion_stages": "1h_context",
                "intent_promotion_pending_direction": -1.0,
                "intent_promotion_pending_reason_codes": "STATE_RANGE_BEAR_PROFIT_RUN_OVERRIDE|TREND_SHORT",
            },
        ]
        runner = SimpleNamespace(_reports=[], build_analysis_rows=lambda: rows)

        attribution = build_bear_window_direction_exposure_attribution(
            runner,
            SimpleNamespace(metrics=metrics),
            variant="candidate",
            window_label="train_temporal_2022",
            source="post_2020_futures_train",
            role="bear_survival_direction_exposure",
            formal_train_truth=True,
            execution_truth=True,
            starting_equity=100000.0,
        )

        self.assertEqual(
            attribution["summary"]["confirmed_short_promotion_when_range_bear_profit_candidate_pct"],
            1.0,
        )
        self.assertEqual(attribution["summary"]["short_when_confirmed_short_promotion_pct"], 0.333333)
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_confirmed_short_target_directions"]["flat"],
            2,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_confirmed_short_target_directions"]["short"],
            1,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_confirmed_short_blockers"][
                "no_short_target_after_confirmed_promotion"
            ],
            2,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_confirmed_short_blockers"][
                "no_direction_after_confirmed_promotion"
            ],
            1,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_confirmed_short_blockers"][
                "hold_execution_position_after_confirmed_promotion"
            ],
            1,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_confirmed_short_latest_age_buckets"]["exact_bar"],
            2,
        )
        self.assertEqual(
            attribution["counts"]["range_bear_profit_candidate_confirmed_short_latest_age_buckets"]["5m_1h"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
