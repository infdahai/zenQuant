from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest.mock import patch

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.auto_research import Campaign, CampaignConfig
from zenquant.research.framework_params import hostile_constraint_violations
from zenquant.research.framework_search import FrameworkEvaluator, run_optuna_search
from zenquant.research.splits import default_btc_split_spec


class BaselineAnchorSmokeContractTests(unittest.TestCase):
    def test_one_trial_search_starts_with_guardrail_safe_seed(self) -> None:
        @dataclass
        class FakeSweep:
            total_combinations: int
            results: list[dict]
            top_n: list[dict]
            runtime_s: float

        captured_config: CampaignConfig | None = None

        def fake_run_search_stage(config: CampaignConfig) -> FakeSweep:
            nonlocal captured_config
            captured_config = config
            return FakeSweep(total_combinations=0, results=[], top_n=[], runtime_s=0.0)

        evaluator = FrameworkEvaluator(
            HistoricalBacktestConfig(),
            split_spec=default_btc_split_spec(),
            search_space_mode="baseline_anchored_train_only",
        )
        with patch("zenquant.research.framework_search.run_search_stage", side_effect=fake_run_search_stage):
            run_optuna_search(evaluator, n_trials=1)

        self.assertIsNotNone(captured_config)
        assert captured_config is not None
        self.assertEqual(len(captured_config.seed_trials), 1)
        self.assertEqual(hostile_constraint_violations(captured_config.seed_trials[0]), [])

    def test_constraint_pruned_trials_are_not_reported_as_zero_trade_replays(self) -> None:
        campaign = Campaign(CampaignConfig(split_spec=default_btc_split_spec(), verbose=False))
        diagnostics, reason = campaign._train_candidate_diagnostics(
            [
                {
                    "params": {"max_leverage": 6.0},
                    "metrics": {
                        "sharpe": -10.0,
                        "trade_count": 0,
                        "constraint_violations": ["high_target_vol_with_excess_leverage"],
                    },
                }
            ],
            [],
        )

        self.assertEqual(reason, "all_constraint_filtered")
        self.assertEqual(diagnostics["constraint_filtered_trial_count"], 1)
        self.assertEqual(diagnostics["executed_trial_count"], 0)


if __name__ == "__main__":
    unittest.main()
