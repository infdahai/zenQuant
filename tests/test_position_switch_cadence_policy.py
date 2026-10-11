from __future__ import annotations

import unittest

from zenquant.research.auto_research import _hostile_proxy_diagnostics
from zenquant.research.regime_objective import score_regime_objective_candidate_generation


def _candidate_metrics(*, switches_per_30d: float) -> dict[str, object]:
    return {
        "sharpe": 2.0,
        "profit_factor": 1.4,
        "net_pnl": 5000.0,
        "max_drawdown": 0.08,
        "trade_count": 120,
        "fee_drag_pct": 0.01,
        "position_switches_per_30d": switches_per_30d,
        "flip_reduce_fee_drag_pct": 0.008,
        "net_pnl_per_trade": 40.0,
        "net_pnl_per_position_switch": 200.0,
        "profit_factor_after_fee": 1.386,
    }


class PositionSwitchCadencePolicyTests(unittest.TestCase):
    def test_ten_monthly_switches_do_not_block_dev_review(self) -> None:
        score = score_regime_objective_candidate_generation(
            _candidate_metrics(switches_per_30d=10.0),
            baseline_metrics={},
        )

        self.assertAlmostEqual(score["position_switches_per_day"], 1.0 / 3.0, places=6)
        self.assertEqual(score["position_switch_penalty"], 0.0)
        self.assertNotIn("train_position_switch_rate_excessive", score["bucket_blockers"])
        self.assertNotIn("train_position_switch_cadence_worse", score["bucket_blockers"])
        self.assertTrue(score["allow_dev_diagnostic_review"])

    def test_five_daily_switches_remain_soft_penalty_and_allow_dev_review(self) -> None:
        score = score_regime_objective_candidate_generation(
            _candidate_metrics(switches_per_30d=150.0),
            baseline_metrics={},
        )

        self.assertEqual(score["position_switches_per_day"], 5.0)
        self.assertGreater(score["position_switch_penalty"], 0.0)
        self.assertNotIn("train_position_switch_rate_excessive", score["bucket_blockers"])
        self.assertTrue(score["allow_dev_diagnostic_review"])

    def test_more_than_five_daily_switches_block_dev_review(self) -> None:
        score = score_regime_objective_candidate_generation(
            _candidate_metrics(switches_per_30d=151.0),
            baseline_metrics={},
        )

        self.assertAlmostEqual(score["position_switches_per_day"], 151.0 / 30.0, places=6)
        self.assertIn("train_position_switch_rate_excessive", score["bucket_blockers"])
        self.assertFalse(score["allow_dev_diagnostic_review"])

    def test_hostile_proxy_only_penalizes_more_than_one_daily_switch(self) -> None:
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
        normal = _hostile_proxy_diagnostics(params, _candidate_metrics(switches_per_30d=10.0))
        intraday_churn = _hostile_proxy_diagnostics(params, _candidate_metrics(switches_per_30d=45.0))

        self.assertAlmostEqual(normal["position_switches_per_day"], 1.0 / 3.0, places=6)
        self.assertEqual(normal["position_switch_penalty"], 0.0)
        self.assertEqual(normal["mature_position_switch_penalty"], 0.0)
        self.assertGreater(intraday_churn["position_switch_penalty"], 0.0)


if __name__ == "__main__":
    unittest.main()
