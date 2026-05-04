import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from zenquant.research.mature_progression import (
    build_mature_progression_markdown,
    build_mature_progression_payload,
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
    "mature_progression_readiness_script",
    "scripts/check_mature_progression_readiness.py",
)


def _readiness(*, blockers: list[str] | None = None) -> dict:
    blockers = blockers or []
    return {
        "deribit_status": {
            "observation_ready": not blockers,
            "research_ready": not blockers,
            "blocked_by": "deribit_sample_window_incomplete" if blockers else None,
            "next_action": "keep_collector_running",
        },
        "deribit_block_flow_status": {"blocked_by": None, "next_action": "keep_block_flow_collector_running"},
        "binance_liquidation_status": {
            "blocked_by": "missing_liquidation_history" if blockers else None,
            "next_action": "ingest_binance_liquidation_history",
        },
        "hyperliquid_status": {
            "promotion_ready": not blockers,
            "blocked_by": "hyperliquid_history_gap" if blockers else None,
            "next_action": "keep_recent_collector_running",
        },
        "merged_liquidation_status": {
            "research_ready_min": True,
            "promotion_ready": not blockers,
            "blocked_by": "not_promotion_ready" if blockers else None,
            "next_action": "continue_liquidation_backfill_and_merge",
        },
        "overall_blockers": blockers,
    }


def _baseline() -> dict:
    return {
        "baseline_id": "paulwei_mature_style_baseline",
        "promotion_status": "research_only",
        "mapping_confidence": "medium",
        "behavior_summary": {
            "position_switches_per_30d": 1.6807,
            "median_adds_per_episode": 18.0,
            "median_reduces_per_episode": 17.0,
            "median_recovery_days": 11.5,
        },
        "execution_config_overrides": {"pyramid_add_pct": 0.3},
        "risk_config_overrides": {"kill_cooldown_bars": 48},
        "unsupported_dimensions": ["discretionary_macro_inputs"],
    }


def _behavior_profile() -> dict:
    return {
        "position_switches_per_30d": 1.6807,
        "median_adds_per_episode": 18.0,
        "median_reduces_per_episode": 17.0,
        "protective_exit_profile": {"protective_reduce_share": 0.9424, "flip_exit_share": 0.1558},
        "recovery_profile": {"median_recovery_days": 11.5},
    }


def _strict_review() -> dict:
    return {
        "strict_learning_modules": [
            {"module": "inventory_build_profile", "worth_absorbing": True},
            {"module": "unsupported_note", "worth_absorbing": False},
        ],
        "learning_boundaries": {"unsupported_dimensions": ["news_and_narrative_interpretation"]},
    }


def _framework_report(*, no_candidate: bool = True) -> dict:
    return {
        "created_at": "2026-05-03T18:59:32+00:00",
        "best_variant": "paulwei_baseline" if no_candidate else "candidate_v1",
        "best_score": 0.0 if no_candidate else 1.2,
        "empty_shortlist_reason": "primary_and_fallback_empty" if no_candidate else None,
        "full_training_decision": "defer_handoff" if no_candidate else "candidate_ready",
        "full_training_blockers": ["no_tuned_candidate_from_smoke"] if no_candidate else [],
        "final_verdict": {
            "best_variant": "paulwei_baseline" if no_candidate else "candidate_v1",
            "candidate_ready_for_test": False,
            "candidate_ready_for_handoff": False,
            "blocked_by": "not_promotion_ready" if no_candidate else None,
        },
        "train_candidate_diagnostics": {
            "candidate_existence_recovered": not no_candidate,
            "no_tuned_candidate_reason": "all_negative_primary_metric" if no_candidate else None,
            "primary_search_diagnostics": {
                "raw_trial_count": 12,
                "eligible_trade_count_trials": 7,
                "max_train_sharpe": 1.916,
                "max_hostile_proxy_score": 1.958031,
                "candidate_existence_recovered": not no_candidate,
                "stage1_fail_fast_reasons": {"worst_dev_sharpe_fail_fast": 1} if no_candidate else {},
            },
        },
    }


