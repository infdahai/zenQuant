"""Research-only data source admission review helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.data.deribit_block_flow import collect_deribit_block_flow_status
from zenquant.data.deribit_status import collect_deribit_status
from zenquant.data.liquidation_pipeline import liquidation_readiness
from zenquant.ingestor.macro import read_btc_mvrv_metadata


@dataclass(slots=True, frozen=True)
class MarketEvidence:
    source_name: str
    source_url: str
    observed_at: str
    summary: str
    metrics: dict[str, Any]


@dataclass(slots=True, frozen=True)
class DataSourceAdmissionReview:
    generated_at: str
    source_name: str
    symbol: str
    source_role_candidate: list[str]
    tier_decision: str
    time_granularity: str
    pit_safe: str
    coverage_ready: str
    observation_ready: bool
    research_ready: bool
    admit_as_secondary: bool
    admit_as_primary: bool
    use_for_research_only: bool
    exclude_from_promote_truth: bool
    economic_hypothesis: str
    incremental_value_evidence: str
    blocked_by: str | None
    next_action: str
    reviewed_sources: list[str]
    source_tiers: dict[str, str]
    market_evidence: list[MarketEvidence]
    repo_evidence: dict[str, Any]
    recommendation: dict[str, Any]


def _write_review(review: DataSourceAdmissionReview, output_dir: Path, stem: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(asdict(review), indent=2), encoding="utf-8")
    md_path.write_text(build_markdown_review(review), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "tier_decision": review.tier_decision,
        "admit_as_secondary": review.admit_as_secondary,
        "admit_as_primary": review.admit_as_primary,
        "recommendation": review.recommendation,
    }


def _review_title(review: DataSourceAdmissionReview) -> str:
    return f"# {review.source_name} Admission Review"


def build_markdown_review(review: DataSourceAdmissionReview) -> str:
    lines = [
        _review_title(review),
        "",
        f"- generated_at: `{review.generated_at}`",
        f"- source_name: `{review.source_name}`",
        f"- symbol: `{review.symbol}`",
        f"- tier_decision: `{review.tier_decision}`",
        f"- time_granularity: `{review.time_granularity}`",
        f"- pit_safe: `{review.pit_safe}`",
        f"- coverage_ready: `{review.coverage_ready}`",
        f"- observation_ready: `{review.observation_ready}`",
        f"- research_ready: `{review.research_ready}`",
        f"- admit_as_secondary: `{review.admit_as_secondary}`",
        f"- admit_as_primary: `{review.admit_as_primary}`",
        f"- use_for_research_only: `{review.use_for_research_only}`",
        f"- exclude_from_promote_truth: `{review.exclude_from_promote_truth}`",
        f"- blocked_by: `{review.blocked_by}`",
        f"- next_action: `{review.next_action}`",
        "",
        "## Source Role Candidate",
        "",
    ]
    lines.extend(f"- `{role}`" for role in review.source_role_candidate)
    lines.extend(
        [
            "",
            "## Economic Hypothesis",
            "",
            f"- {review.economic_hypothesis}",
            "",
            "## Incremental Value",
            "",
            f"- {review.incremental_value_evidence}",
            "",
            "## Source Tiers",
            "",
        ]
    )
    for name, tier in review.source_tiers.items():
        lines.append(f"- {name}: `{tier}`")
    lines.extend(["", "## Market Evidence", ""])
    for evidence in review.market_evidence:
        lines.extend(
            [
                f"### {evidence.source_name}",
                "",
                f"- observed_at: `{evidence.observed_at}`",
                f"- source_url: `{evidence.source_url}`",
                f"- summary: `{evidence.summary}`",
                f"- metrics: `{evidence.metrics}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Recommendation",
            "",
            f"- collect_continue: `{review.recommendation.get('collect_continue')}`",
            f"- use_for_research_only: `{review.recommendation.get('use_for_research_only')}`",
            f"- exclude_from_promote_truth: `{review.recommendation.get('exclude_from_promote_truth')}`",
            f"- admission_reason: `{review.recommendation.get('admission_reason')}`",
        ]
    )
    return "\n".join(lines)


def build_hyperliquid_admission_review(
    *,
    symbol: str,
    binance_root: Path,
    merged_root: Path,
    hyperliquid_root: Path,
    deribit_root: Path,
) -> DataSourceAdmissionReview:
    merged_readiness = liquidation_readiness(
        binance_root,
        symbol,
        liquidation_dir=merged_root / "aggregated",
    )
    binance_readiness = liquidation_readiness(binance_root, symbol)
    deribit_status = asdict(collect_deribit_status(deribit_root, "BTC"))

    market_evidence = [
        MarketEvidence(
            source_name="CoinGecko Binance Futures",
            source_url="https://www.coingecko.com/en/exchanges/binance_futures",
            observed_at="2026-04-09",
            summary="Binance Futures remains the dominant centralized perpetual venue and primary truth candidate.",
            metrics={
                "binance_24h_volume_usd": 48_125_796_012.97,
                "binance_24h_open_interest_usd": 21_348_846_562.08,
            },
        ),
        MarketEvidence(
            source_name="CoinMarketCap Hyperliquid share article",
            source_url="https://coinmarketcap.com/academy/article/hyperliquid-hits-near-6percent-perps-share-with-dollar200b-monthly-volume",
            observed_at="2026-04-09",
            summary="Hyperliquid is no longer peripheral and has enough market presence to justify secondary collection.",
            metrics={
                "hyperliquid_global_perps_share_pct": 5.9,
                "hyperliquid_monthly_volume_usd": 200_000_000_000.0,
            },
        ),
        MarketEvidence(
            source_name="CoinGecko 2025 annual industry report",
            source_url="https://assets.coingecko.com/reports/2025/CoinGecko-2025-Annual-Crypto-Industry-Report.pdf",
            observed_at="2026-04-09",
            summary="Binance retained the lead across perp venues in the latest annual industry report, supporting Binance as primary truth.",
            metrics={
                "binance_primary_truth_supported": True,
                "hyperliquid_secondary_only_supported": True,
            },
        ),
    ]

    repo_evidence = {
        "binance_liquidation_primary_supported": True,
        "binance_liquidation_data_ready": binance_readiness.data_ready,
        "hyperliquid_collector_exists": (hyperliquid_root / "_collector_status.json").exists()
        or (hyperliquid_root / "raw").exists(),
        "hyperliquid_merge_pipeline_exists": True,
        "merged_liquidation_data_ready": merged_readiness.data_ready,
        "merged_liquidation_promotion_ready": merged_readiness.promotion_ready,
        "merged_liquidation_blocked_by": merged_readiness.blocked_by,
        "deribit_status": {
            "enabled": deribit_status["enabled"],
            "observation_ready": deribit_status["observation_ready"],
            "research_ready": deribit_status["research_ready"],
        },
    }

    recommendation = {
        "collect_continue": True,
        "use_for_research_only": True,
        "exclude_from_promote_truth": True,
        "admission_reason": (
            "Hyperliquid meets the threshold for secondary collection because it has material perpetual market share "
            "and a meaningful onchain-perps footprint, but it does not displace Binance as the primary venue truth."
        ),
        "source_mix_policy": {
            "binance_liquidation": "tier1_primary",
            "hyperliquid_public_proxy": "tier2_supplemental",
            "merged_liquidations": "research_input_not_primary_truth",
        },
        "next_actions": [
            "keep_collecting_hyperliquid",
            "keep_binance_as_primary_truth",
            "continue_readiness_checks",
            "do_not_promote_hyperliquid_as_primary_until_stricter_criteria_are_met",
        ],
    }

    return DataSourceAdmissionReview(
        generated_at=datetime.now(UTC).isoformat(),
        source_name="hyperliquid_public_proxy",
        symbol=symbol,
        source_role_candidate=["state", "tactical_confirmation"],
        tier_decision="tier2_research_only_feature",
        time_granularity="4h",
        pit_safe="conditional",
        coverage_ready="conditional" if not merged_readiness.promotion_ready else "pass",
        observation_ready=bool(repo_evidence["hyperliquid_collector_exists"]),
        research_ready=merged_readiness.data_ready,
        admit_as_secondary=True,
        admit_as_primary=False,
        use_for_research_only=True,
        exclude_from_promote_truth=True,
        economic_hypothesis="Supplemental public-perps liquidation proxy can improve hostile-state awareness without replacing venue-native truth.",
        incremental_value_evidence="Adds non-Binance stress perspective, but current evidence only supports research-side supplementation.",
        blocked_by=merged_readiness.blocked_by,
        next_action="continue_readiness_checks",
        reviewed_sources=["binance_liquidation", "hyperliquid_public_proxy", "merged_liquidations"],
        source_tiers={
            "binance_liquidation": "tier1_primary",
            "hyperliquid_public_proxy": "tier2_supplemental",
            "merged_liquidations": "research_input_not_primary_truth",
        },
        market_evidence=market_evidence,
        repo_evidence=repo_evidence,
        recommendation=recommendation,
    )


def write_hyperliquid_admission_review(
    *,
    symbol: str,
    binance_root: Path,
    merged_root: Path,
    hyperliquid_root: Path,
    deribit_root: Path,
    output_dir: Path = Path("results/data_source_review"),
) -> dict[str, Any]:
    review = build_hyperliquid_admission_review(
        symbol=symbol,
        binance_root=binance_root,
        merged_root=merged_root,
        hyperliquid_root=hyperliquid_root,
        deribit_root=deribit_root,
    )
    return _write_review(review, output_dir, "hyperliquid_admission_review")


def build_deribit_block_flow_admission_review(
    *,
    symbol: str,
    deribit_block_flow_root: Path,
) -> DataSourceAdmissionReview:
    status = asdict(collect_deribit_block_flow_status(deribit_block_flow_root, "BTC"))
    research_ready = bool(status["observation_ready"])
    blocked_by = status["blocked_by"]
    market_evidence = [
        MarketEvidence(
            source_name="Deribit public block/combo flow",
            source_url="https://docs.deribit.com/",
            observed_at="2026-04-18",
            summary=(
                "Public Deribit block/combo options flow is useful as an observation and research-side "
                "positioning proxy, but it is not venue-native account truth."
            ),
            metrics={
                "time_granularity": "1d",
                "row_count_24h": status["row_count_24h"],
                "block_rfq_row_count_7d": status["block_rfq_row_count_7d"],
            },
        )
    ]
    repo_evidence = {
        "status": status,
        "allowed_roles": ["observation", "state", "tactical_confirmation"],
        "forbidden_roles": ["primary_truth", "account_level_position_reconstruction"],
    }
    recommendation = {
        "collect_continue": True,
        "use_for_research_only": True,
        "exclude_from_promote_truth": True,
        "admission_reason": (
            "Public block/combo flow can inform observation and research-side state features, "
            "but it cannot be promoted to primary truth because the public feed does not expose full account context."
        ),
        "next_actions": [
            "keep_block_flow_collector_running",
            "aggregate_daily_block_flow_features",
            "run_independence_audit_before_state_feature_promotion",
        ],
    }
    return DataSourceAdmissionReview(
        generated_at=datetime.now(UTC).isoformat(),
        source_name="deribit_block_flow",
        symbol=symbol,
        source_role_candidate=["observation", "state", "tactical_confirmation"],
        tier_decision="tier1_observation_only" if not research_ready else "tier2_research_only_feature",
        time_granularity="1d",
        pit_safe="pass",
        coverage_ready="pass" if research_ready else "conditional",
        observation_ready=bool(status["enabled"]),
        research_ready=research_ready,
        admit_as_secondary=research_ready,
        admit_as_primary=False,
        use_for_research_only=True,
        exclude_from_promote_truth=True,
        economic_hypothesis=(
            "Large public options block/combo flow may add tactical positioning context during hostile and expansion regimes, "
            "but it should enter only as a secondary research feature."
        ),
        incremental_value_evidence=(
            "Expected to complement options surface snapshots with visible flow, but it must still prove independence from "
            "existing state features before entering the signal stack."
        ),
        blocked_by=blocked_by,
        next_action="refresh_deribit_block_flow_daily" if research_ready else "stabilize_deribit_block_flow_collection",
        reviewed_sources=["deribit_block_flow"],
        source_tiers={
            "deribit_block_flow": "tier2_research_only_feature" if research_ready else "tier1_observation_only"
        },
        market_evidence=market_evidence,
        repo_evidence=repo_evidence,
        recommendation=recommendation,
    )


def build_mvrv_admission_review(
    *,
    symbol: str,
    macro_root: Path,
) -> DataSourceAdmissionReview:
    metadata = read_btc_mvrv_metadata(macro_root)
    data_path = macro_root / "btc_mvrv" / "data.parquet"
    research_ready = data_path.exists()
    blocked_by = None if research_ready else "btc_mvrv_missing"
    confidence = metadata["btc_mvrv_source_confidence"]
    market_evidence = [
        MarketEvidence(
            source_name="MVRV cycle context",
            source_url="https://cryptoquant.com/",
            observed_at="2026-04-18",
            summary="BTC MVRV is treated as a slow cycle-state prior, not an execution trigger.",
            metrics={
                "time_granularity": "1d",
                "intended_roles": ["regime_prior", "risk_filter", "explainability_context"],
            },
        )
    ]
    repo_evidence = {
        "data_path_exists": research_ready,
        "metadata": metadata,
        "allowed_roles": ["regime_prior", "risk_filter", "explainability_context"],
        "forbidden_roles": ["short_term_trigger", "execution_signal", "intraday_tactical_feature"],
    }
    recommendation = {
        "collect_continue": True,
        "use_for_research_only": True,
        "exclude_from_promote_truth": True,
        "admission_reason": (
            "MVRV is a slow on-chain macro series suitable for daily regime and risk context, but not for short-term execution."
        ),
        "next_actions": [
            "keep_daily_mvrv_updated",
            "evaluate_regime_filter_value_on_dev",
            "do_not_use_mvrv_as_intraday_trigger",
        ],
    }
    return DataSourceAdmissionReview(
        generated_at=datetime.now(UTC).isoformat(),
        source_name="btc_mvrv",
        symbol=symbol,
        source_role_candidate=["regime_prior", "risk_filter", "explainability_context"],
        tier_decision="tier2_research_only_feature",
        time_granularity="1d",
        pit_safe="pass",
        coverage_ready="pass" if research_ready else "fail",
        observation_ready=research_ready,
        research_ready=research_ready,
        admit_as_secondary=True,
        admit_as_primary=False,
        use_for_research_only=True,
        exclude_from_promote_truth=True,
        economic_hypothesis="Slow on-chain valuation extremes can improve regime bias and de-risking discipline across cycle phases.",
        incremental_value_evidence=(
            "Expected to add slow-cycle context beyond venue microstructure features; must still prove dev/hostile robustness before promotion."
        ),
        blocked_by=blocked_by,
        next_action="refresh_mvrv_daily" if research_ready else "import_btc_mvrv_history",
        reviewed_sources=["btc_mvrv"],
        source_tiers={"btc_mvrv": "tier2_research_only_feature"},
        market_evidence=market_evidence,
        repo_evidence=repo_evidence,
        recommendation=recommendation,
    )


def write_mvrv_admission_review(
    *,
    symbol: str,
    macro_root: Path = Path("data/raw/macro"),
    output_dir: Path = Path("results/data_source_review"),
) -> dict[str, Any]:
    review = build_mvrv_admission_review(symbol=symbol, macro_root=macro_root)
    return _write_review(review, output_dir, "mvrv_admission_review")


def write_deribit_block_flow_admission_review(
    *,
    symbol: str,
    deribit_block_flow_root: Path = Path("data/raw/deribit_block_flow"),
    output_dir: Path = Path("results/data_source_review"),
) -> dict[str, Any]:
    review = build_deribit_block_flow_admission_review(
        symbol=symbol,
        deribit_block_flow_root=deribit_block_flow_root,
    )
    return _write_review(review, output_dir, "deribit_block_flow_admission_review")


def load_source_admission_review(
    source_name: str, *, review_dir: Path = Path("results/data_source_review")
) -> dict[str, Any]:
    path = review_dir / f"{source_name}_admission_review.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
