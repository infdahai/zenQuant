import unittest

from zenquant.research.framework_params import param_grid_for_mode
from zenquant.research.loop_policy import plan_next_iteration


class LoopPolicyTests(unittest.TestCase):
    def test_hostile_window_failure_stays_in_smoke_and_generates_defensive_seeds(self) -> None:
        final_report = {
            "post_smoke_scenario": {"scenario": "scenario_b_hostile_window_failure"},
            "full_training_assessment": {
                "ready": False,
                "blockers": [
                    "worst_dev_sharpe_worse_than_paulwei",
                    "negative_worst_windows_increased",
                    "drawdown_worse_than_baseline_relative_gate",
                ],
            },
        }
        stage1_payload = {
            "optuna_params": {
                "pyramid_spacing_atr": 0.5,
                "dca_spacing_atr": 0.75,
                "dca_size_decay": 0.75,
                "exit_unwind_pct": 0.3,
                "flip_min_confidence": 0.5,
                "pyramid_add_pct": 0.3,
                "pyramid_short_add_pct": 0.2,
            },
            "top_optuna_trials": [],
        }

        plan = plan_next_iteration(
            round_index=1,
            max_rounds=8,
            current_budget="smoke",
            final_report=final_report,
            stage1_payload=stage1_payload,
        )

        self.assertFalse(plan.stop)
        self.assertEqual(plan.next_budget, "smoke")
        self.assertEqual(plan.adaptation_policy, "hostile_window_defensive")
        self.assertGreater(len(plan.explore_seed_trials), 0)
        self.assertGreater(len(plan.all_seed_trials), 0)

    def test_candidate_ready_for_handoff_stops_loop(self) -> None:
        final_report = {
            "post_smoke_scenario": {"scenario": "scenario_b_hostile_window_failure"},
            "candidate_ready_for_handoff": True,
            "handoff_blockers": [],
        }

        plan = plan_next_iteration(
            round_index=2,
            max_rounds=8,
            current_budget="smoke",
            final_report=final_report,
            stage1_payload={},
        )

        self.assertTrue(plan.stop)
        self.assertEqual(plan.stop_reason, "candidate_ready_for_handoff")
        self.assertFalse(plan.should_open_fast_or_full_training)

    def test_fast_progression_unlocks_after_smoke_round_four(self) -> None:
        final_report = {
            "post_smoke_scenario": {"scenario": "scenario_b_hostile_window_failure"},
            "full_training_assessment": {
                "ready": False,
                "blockers": [
                    "worst_dev_sharpe_worse_than_paulwei",
                    "negative_worst_windows_increased",
                    "drawdown_worse_than_baseline_relative_gate",
                ],
            },
        }
        plan = plan_next_iteration(
            round_index=4,
            max_rounds=8,
            current_budget="smoke",
            final_report=final_report,
            stage1_payload={},
        )

        self.assertEqual(plan.next_budget, "fast")
        self.assertEqual(plan.next_search_space_mode, "execution_first_v2_correlation_constrained")
        fast_grid = param_grid_for_mode(mode="execution_first_v2_correlation_constrained")
        for seed in plan.all_seed_trials:
            for key, value in seed.items():
                if key in fast_grid:
                    self.assertIn(value, fast_grid[key])

    def test_two_negative_rounds_switch_to_fast_anchored_candidate_existence(self) -> None:
        final_report = {"train_candidate_diagnostics": {"no_tuned_candidate_reason": "all_negative_primary_metric"}}
        plan = plan_next_iteration(
            round_index=2,
            max_rounds=8,
            current_budget="fast",
            final_report=final_report,
            stage1_payload={"search_status": {"search_space_mode": "execution_first_v2_correlation_constrained"}},
            recent_history=[
                {
                    "round": 1,
                    "search_space_mode": "execution_first_v2_correlation_constrained",
                    "no_tuned_candidate_reason": "all_negative_primary_metric",
                }
            ],
        )

        self.assertFalse(plan.stop)
        self.assertEqual(plan.next_budget, "fast")
        self.assertEqual(plan.next_search_space_mode, "baseline_anchored_defensive_candidate_existence")
        self.assertEqual(plan.adaptation_policy, "anchored_candidate_existence_check")

    def test_two_anchored_negative_rounds_stop_loop(self) -> None:
        final_report = {"train_candidate_diagnostics": {"no_tuned_candidate_reason": "all_negative_primary_metric"}}
        plan = plan_next_iteration(
            round_index=3,
            max_rounds=8,
            current_budget="fast",
            final_report=final_report,
            stage1_payload={"search_status": {"search_space_mode": "baseline_anchored_defensive_candidate_existence"}},
            recent_history=[
                {
                    "round": 2,
                    "search_space_mode": "baseline_anchored_defensive_candidate_existence",
                    "no_tuned_candidate_reason": "all_negative_primary_metric",
                }
            ],
        )

        self.assertTrue(plan.stop)
        self.assertEqual(plan.stop_reason, "search_space_exhausted_without_candidate")

    def test_terminal_stop_reason_in_final_report_stops_loop_immediately(self) -> None:
        plan = plan_next_iteration(
            round_index=3,
            max_rounds=8,
            current_budget="fast",
            final_report={"loop_stop_reason": "search_space_exhausted_without_candidate"},
            stage1_payload={"search_status": {"search_space_mode": "baseline_anchored_2023_08_hostile_repair"}},
        )

        self.assertTrue(plan.stop)
        self.assertEqual(plan.stop_reason, "search_space_exhausted_without_candidate")
        self.assertTrue(plan.loop_progression_blocked)


if __name__ == "__main__":
    unittest.main()
