"""Deterministic contracts for the research-layer agent swarm."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from zenquant.research.splits import default_btc_split_spec, split_summary_json

SWARM_SCHEMA_VERSION = "research_swarm.v1"
DEFAULT_SPLIT_CONTRACT_VERSION = default_btc_split_spec().version
DEFAULT_ASSET = "BTC"


class SwarmMode(StrEnum):
    DRY_RUN = "dry-run"
    EXECUTE = "execute"


class SwarmLoopMode(StrEnum):
    CAMPAIGN = "campaign"
    RALPH = "ralph"


class SourceScope(StrEnum):
    FREE = "free"


class ProfitPriority(StrEnum):
    GUARDED = "guarded"


class SwarmVerdictStatus(StrEnum):
    CONTINUE_TRAINING = "continue_training"
    STOP_LINE = "stop_line"
    NEEDS_HUMAN_DECISION = "needs_human_decision"
    BLOCKED = "blocked"


class TaskStatus(StrEnum):
    PLANNED = "planned"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ResearchHypothesis:
    """Structured strategy hypothesis required before experiments are planned."""

    goal: str
    regime_assumption: str
    behavioral_hypothesis: str
    signal_inputs: list[str]
    entry_condition: str
    sizing_rule: str
    risk_throttles: list[str]
    exit_reduction_logic: str
    failure_cases: list[str]
    evaluation_metrics: list[str]

    def validate(self) -> None:
        missing = [
            field_name for field_name, value in asdict(self).items() if value == "" or value == [] or value is None
        ]
        if missing:
            raise ValueError(f"research_hypothesis_invalid:missing={','.join(sorted(missing))}")


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One allowed research command managed by the swarm."""

    name: str
    argv: tuple[str, ...]
    purpose: str
    touches_test: bool = False
    promote_truth: bool = False
    output_artifacts: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["argv"] = list(self.argv)
        payload["output_artifacts"] = list(self.output_artifacts)
        return payload


@dataclass(frozen=True, slots=True)
class SwarmRunConfig:
    """Top-level swarm run inputs."""

    goal: str
    output_dir: Path
    mode: SwarmMode = SwarmMode.DRY_RUN
    asset: str = DEFAULT_ASSET
    symbol: str = "BTCUSDT"
    data_root: Path = Path("data/raw/binance")
    profile_path: Path = Path("config/profiles/default.toml")
    budget: str = "smoke"
    search_space_mode: str | None = None
    optuna_trials: int | None = None
    rl_episodes: int | None = None
    liquidation_dir: Path = Path("data/raw/merged_liquidations/aggregated")
    resume: bool = False
    allow_test_final_verdict: bool = False
    loop_mode: SwarmLoopMode = SwarmLoopMode.CAMPAIGN
    max_ralph_rounds: int = 3
    target_candidates: int = 10
    min_candidates: int = 5
    source_scope: SourceScope = SourceScope.FREE
    profit_priority: ProfitPriority = ProfitPriority.GUARDED
    python_bin: str = "python"
    promotion_blockers: tuple[str, ...] = (
        "research_only_diagnostic_run",
        "zenquant_check_gate_A_ruff_format_failed_preexisting",
        "zenquant_check_gate_C_golden_output_failed_preexisting",
        "zenquant_check_gate_E_full_test_suite_failed_preexisting",
    )

    def validate(self) -> None:
        if not self.goal.strip():
            raise ValueError("research_swarm_config_invalid:goal_required")
        if self.asset != DEFAULT_ASSET:
            raise ValueError("research_swarm_config_invalid:only_btc_supported")
        if self.mode not in {SwarmMode.DRY_RUN, SwarmMode.EXECUTE}:
            raise ValueError(f"research_swarm_config_invalid:mode={self.mode}")
        if self.loop_mode not in {SwarmLoopMode.CAMPAIGN, SwarmLoopMode.RALPH}:
            raise ValueError(f"research_swarm_config_invalid:loop_mode={self.loop_mode}")
        if self.source_scope != SourceScope.FREE:
            raise ValueError("research_swarm_config_invalid:only_free_source_scope_supported")
        if self.profit_priority != ProfitPriority.GUARDED:
            raise ValueError("research_swarm_config_invalid:only_guarded_profit_priority_supported")
        if self.max_ralph_rounds < 1:
            raise ValueError("research_swarm_config_invalid:max_ralph_rounds_must_be_positive")
        if self.min_candidates < 1:
            raise ValueError("research_swarm_config_invalid:min_candidates_must_be_positive")
        if self.target_candidates < self.min_candidates:
            raise ValueError("research_swarm_config_invalid:target_candidates_below_min_candidates")
        if self.optuna_trials is not None and self.optuna_trials < 1:
            raise ValueError("research_swarm_config_invalid:optuna_trials_must_be_positive")
        if self.rl_episodes is not None and self.rl_episodes < 0:
            raise ValueError("research_swarm_config_invalid:rl_episodes_must_be_non_negative")


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """Research-only candidate input source for Ralph loop discovery."""

    name: str
    provider: str
    source_type: str
    role: str
    symbol_scope: tuple[str, ...]
    time_granularity: str
    free_tier: bool
    pit_safe: bool
    local_cache_path: str
    regime_assumption: str
    behavioral_hypothesis: str
    failure_cases: tuple[str, ...]
    correlation_group: str
    prior_rank_ic: float

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["symbol_scope"] = list(self.symbol_scope)
        payload["failure_cases"] = list(self.failure_cases)
        return payload


