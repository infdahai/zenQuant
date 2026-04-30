import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.research.comparison import (
    behavior_candidate_existence_variant_specs,
    build_execution_architecture_profile,
    build_risk_architecture_profile,
    execution_inventory_rethink_variant_specs,
)


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CHECK_SCRIPT = _load_module(
    "execution_inventory_architecture_check_module",
    "scripts/check_execution_inventory_architecture.py",
)
BEHAVIOR_SCRIPT = _load_module(
    "behavior_candidate_existence_check_module",
    "scripts/check_behavior_candidate_existence.py",
)


class ExecutionInventoryArchitectureTests(unittest.TestCase):
    def test_hostile_inventory_profile_tightens_short_side_and_unwind(self) -> None:
        config = build_execution_architecture_profile("hostile_inventory_rethink_v1")

        self.assertEqual(config.pyramid_initial_pct, 0.30)
        self.assertEqual(config.pyramid_add_pct, 0.30)
        self.assertTrue(config.dca_on_loss_long)
        self.assertEqual(config.pyramid_short_initial_pct, 0.15)
        self.assertEqual(config.pyramid_short_add_pct, 0.10)
        self.assertEqual(config.max_short_inventory_tranches, 2)
        self.assertEqual(config.max_long_dca_tranches, 1)
        self.assertEqual(config.exit_unwind_pct, 0.40)
        self.assertEqual(config.exit_unwind_min_pct, 0.30)
        self.assertEqual(config.exit_unwind_max_pct, 0.55)
        self.assertEqual(config.flip_min_confidence, 0.70)

    def test_execution_inventory_rethink_variant_specs_include_fixed_candidate(self) -> None:
        variants = execution_inventory_rethink_variant_specs()
        names = [item.name for item in variants]

        self.assertEqual(names, ["current", "paulwei_baseline", "hostile_inventory_rethink_v1"])
        profile_map = {item.name: item.execution_architecture_profile for item in variants}
        self.assertEqual(profile_map["hostile_inventory_rethink_v1"], "hostile_inventory_rethink_v1")
        self.assertEqual(profile_map["paulwei_baseline"], "legacy_paulwei_like")

    def test_behavior_candidate_specs_are_single_behavior_triplets(self) -> None:
        expected = {
            "short_restraint": "short_restraint_v1",
            "long_dca_cap": "long_dca_cap_v1",
            "protective_exit_speed": "protective_exit_speed_v1",
            "flip_confidence": "flip_confidence_v1",
            "vol_shock_compression": "vol_shock_compression_v1",
            "ahr999_slow_risk_modifier": "ahr999_slow_risk_modifier_v1",
            "hostile_aware_behavior_router": "hostile_aware_behavior_router_v1",
            "squeeze_flip_flatten_only": "squeeze_flip_flatten_only_v1",
            "squeeze_flip_cooldown": "squeeze_flip_cooldown_v1",
            "squeeze_conditional_cooldown": "squeeze_conditional_cooldown_v1",
            "squeeze_conditional_cooldown_dd025_b2": "squeeze_conditional_cooldown_dd025_b2",
            "squeeze_conditional_cooldown_dd040_b1": "squeeze_conditional_cooldown_dd040_b1",
            "squeeze_conditional_cooldown_dd060_b1": "squeeze_conditional_cooldown_dd060_b1",
            "squeeze_conditional_cooldown_dd060_chop_cancel_b1": ("squeeze_conditional_cooldown_dd060_chop_cancel_b1"),
            "squeeze_toxic_only_cooldown_b1": "squeeze_toxic_only_cooldown_b1",
        }
        for behavior, candidate_name in expected.items():
            variants = behavior_candidate_existence_variant_specs(behavior)
            self.assertEqual([item.name for item in variants], ["current", "paulwei_baseline", candidate_name])

    def test_behavior_profiles_touch_only_declared_family(self) -> None:
        short = build_execution_architecture_profile("short_restraint_v1")
        baseline = build_execution_architecture_profile("legacy_paulwei_like")
        self.assertLess(short.pyramid_short_add_pct, baseline.pyramid_short_add_pct)
        self.assertEqual(short.exit_unwind_pct, baseline.exit_unwind_pct)

        vol = build_risk_architecture_profile("vol_shock_compression_v1")
        risk_baseline = build_risk_architecture_profile("legacy_paulwei_like")
        self.assertLess(vol.vol_shock_mult, risk_baseline.vol_shock_mult)
        self.assertEqual(vol.dd_step_1_long, risk_baseline.dd_step_1_long)

        router = build_execution_architecture_profile("hostile_aware_behavior_router_v1")
        self.assertTrue(router.hostile_behavior_router_enabled)
        self.assertEqual(router.pyramid_short_initial_pct, baseline.pyramid_short_initial_pct)
        self.assertEqual(router.pyramid_short_add_pct, baseline.pyramid_short_add_pct)

        squeeze = build_execution_architecture_profile("squeeze_flip_flatten_only_v1")
        self.assertTrue(squeeze.squeeze_flip_flatten_only_enabled)
        self.assertFalse(squeeze.hostile_behavior_router_enabled)
        self.assertEqual(squeeze.pyramid_short_initial_pct, baseline.pyramid_short_initial_pct)
        self.assertEqual(squeeze.pyramid_short_add_pct, baseline.pyramid_short_add_pct)
        self.assertEqual(squeeze.exit_unwind_pct, baseline.exit_unwind_pct)

        cooldown = build_execution_architecture_profile("squeeze_flip_cooldown_v1")
        self.assertTrue(cooldown.squeeze_flip_cooldown_enabled)
        self.assertEqual(cooldown.squeeze_flip_cooldown_bars, 2)
        self.assertFalse(cooldown.squeeze_flip_flatten_only_enabled)
        self.assertFalse(cooldown.hostile_behavior_router_enabled)
        self.assertEqual(cooldown.pyramid_short_initial_pct, baseline.pyramid_short_initial_pct)
        self.assertEqual(cooldown.pyramid_short_add_pct, baseline.pyramid_short_add_pct)
        self.assertEqual(cooldown.exit_unwind_pct, baseline.exit_unwind_pct)

        conditional = build_execution_architecture_profile("squeeze_conditional_cooldown_v1")
        self.assertTrue(conditional.squeeze_conditional_cooldown_enabled)
        self.assertEqual(conditional.squeeze_flip_cooldown_bars, 2)
        self.assertEqual(conditional.squeeze_conditional_cooldown_drawdown_threshold, 0.025)
        self.assertFalse(conditional.squeeze_flip_cooldown_enabled)
        self.assertFalse(conditional.squeeze_flip_flatten_only_enabled)
        self.assertFalse(conditional.hostile_behavior_router_enabled)
        self.assertEqual(conditional.pyramid_short_initial_pct, baseline.pyramid_short_initial_pct)
        self.assertEqual(conditional.pyramid_short_add_pct, baseline.pyramid_short_add_pct)
        self.assertEqual(conditional.exit_unwind_pct, baseline.exit_unwind_pct)

        dd040 = build_execution_architecture_profile("squeeze_conditional_cooldown_dd040_b1")
        self.assertTrue(dd040.squeeze_conditional_cooldown_enabled)
        self.assertEqual(dd040.squeeze_flip_cooldown_bars, 1)
        self.assertEqual(dd040.squeeze_conditional_cooldown_drawdown_threshold, 0.04)

        dd060 = build_execution_architecture_profile("squeeze_conditional_cooldown_dd060_b1")
        self.assertTrue(dd060.squeeze_conditional_cooldown_enabled)
        self.assertEqual(dd060.squeeze_flip_cooldown_bars, 1)
        self.assertEqual(dd060.squeeze_conditional_cooldown_drawdown_threshold, 0.06)
        self.assertFalse(dd060.squeeze_cooldown_cancel_on_non_squeeze_enabled)

        chop_cancel = build_execution_architecture_profile("squeeze_conditional_cooldown_dd060_chop_cancel_b1")
        self.assertTrue(chop_cancel.squeeze_conditional_cooldown_enabled)
        self.assertEqual(chop_cancel.squeeze_flip_cooldown_bars, 1)
        self.assertEqual(chop_cancel.squeeze_conditional_cooldown_drawdown_threshold, 0.06)
        self.assertTrue(chop_cancel.squeeze_cooldown_cancel_on_non_squeeze_enabled)

        toxic = build_execution_architecture_profile("squeeze_toxic_only_cooldown_b1")
        self.assertTrue(toxic.squeeze_conditional_cooldown_enabled)
        self.assertEqual(toxic.squeeze_flip_cooldown_bars, 1)
        self.assertTrue(toxic.squeeze_conditional_cooldown_toxic_only)
        self.assertFalse(toxic.squeeze_flip_cooldown_enabled)

    def test_verdict_rejects_hostile_regression_even_with_higher_dev_sharpe(self) -> None:
        payload = {
            "variants": [
                {"name": "current", "execution_architecture_profile": "current_production"},
                {"name": "paulwei_baseline", "execution_architecture_profile": "legacy_paulwei_like"},
                {
                    "name": "hostile_inventory_rethink_v1",
                    "execution_architecture_profile": "hostile_inventory_rethink_v1",
                },
            ],
            "window_reports": [
                {
                    "variant": "current",
                    "window_label": "dev",
                    "metrics": {"sharpe": 1.0, "turnover_per_day": 100.0, "fee_drag_pct": 0.01},
                },
                {"variant": "current", "window_label": "2023-08", "metrics": {"sharpe": 0.1}},
                {"variant": "current", "window_label": "worst-other", "metrics": {"sharpe": 0.08}},
                {
                    "variant": "paulwei_baseline",
                    "window_label": "dev",
                    "metrics": {"sharpe": 0.9, "turnover_per_day": 120.0, "fee_drag_pct": 0.012},
                },
                {"variant": "paulwei_baseline", "window_label": "2023-08", "metrics": {"sharpe": 0.07}},
                {"variant": "paulwei_baseline", "window_label": "worst-other", "metrics": {"sharpe": 0.06}},
                {
                    "variant": "hostile_inventory_rethink_v1",
                    "window_label": "dev",
                    "metrics": {"sharpe": 1.8, "turnover_per_day": 500.0, "fee_drag_pct": 0.03},
                },
                {"variant": "hostile_inventory_rethink_v1", "window_label": "2023-08", "metrics": {"sharpe": -1.0}},
                {"variant": "hostile_inventory_rethink_v1", "window_label": "worst-other", "metrics": {"sharpe": -2.0}},
            ],
            "validation_reports": [],
        }

        verdict = CHECK_SCRIPT.build_execution_inventory_rethink_verdict(payload)

        self.assertEqual(verdict["terminal_recommendation"], "reject_and_switch_hypothesis")
        self.assertIn("weak_window_sharpe_regressed", verdict["rejection_reasons"])
        self.assertIn("worst_dev_sharpe_regressed", verdict["rejection_reasons"])

    def test_main_writes_standardized_verdict_output(self) -> None:
        fake_payload = {
            "created_at": "2026-04-23T00:00:00+00:00",
            "symbol": "BTCUSDT",
            "portfolio_symbol": "BTCUSDT_PERP",
            "variants": [
                {"name": "current", "execution_architecture_profile": "current_production"},
                {"name": "paulwei_baseline", "execution_architecture_profile": "legacy_paulwei_like"},
                {
                    "name": "hostile_inventory_rethink_v1",
                    "execution_architecture_profile": "hostile_inventory_rethink_v1",
                },
            ],
            "window_reports": [
                {
                    "variant": "current",
                    "window_label": "dev",
                    "metrics": {"sharpe": 1.0, "turnover_per_day": 100.0, "fee_drag_pct": 0.01},
                },
                {"variant": "current", "window_label": "2023-08", "metrics": {"sharpe": 0.1}},
                {"variant": "current", "window_label": "worst-other", "metrics": {"sharpe": 0.08}},
                {
                    "variant": "paulwei_baseline",
                    "window_label": "dev",
                    "metrics": {"sharpe": 0.9, "turnover_per_day": 110.0, "fee_drag_pct": 0.012},
                },
                {"variant": "paulwei_baseline", "window_label": "2023-08", "metrics": {"sharpe": 0.07}},
                {"variant": "paulwei_baseline", "window_label": "worst-other", "metrics": {"sharpe": 0.06}},
                {
                    "variant": "hostile_inventory_rethink_v1",
                    "window_label": "dev",
                    "metrics": {"sharpe": 1.1, "turnover_per_day": 105.0, "fee_drag_pct": 0.011},
                },
                {"variant": "hostile_inventory_rethink_v1", "window_label": "2023-08", "metrics": {"sharpe": 0.05}},
                {"variant": "hostile_inventory_rethink_v1", "window_label": "worst-other", "metrics": {"sharpe": 0.04}},
            ],
            "validation_reports": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            argv = ["check_execution_inventory_architecture.py", "--output-dir", str(out)]
            with (
                patch.object(sys, "argv", argv),
                patch.object(CHECK_SCRIPT, "compare_variants", return_value=fake_payload),
                patch.object(CHECK_SCRIPT, "build_markdown_summary", return_value="# comparison"),
            ):
                CHECK_SCRIPT.main()

            verdict_path = out / "execution_inventory_rethink_check.json"
            comparison_path = out / "comparison.json"
            payload = json.loads(verdict_path.read_text(encoding="utf-8"))
            comparison_exists = comparison_path.exists()

        self.assertTrue(comparison_exists)
        self.assertEqual(payload["execution_architecture_profile"], "hostile_inventory_rethink_v1")
        self.assertIn(payload["terminal_recommendation"], {"promising_for_campaign", "reject_and_switch_hypothesis"})
        self.assertFalse(payload["optuna_search_executed"])
        self.assertFalse(payload["multi_round_loop_executed"])

    def test_behavior_verdict_rejects_fee_or_hostile_regression(self) -> None:
        payload = {
            "window_reports": [
                {
                    "variant": "current",
                    "window_label": "dev",
                    "metrics": {"sharpe": 1.0, "max_drawdown": 0.10, "turnover_per_day": 100.0, "fee_drag_pct": 0.01},
                },
                {"variant": "current", "window_label": "2023-08", "metrics": {"sharpe": 0.2}},
                {
                    "variant": "paulwei_baseline",
                    "window_label": "dev",
                    "metrics": {"sharpe": 1.0, "max_drawdown": 0.10, "turnover_per_day": 100.0, "fee_drag_pct": 0.01},
                },
                {"variant": "paulwei_baseline", "window_label": "2023-08", "metrics": {"sharpe": 0.2}},
                {
                    "variant": "short_restraint_v1",
                    "window_label": "dev",
                    "metrics": {"sharpe": 1.3, "max_drawdown": 0.10, "turnover_per_day": 100.0, "fee_drag_pct": 0.03},
                },
                {"variant": "short_restraint_v1", "window_label": "2023-08", "metrics": {"sharpe": -0.1}},
            ]
        }

        verdict = BEHAVIOR_SCRIPT.build_candidate_existence_verdict(payload, behavior="short_restraint")

        self.assertEqual(verdict["terminal_recommendation"], "reject_behavior_family")
        self.assertIn("worst_hostile_sharpe_regressed", verdict["rejection_reasons"])
        self.assertIn("fee_drag_structurally_worse", verdict["rejection_reasons"])

    def test_risk_tolerant_gate_allows_small_hostile_sharpe_regression_with_compensation(self) -> None:
        payload = {
            "window_reports": [
                {
                    "variant": "current",
                    "window_label": "dev",
                    "metrics": {
                        "sharpe": 3.0,
                        "max_drawdown": 0.12,
                        "turnover_per_day": 500.0,
                        "fee_drag_pct": 0.02,
                    },
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "current",
                    "window_label": "2023-08",
                    "metrics": {"sharpe": 2.8, "max_drawdown": 0.08, "net_pnl": 100.0},
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "paulwei_baseline",
                    "window_label": "dev",
                    "metrics": {
                        "sharpe": 3.0,
                        "max_drawdown": 0.12,
                        "turnover_per_day": 600.0,
                        "fee_drag_pct": 0.02,
                    },
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "paulwei_baseline",
                    "window_label": "2023-08",
                    "metrics": {"sharpe": 2.7, "max_drawdown": 0.09, "net_pnl": 80.0},
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "squeeze_conditional_cooldown_dd040_b1",
                    "window_label": "dev",
                    "metrics": {
                        "sharpe": 2.9,
                        "max_drawdown": 0.15,
                        "turnover_per_day": 400.0,
                        "fee_drag_pct": 0.021,
                    },
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "squeeze_conditional_cooldown_dd040_b1",
                    "window_label": "2023-08",
                    "metrics": {"sharpe": 2.72, "max_drawdown": 0.07, "net_pnl": 120.0},
                    "decision_attribution_ledger": {"summary": {}},
                },
            ]
        }

        verdict = BEHAVIOR_SCRIPT.build_candidate_existence_verdict(
            payload,
            behavior="squeeze_conditional_cooldown_dd040_b1",
            gate_mode="risk_tolerant_candidate_existence",
        )

        self.assertEqual(verdict["terminal_recommendation"], "candidate_exists_open_robustness_gate")
        self.assertFalse(verdict["full_training_allowed"])
        self.assertTrue(verdict["robustness_allowed"])
        self.assertEqual(verdict["next_stage_allowed"], "robustness_only")

    def test_risk_tolerant_gate_rejects_large_sharpe_regression_or_twenty_pct_drawdown(self) -> None:
        payload = {
            "window_reports": [
                {
                    "variant": "current",
                    "window_label": "dev",
                    "metrics": {"sharpe": 3.0, "max_drawdown": 0.12, "turnover_per_day": 500.0, "fee_drag_pct": 0.02},
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "current",
                    "window_label": "2023-08",
                    "metrics": {"sharpe": 2.8, "max_drawdown": 0.08, "net_pnl": 100.0},
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "paulwei_baseline",
                    "window_label": "dev",
                    "metrics": {"sharpe": 3.0, "max_drawdown": 0.12, "turnover_per_day": 600.0, "fee_drag_pct": 0.02},
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "paulwei_baseline",
                    "window_label": "2023-08",
                    "metrics": {"sharpe": 2.7, "max_drawdown": 0.09, "net_pnl": 80.0},
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "squeeze_conditional_cooldown_dd060_b1",
                    "window_label": "dev",
                    "metrics": {"sharpe": 2.9, "max_drawdown": 0.21, "turnover_per_day": 400.0, "fee_drag_pct": 0.02},
                    "decision_attribution_ledger": {"summary": {}},
                },
                {
                    "variant": "squeeze_conditional_cooldown_dd060_b1",
                    "window_label": "2023-08",
                    "metrics": {"sharpe": 2.60, "max_drawdown": 0.07, "net_pnl": 120.0},
                    "decision_attribution_ledger": {"summary": {}},
                },
            ]
        }

        verdict = BEHAVIOR_SCRIPT.build_candidate_existence_verdict(
            payload,
            behavior="squeeze_conditional_cooldown_dd060_b1",
            gate_mode="risk_tolerant_candidate_existence",
        )

        self.assertEqual(verdict["terminal_recommendation"], "reject_behavior_family")
        self.assertIn("drawdown_exceeds_20pct_research_cap", verdict["rejection_reasons"])
        self.assertIn("worst_hostile_sharpe_regressed_beyond_tolerance", verdict["rejection_reasons"])


if __name__ == "__main__":
    unittest.main()
