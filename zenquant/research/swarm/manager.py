"""Manager for the deterministic research-layer swarm."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agents import (
    REQUIRED_VERDICT_METRICS,
    DataAdmissionAgent,
    DataScoutAgent,
    ExperimentAgent,
    ExperimentResult,
    FactorAuditAgent,
    HypothesisAgent,
    RalphLoopAgent,
    ReportAgent,
    SignalFactoryAgent,
    TemporalGuardAgent,
    VerifierAgent,
)
from .contracts import (
    AgentTaskRecord,
    SwarmLoopMode,
    SwarmMode,
    SwarmRunConfig,
    SwarmVerdict,
    SwarmVerdictStatus,
    TaskStatus,
    default_split_contract,
    utc_now_iso,
)


class SwarmManager:
    """Single controller for research-swarm planning, execution, and reporting."""

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        hypothesis_agent: HypothesisAgent | None = None,
        data_scout_agent: DataScoutAgent | None = None,
        data_admission_agent: DataAdmissionAgent | None = None,
        factor_audit_agent: FactorAuditAgent | None = None,
        signal_factory_agent: SignalFactoryAgent | None = None,
        ralph_loop_agent: RalphLoopAgent | None = None,
        temporal_guard: TemporalGuardAgent | None = None,
        experiment_agent: ExperimentAgent | None = None,
        verifier_agent: VerifierAgent | None = None,
        report_agent: ReportAgent | None = None,
    ) -> None:
        self._repo_root = repo_root or Path(__file__).resolve().parents[3]
        self._hypothesis_agent = hypothesis_agent or HypothesisAgent()
        self._data_scout_agent = data_scout_agent or DataScoutAgent()
        self._data_admission_agent = data_admission_agent or DataAdmissionAgent()
        self._factor_audit_agent = factor_audit_agent or FactorAuditAgent()
        self._signal_factory_agent = signal_factory_agent or SignalFactoryAgent()
        self._ralph_loop_agent = ralph_loop_agent or RalphLoopAgent()
        self._temporal_guard = temporal_guard or TemporalGuardAgent()
        self._experiment_agent = experiment_agent or ExperimentAgent()
        self._verifier_agent = verifier_agent or VerifierAgent()
        self._report_agent = report_agent or ReportAgent()

    def run(self, config: SwarmRunConfig) -> dict[str, Any]:
        config.validate()
        config.output_dir.mkdir(parents=True, exist_ok=True)

        hypothesis = self._hypothesis_agent.build(config.goal)
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="001_hypothesis",
                agent=self._hypothesis_agent.name,
                status=TaskStatus.COMPLETED,
                created_at=utc_now_iso(),
                input={"goal": config.goal},
                output=asdict(hypothesis),
            ),
        )

        ralph_artifacts: dict[str, Any] | None = None
        ralph_status: dict[str, Any] | None = None
        if config.loop_mode == SwarmLoopMode.RALPH:
            ralph_artifacts, ralph_status = self._run_ralph_planning(config)

        commands = self._experiment_agent.build_command_graph(config)
        self._experiment_agent.validate_allowlist(commands)
        manifest = self._build_manifest(config, commands)
        manifest_path = self._write_json_preserving(config.output_dir / "swarm_run_manifest.json", manifest)

        preflight = self._temporal_guard.preflight(
            commands,
            allow_test_final_verdict=config.allow_test_final_verdict,
        )
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="002_temporal_preflight",
                agent=self._temporal_guard.name,
                status=TaskStatus.COMPLETED if preflight.passed else TaskStatus.BLOCKED,
                created_at=utc_now_iso(),
                input={"allow_test_final_verdict": config.allow_test_final_verdict},
                output=asdict(preflight),
                blocked_by=preflight.blocked_by,
            ),
        )

        experiment_results: list[ExperimentResult] = []
        ralph_blocks_execution = (
            ralph_status is not None
            and (ralph_status.get("verdict") or {}).get("status") == SwarmVerdictStatus.STOP_LINE.value
        )
        if preflight.passed and config.mode == SwarmMode.EXECUTE and not ralph_blocks_execution:
            experiment_results = self._experiment_agent.run(commands, cwd=self._repo_root)
            for index, result in enumerate(experiment_results, start=1):
                self._append_task(
                    config.output_dir,
                    AgentTaskRecord(
                        task_id=f"003_experiment_{index:02d}",
                        agent=self._experiment_agent.name,
                        status=TaskStatus.COMPLETED if result.status == "completed" else TaskStatus.FAILED,
                        created_at=utc_now_iso(),
                        input=result.command.as_json(),
                        output=result.as_json(),
                        blocked_by=result.blocked_by,
                    ),
                )
        else:
            self._append_task(
                config.output_dir,
                AgentTaskRecord(
                    task_id="003_experiment_plan",
                    agent=self._experiment_agent.name,
                    status=TaskStatus.PLANNED if preflight.passed else TaskStatus.BLOCKED,
                    created_at=utc_now_iso(),
                    input={"mode": config.mode.value},
                    output={"command_graph": [command.as_json() for command in commands]},
                    blocked_by=preflight.blocked_by or ("ralph_loop_stop_line" if ralph_blocks_execution else None),
                ),
            )

        artifact_paths = [Path(path) for command in commands for path in command.output_artifacts]
        postflight = self._temporal_guard.check_artifacts(
            artifact_paths,
            allow_test_final_verdict=config.allow_test_final_verdict,
        )
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="004_temporal_artifact_check",
                agent=self._temporal_guard.name,
                status=TaskStatus.COMPLETED if postflight.passed else TaskStatus.BLOCKED,
                created_at=utc_now_iso(),
                input={"artifact_paths": [str(path) for path in artifact_paths]},
                output=asdict(postflight),
                blocked_by=postflight.blocked_by,
            ),
        )

        verdict_guard = postflight if preflight.passed else preflight
        if config.mode == SwarmMode.DRY_RUN and verdict_guard.passed:
            verdict = SwarmVerdict(
                status=SwarmVerdictStatus.NEEDS_HUMAN_DECISION,
                blocked_by="dry_run_not_evidence",
                evidence_files=(),
                metrics_required=REQUIRED_VERDICT_METRICS,
                reason_codes=("dry_run_command_graph_only",),
            )
        else:
            verdict = self._verifier_agent.verify(
                output_dir=config.output_dir,
                temporal_result=verdict_guard,
                experiment_results=experiment_results,
            )
        verdict_path = self._write_json_preserving(config.output_dir / "swarm_verdict.json", verdict.as_json())
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="005_verifier",
                agent=self._verifier_agent.name,
                status=TaskStatus.COMPLETED if verdict.blocked_by is None else TaskStatus.BLOCKED,
                created_at=utc_now_iso(),
                input={"evidence_root": str(config.output_dir)},
                output=verdict.as_json(),
                blocked_by=verdict.blocked_by,
                artifact_path=str(verdict_path),
            ),
        )

        report = self._report_agent.build_json(
            hypothesis=hypothesis,
            commands=commands,
            verdict=verdict,
            dry_run=config.mode == SwarmMode.DRY_RUN,
            promotion_blockers=config.promotion_blockers,
            ralph_artifacts=ralph_artifacts,
            ralph_status=ralph_status,
        )
        report_json_path = self._write_json_preserving(config.output_dir / "swarm_report.json", report)
        report_md_path = self._write_text_preserving(
            config.output_dir / "swarm_report.md",
            self._report_agent.build_markdown(report),
        )
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="006_report",
                agent=self._report_agent.name,
                status=TaskStatus.COMPLETED,
                created_at=utc_now_iso(),
                input={"verdict_path": str(verdict_path)},
                output={"report_json": str(report_json_path), "report_md": str(report_md_path)},
                artifact_path=str(report_json_path),
            ),
        )

        return {
            "manifest_path": str(manifest_path),
            "ledger_path": str(config.output_dir / "swarm_task_ledger.jsonl"),
            "verdict_path": str(verdict_path),
            "report_json_path": str(report_json_path),
            "report_md_path": str(report_md_path),
            "verdict": verdict.as_json(),
        }

    def _run_ralph_planning(self, config: SwarmRunConfig) -> tuple[dict[str, Any], dict[str, Any]]:
        candidates = self._data_scout_agent.discover(config)
        inventory = {
            "schema_version": "ralph_source_candidate_inventory.v1",
            "source_scope": config.source_scope.value,
            "asset": config.asset,
            "symbol": config.symbol,
            "candidate_count": len(candidates),
            "candidates": [candidate.as_json() for candidate in candidates],
            "research_only_non_promotable": True,
        }
        inventory_path = self._write_json_preserving(config.output_dir / "source_candidate_inventory.json", inventory)
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="001a_data_scout",
                agent=self._data_scout_agent.name,
                status=TaskStatus.COMPLETED,
                created_at=utc_now_iso(),
                input={"source_scope": config.source_scope.value},
                output={"candidate_count": len(candidates)},
                artifact_path=str(inventory_path),
            ),
        )

        reviews = self._data_admission_agent.review(candidates, repo_root=self._repo_root)
        reviews_path = self._write_jsonl_preserving(
            config.output_dir / "source_admission_reviews.jsonl",
            [review.as_json() for review in reviews],
        )
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="001b_data_admission",
                agent=self._data_admission_agent.name,
                status=TaskStatus.COMPLETED,
                created_at=utc_now_iso(),
                input={"candidate_inventory": str(inventory_path)},
                output={
                    "admitted": sum(1 for review in reviews if review.status == "admitted"),
                    "blocked": sum(1 for review in reviews if review.status == "blocked"),
                },
                artifact_path=str(reviews_path),
            ),
        )

        audit = self._factor_audit_agent.audit(candidates, reviews, symbol=config.symbol)
        audit_path = self._write_json_preserving(config.output_dir / "factor_audit.json", audit.as_json())
        audit_md_path = self._write_text_preserving(
            config.output_dir / "factor_audit.md", _factor_audit_markdown(audit.as_json())
        )
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="001c_factor_audit",
                agent=self._factor_audit_agent.name,
                status=TaskStatus.COMPLETED,
                created_at=utc_now_iso(),
                input={"source_admission_reviews": str(reviews_path)},
                output=audit.summary,
                artifact_path=str(audit_path),
            ),
        )

        registry = self._signal_factory_agent.build_registry(audit)
        registry_path = self._write_json_preserving(config.output_dir / "signal_candidate_registry.json", registry)
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="001d_signal_factory",
                agent=self._signal_factory_agent.name,
                status=TaskStatus.COMPLETED,
                created_at=utc_now_iso(),
                input={"factor_audit": str(audit_path)},
                output={
                    "candidate_count": registry["candidate_count"],
                    "admissible_candidate_count": registry["admissible_candidate_count"],
                },
                artifact_path=str(registry_path),
            ),
        )

        status = self._ralph_loop_agent.build_status(config=config, audit=audit, registry=registry)
        status_path = self._write_json_preserving(config.output_dir / "ralph_loop_status.json", status)
        history_path = self._write_jsonl_preserving(config.output_dir / "ralph_loop_history.jsonl", [status])
        self._append_task(
            config.output_dir,
            AgentTaskRecord(
                task_id="001e_ralph_loop",
                agent=self._ralph_loop_agent.name,
                status=TaskStatus.COMPLETED if status["verdict"]["blocked_by"] is None else TaskStatus.BLOCKED,
                created_at=utc_now_iso(),
                input={"signal_candidate_registry": str(registry_path)},
                output=status,
                blocked_by=status["verdict"]["blocked_by"],
                artifact_path=str(status_path),
            ),
        )
        return (
            {
                "source_candidate_inventory": str(inventory_path),
                "source_admission_reviews": str(reviews_path),
                "factor_audit": str(audit_path),
                "factor_audit_md": str(audit_md_path),
                "signal_candidate_registry": str(registry_path),
                "ralph_loop_status": str(status_path),
                "ralph_loop_history": str(history_path),
            },
            status,
        )

    def _build_manifest(self, config: SwarmRunConfig, commands: list[Any]) -> dict[str, Any]:
        git_info = _git_info(self._repo_root)
        return {
            "schema_version": "research_swarm.v1",
            "created_at": utc_now_iso(),
            "run_id": config.output_dir.name,
            "goal": config.goal,
            "asset": config.asset,
            "symbol": config.symbol,
            "mode": config.mode.value,
            "loop_mode": config.loop_mode.value,
            "agent_graph": [
                self._hypothesis_agent.name,
                *(
                    [
                        self._data_scout_agent.name,
                        self._data_admission_agent.name,
                        self._factor_audit_agent.name,
                        self._signal_factory_agent.name,
                        self._ralph_loop_agent.name,
                    ]
                    if config.loop_mode == SwarmLoopMode.RALPH
                    else []
                ),
                self._temporal_guard.name,
                self._experiment_agent.name,
                self._verifier_agent.name,
                self._report_agent.name,
            ],
            "split_contract": default_split_contract(),
            "data_contract": {
                "data_root": str(config.data_root),
                "profile_path": str(config.profile_path),
                "liquidation_dir": str(config.liquidation_dir),
                "multi_frequency_same_temporal_split_required": True,
            },
            "training_controls": {
                "budget": config.budget,
                "search_space_mode": config.search_space_mode,
                "optuna_trials": config.optuna_trials,
                "rl_episodes": config.rl_episodes,
                "resume": config.resume,
                "research_only_non_promotable": True,
            },
            "ralph_loop_controls": {
                "enabled": config.loop_mode == SwarmLoopMode.RALPH,
                "max_rounds": config.max_ralph_rounds,
                "target_candidates": config.target_candidates,
                "min_candidates": config.min_candidates,
                "source_scope": config.source_scope.value,
                "profit_priority": config.profit_priority.value,
            },
            "command_allowlist": [command.as_json() for command in commands],
            "allow_test_final_verdict": config.allow_test_final_verdict,
            "promote_truth_owner": "results/framework_optimization/framework_optimization.json",
            "not_live_decision_path": True,
            "promotion_blockers": list(config.promotion_blockers),
            "code_commit": git_info["code_commit"],
            "dirty_tree": git_info["dirty_tree"],
            "dirty_paths": git_info["dirty_paths"],
        }

    def _append_task(self, output_dir: Path, record: AgentTaskRecord) -> None:
        ledger_path = output_dir / "swarm_task_ledger.jsonl"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_json(), sort_keys=True) + "\n")

    def _write_json_preserving(self, path: Path, payload: dict[str, Any]) -> Path:
        target = _preserved_target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return target

    def _write_text_preserving(self, path: Path, payload: str) -> Path:
        target = _preserved_target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
        return target

    def _write_jsonl_preserving(self, path: Path, rows: list[dict[str, Any]]) -> Path:
        target = _preserved_target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        return target


def _factor_audit_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Ralph Factor Audit",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- symbol: `{payload['symbol']}`",
        f"- corr_threshold: `{payload['corr_threshold']}`",
        f"- admissible_factor_count: `{payload['summary']['admissible_factor_count']}`",
        f"- high_correlation_blocked_count: `{payload['summary']['high_correlation_blocked_count']}`",
        f"- research_only_non_promotable: `{payload['summary']['research_only_non_promotable']}`",
        "",
        "## Factors",
        "",
    ]
    for factor in payload["factors"]:
        lines.extend(
            [
                f"### {factor['source_name']}",
                "",
                f"- role: `{factor['role']}`",
                f"- rank_ic_mean: `{factor['rank_ic_mean']}`",
                f"- max_abs_correlation: `{factor['max_abs_correlation']}`",
                f"- allowed_in_direction_search: `{factor['allowed_in_direction_search']}`",
                f"- allowed_in_state_layer: `{factor['allowed_in_state_layer']}`",
                f"- blocked_by: `{factor['blocked_by']}`",
                "",
            ]
        )
    return "\n".join(lines)


def _preserved_target(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = utc_now_iso().replace(":", "").replace("+", "Z")
    return path.with_name(f"{path.stem}.{suffix}{path.suffix}")


def _git_info(repo_root: Path) -> dict[str, Any]:
    commit = _git_output(repo_root, "rev-parse", "HEAD") or "unknown"
    status = _git_output(repo_root, "status", "--short") or ""
    dirty_paths = [line.strip() for line in status.splitlines() if line.strip()]
    return {
        "code_commit": commit,
        "dirty_tree": bool(dirty_paths),
        "dirty_paths": dirty_paths,
    }


def _git_output(repo_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
