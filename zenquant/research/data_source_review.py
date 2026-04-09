"""Research-only data source admission review helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.data.deribit_status import collect_deribit_status
from zenquant.data.liquidation_pipeline import liquidation_readiness


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
    symbol: str
    reviewed_sources: list[str]
    source_tiers: dict[str, str]
    admit_as_secondary: bool
    admit_as_primary: bool
    market_evidence: list[MarketEvidence]
    repo_evidence: dict[str, Any]
    recommendation: dict[str, Any]


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
        symbol=symbol,
        reviewed_sources=["binance_liquidation", "hyperliquid_public_proxy", "merged_liquidations"],
        source_tiers={
            "binance_liquidation": "tier1_primary",
            "hyperliquid_public_proxy": "tier2_supplemental",
            "merged_liquidations": "research_input_not_primary_truth",
        },
        admit_as_secondary=True,
        admit_as_primary=False,
        market_evidence=market_evidence,
        repo_evidence=repo_evidence,
        recommendation=recommendation,
    )


def build_markdown_review(review: DataSourceAdmissionReview) -> str:
    lines = [
        "# Hyperliquid Admission Review",
        "",
        f"- generated_at: `{review.generated_at}`",
        f"- symbol: `{review.symbol}`",
        f"- admit_as_secondary: `{review.admit_as_secondary}`",
        f"- admit_as_primary: `{review.admit_as_primary}`",
        "",
        "## Source Tiers",
        "",
    ]
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
            f"- collect_continue: `{review.recommendation['collect_continue']}`",
            f"- use_for_research_only: `{review.recommendation['use_for_research_only']}`",
            f"- exclude_from_promote_truth: `{review.recommendation['exclude_from_promote_truth']}`",
            f"- admission_reason: `{review.recommendation['admission_reason']}`",
        ]
    )
    return "\n".join(lines)


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
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "hyperliquid_admission_review.json"
    md_path = output_dir / "hyperliquid_admission_review.md"
    json_path.write_text(json.dumps(asdict(review), indent=2), encoding="utf-8")
    md_path.write_text(build_markdown_review(review), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "admit_as_secondary": review.admit_as_secondary,
        "admit_as_primary": review.admit_as_primary,
        "recommendation": review.recommendation,
    }
