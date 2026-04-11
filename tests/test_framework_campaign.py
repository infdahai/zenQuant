import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.research.framework_search import CandidateSummary, SearchReport


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CAMPAIGN = _load_module("framework_campaign_module", "scripts/run_framework_campaign.py")


class FrameworkCampaignTests(unittest.TestCase):
    def test_campaign_blocks_when_optuna_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            argv = [
                "run_framework_campaign.py",
                "--output-dir",
                str(out),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch("importlib.util.find_spec", return_value=None),
            ):
                CAMPAIGN.main()

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocked_by"], "optuna_unavailable")
        self.assertFalse(payload["full_training_allowed"])
        self.assertEqual(payload["full_training_decision"], "defer_full_training")

    def test_campaign_resumes_from_stage1_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            checkpoints = out / "checkpoints"
            checkpoints.mkdir(parents=True, exist_ok=True)
            stage1 = checkpoints / "stage1_search.json"
            stage1.write_text(
                json.dumps(
                    {
                        "optuna_params": {"w_momentum": 0.3},
                        "rl_params": {"w_momentum": 0.4},
                        "search_status": {
                            "optuna_trials_completed": 7,
                            "rl_episodes_completed": 5,
                            "budget": "smoke",
                            "optuna_available": True,
                            "train_only_search": True,
                            "dev_only_ranking": False,
                            "dev_diagnostics_only": True,
                            "split_contract_version": "btc_temporal_v2_train_selection_only",
                        },
                        "split_contract": {"version": "btc_temporal_v2_train_selection_only"},
                    }
                ),
                encoding="utf-8",
            )

            class FakeEvaluator:
                def __init__(self, *_args, **_kwargs) -> None:
                    self.readiness = {"promotion_ready": False}
                    self.search_param_grid = {"w_momentum": [0.2, 0.3]}
                    self.search_space_profile = {
                        "profile": "execution_first_v2_correlation_constrained",
                        "search_space_mode": "baseline_anchored_train_only",
                        "baseline_anchored_search": True,
                        "baseline_anchor_variant": "paulwei_baseline",
                        "direction_weight_policy": {
                            "correlation_constrained": True,
                            "capped_direction_signal": "crowding_signal",
                            "demoted_signal": "funding_alpha_signal",
                            "demoted_signal_role": "state",
                        },
                    }
                    self._deribit_status = {"enabled": True, "observation_ready": False, "research_ready": False}
                    self._training_window_coverage = {"fast_windows": ["overall_recent"]}
                    self._current_variant = object()
                    self._paulwei_variant = object()
                    self.training_input_contract = {
                        "primary_truth_source": "binance",
                        "execution_framework": {
                            "five_min_feature_aggregation_enabled": True,
                            "five_min_direct_execution_enabled": True,
                            "one_hour_bars_available": True,
                            "four_hour_bars_available": True,
                        },
                        "state_feature_sources": {
                            "hyperliquid_in_state_features": True,
                            "deribit_in_state_features": True,
                            "etf_flow_in_state_features": False,
                            "etf_flow_source": None,
                            "etf_flow_source_confidence": "missing",
                            "etf_flow_last_success_at": None,
                            "cme_positioning_in_state_features": False,
                        },
                        "signal_stack_contract": {"direction_state_execution_split_enabled": True},
                    }

                def _evaluate_variant(self, *_args, **_kwargs):
                    from zenquant.research.framework_search import CandidateSummary

                    return CandidateSummary(
                        variant="current",
                        robust_score=1.0,
                        selection_split="train",
                        dev_sharpe=1.0,
                        dev_pnl=100.0,
                        dev_drawdown=0.1,
                        avg_dev_sharpe=1.0,
                        avg_dev_ratio=1.0,
                        worst_dev_sharpe=0.5,
                        weak_window_sharpe=0.2,
                        negative_dev_worst_windows=0,
                        promotion_candidate=True,
                        blocked_by=None,
                    )

                def final_report(self, **_kwargs):
                    class Report:
                        best_variant = "current"
                        best_score = 1.0
                        report_mode = "full"
                        signal_audit_summary = None

                    return Report()

            argv = ["run_framework_campaign.py", "--output-dir", str(out), "--resume"]
            with (
                patch.object(sys, "argv", argv),
                patch("importlib.util.find_spec", return_value=object()),
                patch.object(CAMPAIGN, "FrameworkEvaluator", FakeEvaluator),
                patch.object(CAMPAIGN, "write_search_report") as write_report,
                patch.object(CAMPAIGN, "build_markdown_report", return_value="# ok"),
                patch.object(CAMPAIGN, "_resolved_budget", return_value=(5, 5)),
            ):
                CAMPAIGN.main()

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["optuna_trials_completed"], 7)
        self.assertEqual(payload["rl_episodes_completed"], 5)
        self.assertTrue(payload["resume_supported"])
        self.assertIn("full_training_allowed", payload)
        self.assertIn("etf_flow_source_confidence", payload)
        write_report.assert_called_once()

    def test_campaign_archives_prior_outputs_on_fresh_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            out.mkdir(parents=True, exist_ok=True)
            (out / "campaign_status.json").write_text('{"status":"completed"}', encoding="utf-8")
            (out / "framework_optimization.json").write_text("{}", encoding="utf-8")
            (out / "framework_optimization.md").write_text("# old", encoding="utf-8")
            checkpoints = out / "checkpoints"
            checkpoints.mkdir()
            (checkpoints / "stage1_search.json").write_text("{}", encoding="utf-8")

            argv = ["run_framework_campaign.py", "--output-dir", str(out)]
            with (
                patch.object(sys, "argv", argv),
                patch("importlib.util.find_spec", return_value=None),
            ):
                CAMPAIGN.main()

            archive_root = out.parent / "archive" / "dev_runs"
            archived_runs = list(archive_root.iterdir())
            self.assertEqual(len(archived_runs), 1)
            archived = archived_runs[0]
            self.assertTrue((archived / "campaign_status.json").exists())
            self.assertTrue((archived / "framework_optimization.json").exists())
            self.assertTrue((archived / "checkpoints" / "stage1_search.json").exists())

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "blocked")

    def test_campaign_invalidates_resume_on_split_contract_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            checkpoints = out / "checkpoints"
            checkpoints.mkdir(parents=True, exist_ok=True)
            (checkpoints / "stage0_readiness.json").write_text(
                json.dumps({"split_contract": {"version": "btc_temporal_v0"}}),
                encoding="utf-8",
            )

            argv = ["run_framework_campaign.py", "--output-dir", str(out), "--resume"]
            with patch.object(sys, "argv", argv):
                CAMPAIGN.main()

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocked_by"], "invalidated_checkpoint")
        self.assertEqual(payload["invalidated_reason"], "split_contract_mismatch")
        self.assertEqual(payload["split_contract_version"], "btc_temporal_v2_train_selection_only")
        self.assertFalse((out / "checkpoints" / "stage0_readiness.json").exists())

    def test_campaign_invalidates_resume_when_stage1_is_not_train_dev_disciplined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            checkpoints = out / "checkpoints"
            checkpoints.mkdir(parents=True, exist_ok=True)
            (checkpoints / "stage1_search.json").write_text(
                json.dumps(
                    {
                        "search_status": {
                            "split_contract_version": "btc_temporal_v2_train_selection_only",
                            "train_only_search": False,
                            "dev_only_ranking": False,
                        },
                        "split_contract": {"version": "btc_temporal_v2_train_selection_only"},
                    }
                ),
                encoding="utf-8",
            )

            argv = ["run_framework_campaign.py", "--output-dir", str(out), "--resume"]
            with patch.object(sys, "argv", argv):
                CAMPAIGN.main()

            payload = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["blocked_by"], "invalidated_checkpoint")
        self.assertEqual(payload["invalidated_reason"], "checkpoint_not_train_only_search")

    def test_campaign_stage2_updates_progress_and_writes_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"
            checkpoints = out / "checkpoints"
            checkpoints.mkdir(parents=True, exist_ok=True)
            (checkpoints / "stage1_search.json").write_text(
                json.dumps(
                    {
                        "optuna_params": {"w_momentum": 0.3},
                        "rl_params": {"w_momentum": 0.4},
                        "search_status": {
                            "optuna_trials_completed": 5,
                            "rl_episodes_completed": 5,
                            "budget": "smoke",
                            "optuna_available": True,
                            "train_only_search": True,
                            "dev_only_ranking": False,
                            "dev_diagnostics_only": True,
                            "split_contract_version": "btc_temporal_v2_train_selection_only",
                            "stage1_execution_mode": "1h_context_only",
                            "authoritative_verdict_execution_mode": "5m_enabled",
                        "search_space_profile": {
                            "direction_weight_policy": {
                                "correlation_constrained": True,
                                "capped_direction_signal": "crowding_signal",
                                "demoted_signal": "funding_alpha_signal",
                                "demoted_signal_role": "state",
                            }
                        },
                            "liquidation_in_search": False,
                            "etf_flow_in_state_features": True,
                            "hyperliquid_in_state_features": True,
                            "deribit_in_state_features": True,
                            "cme_positioning_in_state_features": True,
                            "macro_state_sources": ["hyperliquid", "deribit", "etf_flow", "cme_positioning"],
                            "state_data_confidence": "high",
                            "etf_flow_source": "bitbo_etf_flows",
                            "etf_flow_source_confidence": "medium",
                            "etf_flow_last_success_at": "2026-04-10T00:00:00+00:00",
                        },
                        "split_contract": {"version": "btc_temporal_v2_train_selection_only"},
                    }
                ),
                encoding="utf-8",
            )

            class FakeEvaluator:
                def __init__(self, *_args, **_kwargs) -> None:
                    self._base_config = type(
                        "BaseConfig",
                        (),
                        {"execution_timeframe": CAMPAIGN.Timeframe.TF_5M, "context_timeframe": CAMPAIGN.Timeframe.TF_1H},
                    )()
                    self.readiness = {"promotion_ready": False, "blocked_by": "not_promotion_ready"}
                    self.search_param_grid = {"w_momentum": [0.2, 0.3]}
                    self.search_space_profile = {
                        "profile": "execution_first_v2_correlation_constrained",
                        "search_space_mode": "baseline_anchored_train_only",
                        "baseline_anchored_search": True,
                        "baseline_anchor_variant": "paulwei_baseline",
                        "direction_weight_policy": {
                            "correlation_constrained": True,
                            "capped_direction_signal": "crowding_signal",
                            "demoted_signal": "funding_alpha_signal",
                            "demoted_signal_role": "state",
                        },
                    }
                    self._deribit_status = {"enabled": True, "observation_ready": False, "research_ready": False}
                    self._training_window_coverage = {"fast_windows": ["overall_recent"]}
                    self.training_input_contract = {
                        "primary_truth_source": "binance",
                        "execution_framework": {
                            "five_min_feature_aggregation_enabled": True,
                            "five_min_direct_execution_enabled": True,
                            "one_hour_bars_available": True,
                            "four_hour_bars_available": True,
                        },
                        "state_feature_sources": {
                            "hyperliquid_in_state_features": True,
                            "deribit_in_state_features": True,
                            "etf_flow_in_state_features": True,
                            "etf_flow_source": "bitbo_etf_flows",
                            "etf_flow_source_confidence": "medium",
                            "etf_flow_last_success_at": "2026-04-10T00:00:00+00:00",
                            "cme_positioning_in_state_features": True,
                        },
                        "signal_stack_contract": {"direction_state_execution_split_enabled": True},
                    }

                def final_report(self, **kwargs):
                    callback = kwargs["status_callback"]
                    callback(event="stage2_prepare_variants_started", completed_units=0, total_units=4)
                    callback(event="comparison_window_completed", variant="current", window_label="dev", completed_units=1, total_units=4)
                    callback(event="comparison_ranking_completed", variant="current", rank=1, completed_units=3, total_units=4)
                    callback(event="stage2_rank_variants_completed", variant="current", completed_units=3, total_units=4)
                    return SearchReport(
                        created_at="2026-04-10T00:00:00+00:00",
                        symbol="BTCUSDT",
                        readiness={"promotion_ready": False, "blocked_by": "not_promotion_ready"},
                        deribit_status={"enabled": True, "observation_ready": False, "research_ready": False},
                        split_contract={"version": "btc_temporal_v2_train_selection_only", "windows": {"train": {}, "dev": {}, "test": {}}},
                        training_window_coverage={},
                        training_input_contract=self.training_input_contract,
                        search_status=kwargs["search_status"],
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
                            promotion_candidate=False,
                        ),
                        paulwei_baseline=CandidateSummary(
                            variant="paulwei_baseline",
                            robust_score=0.9,
                            selection_split="dev",
                            dev_sharpe=0.9,
                            dev_pnl=9.0,
                            dev_drawdown=0.09,
                            avg_dev_sharpe=0.9,
                            avg_dev_ratio=1.0,
                            worst_dev_sharpe=0.6,
                            weak_window_sharpe=0.1,
                            negative_dev_worst_windows=0,
                            promotion_candidate=False,
                        ),
                        optuna_best=None,
                        rl_best=None,
                        best_variant="current",
                        best_score=1.0,
                        best_params=None,
                        comparison_payload={"comparison_compute_mode": "smoke_minimal"},
                        report_mode="fast",
                        report_degraded_due_to_memory=False,
                        smoke_completed=True,
                    )

            def _write_report(path, report):
                payload = {
                    "full_training_assessment": {
                        "ready": False,
                        "decision": "defer_full_training",
                        "blockers": ["deribit_observation_not_ready"],
                    },
                    "post_smoke_scenario": {
                        "scenario": "scenario_c_state_data_gap",
                        "category": "state_data_readiness",
                        "next_action": "close_state_readiness_gaps_before_budget_increase",
                    },
                }
                Path(path).write_text(json.dumps(payload), encoding="utf-8")

            argv = ["run_framework_campaign.py", "--output-dir", str(out), "--resume"]
            with (
                patch.object(sys, "argv", argv),
                patch("importlib.util.find_spec", return_value=object()),
                patch.object(CAMPAIGN, "FrameworkEvaluator", FakeEvaluator),
                patch.object(CAMPAIGN, "write_search_report", side_effect=_write_report),
                patch.object(CAMPAIGN, "build_markdown_report", return_value="# ok"),
                patch.object(CAMPAIGN, "generate_signal_combination_audit", return_value=None),
                patch.object(CAMPAIGN, "_resolved_budget", return_value=(5, 5)),
            ):
                CAMPAIGN.main()

            status = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))
            log_text = (out / "campaign.log").read_text(encoding="utf-8")
            final_json_exists = (out / "framework_optimization.json").exists()
            final_md_exists = (out / "framework_optimization.md").exists()

        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["phase"], "stage3_complete")
        self.assertEqual(status["stage2_subphase"], "stage3_complete")
        self.assertEqual(status["post_smoke_scenario"], "scenario_c_state_data_gap")
        self.assertIn("stage2_full_verdict started", log_text)
        self.assertIn("stage2_write_artifacts", log_text)
        self.assertIn("campaign_completed scenario=scenario_c_state_data_gap", log_text)
        self.assertTrue(final_json_exists)
        self.assertTrue(final_md_exists)

    def test_campaign_writes_partial_stage1_checkpoint_before_rl_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"

            class FakeEvaluator:
                def __init__(self, *_args, **_kwargs) -> None:
                    self._base_config = type(
                        "BaseConfig",
                        (),
                        {"execution_timeframe": CAMPAIGN.Timeframe.TF_5M, "context_timeframe": CAMPAIGN.Timeframe.TF_1H},
                    )()
                    self.readiness = {"promotion_ready": False, "blocked_by": "not_promotion_ready"}
                    self.search_param_grid = {"w_momentum": [0.2, 0.3]}
                    self.search_space_profile = {
                        "profile": "execution_first_v2_correlation_constrained",
                        "direction_weight_policy": {
                            "correlation_constrained": True,
                            "capped_direction_signal": "crowding_signal",
                            "demoted_signal": "funding_alpha_signal",
                            "demoted_signal_role": "state",
                        },
                    }
                    self._deribit_status = {"enabled": True, "observation_ready": False, "research_ready": False}
                    self._training_window_coverage = {"fast_windows": ["overall_recent"]}
                    self._current_variant = object()
                    self._paulwei_variant = object()
                    self.training_input_contract = {
                        "primary_truth_source": "binance",
                        "execution_framework": {
                            "five_min_feature_aggregation_enabled": True,
                            "five_min_direct_execution_enabled": True,
                            "one_hour_bars_available": True,
                            "four_hour_bars_available": True,
                        },
                        "state_feature_sources": {
                            "hyperliquid_in_state_features": True,
                            "deribit_in_state_features": True,
                            "etf_flow_in_state_features": True,
                            "etf_flow_source": "bitbo_etf_flows",
                            "etf_flow_source_confidence": "medium",
                            "etf_flow_last_success_at": "2026-04-10T00:00:00+00:00",
                            "cme_positioning_in_state_features": True,
                        },
                        "signal_stack_contract": {"direction_state_execution_split_enabled": True},
                    }

                def _evaluate_variant(self, *_args, **_kwargs):
                    return CandidateSummary(
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
                    )

                def evaluate_params(self, *_args, **_kwargs):
                    return self._evaluate_variant()

                def final_report(self, **kwargs):
                    return SearchReport(
                        created_at="2026-04-10T00:00:00+00:00",
                        symbol="BTCUSDT",
                        readiness={"promotion_ready": False, "blocked_by": "not_promotion_ready"},
                        deribit_status={"enabled": True, "observation_ready": False, "research_ready": False},
                        split_contract={"version": "btc_temporal_v2_train_selection_only", "windows": {"train": {}, "dev": {}, "test": {}}},
                        training_window_coverage={},
                        training_input_contract=self.training_input_contract,
                        search_status=kwargs["search_status"],
                        current=self._evaluate_variant(),
                        paulwei_baseline=CandidateSummary(
                            variant="paulwei_baseline",
                            robust_score=0.9,
                            selection_split="dev",
                            dev_sharpe=0.9,
                            dev_pnl=9.0,
                            dev_drawdown=0.09,
                            avg_dev_sharpe=0.9,
                            avg_dev_ratio=1.0,
                            worst_dev_sharpe=0.6,
                            weak_window_sharpe=0.1,
                            negative_dev_worst_windows=0,
                            promotion_candidate=False,
                        ),
                        optuna_best=None,
                        rl_best=None,
                        best_variant="current",
                        best_score=1.0,
                        best_params=None,
                        comparison_payload={"comparison_compute_mode": "smoke_minimal"},
                        report_mode="fast",
                        report_degraded_due_to_memory=False,
                        smoke_completed=True,
                    )

            def _fake_run_optuna_search(*_args, **kwargs):
                callback = kwargs["status_callback"]
                callback(event="trial_completed", current_trial=1, completed_trials=1, total_trials=5, runtime_s=1.0)
                callback(event="ranking_candidate_completed", current_candidate=1, completed_candidates=1, total_candidates=2, runtime_s=2.0)
                return {"w_momentum": 0.3}, [{"params": {"w_momentum": 0.3}, "summary": {"robust_score": 1.0}}]

            def _fake_train_tuner(*_args, **kwargs):
                callback = kwargs["status_callback"]
                callback(event="rl_progress", completed_steps=7, total_steps=5, runtime_s=3.0)
                raise RuntimeError("rl_sidecar_failed")

            argv = ["run_framework_campaign.py", "--output-dir", str(out)]
            with (
                patch.object(sys, "argv", argv),
                patch("importlib.util.find_spec", return_value=object()),
                patch.object(CAMPAIGN, "FrameworkEvaluator", FakeEvaluator),
                patch.object(CAMPAIGN, "run_optuna_search", side_effect=_fake_run_optuna_search),
                patch.object(CAMPAIGN, "train_tuner", side_effect=_fake_train_tuner),
                patch.object(CAMPAIGN, "write_search_report"),
                patch.object(CAMPAIGN, "build_markdown_report", return_value="# ok"),
                patch.object(CAMPAIGN, "generate_signal_combination_audit", return_value=None),
                patch.object(CAMPAIGN, "_resolved_budget", return_value=(5, 5)),
            ):
                CAMPAIGN.main()

            stage1_payload = json.loads((out / "checkpoints" / "stage1_search.json").read_text(encoding="utf-8"))
            status = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))

        self.assertIn(stage1_payload["search_status"]["search_outcome"], {"partial", "success"})
        self.assertTrue(stage1_payload["search_status"]["optuna_candidate_found"])
        self.assertEqual(stage1_payload["search_status"]["rl_episodes_completed"], 0)
        self.assertEqual(stage1_payload["search_status"]["failure_reason"], "stage1_rl_sidecar_failed")
        self.assertEqual(status["status"], "completed")

    def test_campaign_smoke_falls_back_to_5m_search_after_empty_1h_shortlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results"

            class FakeEvaluator:
                def __init__(self, *_args, **_kwargs) -> None:
                    self._base_config = type(
                        "BaseConfig",
                        (),
                        {"execution_timeframe": CAMPAIGN.Timeframe.TF_5M, "context_timeframe": CAMPAIGN.Timeframe.TF_1H},
                    )()
                    self.readiness = {"promotion_ready": False, "blocked_by": "not_promotion_ready"}
                    self.search_param_grid = {"w_momentum": [0.2, 0.3]}
                    self.search_space_profile = {
                        "profile": "execution_first_v2_correlation_constrained",
                        "direction_weight_policy": {
                            "correlation_constrained": True,
                            "capped_direction_signal": "crowding_signal",
                            "demoted_signal": "funding_alpha_signal",
                            "demoted_signal_role": "state",
                        },
                    }
                    self._deribit_status = {"enabled": True, "observation_ready": False, "research_ready": False}
                    self._training_window_coverage = {"fast_windows": ["overall_recent"]}
                    self.training_input_contract = {
                        "primary_truth_source": "binance",
                        "execution_framework": {
                            "five_min_feature_aggregation_enabled": True,
                            "five_min_direct_execution_enabled": True,
                            "one_hour_bars_available": True,
                            "four_hour_bars_available": True,
                        },
                        "state_feature_sources": {
                            "hyperliquid_in_state_features": True,
                            "deribit_in_state_features": True,
                            "etf_flow_in_state_features": True,
                            "etf_flow_source": "bitbo_etf_flows",
                            "etf_flow_source_confidence": "medium",
                            "etf_flow_last_success_at": "2026-04-10T00:00:00+00:00",
                            "cme_positioning_in_state_features": True,
                        },
                        "signal_stack_contract": {"direction_state_execution_split_enabled": True},
                    }

                def final_report(self, **_kwargs):
                    return SearchReport(
                        created_at="2026-04-10T00:00:00+00:00",
                        symbol="BTCUSDT",
                        readiness={"promotion_ready": False, "blocked_by": "not_promotion_ready"},
                        deribit_status=self._deribit_status,
                        split_contract={"version": "btc_temporal_v2_train_selection_only"},
                        training_window_coverage={"fast_windows": ["overall_recent"]},
                        training_input_contract=self.training_input_contract,
                        search_status={
                            "budget": "smoke",
                            "optuna_available": True,
                            "optuna_trials_completed": 5,
                            "rl_episodes_completed": 0,
                            "optuna_candidate_found": True,
                            "rl_candidate_found": False,
                            "liquidation_in_search": False,
                            "search_space_profile": self.search_space_profile,
                            "search_space_mode": "baseline_anchored_train_only",
                            "baseline_anchored_search": True,
                            "baseline_anchor_variant": "paulwei_baseline",
                            "stage1_execution_mode": "1h_context_only_with_5m_fallback",
                            "authoritative_verdict_execution_mode": "5m_enabled",
                            "candidate_sources": ["auto_research_train_only", "auto_research_train_only_5m_fallback"],
                            "train_only_search": True,
                            "dev_only_ranking": False,
                            "dev_diagnostics_only": True,
                        },
                        current=CandidateSummary(
                            variant="current",
                            robust_score=1.0,
                            selection_split="train",
                            dev_sharpe=1.0,
                            dev_pnl=100.0,
                            dev_drawdown=0.1,
                            avg_dev_sharpe=1.0,
                            avg_dev_ratio=1.0,
                            worst_dev_sharpe=0.9,
                            weak_window_sharpe=0.2,
                            negative_dev_worst_windows=0,
                            promotion_candidate=False,
                        ),
                        paulwei_baseline=CandidateSummary(
                            variant="paulwei_baseline",
                            robust_score=1.0,
                            selection_split="train",
                            dev_sharpe=1.0,
                            dev_pnl=100.0,
                            dev_drawdown=0.1,
                            avg_dev_sharpe=1.0,
                            avg_dev_ratio=1.0,
                            worst_dev_sharpe=0.9,
                            weak_window_sharpe=0.2,
                            negative_dev_worst_windows=0,
                            promotion_candidate=False,
                        ),
                        optuna_best=CandidateSummary(
                            variant="optuna_tuned",
                            robust_score=1.0,
                            selection_split="train",
                            dev_sharpe=1.0,
                            dev_pnl=100.0,
                            dev_drawdown=0.1,
                            avg_dev_sharpe=1.0,
                            avg_dev_ratio=1.0,
                            worst_dev_sharpe=0.9,
                            weak_window_sharpe=0.2,
                            negative_dev_worst_windows=0,
                            promotion_candidate=False,
                        ),
                        rl_best=None,
                        best_variant="optuna_tuned",
                        best_score=1.0,
                        best_params={"w_momentum": 0.3},
                        comparison_payload={"comparison_compute_mode": "smoke_minimal"},
                        report_mode="fast",
                        report_degraded_due_to_memory=False,
                        smoke_completed=True,
                    )

            def _fake_run_optuna_search(*_args, **kwargs):
                callback = kwargs["status_callback"]
                search_execution_timeframe = kwargs["search_execution_timeframe"]
                if search_execution_timeframe == CAMPAIGN.Timeframe.TF_1H:
                    for idx in range(1, 6):
                        callback(event="trial_completed", current_trial=idx, completed_trials=idx, total_trials=5, runtime_s=float(idx))
                    return None, [], {
                        "train_candidate_diagnostics": {
                            "raw_trial_count": 5,
                            "raw_nonzero_trade_trials": 0,
                            "eligible_trade_count_trials": 0,
                            "max_train_trade_count": 0,
                            "max_train_sharpe": 0.0,
                        },
                        "empty_shortlist_reason": "all_zero_trade",
                    }
                callback(event="trial_completed", current_trial=1, completed_trials=1, total_trials=5, runtime_s=1.0)
                callback(event="ranking_candidate_completed", current_candidate=1, completed_candidates=1, total_candidates=1, runtime_s=2.0)
                return {"w_momentum": 0.3}, [{"params": {"w_momentum": 0.3}, "summary": {"robust_score": 1.0}}], {
                    "train_candidate_diagnostics": {
                        "raw_trial_count": 1,
                        "raw_nonzero_trade_trials": 1,
                        "eligible_trade_count_trials": 1,
                        "max_train_trade_count": 24,
                        "max_train_sharpe": 1.0,
                    },
                    "empty_shortlist_reason": None,
                }

            argv = ["run_framework_campaign.py", "--output-dir", str(out), "--budget", "smoke"]
            with (
                patch.object(sys, "argv", argv),
                patch("importlib.util.find_spec", return_value=object()),
                patch.object(CAMPAIGN, "FrameworkEvaluator", FakeEvaluator),
                patch.object(CAMPAIGN, "run_optuna_search", side_effect=_fake_run_optuna_search),
                patch.object(CAMPAIGN, "write_search_report"),
                patch.object(CAMPAIGN, "build_markdown_report", return_value="# ok"),
                patch.object(CAMPAIGN, "generate_signal_combination_audit", return_value=None),
                patch.object(CAMPAIGN, "_resolved_budget", return_value=(5, 0)),
            ):
                CAMPAIGN.main()

            stage1_payload = json.loads((out / "checkpoints" / "stage1_search.json").read_text(encoding="utf-8"))
            status = json.loads((out / "campaign_status.json").read_text(encoding="utf-8"))
            log_text = (out / "campaign.log").read_text(encoding="utf-8")

        self.assertTrue(stage1_payload["search_status"]["optuna_candidate_found"])
        self.assertEqual(stage1_payload["search_status"]["optuna_trials_completed"], 5)
        self.assertIn("auto_research_train_only_5m_fallback", stage1_payload["search_status"]["candidate_sources"])
        self.assertEqual(stage1_payload["search_status"]["search_space_mode"], "baseline_anchored_train_only")
        self.assertTrue(stage1_payload["search_status"]["baseline_anchored_search"])
        self.assertEqual(stage1_payload["search_status"]["baseline_anchor_variant"], "paulwei_baseline")
        self.assertIn("fallback_to_5m", log_text)
        self.assertEqual(status["status"], "completed")


if __name__ == "__main__":
    unittest.main()