class MatureProgressionTests(unittest.TestCase):
    def test_data_blockers_hold_expanded_training_before_more_trials(self) -> None:
        payload = build_mature_progression_payload(
            data_readiness_payload=_readiness(
                blockers=[
                    "deribit_sample_window_incomplete",
                    "missing_liquidation_history",
                    "not_promotion_ready",
                ]
            ),
            framework_payload=_framework_report(no_candidate=True),
            mature_baseline_payload=_baseline(),
            behavior_profile_payload=_behavior_profile(),
            strict_learning_payload=_strict_review(),
            created_at=datetime(2026, 5, 4, tzinfo=UTC),
        )

        self.assertEqual(payload["decision"], "strengthen_data_engineering_before_expanded_training")
        self.assertFalse(payload["expanded_training_allowed"])
        self.assertFalse(payload["small_train_only_smoke_allowed"])
        self.assertFalse(payload["test_evaluated"])
        self.assertIn("data_not_promotion_ready", payload["blockers"])
        self.assertIn("no_tuned_candidate_from_latest_report", payload["blockers"])

    def test_clean_data_and_negative_candidate_existence_allows_only_contract_limited_smoke(self) -> None:
        payload = build_mature_progression_payload(
            data_readiness_payload=_readiness(),
            framework_payload=_framework_report(no_candidate=True),
            mature_baseline_payload=_baseline(),
            behavior_profile_payload=_behavior_profile(),
            strict_learning_payload=_strict_review(),
            created_at=datetime(2026, 5, 4, tzinfo=UTC),
        )

        self.assertEqual(payload["decision"], "run_contract_limited_train_only_smoke")
        self.assertFalse(payload["expanded_training_allowed"])
        self.assertTrue(payload["small_train_only_smoke_allowed"])
        self.assertEqual(payload["selection_scope"], "research_planning_only")
        self.assertFalse(payload["test_access_required"])

    def test_behavior_contract_preserves_mature_targets_and_transfer_boundaries(self) -> None:
        payload = build_mature_progression_payload(
            data_readiness_payload=_readiness(),
            framework_payload=_framework_report(no_candidate=False),
            mature_baseline_payload=_baseline(),
            behavior_profile_payload=_behavior_profile(),
            strict_learning_payload=_strict_review(),
            created_at=datetime(2026, 5, 4, tzinfo=UTC),
        )

        contract = payload["mature_behavior_contract"]
        self.assertEqual(contract["targets"]["position_switches_per_30d"]["target"], 1.6807)
        self.assertEqual(contract["targets"]["position_switches_per_30d"]["max"], 3.0)
        self.assertEqual(contract["targets"]["protective_reduce_share"]["target"], 0.9424)
        self.assertIn("inventory_build_profile", contract["strict_learning_modules"])
        self.assertIn("news_and_narrative_interpretation", contract["unsupported_dimensions"])

    def test_missing_behavior_profile_keeps_contract_incomplete(self) -> None:
        payload = build_mature_progression_payload(
            data_readiness_payload=_readiness(),
            framework_payload=_framework_report(no_candidate=False),
            mature_baseline_payload=_baseline(),
            created_at=datetime(2026, 5, 4, tzinfo=UTC),
        )

        self.assertEqual(payload["decision"], "complete_mature_behavior_contract_before_training")
        self.assertIn("mature_behavior_contract_incomplete", payload["blockers"])
        self.assertFalse(payload["mature_behavior_contract"]["ready_for_train_only_scoring"])

    def test_markdown_and_script_write_non_promotion_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readiness_path = root / "data_readiness.json"
            framework_path = root / "framework_optimization.json"
            baseline_path = root / "baseline.json"
            profile_path = root / "profile.json"
            strict_path = root / "strict.json"
            output_dir = root / "out"
            readiness_path.write_text(json.dumps(_readiness(blockers=["not_promotion_ready"])), encoding="utf-8")
            framework_path.write_text(json.dumps(_framework_report(no_candidate=True)), encoding="utf-8")
            baseline_path.write_text(json.dumps(_baseline()), encoding="utf-8")
            profile_path.write_text(json.dumps(_behavior_profile()), encoding="utf-8")
            strict_path.write_text(json.dumps(_strict_review()), encoding="utf-8")

            argv = [
                "check_mature_progression_readiness.py",
                "--data-readiness",
                str(readiness_path),
                "--framework-report",
                str(framework_path),
                "--mature-baseline",
                str(baseline_path),
                "--behavior-profile",
                str(profile_path),
                "--strict-learning-review",
                str(strict_path),
                "--output-dir",
                str(output_dir),
            ]
            with patch.object(sys, "argv", argv):
                CHECK_SCRIPT.main()

            written = json.loads((output_dir / "mature_progression_readiness.json").read_text(encoding="utf-8"))

        markdown = build_mature_progression_markdown(written)
        self.assertIn("strengthen_data_engineering_before_expanded_training", markdown)
        self.assertFalse(written["test_evaluated"])
        self.assertFalse(written["expanded_training_allowed"])


if __name__ == "__main__":
    unittest.main()
