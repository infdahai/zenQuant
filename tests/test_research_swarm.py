import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from zenquant.research.swarm import (
    CommandSpec,
    DataAdmissionAgent,
    ExperimentAgent,
    FactorAuditAgent,
    RalphLoopAgent,
    SourceAdmissionReview,
    SourceCandidate,
    SourceScope,
    SwarmLoopMode,
    SwarmManager,
    SwarmMode,
    SwarmRunConfig,
    TemporalGuardAgent,
)
from zenquant.research.swarm.agents import ExperimentResult, VerifierAgent
from zenquant.research.swarm.contracts import DEFAULT_SPLIT_CONTRACT_VERSION, SwarmVerdictStatus


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RESEARCH_SWARM_CLI = _load_module("research_swarm_cli_module", "scripts/run_research_swarm.py")


class ResearchSwarmTests(unittest.TestCase):
    def test_experiment_agent_rejects_non_allowlisted_command(self) -> None:
        agent = ExperimentAgent()
        command = CommandSpec(
            name="bad",
            argv=("python", "scripts/download_cot.py"),
            purpose="not a swarm research command",
        )

        with self.assertRaisesRegex(ValueError, "research_swarm_command_not_allowlisted"):
            agent.validate_allowlist([command])

    def test_temporal_guard_blocks_test_without_final_verdict_flag(self) -> None:
        command = CommandSpec(
            name="compare_test",
            argv=("python", "scripts/compare_variants.py", "--split", "test"),
            purpose="test comparison",
            touches_test=True,
        )

        result = TemporalGuardAgent().preflight([command], allow_test_final_verdict=False)

        self.assertFalse(result.passed)
        self.assertEqual(result.blocked_by, "test_split_policy_violation")

    def test_temporal_guard_blocks_test_artifact_without_final_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "comparison.json"
            artifact.write_text(
                json.dumps(
                    {
                        "comparison_split": "test",
                        "split_contract": {"version": DEFAULT_SPLIT_CONTRACT_VERSION},
                    }
                ),
                encoding="utf-8",
            )

            result = TemporalGuardAgent().check_artifacts([artifact], allow_test_final_verdict=False)

        self.assertFalse(result.passed)
        self.assertEqual(result.blocked_by, "test_split_policy_violation")

    def test_swarm_dry_run_writes_manifest_ledger_verdict_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "swarm_run"
            with patch(
                "zenquant.research.swarm.manager._git_info",
                return_value={"code_commit": "abc123", "dirty_tree": True, "dirty_paths": [" M x.py"]},
            ):
                result = SwarmManager().run(
                    SwarmRunConfig(
                        goal="研究 BTC hostile regime churn control",
                        output_dir=output_dir,
                        mode=SwarmMode.DRY_RUN,
                    )
                )

            manifest = json.loads((output_dir / "swarm_run_manifest.json").read_text(encoding="utf-8"))
            ledger_lines = (output_dir / "swarm_task_ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
            verdict = json.loads((output_dir / "swarm_verdict.json").read_text(encoding="utf-8"))
            report = json.loads((output_dir / "swarm_report.json").read_text(encoding="utf-8"))

        self.assertEqual(Path(result["manifest_path"]).name, "swarm_run_manifest.json")
        self.assertEqual(manifest["asset"], "BTC")
        self.assertEqual(manifest["split_contract"]["version"], DEFAULT_SPLIT_CONTRACT_VERSION)
        self.assertTrue(manifest["dirty_tree"])
        self.assertTrue(manifest["training_controls"]["research_only_non_promotable"])
        self.assertIn("zenquant_check_gate_C_golden_output_failed_preexisting", manifest["promotion_blockers"])
        self.assertGreaterEqual(len(ledger_lines), 6)
        self.assertEqual(verdict["status"], SwarmVerdictStatus.NEEDS_HUMAN_DECISION.value)
        self.assertEqual(verdict["blocked_by"], "dry_run_not_evidence")
        self.assertTrue(report["not_live_decision_path"])
        self.assertTrue(report["research_only_non_promotable"])
        self.assertIn("research_only_diagnostic_run", report["promotion_blockers"])
        self.assertTrue(report["dry_run"])

    def test_ralph_dry_run_writes_discovery_audit_loop_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            repo_root.mkdir()
            output_dir = Path(tmp) / "ralph_run"
            with patch(
                "zenquant.research.swarm.manager._git_info",
                return_value={"code_commit": "abc123", "dirty_tree": False, "dirty_paths": []},
            ):
                SwarmManager(repo_root=repo_root).run(
                    SwarmRunConfig(
                        goal="ralph source discovery",
                        output_dir=output_dir,
                        mode=SwarmMode.DRY_RUN,
                        loop_mode=SwarmLoopMode.RALPH,
                        target_candidates=10,
                        min_candidates=5,
                    )
                )

            manifest = json.loads((output_dir / "swarm_run_manifest.json").read_text(encoding="utf-8"))
            status = json.loads((output_dir / "ralph_loop_status.json").read_text(encoding="utf-8"))
            report = json.loads((output_dir / "swarm_report.json").read_text(encoding="utf-8"))
            ralph_paths_exist = {
                "source_candidate_inventory": (output_dir / "source_candidate_inventory.json").exists(),
                "source_admission_reviews": (output_dir / "source_admission_reviews.jsonl").exists(),
                "factor_audit": (output_dir / "factor_audit.json").exists(),
                "factor_audit_md": (output_dir / "factor_audit.md").exists(),
                "signal_candidate_registry": (output_dir / "signal_candidate_registry.json").exists(),
                "ralph_loop_history": (output_dir / "ralph_loop_history.jsonl").exists(),
            }

        self.assertEqual(manifest["loop_mode"], SwarmLoopMode.RALPH.value)
        self.assertEqual(manifest["ralph_loop_controls"]["source_scope"], SourceScope.FREE.value)
        self.assertTrue(all(ralph_paths_exist.values()))
        self.assertEqual(status["verdict"]["status"], SwarmVerdictStatus.NEEDS_HUMAN_DECISION.value)
        self.assertEqual(status["verdict"]["blocked_by"], "candidate_count_shrunk")
        self.assertTrue(report["ralph_artifacts"])
        self.assertEqual(report["ralph_status"]["profit_priority"], "guarded")

    def test_default_swarm_command_uses_safe_smoke_defaults_without_search_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            commands = ExperimentAgent().build_command_graph(
                SwarmRunConfig(goal="default command graph", output_dir=Path(tmp), mode=SwarmMode.DRY_RUN)
            )

        framework = commands[0]
        self.assertEqual(framework.name, "framework_campaign")
        self.assertIn("--budget", framework.argv)
        self.assertEqual(framework.argv[framework.argv.index("--budget") + 1], "smoke")
        self.assertIn("--profile-path", framework.argv)
        self.assertEqual(framework.argv[framework.argv.index("--profile-path") + 1], "config/profiles/default.toml")
        self.assertNotIn("--search-space-mode", framework.argv)
        self.assertNotIn("--optuna-trials", framework.argv)
        self.assertNotIn("--rl-episodes", framework.argv)
        self.assertNotIn("--resume", framework.argv)

    def test_data_admission_blocks_non_free_sources(self) -> None:
        candidate = SourceCandidate(
            name="paid_feed",
            provider="paid_vendor",
            source_type="orderbook",
            role="direction",
            symbol_scope=("BTC",),
            time_granularity="1m",
            free_tier=False,
            pit_safe=True,
            local_cache_path="data/raw/paid/feed.parquet",
            regime_assumption="Paid venue depth might expose liquidity stress.",
            behavioral_hypothesis="Order book imbalance can forecast short-horizon pressure.",
            failure_cases=("cost",),
            correlation_group="orderbook",
            prior_rank_ic=0.02,
        )

        with tempfile.TemporaryDirectory() as tmp:
            review = DataAdmissionAgent().review([candidate], repo_root=Path(tmp))[0]

        self.assertEqual(review.status, "blocked")
        self.assertEqual(review.blocked_by, "non_free_source_scope")

    def test_data_admission_blocks_cache_outside_train_dev(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "data" / "recent.parquet"
            cache.parent.mkdir(parents=True)
            pd.DataFrame(
                {
                    "ts_ns": [
                        1772323200000000000,
                        1772409600000000000,
                    ],
                    "value": [1.0, 2.0],
                }
            ).to_parquet(cache, index=False)
            candidate = SourceCandidate(
                name="recent_only",
                provider="free_public",
                source_type="perp",
                role="state",
                symbol_scope=("BTC",),
                time_granularity="1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/recent.parquet",
                regime_assumption="Recent-only public data might be tempting but cannot train.",
                behavioral_hypothesis="It should be blocked until train/dev history exists.",
                failure_cases=("test_leakage",),
                correlation_group="recent",
                prior_rank_ic=0.01,
            )

            review = DataAdmissionAgent().review([candidate], repo_root=Path(tmp))[0]

        self.assertEqual(review.status, "blocked")
        self.assertEqual(review.blocked_by, "source_cache_outside_train_dev")

    def test_factor_audit_rejects_high_same_role_correlation(self) -> None:
        audit = FactorAuditAgent().audit_series(
            symbol="BTCUSDT",
            factor_series={
                "left": [1, 2, 3, 4, 5, 6, 7],
                "right": [2, 4, 6, 8, 10, 12, 14],
            },
            factor_roles={"left": "direction", "right": "direction"},
            forward_returns=[0.01, 0.02, 0.03, 0.02, 0.01, 0.03, 0.04],
        )

        self.assertEqual(audit.summary["high_correlation_blocked_count"], 1)
        allowed = [entry for entry in audit.factors if entry.allowed_in_direction_search]
        blocked = [entry for entry in audit.factors if entry.blocked_by == "high_same_role_correlation"]
        self.assertEqual([entry.source_name for entry in allowed], ["left"])
        self.assertEqual([entry.source_name for entry in blocked], ["right"])

    def test_factor_audit_keeps_stronger_candidate_in_high_correlation_group(self) -> None:
        candidates = [
            SourceCandidate(
                name="weaker_context",
                provider="free",
                source_type="cross_asset",
                role="state",
                symbol_scope=("BTC",),
                time_granularity="1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/weaker.parquet",
                regime_assumption="Alt context can describe risk appetite.",
                behavioral_hypothesis="Weak context should lose to stronger redundant context.",
                failure_cases=("redundant",),
                correlation_group="cross_asset_context",
                prior_rank_ic=0.012,
            ),
            SourceCandidate(
                name="stronger_context",
                provider="free",
                source_type="cross_asset",
                role="state",
                symbol_scope=("BTC",),
                time_granularity="1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/stronger.parquet",
                regime_assumption="ETH context can describe risk appetite.",
                behavioral_hypothesis="Stronger context should remain admissible.",
                failure_cases=("redundant",),
                correlation_group="cross_asset_context",
                prior_rank_ic=0.014,
            ),
        ]
        reviews = [
            SourceAdmissionReview(
                source_name=candidate.name,
                provider=candidate.provider,
                role=candidate.role,
                status="admitted",
                local_cache_path=candidate.local_cache_path,
                lineage_complete=True,
                pit_safe=True,
                missing_stale_coverage="ok",
                use_for_research_only=True,
                exclude_from_promote_truth=True,
                blocked_by=None,
                next_action="run_factor_audit",
            )
            for candidate in candidates
        ]

        audit = FactorAuditAgent().audit(candidates, reviews, symbol="BTCUSDT")

        by_name = {entry.source_name: entry for entry in audit.factors}
        self.assertTrue(by_name["stronger_context"].allowed_in_state_layer)
        self.assertEqual(by_name["weaker_context"].blocked_by, "high_same_role_correlation")
        self.assertEqual(audit.summary["admissible_factor_count"], 1)

    def test_ralph_loop_zero_admissible_factor_stops_line(self) -> None:
        audit = FactorAuditAgent().audit_series(
            symbol="BTCUSDT",
            factor_series={},
            factor_roles={},
            forward_returns=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            status = RalphLoopAgent().build_status(
                config=SwarmRunConfig(goal="zero factor", output_dir=Path(tmp), loop_mode=SwarmLoopMode.RALPH),
                audit=audit,
                registry={"admissible_candidate_count": 0},
            )

        self.assertEqual(status["verdict"]["status"], SwarmVerdictStatus.STOP_LINE.value)
        self.assertEqual(status["verdict"]["blocked_by"], "no_admissible_factor")

    def test_intent_churn_repair_command_passes_training_controls_and_stays_train_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            commands = ExperimentAgent().build_command_graph(
                SwarmRunConfig(
                    goal="continue churn repair",
                    output_dir=Path(tmp),
                    mode=SwarmMode.DRY_RUN,
                    search_space_mode="intent_churn_repair_train_only",
                    optuna_trials=32,
                    rl_episodes=0,
                    resume=True,
                    liquidation_dir=Path("data/raw/merged_liquidations/aggregated"),
                )
            )

        framework = commands[0]
        self.assertEqual(
            framework.argv[framework.argv.index("--search-space-mode") + 1], "intent_churn_repair_train_only"
        )
        self.assertEqual(framework.argv[framework.argv.index("--optuna-trials") + 1], "32")
        self.assertEqual(framework.argv[framework.argv.index("--rl-episodes") + 1], "0")
        self.assertEqual(
            framework.argv[framework.argv.index("--liquidation-dir") + 1],
            "data/raw/merged_liquidations/aggregated",
        )
        self.assertIn("--resume", framework.argv)
        self.assertFalse(framework.touches_test)
        self.assertTrue(TemporalGuardAgent().preflight(commands, allow_test_final_verdict=False).passed)
        for command in commands:
            if "--split" in command.argv:
                self.assertNotEqual(command.argv[command.argv.index("--split") + 1], "test")

    def test_swarm_does_not_overwrite_existing_artifacts_on_repeated_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "swarm_run"
            manager = SwarmManager()
            with patch(
                "zenquant.research.swarm.manager._git_info",
                return_value={"code_commit": "abc123", "dirty_tree": False, "dirty_paths": []},
            ):
                manager.run(SwarmRunConfig(goal="first run", output_dir=output_dir))
                first_manifest = (output_dir / "swarm_run_manifest.json").read_text(encoding="utf-8")
                manager.run(SwarmRunConfig(goal="second run", output_dir=output_dir))

            manifests = sorted(output_dir.glob("swarm_run_manifest*.json"))
            self.assertIn('"goal": "first run"', first_manifest)
            self.assertEqual((output_dir / "swarm_run_manifest.json").read_text(encoding="utf-8"), first_manifest)
            self.assertGreaterEqual(len(manifests), 2)

    def test_verifier_maps_failed_command_to_blocked(self) -> None:
        command = CommandSpec(
            name="framework_campaign",
            argv=("python", "scripts/run_framework_campaign.py"),
            purpose="campaign",
        )
        temporal = TemporalGuardAgent().preflight([command], allow_test_final_verdict=False)
        verdict = VerifierAgent().verify(
            output_dir=Path("/tmp/nonexistent-swarm-output"),
            temporal_result=temporal,
            experiment_results=[
                ExperimentResult(
                    command=command,
                    returncode=1,
                    status="failed",
                    blocked_by="command_failed",
                )
            ],
        )

        self.assertEqual(verdict.status, SwarmVerdictStatus.BLOCKED)
        self.assertEqual(verdict.blocked_by, "command_failed")

    def test_verifier_maps_negative_candidate_artifact_to_stop_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            campaign_dir = output_dir / "framework_campaign"
            campaign_dir.mkdir()
            (campaign_dir / "framework_optimization.json").write_text(
                json.dumps(
                    {
                        "split_contract": {"version": DEFAULT_SPLIT_CONTRACT_VERSION},
                        "candidate_existence_recovered": False,
                        "no_tuned_candidate_reason": "all_negative_primary_metric",
                    }
                ),
                encoding="utf-8",
            )
            temporal = TemporalGuardAgent().check_artifacts(
                [campaign_dir / "framework_optimization.json"],
                allow_test_final_verdict=False,
            )
            verdict = VerifierAgent().verify(output_dir=output_dir, temporal_result=temporal, experiment_results=[])

        self.assertEqual(verdict.status, SwarmVerdictStatus.STOP_LINE)
        self.assertEqual(verdict.blocked_by, "all_negative_primary_metric")

    def test_cli_dry_run_entrypoint_generates_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "cli_swarm"
            argv = [
                "run_research_swarm.py",
                "--goal",
                "dry run from cli",
                "--output-dir",
                str(output_dir),
                "--mode",
                "dry-run",
                "--search-space-mode",
                "intent_churn_repair_train_only",
                "--optuna-trials",
                "32",
                "--rl-episodes",
                "0",
                "--resume",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch(
                    "zenquant.research.swarm.manager._git_info",
                    return_value={"code_commit": "abc123", "dirty_tree": False, "dirty_paths": []},
                ),
            ):
                RESEARCH_SWARM_CLI.main()

            self.assertTrue((output_dir / "swarm_run_manifest.json").exists())
            self.assertTrue((output_dir / "swarm_task_ledger.jsonl").exists())
            self.assertTrue((output_dir / "swarm_report.md").exists())
            manifest = json.loads((output_dir / "swarm_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["training_controls"]["search_space_mode"], "intent_churn_repair_train_only")
            self.assertEqual(manifest["training_controls"]["optuna_trials"], 32)
            self.assertEqual(manifest["training_controls"]["rl_episodes"], 0)
            self.assertTrue(manifest["training_controls"]["resume"])

    def test_cli_ralph_dry_run_generates_ralph_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "cli_ralph"
            argv = [
                "run_research_swarm.py",
                "--goal",
                "ralph dry run from cli",
                "--output-dir",
                str(output_dir),
                "--mode",
                "dry-run",
                "--loop-mode",
                "ralph",
                "--max-ralph-rounds",
                "2",
                "--target-candidates",
                "10",
                "--min-candidates",
                "5",
                "--source-scope",
                "free",
                "--profit-priority",
                "guarded",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch(
                    "zenquant.research.swarm.manager._git_info",
                    return_value={"code_commit": "abc123", "dirty_tree": False, "dirty_paths": []},
                ),
            ):
                RESEARCH_SWARM_CLI.main()

            manifest = json.loads((output_dir / "swarm_run_manifest.json").read_text(encoding="utf-8"))
            inventory_exists = (output_dir / "source_candidate_inventory.json").exists()
            status_exists = (output_dir / "ralph_loop_status.json").exists()

        self.assertEqual(manifest["loop_mode"], "ralph")
        self.assertEqual(manifest["ralph_loop_controls"]["max_rounds"], 2)
        self.assertTrue(inventory_exists)
        self.assertTrue(status_exists)

    def test_execute_mode_uses_injected_runner(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_runner(argv: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            return subprocess.CompletedProcess(args=list(argv), returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "execute_swarm"
            manager = SwarmManager(experiment_agent=ExperimentAgent(runner=fake_runner))
            with patch(
                "zenquant.research.swarm.manager._git_info",
                return_value={"code_commit": "abc123", "dirty_tree": False, "dirty_paths": []},
            ):
                manager.run(
                    SwarmRunConfig(
                        goal="execute mocked commands",
                        output_dir=output_dir,
                        mode=SwarmMode.EXECUTE,
                        search_space_mode="intent_churn_repair_train_only",
                        optuna_trials=32,
                        rl_episodes=0,
                    )
                )

        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1], "scripts/run_framework_campaign.py")
        self.assertEqual(calls[0][calls[0].index("--search-space-mode") + 1], "intent_churn_repair_train_only")
        self.assertEqual(calls[0][calls[0].index("--optuna-trials") + 1], "32")
        self.assertEqual(calls[0][calls[0].index("--rl-episodes") + 1], "0")


if __name__ == "__main__":
    unittest.main()