@dataclass(frozen=True, slots=True)
class SourceAdmissionReview:
    """Point-in-time and lineage review for one source candidate."""

    source_name: str
    provider: str
    role: str
    status: str
    local_cache_path: str
    lineage_complete: bool
    pit_safe: bool
    missing_stale_coverage: str
    use_for_research_only: bool
    exclude_from_promote_truth: bool
    blocked_by: str | None
    next_action: str

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FactorAuditEntry:
    """Correlation and IC gate result for one admitted factor."""

    source_name: str
    role: str
    observations: int
    rank_ic_mean: float
    ic_stability: float
    hit_rate: float
    max_abs_correlation: float
    redundant_with: tuple[str, ...]
    ablation_delta: float | None
    allowed_in_direction_search: bool
    allowed_in_state_layer: bool
    blocked_by: str | None

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["redundant_with"] = list(self.redundant_with)
        return payload


@dataclass(frozen=True, slots=True)
class FactorAuditReport:
    """Research-only factor audit artifact."""

    schema_version: str
    symbol: str
    corr_threshold: float
    pairwise_correlation_matrix: dict[str, dict[str, float]]
    factors: tuple[FactorAuditEntry, ...]
    summary: dict[str, Any]

    def as_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "corr_threshold": self.corr_threshold,
            "pairwise_correlation_matrix": self.pairwise_correlation_matrix,
            "factors": [entry.as_json() for entry in self.factors],
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class TemporalGuardResult:
    """Result of pre/post temporal policy checks."""

    passed: bool
    blocked_by: str | None
    split_contract_version: str
    test_access_allowed: bool
    checked_artifacts: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentTaskRecord:
    """Append-only ledger row for one agent action."""

    task_id: str
    agent: str
    status: TaskStatus
    created_at: str
    input: dict[str, Any]
    output: dict[str, Any]
    blocked_by: str | None = None
    artifact_path: str | None = None

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True, slots=True)
class SwarmVerdict:
    """Final research-swarm verdict. This is not promote truth."""

    status: SwarmVerdictStatus
    blocked_by: str | None
    evidence_files: tuple[str, ...]
    metrics_required: tuple[str, ...]
    reason_codes: tuple[str, ...]
    promote_truth_owner: str = "results/framework_optimization/framework_optimization.json"

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["evidence_files"] = list(self.evidence_files)
        payload["metrics_required"] = list(self.metrics_required)
        payload["reason_codes"] = list(self.reason_codes)
        return payload


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def default_split_contract() -> dict[str, Any]:
    return split_summary_json(default_btc_split_spec())
