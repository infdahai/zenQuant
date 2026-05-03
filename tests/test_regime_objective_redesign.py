import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.research.regime_objective import (
    build_regime_objective_verdict,
    compare_regime_objective_candidate_rankings,
    score_regime_objective_candidate_generation,
    train_regime_bucket_specs,
    train_regime_windows,
    validate_train_regime_buckets,
)
from zenquant.research.splits import default_btc_split_spec, period_within_split


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CHECK_SCRIPT = _load_module(
    "regime_objective_candidate_check_module",
    "scripts/check_regime_objective_candidate.py",
)
CANDIDATE_GENERATION_SCRIPT = _load_module(
    "regime_objective_candidate_generation_check_module",
    "scripts/check_regime_objective_candidate_generation.py",
)


def _run(variant: str, label: str, sharpe: float, *, turnover: float = 100.0, fee: float = 0.01) -> dict:
    return {
        "variant": variant,
        "window_label": label,
        "metrics": {
            "sharpe": sharpe,
            "net_pnl": sharpe * 10.0,
            "max_drawdown": 0.10,
            "profit_factor": 1.20,
            "trade_count": 10,
            "turnover_per_day": turnover,
            "fee_drag_pct": fee,
        },
        "win_rate": 0.50,
        "directional_state_distribution": {},
        "regime_distribution": {},
        "playbook_usage": {},
    }


def _payload(
    candidate_sharpes: dict[str, float],
    *,
    candidate_variant: str = "candidate_v1",
    current_full: float = 1.0,
    candidate_full: float = 1.1,
) -> dict:
    labels = [window.label for window in train_regime_windows(default_btc_split_spec())]
    window_reports = []
    for label in labels:
        current_sharpe = current_full if label == "train_full" else 0.80
        paulwei_sharpe = 0.90 if label == "train_full" else 0.75
        candidate_sharpe = candidate_full if label == "train_full" else candidate_sharpes.get(label, 0.82)
        window_reports.extend(
            [
                _run("current", label, current_sharpe),
                _run("paulwei_baseline", label, paulwei_sharpe),
                _run(candidate_variant, label, candidate_sharpe),
            ]
        )
    return {
        "created_at": "2026-04-25T00:00:00+00:00",
        "variants": [
            {"name": "current"},
            {"name": "paulwei_baseline"},
            {"name": candidate_variant},
        ],
        "windows": [{"label": label} for label in labels],
        "window_reports": window_reports,
        "validation_reports": [],
    }


