import unittest
from types import SimpleNamespace

from zenquant.domain import BacktestMetrics, ExecutionReport
from zenquant.research.attribution import build_decision_attribution_ledger, classify_hostile_micro_state


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


if __name__ == "__main__":
    unittest.main()
