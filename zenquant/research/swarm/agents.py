"""Specialist agents for the repo-native research swarm."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
import pyarrow.parquet as pq

from zenquant.data.cot_store import has_raw_cot_fragments

from .contracts import (
    DEFAULT_SPLIT_CONTRACT_VERSION,
    CommandSpec,
    FactorAuditEntry,
    FactorAuditReport,
    ResearchHypothesis,
    SourceAdmissionReview,
    SourceCandidate,
    SwarmRunConfig,
    SwarmVerdict,
    SwarmVerdictStatus,
    TemporalGuardResult,
    default_split_contract,
)

REQUIRED_VERDICT_METRICS: tuple[str, ...] = (
    "sharpe",
    "sortino",
    "max_drawdown",
    "profit_factor",
    "trade_count",
    "fee_drag_pct",
    "worst_window",
    "candidate_existence_recovered",
    "cache_reuse",
    "blocker",
)


class CommandRunner(Protocol):
    def __call__(self, argv: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]: ...


def subprocess_runner(argv: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )


class DataScoutAgent:
    """Build a deterministic free-source candidate inventory for Ralph loop."""

    name = "DataScoutAgent"

    def discover(self, config: SwarmRunConfig) -> list[SourceCandidate]:
        symbol = config.symbol
        return [
            SourceCandidate(
                name="btc_ohlcv_bar_truth",
                provider="zenquant_builtin",
                source_type="market_data",
                role="direction",
                symbol_scope=("BTC",),
                time_granularity="bar_close",
                free_tier=True,
                pit_safe=True,
                local_cache_path="builtin:indicator_pack",
                regime_assumption="All BTC regimes need deterministic bar-close price truth.",
                behavioral_hypothesis="Returns, volatility, and trend state are the baseline context for every factor.",
                failure_cases=("bar_gap", "timestamp_misalignment", "exchange_outage"),
                correlation_group="btc_price",
                prior_rank_ic=0.03,
            ),
            SourceCandidate(
                name="binance_perp_funding",
                provider="binance_public",
                source_type="perp_derivatives",
                role="state",
                symbol_scope=("BTC",),
                time_granularity="8h",
                free_tier=True,
                pit_safe=True,
                local_cache_path=str(config.data_root / "funding" / symbol / "data.parquet"),
                regime_assumption="Perp crowding regimes change continuation and squeeze risk.",
                behavioral_hypothesis="Extreme funding can identify crowded direction and mean-reversion pressure.",
                failure_cases=("funding_gap", "venue_specific_crowding", "delayed_download"),
                correlation_group="perp_crowding",
                prior_rank_ic=0.025,
            ),
            SourceCandidate(
                name="binance_open_interest",
                provider="binance_public",
                source_type="perp_derivatives",
                role="state",
                symbol_scope=("BTC",),
                time_granularity="5m_to_1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path=str(config.data_root / "open_interest" / symbol / "4h" / "data.parquet"),
                regime_assumption="Open interest expansion can separate healthy trend from crowded leverage.",
                behavioral_hypothesis="OI shocks paired with price direction expose leverage build-up or liquidation risk.",
                failure_cases=("contract_roll_mapping", "venue_coverage_bias", "stale_snapshot"),
                correlation_group="open_interest_leverage",
                prior_rank_ic=0.02,
            ),
            SourceCandidate(
                name="deribit_options_summary",
                provider="deribit_public",
                source_type="options",
                role="state",
                symbol_scope=("BTC",),
                time_granularity="1h_to_1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/raw/deribit_options/BTC/_collector_status.json",
                regime_assumption="Options surface stress can identify convexity demand and tail-risk regimes.",
                behavioral_hypothesis="IV, skew, OI, and volume changes can improve risk throttles in hostile windows.",
                failure_cases=("option_symbol_mapping", "thin_expiry_noise", "surface_snapshot_gap"),
                correlation_group="options_vol",
                prior_rank_ic=0.018,
            ),
            SourceCandidate(
                name="hyperliquid_perp_meta",
                provider="hyperliquid_public",
                source_type="perp_microstructure",
                role="execution_modifier",
                symbol_scope=("BTC",),
                time_granularity="1m_to_1h",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/raw/hyperliquid/liquidations/_backfill_status.json",
                regime_assumption="Cross-venue perp microstructure can reveal liquidity stress.",
                behavioral_hypothesis="Venue-level OI, funding, and book context can reduce churn in stressed periods.",
                failure_cases=("venue_microstructure_bias", "short_history", "schema_drift"),
                correlation_group="perp_microstructure",
                prior_rank_ic=0.015,
            ),
            SourceCandidate(
                name="coinmetrics_community_onchain",
                provider="coinmetrics_community",
                source_type="onchain",
                role="state",
                symbol_scope=("BTC",),
                time_granularity="1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/raw/coinmetrics/community/BTC/data.parquet",
                regime_assumption="On-chain activity helps distinguish adoption stress from pure market noise.",
                behavioral_hypothesis="Active addresses, transfer value, and realized cap proxies can improve regime state.",
                failure_cases=("metric_revision", "daily_latency", "chain_activity_spam"),
                correlation_group="onchain_activity",
                prior_rank_ic=0.017,
            ),
            SourceCandidate(
                name="btc_mvrv",
                provider="local_macro_cache",
                source_type="onchain_macro",
                role="state",
                symbol_scope=("BTC",),
                time_granularity="1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/raw/macro/btc_mvrv/data.parquet",
                regime_assumption="Cycle valuation state can help avoid hostile late-cycle exposure.",
                behavioral_hypothesis="MVRV extremes identify overextension and deep-value regimes.",
                failure_cases=("vendor_revision", "low_frequency", "cycle_regime_shift"),
                correlation_group="onchain_state",
                prior_rank_ic=0.02,
            ),
            SourceCandidate(
                name="btc_etf_netflow",
                provider="local_macro_cache",
                source_type="flow",
                role="state",
                symbol_scope=("BTC",),
                time_granularity="1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/raw/macro/btc_etf_netflow/data.parquet",
                regime_assumption="Spot ETF flow can change BTC demand persistence.",
                behavioral_hypothesis="Large sustained inflows can support trend persistence; outflows can throttle risk.",
                failure_cases=("reporting_delay", "calendar_gap", "source_restatement"),
                correlation_group="flow_demand",
                prior_rank_ic=0.016,
            ),
            SourceCandidate(
                name="eth_btc_transfer_entropy",
                provider="zenquant_research",
                source_type="cross_asset",
                role="state",
                symbol_scope=("BTC", "ETH"),
                time_granularity="1h_to_1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/research/transfer_entropy/eth_to_btc.json",
                regime_assumption="ETH risk appetite can lead BTC tactical context in some regimes.",
                behavioral_hypothesis="Cross-asset information flow can identify crypto beta expansions or stress.",
                failure_cases=("unstable_directionality", "sample_size_bias", "asset_regime_decoupling"),
                correlation_group="cross_asset_context",
                prior_rank_ic=0.014,
            ),
            SourceCandidate(
                name="sol_bnb_btc_context",
                provider="public_exchange_derived",
                source_type="cross_asset",
                role="state",
                symbol_scope=("BTC", "SOL", "BNB"),
                time_granularity="1h_to_1d",
                free_tier=True,
                pit_safe=True,
                local_cache_path="data/research/cross_asset/sol_bnb_btc_context.parquet",
                regime_assumption="Major alt breadth can confirm or reject broad crypto risk expansion.",
                behavioral_hypothesis="Breadth deterioration can prevent BTC trend entries from overtrading weak beta.",
                failure_cases=("alt_specific_news", "liquidity_mismatch", "exchange_listing_bias"),
                correlation_group="cross_asset_context",
                prior_rank_ic=0.012,
            ),
        ]


class DataAdmissionAgent:
    """Apply source-scope, point-in-time, and local lineage gates."""

    name = "DataAdmissionAgent"

    def review(self, candidates: list[SourceCandidate], *, repo_root: Path) -> list[SourceAdmissionReview]:
        reviews: list[SourceAdmissionReview] = []
        for candidate in candidates:
            cache_path = repo_root / candidate.local_cache_path
            lineage_complete = candidate.local_cache_path.startswith("builtin:") or cache_path.exists()
            status_blocker = _source_status_blocker(cache_path) if lineage_complete else None
            cache_blocker = _source_cache_blocker(cache_path) if lineage_complete else None
            blocked_by = None
            if not candidate.free_tier:
                blocked_by = "non_free_source_scope"
            elif not candidate.pit_safe:
                blocked_by = "pit_unavailable"
            elif not lineage_complete:
                blocked_by = "source_cache_missing"
            elif status_blocker:
                blocked_by = status_blocker
            elif cache_blocker:
                blocked_by = cache_blocker
            status = "admitted" if blocked_by is None else "blocked"
            reviews.append(
                SourceAdmissionReview(
                    source_name=candidate.name,
                    provider=candidate.provider,
                    role=candidate.role,
                    status=status,
                    local_cache_path=candidate.local_cache_path,
                    lineage_complete=lineage_complete,
                    pit_safe=candidate.pit_safe,
                    missing_stale_coverage=status_blocker
                    or cache_blocker
                    or ("ok" if lineage_complete else "cache_missing"),
                    use_for_research_only=True,
                    exclude_from_promote_truth=True,
                    blocked_by=blocked_by,
                    next_action="run_factor_audit" if status == "admitted" else "collect_or_repair_source_cache",
                )
            )
        return reviews


class FactorAuditAgent:
    """Gate admitted factors by correlation, rank IC, and lineage."""

    name = "FactorAuditAgent"

    def audit(
        self,
        candidates: list[SourceCandidate],
        reviews: list[SourceAdmissionReview],
        *,
        symbol: str,
        corr_threshold: float = 0.75,
    ) -> FactorAuditReport:
        admitted = {review.source_name for review in reviews if review.status == "admitted"}
        active = [candidate for candidate in candidates if candidate.name in admitted]
        pairwise = _candidate_pairwise_correlations(active)
        factors: list[FactorAuditEntry] = []
        for candidate in active:
            peers = [
                peer
                for peer in active
                if peer.name != candidate.name
                and peer.role == candidate.role
                and abs(pairwise[candidate.name][peer.name]) >= corr_threshold
            ]
            max_abs_corr = max(
                (abs(pairwise[candidate.name][peer.name]) for peer in active if peer.name != candidate.name),
                default=0.0,
            )
            blocked_by = None
            if any(_candidate_is_stronger(peer, candidate) for peer in peers):
                blocked_by = "high_same_role_correlation"
            elif candidate.prior_rank_ic <= 0.0:
                blocked_by = "non_positive_rank_ic"
            factors.append(
                FactorAuditEntry(
                    source_name=candidate.name,
                    role=candidate.role,
                    observations=0,
                    rank_ic_mean=round(candidate.prior_rank_ic, 4),
                    ic_stability=round(abs(candidate.prior_rank_ic) * 10.0, 4),
                    hit_rate=0.5,
                    max_abs_correlation=round(max_abs_corr, 4),
                    redundant_with=tuple(peer.name for peer in peers),
                    ablation_delta=None,
                    allowed_in_direction_search=candidate.role == "direction" and blocked_by is None,
                    allowed_in_state_layer=candidate.role == "state" and blocked_by is None,
                    blocked_by=blocked_by,
                )
            )
        allowed_count = sum(1 for entry in factors if entry.allowed_in_direction_search or entry.allowed_in_state_layer)
        summary = {
            "research_only_non_promotable": True,
            "admitted_source_count": len(active),
            "audited_factor_count": len(factors),
            "admissible_factor_count": allowed_count,
            "high_correlation_blocked_count": sum(
                1 for entry in factors if entry.blocked_by == "high_same_role_correlation"
            ),
            "blocked_by": "no_admissible_factor" if not allowed_count else None,
        }
        return FactorAuditReport(
            schema_version="ralph_factor_audit.v1",
            symbol=symbol,
            corr_threshold=corr_threshold,
            pairwise_correlation_matrix=pairwise,
            factors=tuple(factors),
            summary=summary,
        )

    def audit_series(
        self,
        *,
        symbol: str,
        factor_series: dict[str, list[float]],
        factor_roles: dict[str, str],
        forward_returns: list[float],
        corr_threshold: float = 0.75,
    ) -> FactorAuditReport:
        names = sorted(factor_series)
        pairwise = {name: {} for name in names}
        for left in names:
            for right in names:
                pairwise[left][right] = round(
                    _pearson_corr(factor_series[left], factor_series[right]) if left != right else 1.0,
                    4,
                )
        rank_ic_by_name: dict[str, float] = {}
        stability_by_name: dict[str, float] = {}
        for name in names:
            rank_ics = _rolling_rank_ic(factor_series[name], forward_returns, window=5)
            rank_ic_mean = (
                sum(rank_ics) / len(rank_ics) if rank_ics else _rank_corr(factor_series[name], forward_returns)
            )
            rank_ic_std = (
                (sum((value - rank_ic_mean) ** 2 for value in rank_ics) / len(rank_ics)) ** 0.5 if rank_ics else 0.0
            )
            rank_ic_by_name[name] = rank_ic_mean
            stability_by_name[name] = abs(rank_ic_mean) / rank_ic_std if rank_ic_std > 1e-9 else abs(rank_ic_mean)

        factors: list[FactorAuditEntry] = []
        for name in names:
            role = factor_roles.get(name, "direction")
            redundant = [
                peer
                for peer in names
                if peer != name
                and factor_roles.get(peer, "direction") == role
                and abs(pairwise[name][peer]) >= corr_threshold
            ]
            blocked_by = None
            if any(
                _series_factor_is_stronger(
                    peer,
                    name,
                    rank_ic_by_name=rank_ic_by_name,
                    stability_by_name=stability_by_name,
                )
                for peer in redundant
            ):
                blocked_by = "high_same_role_correlation"
            factors.append(
                FactorAuditEntry(
                    source_name=name,
                    role=role,
                    observations=min(len(factor_series[name]), len(forward_returns)),
                    rank_ic_mean=round(rank_ic_by_name[name], 4),
                    ic_stability=round(stability_by_name[name], 4),
                    hit_rate=0.5,
                    max_abs_correlation=round(
                        max((abs(pairwise[name][peer]) for peer in names if peer != name), default=0.0),
                        4,
                    ),
                    redundant_with=tuple(redundant),
                    ablation_delta=None,
                    allowed_in_direction_search=role == "direction" and blocked_by is None,
                    allowed_in_state_layer=role == "state" and blocked_by is None,
                    blocked_by=blocked_by,
                )
            )
        allowed_count = sum(1 for entry in factors if entry.allowed_in_direction_search or entry.allowed_in_state_layer)
        return FactorAuditReport(
            schema_version="ralph_factor_audit.v1",
            symbol=symbol,
            corr_threshold=corr_threshold,
            pairwise_correlation_matrix=pairwise,
            factors=tuple(factors),
            summary={
                "research_only_non_promotable": True,
                "admitted_source_count": len(names),
                "audited_factor_count": len(names),
                "admissible_factor_count": allowed_count,
                "high_correlation_blocked_count": sum(
                    1 for entry in factors if entry.blocked_by == "high_same_role_correlation"
                ),
                "blocked_by": "no_admissible_factor" if not allowed_count else None,
            },
        )


class SignalFactoryAgent:
    """Build a research-only registry from admitted factors."""

    name = "SignalFactoryAgent"

    def build_registry(self, audit: FactorAuditReport) -> dict[str, Any]:
        candidates = []
        for entry in audit.factors:
            if entry.allowed_in_direction_search:
                recommendation = "direction_search_candidate"
            elif entry.allowed_in_state_layer:
                recommendation = "state_layer_candidate"
            elif entry.blocked_by:
                recommendation = "blocked"
            else:
                recommendation = "observation_only"
            candidates.append(
                {
                    "name": entry.source_name,
                    "role": entry.role,
                    "recommendation": recommendation,
                    "rank_ic_mean": entry.rank_ic_mean,
                    "max_abs_correlation": entry.max_abs_correlation,
                    "blocked_by": entry.blocked_by,
                    "research_only_non_promotable": True,
                }
            )
        return {
            "schema_version": "ralph_signal_candidate_registry.v1",
            "candidate_count": len(candidates),
            "admissible_candidate_count": sum(
                1
                for item in candidates
                if item["recommendation"] in {"direction_search_candidate", "state_layer_candidate"}
            ),
            "candidates": candidates,
            "not_live_decision_path": True,
            "research_only_non_promotable": True,
        }


class RalphLoopAgent:
    """Plan the recursive research loop and produce the pre-experiment stop signal."""

    name = "RalphLoopAgent"

    def build_status(
        self,
        *,
        config: SwarmRunConfig,
        audit: FactorAuditReport,
        registry: dict[str, Any],
    ) -> dict[str, Any]:
        admissible_count = int(registry.get("admissible_candidate_count", 0))
        if admissible_count <= 0:
            verdict = {
                "status": SwarmVerdictStatus.STOP_LINE.value,
                "blocked_by": "no_admissible_factor",
                "reason_codes": ["no_admissible_factor", "collect_new_source_before_training"],
            }
        elif admissible_count < config.min_candidates:
            verdict = {
                "status": SwarmVerdictStatus.NEEDS_HUMAN_DECISION.value,
                "blocked_by": "candidate_count_shrunk",
                "reason_codes": ["candidate_count_shrunk", f"admissible_count={admissible_count}"],
            }
        else:
            verdict = {
                "status": SwarmVerdictStatus.CONTINUE_TRAINING.value,
                "blocked_by": None,
                "reason_codes": ["candidate_pool_ready", f"admissible_count={admissible_count}"],
            }
        return {
            "schema_version": "ralph_loop_status.v1",
            "loop_mode": "ralph",
            "research_only_non_promotable": True,
            "profit_priority": config.profit_priority.value,
            "source_scope": config.source_scope.value,
            "max_rounds": config.max_ralph_rounds,
            "target_candidates": config.target_candidates,
            "min_candidates": config.min_candidates,
            "admissible_candidate_count": admissible_count,
            "factor_audit_summary": audit.summary,
            "verdict": verdict,
            "next_action": "run_train_only_search"
            if verdict["status"] != SwarmVerdictStatus.STOP_LINE.value
            else "discover_new_sources",
        }


class HypothesisAgent:
    """Turn a broad research goal into the required strategy hypothesis shape."""

    name = "HypothesisAgent"

    def build(self, goal: str) -> ResearchHypothesis:
        hypothesis = ResearchHypothesis(
            goal=goal.strip(),
            regime_assumption=(
                "BTC remains the first asset; hostile/bear regimes need capital preservation, "
                "volatility-shock monetization, and churn control before trend expansion."
            ),
            behavioral_hypothesis=(
                "A research-layer swarm can improve experiment quality by separating hypothesis generation, "
                "temporal guardrails, execution of existing research tools, and independent verification."
            ),
            signal_inputs=[
                "existing IndicatorPack values",
                "framework_optimization artifacts",
                "comparison artifacts",
                "adversarial stress artifacts",
            ],
            entry_condition="No live entry condition is changed; only research tasks are scheduled.",
            sizing_rule="No sizing rule is changed; risk overlay authority remains downstream of strategy intent.",
            risk_throttles=[
                "test split access blocked unless final-verdict mode is explicit",
                "commands restricted to research allowlist",
                "verdict requires drawdown, churn, weak-window, and blocker evidence",
            ],
            exit_reduction_logic="No execution exit logic is changed by the swarm.",
            failure_cases=[
                "artifact missing split_contract",
                "test split touched outside final acceptance",
                "candidate_existence_recovered=false",
                "all_negative_primary_metric",
                "search_space_exhausted_without_candidate",
            ],
            evaluation_metrics=list(REQUIRED_VERDICT_METRICS),
        )
        hypothesis.validate()
        return hypothesis


class TemporalGuardAgent:
    """Enforce the BTC temporal contract before and after experiments."""

    name = "TemporalGuardAgent"

    def preflight(self, commands: list[CommandSpec], *, allow_test_final_verdict: bool) -> TemporalGuardResult:
        for command in commands:
            violation = self._command_violation(command, allow_test_final_verdict=allow_test_final_verdict)
            if violation is not None:
                return TemporalGuardResult(
                    passed=False,
                    blocked_by=violation,
                    split_contract_version=DEFAULT_SPLIT_CONTRACT_VERSION,
                    test_access_allowed=allow_test_final_verdict,
                    reason_codes=("temporal_guard_preflight_failed", command.name),
                )
        return TemporalGuardResult(
            passed=True,
            blocked_by=None,
            split_contract_version=DEFAULT_SPLIT_CONTRACT_VERSION,
            test_access_allowed=allow_test_final_verdict,
            reason_codes=("temporal_guard_preflight_passed",),
        )

    def check_artifacts(
        self,
        artifacts: list[Path],
        *,
        allow_test_final_verdict: bool,
    ) -> TemporalGuardResult:
        checked: list[str] = []
        for artifact in artifacts:
            if not artifact.exists() or artifact.suffix != ".json":
                continue
            checked.append(str(artifact))
            payload = _read_json(artifact)
            split_version = _extract_split_version(payload)
            if split_version is not None and split_version != DEFAULT_SPLIT_CONTRACT_VERSION:
                return TemporalGuardResult(
                    passed=False,
                    blocked_by="split_contract_mismatch",
                    split_contract_version=split_version,
                    test_access_allowed=allow_test_final_verdict,
                    checked_artifacts=tuple(checked),
                    reason_codes=("temporal_guard_artifact_failed",),
                )
            if _artifact_touched_test(payload) and not _artifact_is_final_test_acceptance(
                payload, allow_test_final_verdict
            ):
                return TemporalGuardResult(
                    passed=False,
                    blocked_by="test_split_policy_violation",
                    split_contract_version=split_version or DEFAULT_SPLIT_CONTRACT_VERSION,
                    test_access_allowed=allow_test_final_verdict,
                    checked_artifacts=tuple(checked),
                    reason_codes=("test_access_without_final_acceptance",),
                )
        return TemporalGuardResult(
            passed=True,
            blocked_by=None,
            split_contract_version=DEFAULT_SPLIT_CONTRACT_VERSION,
            test_access_allowed=allow_test_final_verdict,
            checked_artifacts=tuple(checked),
            reason_codes=("temporal_guard_artifact_passed",),
        )

    def _command_violation(self, command: CommandSpec, *, allow_test_final_verdict: bool) -> str | None:
        if command.touches_test and not allow_test_final_verdict:
            return "test_split_policy_violation"
        if "--split" in command.argv:
            split = command.argv[command.argv.index("--split") + 1]
            if split == "test" and not allow_test_final_verdict:
                return "test_split_policy_violation"
        return None


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    command: CommandSpec
    returncode: int | None
    status: str
    stdout_tail: str = ""
    stderr_tail: str = ""
    blocked_by: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "command": self.command.as_json(),
            "returncode": self.returncode,
            "status": self.status,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "blocked_by": self.blocked_by,
        }


class ExperimentAgent:
    """Plan and optionally run allowlisted research commands."""

    name = "ExperimentAgent"

    def __init__(self, *, runner: CommandRunner = subprocess_runner) -> None:
        self._runner = runner

    def build_command_graph(self, config: SwarmRunConfig) -> list[CommandSpec]:
        output_dir = config.output_dir
        framework_argv = [
            config.python_bin,
            "scripts/run_framework_campaign.py",
            "--root",
            str(config.data_root),
            "--symbol",
            config.symbol,
            "--output-dir",
            str(output_dir / "framework_campaign"),
            "--budget",
            config.budget,
            "--liquidation-dir",
            str(config.liquidation_dir),
            "--profile-path",
            str(config.profile_path),
        ]
        if config.search_space_mode:
            framework_argv.extend(["--search-space-mode", config.search_space_mode])
        if config.optuna_trials is not None:
            framework_argv.extend(["--optuna-trials", str(config.optuna_trials)])
        if config.rl_episodes is not None:
            framework_argv.extend(["--rl-episodes", str(config.rl_episodes)])
        if config.resume:
            framework_argv.append("--resume")
        commands = [
            CommandSpec(
                name="framework_campaign",
                argv=tuple(framework_argv),
                purpose="authoritative clean campaign wrapper; promote truth still belongs to framework_optimization.json",
                promote_truth=True,
                output_artifacts=(
                    str(output_dir / "framework_campaign" / "campaign_status.json"),
                    str(output_dir / "framework_campaign" / "framework_optimization.json"),
                ),
            ),
            CommandSpec(
                name="compare_variants_dev",
                argv=(
                    config.python_bin,
                    "scripts/compare_variants.py",
                    "--root",
                    str(config.data_root),
                    "--symbol",
                    config.symbol,
                    "--split",
                    "dev",
                    "--output-dir",
                    str(output_dir / "compare_dev"),
                ),
                purpose="dev-only diagnostic comparison and weak-window review",
                output_artifacts=(str(output_dir / "compare_dev" / "comparison.json"),),
            ),
            CommandSpec(
                name="adversarial_stress_dev",
                argv=(
                    config.python_bin,
                    "scripts/adversarial_stress.py",
                    "--historical",
                    "--split",
                    "dev",
                ),
                purpose="dev-only adversarial robustness check",
            ),
        ]
        if config.allow_test_final_verdict:
            commands.append(
                CommandSpec(
                    name="compare_variants_test_final",
                    argv=(
                        config.python_bin,
                        "scripts/compare_variants.py",
                        "--root",
                        str(config.data_root),
                        "--symbol",
                        config.symbol,
                        "--split",
                        "test",
                        "--output-dir",
                        str(output_dir / "compare_test_final"),
                    ),
                    purpose="explicit final historical acceptance check only",
                    touches_test=True,
                    promote_truth=True,
                    output_artifacts=(str(output_dir / "compare_test_final" / "comparison.json"),),
                )
            )
        return commands

    def validate_allowlist(self, commands: list[CommandSpec]) -> None:
        for command in commands:
            script = command.argv[1] if len(command.argv) > 1 else ""
            if script not in _ALLOWED_SCRIPTS:
                raise ValueError(f"research_swarm_command_not_allowlisted:{script}")
            allowed_splits = _ALLOWED_SCRIPTS[script]
            if "--split" in command.argv:
                split = command.argv[command.argv.index("--split") + 1]
                if split not in allowed_splits:
                    raise ValueError(f"research_swarm_command_split_not_allowlisted:{script}:{split}")

    def run(self, commands: list[CommandSpec], *, cwd: Path) -> list[ExperimentResult]:
        self.validate_allowlist(commands)
        results: list[ExperimentResult] = []
        for command in commands:
            completed = self._runner(command.argv, cwd)
            status = "completed" if completed.returncode == 0 else "failed"
            results.append(
                ExperimentResult(
                    command=command,
                    returncode=completed.returncode,
                    status=status,
                    stdout_tail=_tail(completed.stdout),
                    stderr_tail=_tail(completed.stderr),
                    blocked_by=None if completed.returncode == 0 else "command_failed",
                )
            )
            if completed.returncode != 0:
                break
        return results


class VerifierAgent:
    """Read authoritative artifacts and turn them into a research-layer verdict."""

    name = "VerifierAgent"

    def verify(
        self,
        *,
        output_dir: Path,
        temporal_result: TemporalGuardResult,
        experiment_results: list[ExperimentResult],
    ) -> SwarmVerdict:
        evidence = _discover_evidence_files(output_dir)
        reason_codes: list[str] = list(temporal_result.reason_codes)
        if not temporal_result.passed:
            return SwarmVerdict(
                status=SwarmVerdictStatus.BLOCKED,
                blocked_by=temporal_result.blocked_by,
                evidence_files=tuple(evidence),
                metrics_required=REQUIRED_VERDICT_METRICS,
                reason_codes=tuple(reason_codes),
            )
        failed = next((item for item in experiment_results if item.status == "failed"), None)
        if failed is not None:
            return SwarmVerdict(
                status=SwarmVerdictStatus.BLOCKED,
                blocked_by=failed.blocked_by or "experiment_failed",
                evidence_files=tuple(evidence),
                metrics_required=REQUIRED_VERDICT_METRICS,
                reason_codes=tuple(reason_codes + [failed.command.name]),
            )

        ralph_payload = _first_existing_json([output_dir / "ralph_loop_status.json"])
        ralph_verdict = (ralph_payload or {}).get("verdict") if isinstance(ralph_payload, dict) else {}
        if isinstance(ralph_verdict, dict):
            ralph_status = ralph_verdict.get("status")
            ralph_blocker = ralph_verdict.get("blocked_by")
            ralph_reasons = [str(item) for item in ralph_verdict.get("reason_codes", [])]
            if ralph_status == SwarmVerdictStatus.BLOCKED.value:
                return SwarmVerdict(
                    status=SwarmVerdictStatus.BLOCKED,
                    blocked_by=str(ralph_blocker or "ralph_loop_blocked"),
                    evidence_files=tuple(evidence),
                    metrics_required=REQUIRED_VERDICT_METRICS,
                    reason_codes=tuple(reason_codes + ralph_reasons),
                )
            if ralph_status == SwarmVerdictStatus.STOP_LINE.value:
                return SwarmVerdict(
                    status=SwarmVerdictStatus.STOP_LINE,
                    blocked_by=str(ralph_blocker or "ralph_loop_stop_line"),
                    evidence_files=tuple(evidence),
                    metrics_required=REQUIRED_VERDICT_METRICS,
                    reason_codes=tuple(reason_codes + ralph_reasons),
                )

        framework_payload = _first_existing_json(
            [
                output_dir / "framework_campaign" / "framework_optimization.json",
                output_dir / "framework_optimization.json",
            ]
        )
        status_payload = _first_existing_json(
            [
                output_dir / "framework_campaign" / "campaign_status.json",
                output_dir / "campaign_status.json",
            ]
        )
        merged = {**(status_payload or {}), **(framework_payload or {})}
        if not merged and isinstance(ralph_verdict, dict):
            ralph_status = ralph_verdict.get("status")
            if ralph_status == SwarmVerdictStatus.NEEDS_HUMAN_DECISION.value:
                return SwarmVerdict(
                    status=SwarmVerdictStatus.NEEDS_HUMAN_DECISION,
                    blocked_by=str(ralph_verdict.get("blocked_by") or "ralph_loop_needs_human_decision"),
                    evidence_files=tuple(evidence),
                    metrics_required=REQUIRED_VERDICT_METRICS,
                    reason_codes=tuple(reason_codes + [str(item) for item in ralph_verdict.get("reason_codes", [])]),
                )
        blocker = _extract_blocker(merged)
        if blocker in {
            "all_negative_primary_metric",
            "search_space_exhausted_without_candidate",
            "hostile_window_quality_failure",
            "primary_and_fallback_empty",
            "candidate_existence_recovered_false",
        }:
            return SwarmVerdict(
                status=SwarmVerdictStatus.STOP_LINE,
                blocked_by=blocker,
                evidence_files=tuple(evidence),
                metrics_required=REQUIRED_VERDICT_METRICS,
                reason_codes=tuple(reason_codes + [blocker]),
            )
        if merged.get("candidate_existence_recovered") is False:
            return SwarmVerdict(
                status=SwarmVerdictStatus.STOP_LINE,
                blocked_by="candidate_existence_recovered_false",
                evidence_files=tuple(evidence),
                metrics_required=REQUIRED_VERDICT_METRICS,
                reason_codes=tuple(reason_codes + ["candidate_existence_recovered=false"]),
            )
        if blocker:
            return SwarmVerdict(
                status=SwarmVerdictStatus.NEEDS_HUMAN_DECISION,
                blocked_by=blocker,
                evidence_files=tuple(evidence),
                metrics_required=REQUIRED_VERDICT_METRICS,
                reason_codes=tuple(reason_codes + [blocker]),
            )
        return SwarmVerdict(
            status=SwarmVerdictStatus.CONTINUE_TRAINING,
            blocked_by=None,
            evidence_files=tuple(evidence),
            metrics_required=REQUIRED_VERDICT_METRICS,
            reason_codes=tuple(reason_codes + ["no_blocking_artifact_found"]),
        )


class ReportAgent:
    """Build user-facing JSON/Markdown summaries from swarm artifacts."""

    name = "ReportAgent"

    def build_json(
        self,
        *,
        hypothesis: ResearchHypothesis,
        commands: list[CommandSpec],
        verdict: SwarmVerdict,
        dry_run: bool,
        promotion_blockers: tuple[str, ...],
        ralph_artifacts: dict[str, Any] | None = None,
        ralph_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "research_swarm_report.v1",
            "dry_run": dry_run,
            "hypothesis": asdict(hypothesis),
            "command_graph": [command.as_json() for command in commands],
            "ralph_artifacts": ralph_artifacts or {},
            "ralph_status": ralph_status,
            "verdict": verdict.as_json(),
            "promote_truth_owner": verdict.promote_truth_owner,
            "not_live_decision_path": True,
            "research_only_non_promotable": True,
            "promotion_blockers": list(promotion_blockers),
        }

    def build_markdown(self, report: dict[str, Any]) -> str:
        verdict = report["verdict"]
        lines = [
            "# Research Swarm Report",
            "",
            f"- dry_run: `{report['dry_run']}`",
            f"- verdict: `{verdict['status']}`",
            f"- blocked_by: `{verdict.get('blocked_by') or 'none'}`",
            f"- promote_truth_owner: `{report['promote_truth_owner']}`",
            f"- not_live_decision_path: `{report['not_live_decision_path']}`",
            f"- research_only_non_promotable: `{report['research_only_non_promotable']}`",
            "",
        ]
        if report.get("ralph_status"):
            ralph_verdict = report["ralph_status"]["verdict"]
            lines.extend(
                [
                    "## Ralph Loop",
                    "",
                    f"- status: `{ralph_verdict['status']}`",
                    f"- blocked_by: `{ralph_verdict.get('blocked_by') or 'none'}`",
                    f"- admissible_candidate_count: `{report['ralph_status']['admissible_candidate_count']}`",
                    f"- target_candidates: `{report['ralph_status']['target_candidates']}`",
                    f"- min_candidates: `{report['ralph_status']['min_candidates']}`",
                    "",
                ]
            )
        lines.extend(
            [
                "## Promotion Blockers",
                "",
                *[f"- `{blocker}`" for blocker in report["promotion_blockers"]],
                "",
                "## Hypothesis",
                "",
                f"- goal: `{report['hypothesis']['goal']}`",
                f"- regime_assumption: `{report['hypothesis']['regime_assumption']}`",
                f"- behavioral_hypothesis: `{report['hypothesis']['behavioral_hypothesis']}`",
                "",
                "## Command Graph",
                "",
            ]
        )
        for command in report["command_graph"]:
            lines.append(f"- `{command['name']}`: `{' '.join(command['argv'])}`")
        lines.extend(
            [
                "",
                "## Evidence",
                "",
                *[f"- `{path}`" for path in verdict["evidence_files"]],
            ]
        )
        if report.get("ralph_artifacts"):
            lines.extend(["", "## Ralph Artifacts", ""])
            lines.extend(f"- `{name}`: `{path}`" for name, path in sorted(report["ralph_artifacts"].items()))
        return "\n".join(lines) + "\n"


_ALLOWED_SCRIPTS: dict[str, set[str]] = {
    "scripts/run_framework_campaign.py": {""},
    "scripts/compare_variants.py": {"dev", "test"},
    "scripts/adversarial_stress.py": {"dev", "test"},
    "scripts/run_auto_research.py": {""},
    "scripts/signal_ablation.py": {""},
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _first_existing_json(paths: list[Path]) -> dict[str, Any] | None:
    for path in paths:
        if path.exists():
            return _read_json(path)
    return None


def _extract_split_version(payload: dict[str, Any]) -> str | None:
    split_contract = payload.get("split_contract")
    if isinstance(split_contract, dict):
        version = split_contract.get("version")
        if version:
            return str(version)
    version = payload.get("split_contract_version")
    return str(version) if version else None


def _artifact_touched_test(payload: dict[str, Any]) -> bool:
    if payload.get("test_evaluated") is True:
        return True
    if payload.get("comparison_split") == "test":
        return True
    if payload.get("split") == "test":
        return True
    return payload.get("final_verdict", {}).get("test_evaluated") is True


def _artifact_is_final_test_acceptance(payload: dict[str, Any], allow_test_final_verdict: bool) -> bool:
    if not allow_test_final_verdict:
        return False
    if payload.get("comparison_split") == "test":
        return True
    final_verdict = payload.get("final_verdict", {})
    return payload.get("test_evaluated") is True or final_verdict.get("test_evaluated") is True


def _discover_evidence_files(output_dir: Path) -> list[str]:
    names = {
        "campaign_status.json",
        "framework_optimization.json",
        "comparison.json",
        "research_closure.json",
        "candidate_checkpoint.json",
        "ralph_loop_status.json",
        "factor_audit.json",
        "signal_candidate_registry.json",
    }
    if not output_dir.exists():
        return []
    return sorted(str(path) for path in output_dir.rglob("*.json") if path.name in names)


def _extract_blocker(payload: dict[str, Any]) -> str | None:
    for key in (
        "blocked_by",
        "no_tuned_candidate_reason",
        "loop_stop_reason",
        "promotion_failure_reason",
        "failure_class",
    ):
        value = payload.get(key)
        if value:
            return str(value)
    final_verdict = payload.get("final_verdict")
    if isinstance(final_verdict, dict):
        value = final_verdict.get("blocked_by")
        if value:
            return str(value)
    return None


def _source_status_blocker(path: Path) -> str | None:
    if path.name != "_collector_status.json" and path.name != "_backfill_status.json":
        return None
    payload = _read_json(path)
    blocker = payload.get("blocked_by")
    if blocker:
        return str(blocker)
    if payload.get("stale") is True:
        return "source_cache_stale"
    if payload.get("research_ready") is False and payload.get("observation_ready") is False:
        return "source_not_research_ready"
    coverage = payload.get("coverage")
    if isinstance(coverage, dict) and _coverage_starts_at_or_after_test(coverage.get("start")):
        return "source_cache_outside_train_dev"
    return None


def _source_cache_blocker(path: Path) -> str | None:
    if path.suffix != ".parquet":
        return None
    try:
        dataset = pq.ParquetDataset(path)
        cot_fields = {"asset_mgr_net", "leveraged_net", "dealer_net", "total_oi"}
        physical_fields = set().union(*(set(fragment.physical_schema.names) for fragment in dataset.fragments))
        if has_raw_cot_fragments(dataset) and cot_fields.intersection(physical_fields):
            return "cot_publication_time_unavailable"
        frame = dataset.read().to_pandas()
    except Exception:
        return "source_cache_unreadable"
    if frame.empty:
        return "source_cache_empty"
    time_values = _cache_time_values(frame)
    if time_values is None or time_values.empty:
        return None
    test_start_ns = int(default_split_contract()["test"]["start_ns"])
    if int(time_values.max()) < test_start_ns:
        return None
    if int(time_values.min()) >= test_start_ns:
        return "source_cache_outside_train_dev"
    return None


def _cache_time_values(frame: pd.DataFrame) -> pd.Series | None:
    for column in ("ts_ns", "close_time_ns", "funding_time_ns", "open_time_ns", "report_date_ns"):
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce").dropna()
    for column in ("date", "time"):
        if column in frame.columns:
            return pd.to_datetime(frame[column], utc=True, errors="coerce").dropna().astype("int64")
    return None


def _coverage_starts_at_or_after_test(value: Any) -> bool:
    if not value:
        return False
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return False
    test_start_ns = int(default_split_contract()["test"]["start_ns"])
    return int(parsed.value) >= test_start_ns


def _tail(text: str, *, limit: int = 1200) -> str:
    return text[-limit:] if len(text) > limit else text


def _candidate_pairwise_correlations(candidates: list[SourceCandidate]) -> dict[str, dict[str, float]]:
    matrix: dict[str, dict[str, float]] = {candidate.name: {} for candidate in candidates}
    for left in candidates:
        for right in candidates:
            if left.name == right.name:
                corr = 1.0
            elif left.correlation_group == right.correlation_group:
                corr = 0.92
            elif left.role == right.role:
                corr = 0.32
            else:
                corr = 0.18
            matrix[left.name][right.name] = corr
    return matrix


def _candidate_is_stronger(left: SourceCandidate, right: SourceCandidate) -> bool:
    if left.prior_rank_ic != right.prior_rank_ic:
        return left.prior_rank_ic > right.prior_rank_ic
    left_stability = abs(left.prior_rank_ic) * 10.0
    right_stability = abs(right.prior_rank_ic) * 10.0
    if left_stability != right_stability:
        return left_stability > right_stability
    return left.name < right.name


def _series_factor_is_stronger(
    left: str,
    right: str,
    *,
    rank_ic_by_name: dict[str, float],
    stability_by_name: dict[str, float],
) -> bool:
    left_rank_ic = rank_ic_by_name.get(left, 0.0)
    right_rank_ic = rank_ic_by_name.get(right, 0.0)
    if left_rank_ic != right_rank_ic:
        return left_rank_ic > right_rank_ic
    left_stability = stability_by_name.get(left, 0.0)
    right_stability = stability_by_name.get(right, 0.0)
    if left_stability != right_stability:
        return left_stability > right_stability
    return left < right


def _pearson_corr(left: list[float], right: list[float]) -> float:
    n = min(len(left), len(right))
    if n < 5:
        return 0.0
    left_values = left[:n]
    right_values = right[:n]
    left_mean = sum(left_values) / n
    right_mean = sum(right_values) / n
    covariance = sum((left_values[idx] - left_mean) * (right_values[idx] - right_mean) for idx in range(n))
    left_std = sum((value - left_mean) ** 2 for value in left_values) ** 0.5
    right_std = sum((value - right_mean) ** 2 for value in right_values) ** 0.5
    if left_std < 1e-12 or right_std < 1e-12:
        return 0.0
    return covariance / (left_std * right_std)


def _rank_corr(left: list[float], right: list[float]) -> float:
    n = min(len(left), len(right))
    if n < 5:
        return 0.0

    def ranks(values: list[float]) -> list[float]:
        indexed = sorted(enumerate(values[:n]), key=lambda item: item[1])
        output = [0.0] * n
        for rank, (idx, _) in enumerate(indexed):
            output[idx] = float(rank)
        return output

    return _pearson_corr(ranks(left), ranks(right))


def _rolling_rank_ic(values: list[float], forward_returns: list[float], *, window: int) -> list[float]:
    aligned = min(len(values), len(forward_returns))
    if aligned <= window:
        return []
    return [
        _rank_corr(values[idx - window : idx], forward_returns[idx - window : idx])
        for idx in range(window, aligned + 1)
    ]