class RegimeObjectiveRedesignTests(unittest.TestCase):
    def test_train_regime_buckets_stay_inside_train_split(self) -> None:
        spec = default_btc_split_spec()
        buckets = train_regime_bucket_specs(spec)

        self.assertEqual(
            [bucket.label for bucket in buckets],
            [
                "train_full",
                "train_hostile_grind_2022_10",
                "train_transition_drawdown_2023_05",
                "train_neutral_chop_2023_08",
                "train_late_cycle_recovery_2023_11",
            ],
        )
        for bucket in buckets:
            self.assertIsNotNone(bucket.window.start_ns)
            self.assertIsNotNone(bucket.window.end_ns)
            assert bucket.window.start_ns is not None
            assert bucket.window.end_ns is not None
            self.assertTrue(period_within_split(bucket.window.start_ns, bucket.window.end_ns, spec.train))
            self.assertLessEqual(bucket.window.end_ns, spec.dev.start_ns)

        validate_train_regime_buckets(buckets, spec)

    def test_objective_rejects_aggregate_improvement_with_hostile_regression(self) -> None:
        payload = _payload({"train_hostile_grind_2022_10": 0.40}, current_full=1.0, candidate_full=1.3)

        verdict = build_regime_objective_verdict(payload, candidate_variant="candidate_v1")

        self.assertEqual(verdict["terminal_recommendation"], "reject_regime_objective")
        self.assertIn("train_hostile_grind_2022_10_sharpe_regressed", verdict["rejection_reasons"])
        self.assertIn("aggregate_sharpe_only_improvement", verdict["rejection_reasons"])
        self.assertFalse(verdict["dev_diagnostic_review_allowed"])

    def test_objective_allows_candidate_only_to_dev_diagnostic_review(self) -> None:
        payload = _payload({}, current_full=1.0, candidate_full=1.15)

        verdict = build_regime_objective_verdict(payload, candidate_variant="candidate_v1")

        self.assertEqual(verdict["terminal_recommendation"], "allow_dev_diagnostic_review")
        self.assertTrue(verdict["dev_diagnostic_review_allowed"])
        self.assertFalse(verdict["test_evaluated"])
        self.assertFalse(verdict["promotion_allowed"])
        self.assertFalse(verdict["paper_live_eligible"])

    def test_objective_rejects_structural_fee_and_turnover_degradation(self) -> None:
        labels = [window.label for window in train_regime_windows(default_btc_split_spec())]
        window_reports = []
        for label in labels:
            window_reports.extend(
                [
                    _run("current", label, 1.0, turnover=100.0, fee=0.010),
                    _run("paulwei_baseline", label, 0.9, turnover=100.0, fee=0.010),
                    _run("candidate_v1", label, 1.1, turnover=250.0, fee=0.020),
                ]
            )
        verdict = build_regime_objective_verdict(
            {"window_reports": window_reports},
            candidate_variant="candidate_v1",
        )

        self.assertEqual(verdict["terminal_recommendation"], "reject_regime_objective")
        self.assertIn("train_fee_drag_structurally_worse", verdict["rejection_reasons"])
        self.assertIn("train_turnover_structurally_worse", verdict["rejection_reasons"])

    def test_candidate_generation_scorer_rejects_dev_or_test_metrics(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not use dev/test metrics"):
            score_regime_objective_candidate_generation({"sharpe": 1.0, "dev_sharpe": 2.0})

        with self.assertRaisesRegex(ValueError, "bucket must be train-only"):
            score_regime_objective_candidate_generation(
                {
                    "sharpe": 1.0,
                    "train_bucket_metrics": {"test_window": {"sharpe": 1.0}},
                }
            )

    def test_candidate_generation_scorer_blocks_bucket_regression_before_dev(self) -> None:
        score = score_regime_objective_candidate_generation(
            {
                "sharpe": 2.0,
                "profit_factor": 1.2,
                "max_drawdown": 0.1,
                "turnover_per_day": 100.0,
                "fee_drag_pct": 0.01,
                "train_bucket_metrics": {
                    "train_late_cycle_recovery_2023_11": {"sharpe": 0.1},
                    "train_hostile_grind_2022_10": {"sharpe": 0.9},
                },
            },
            baseline_metrics={
                "turnover_per_day": 100.0,
                "fee_drag_pct": 0.01,
                "train_bucket_metrics": {
                    "train_late_cycle_recovery_2023_11": {"sharpe": 0.4},
                    "train_hostile_grind_2022_10": {"sharpe": 0.8},
                },
            },
        )

        self.assertFalse(score["allow_dev_diagnostic_review"])
        self.assertIn("train_late_cycle_recovery_2023_11_sharpe_regressed", score["bucket_blockers"])
        self.assertFalse(score["test_evaluated"])
        self.assertFalse(score["promotion_allowed"])

    def test_candidate_generation_scorer_allows_cleaner_stable_bucket_candidate(self) -> None:
        score = score_regime_objective_candidate_generation(
            {
                "sharpe": 1.7,
                "profit_factor": 1.3,
                "max_drawdown": 0.08,
                "turnover_per_day": 80.0,
                "fee_drag_pct": 0.008,
                "train_bucket_metrics": {
                    "train_late_cycle_recovery_2023_11": {"sharpe": 0.42},
                    "train_hostile_grind_2022_10": {"sharpe": 0.82},
                },
            },
            baseline_metrics={
                "turnover_per_day": 100.0,
                "fee_drag_pct": 0.01,
                "train_bucket_metrics": {
                    "train_late_cycle_recovery_2023_11": {"sharpe": 0.4},
                    "train_hostile_grind_2022_10": {"sharpe": 0.8},
                },
            },
        )

        self.assertTrue(score["allow_dev_diagnostic_review"])
        self.assertEqual(score["bucket_blockers"], [])
        self.assertLess(score["turnover_penalty"], 0.01)
        self.assertLess(score["trade_count_penalty"], 0.01)
        self.assertLess(score["fee_drag_penalty"], 0.01)

    def test_candidate_generation_scorer_uses_trade_count_when_turnover_missing(self) -> None:
        score = score_regime_objective_candidate_generation(
            {
                "sharpe": 2.2,
                "profit_factor": 1.2,
                "max_drawdown": 0.08,
                "trade_count": 5200,
                "fee_drag_pct": 0.01,
            },
            baseline_metrics={
                "trade_count": 3600,
                "fee_drag_pct": 0.01,
            },
        )

        self.assertFalse(score["allow_dev_diagnostic_review"])
        self.assertGreater(score["trade_count_penalty"], 0.20)
        self.assertIn("train_trade_count_churn_worse", score["bucket_blockers"])

    def test_candidate_generation_scorer_uses_absolute_fee_drag_when_baseline_missing(self) -> None:
        score = score_regime_objective_candidate_generation(
            {
                "sharpe": 2.2,
                "profit_factor": 1.2,
                "max_drawdown": 0.08,
                "trade_count": 4100,
                "fee_drag_pct": 0.032,
            }
        )

        self.assertFalse(score["allow_dev_diagnostic_review"])
        self.assertGreater(score["fee_drag_penalty"], 0.15)
        self.assertIn("train_fee_drag_structurally_worse", score["bucket_blockers"])

    def test_candidate_generation_scorer_blocks_hard_fee_efficiency_failures(self) -> None:
        score = score_regime_objective_candidate_generation(
            {
                "sharpe": 2.2,
                "profit_factor": 1.0,
                "net_pnl": 100.0,
                "max_drawdown": 0.08,
                "trade_count": 3000,
                "fee_drag_pct": 0.03,
            },
            baseline_metrics={
                "profit_factor": 1.3,
                "net_pnl": 1200.0,
                "trade_count": 120,
                "fee_drag_pct": 0.01,
            },
        )

        self.assertFalse(score["allow_dev_diagnostic_review"])
        self.assertIn("train_fee_drag_cap_exceeded", score["bucket_blockers"])
        self.assertIn("train_trade_count_per_day_cap_exceeded", score["bucket_blockers"])
        self.assertIn("train_net_pnl_per_trade_inefficient", score["bucket_blockers"])
        self.assertIn("train_profit_factor_after_fee_weak", score["bucket_blockers"])
        self.assertLess(score["profit_factor_after_fee"], 1.05)
        self.assertGreater(score["trade_count_per_day"], 1.5)

    def test_candidate_generation_scorer_penalizes_weak_time_leverage_cadence(self) -> None:
        score = score_regime_objective_candidate_generation(
            {
                "sharpe": 2.3,
                "profit_factor": 1.2,
                "net_pnl": 3000.0,
                "max_drawdown": 0.08,
                "trade_count": 300,
                "fee_drag_pct": 0.02,
                "position_switches_per_30d": 8.0,
                "flip_reduce_fee_drag_pct": 0.025,
                "net_pnl_per_position_switch": 20.0,
                "action_trade_counts": {"flip": 150, "reduce": 100},
                "action_fee_drag_pct": {"flip": 0.010, "reduce": 0.015},
            },
            baseline_metrics={
                "profit_factor": 1.2,
                "net_pnl": 5000.0,
                "trade_count": 120,
                "fee_drag_pct": 0.015,
                "position_switches_per_30d": 2.5,
                "flip_reduce_fee_drag_pct": 0.010,
                "net_pnl_per_position_switch": 120.0,
            },
        )

        self.assertFalse(score["allow_dev_diagnostic_review"])
        self.assertIn("train_position_switch_cadence_worse", score["bucket_blockers"])
        self.assertIn("train_flip_reduce_fee_drag_worse", score["bucket_blockers"])
        self.assertIn("train_time_leverage_efficiency_weak", score["bucket_blockers"])
        self.assertGreater(score["position_switch_penalty"], 0.0)
        self.assertGreater(score["mature_position_switch_penalty"], 0.0)
        self.assertGreater(score["flip_reduce_fee_drag_penalty"], 0.0)
        self.assertLess(score["net_pnl_per_position_switch"], 90.0)

    def test_candidate_generation_scorer_rewards_profitable_low_switch_time_leverage(self) -> None:
        score = score_regime_objective_candidate_generation(
            {
                "sharpe": 1.8,
                "profit_factor": 1.2,
                "net_pnl": 3000.0,
                "max_drawdown": 0.08,
                "trade_count": 120,
                "fee_drag_pct": 0.015,
                "position_switches_per_30d": 1.8,
                "flip_reduce_fee_drag_pct": 0.008,
                "net_pnl_per_trade": 25.0,
                "net_pnl_per_position_switch": 250.0,
                "profit_factor_after_fee": 1.18,
            },
            baseline_metrics={
                "profit_factor": 1.1,
                "net_pnl": 2500.0,
                "trade_count": 140,
                "fee_drag_pct": 0.015,
                "position_switches_per_30d": 2.5,
                "flip_reduce_fee_drag_pct": 0.010,
                "net_pnl_per_position_switch": 150.0,
            },
        )

        self.assertTrue(score["allow_dev_diagnostic_review"])
        self.assertEqual(score["target_position_switches_per_30d"], 1.6807)
        self.assertEqual(score["position_switch_penalty"], 0.0)
        self.assertGreater(score["mature_position_switch_penalty"], 0.0)
        self.assertEqual(score["flip_reduce_fee_drag_penalty"], 0.0)
        self.assertGreater(score["time_leverage_bonus"], 0.0)

    def test_candidate_generation_ranking_compares_original_and_regime_order(self) -> None:
        candidates = [
            {
                "candidate_id": "high_sharpe_fragile",
                "metrics": {
                    "sharpe": 2.5,
                    "score": 2.5,
                    "turnover_per_day": 180.0,
                    "fee_drag_pct": 0.01,
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.1}},
                },
                "baseline_train_metrics": {
                    "turnover_per_day": 100.0,
                    "fee_drag_pct": 0.01,
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.4}},
                },
            },
            {
                "candidate_id": "stable_efficient",
                "metrics": {
                    "sharpe": 1.7,
                    "score": 1.7,
                    "turnover_per_day": 80.0,
                    "fee_drag_pct": 0.008,
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.42}},
                },
                "baseline_train_metrics": {
                    "turnover_per_day": 100.0,
                    "fee_drag_pct": 0.01,
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.4}},
                },
            },
        ]

        comparison = compare_regime_objective_candidate_rankings(candidates)

        self.assertEqual(comparison["original_top_candidate"], "high_sharpe_fragile")
        self.assertEqual(comparison["regime_objective_top_candidate"], "stable_efficient")
        self.assertTrue(comparison["top_candidate_changed"])
        self.assertEqual(comparison["allowed_count"], 1)
        self.assertEqual(comparison["rejected_count"], 1)
        self.assertFalse(comparison["test_evaluated"])
        self.assertFalse(comparison["promotion_allowed"])

    def test_main_writes_train_only_non_promotion_artifacts(self) -> None:
        fake_payload = _payload(
            {},
            candidate_variant=CHECK_SCRIPT.DEFAULT_BEHAVIOR_CANDIDATE,
            current_full=1.0,
            candidate_full=1.2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "regime_objective"
            argv = ["check_regime_objective_candidate.py", "--output-dir", str(out)]
            with (
                patch.object(sys, "argv", argv),
                patch.object(CHECK_SCRIPT, "compare_variants", return_value=fake_payload),
                patch.object(CHECK_SCRIPT, "build_markdown_summary", return_value="# comparison"),
            ):
                CHECK_SCRIPT.main()

            verdict = json.loads((out / "regime_objective_check.json").read_text(encoding="utf-8"))
            comparison = json.loads((out / "comparison.json").read_text(encoding="utf-8"))

        self.assertEqual(verdict["terminal_recommendation"], "allow_dev_diagnostic_review")
        self.assertFalse(verdict["train_search_executed"])
        self.assertFalse(verdict["test_evaluated"])
        self.assertFalse(verdict["promotion_allowed"])
        self.assertEqual(comparison["split_scope"], "train_only_regime_objective")
        self.assertFalse(comparison["test_evaluated"])
        self.assertFalse(comparison["promotion_allowed"])

    def test_candidate_generation_dry_run_writes_non_promotion_report(self) -> None:
        verdict = build_regime_objective_verdict(
            _payload(
                {"train_late_cycle_recovery_2023_11": 0.1},
                candidate_variant="candidate_v1",
                current_full=1.0,
                candidate_full=1.3,
            ),
            candidate_variant="candidate_v1",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "regime_objective_check.json"
            out = Path(tmp) / "candidate_generation"
            source.write_text(json.dumps(verdict), encoding="utf-8")
            argv = ["check_regime_objective_candidate_generation.py", "--source", str(source), "--output-dir", str(out)]
            with patch.object(sys, "argv", argv):
                CANDIDATE_GENERATION_SCRIPT.main()

            payload = json.loads((out / "candidate_generation_check.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["selection_scope"], "train_only")
        self.assertFalse(payload["candidate_generation_score"]["allow_dev_diagnostic_review"])
        self.assertEqual(payload["ranking_comparison"]["candidate_count"], 1)
        self.assertFalse(payload["test_evaluated"])
        self.assertFalse(payload["promotion_allowed"])

    def test_candidate_generation_dry_run_compares_candidate_file_rankings(self) -> None:
        verdict = build_regime_objective_verdict(
            _payload({}, candidate_variant="candidate_v1", current_full=1.0, candidate_full=1.2),
            candidate_variant="candidate_v1",
        )
        candidates = [
            {
                "candidate_id": "fragile",
                "metrics": {
                    "sharpe": 2.0,
                    "score": 2.0,
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.1}},
                },
                "baseline_train_metrics": {
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.4}},
                },
            },
            {
                "candidate_id": "stable",
                "metrics": {
                    "sharpe": 1.5,
                    "score": 1.5,
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.42}},
                },
                "baseline_train_metrics": {
                    "train_bucket_metrics": {"train_late_cycle_recovery_2023_11": {"sharpe": 0.4}},
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "regime_objective_check.json"
            candidate_path = Path(tmp) / "candidates.json"
            out = Path(tmp) / "candidate_generation"
            source.write_text(json.dumps(verdict), encoding="utf-8")
            candidate_path.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")
            argv = [
                "check_regime_objective_candidate_generation.py",
                "--source",
                str(source),
                "--candidates",
                str(candidate_path),
                "--output-dir",
                str(out),
            ]
            with patch.object(sys, "argv", argv):
                CANDIDATE_GENERATION_SCRIPT.main()

            payload = json.loads((out / "candidate_generation_check.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["ranking_comparison"]["original_top_candidate"], "fragile")
        self.assertEqual(payload["ranking_comparison"]["regime_objective_top_candidate"], "stable")
        self.assertTrue(payload["ranking_comparison"]["top_candidate_changed"])


if __name__ == "__main__":
    unittest.main()
