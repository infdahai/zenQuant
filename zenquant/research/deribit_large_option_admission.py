"""Admission review for Deribit large-option-flow research factors."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_deribit_large_option_flow_admission_review(
    *,
    audit_payload: dict[str, Any],
    audit_path: Path,
    symbol: str = "BTCUSDT",
    min_train_observations: int = 500,
    min_admissible_factor_targets: int = 1,
) -> dict[str, Any]:
    summary = dict(audit_payload.get("summary") or {})
    train_observations = int(summary.get("train_aligned_observations", 0) or 0)
    admissible_count = int(summary.get("admissible_factor_target_count", 0) or 0)
    coverage_ready = train_observations >= min_train_observations
    factor_ready = admissible_count >= min_admissible_factor_targets
    research_ready = bool(coverage_ready and factor_ready and summary.get("blocked_by") is None)
    blocked_by: str | None = None
    if not coverage_ready:
        blocked_by = "insufficient_train_observations"
    elif not factor_ready:
        blocked_by = "no_admissible_large_option_flow_factor"
    elif summary.get("blocked_by"):
        blocked_by = str(summary["blocked_by"])

    return {
        "schema_version": "deribit_large_option_flow_admission_review.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_name": "deribit_large_option_flow",
        "symbol": symbol,
        "role": "state_context_pressure_diagnostic",
        "tier_decision": "tier2_research_candidate" if research_ready else "tier1_observation_only",
        "time_granularity": "4h",
        "pit_safe": "pass",
        "coverage_ready": "pass" if coverage_ready else "conditional",
        "observation_ready": bool(summary.get("source_row_count", 0)),
        "research_ready": research_ready,
        "admit_as_secondary": research_ready,
        "admit_as_primary": False,
        "use_for_research_only": True,
        "exclude_from_promote_truth": True,
        "allowed_in_training_observation": research_ready,
        "allowed_in_candidate_explanation": research_ready,
        "direction_vote_allowed": False,
        "intent_aggregation_allowed": False,
        "position_sizing_allowed": False,
        "execution_wiring_allowed": False,
        "strategy_wiring_allowed": False,
        "risk_overlay_wiring_allowed": False,
        "feature_freeze_candidate": research_ready,
        "replay_attribution_required_before_wiring": True,
        "blocked_by": blocked_by,
        "next_action": _next_action(research_ready, blocked_by),
        "audit_path": str(audit_path),
        "features_path": audit_payload.get("features_path"),
        "audit_summary": summary,
        "feature_coverage": audit_payload.get("feature_coverage") or {},
        "top_feature_candidates": _top_feature_candidates(audit_payload),
        "recommendation": {
            "collect_continue": True,
            "use_for_research_only": True,
            "exclude_from_promote_truth": True,
            "freeze_feature_set_before_strategy_wiring": True,
            "admission_reason": _admission_reason(research_ready, blocked_by),
            "next_actions": _next_actions(research_ready, blocked_by),
        },
    }


def write_deribit_large_option_flow_admission_review(
    *,
    audit_path: Path,
    output_dir: Path = Path("results/data_source_review"),
    symbol: str = "BTCUSDT",
) -> dict[str, Any]:
    audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
    payload = build_deribit_large_option_flow_admission_review(
        audit_payload=audit_payload,
        audit_path=audit_path,
        symbol=symbol,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "deribit_large_option_flow_admission_review.json"
    md_path = output_dir / "deribit_large_option_flow_admission_review.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(build_deribit_large_option_flow_admission_markdown(payload), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "tier_decision": payload["tier_decision"],
        "research_ready": payload["research_ready"],
        "blocked_by": payload["blocked_by"],
        "next_action": payload["next_action"],
    }


def build_deribit_large_option_flow_admission_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Deribit Large Option Flow Admission Review",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- source_name: `{payload['source_name']}`",
        f"- symbol: `{payload['symbol']}`",
        f"- role: `{payload['role']}`",
        f"- tier_decision: `{payload['tier_decision']}`",
        f"- time_granularity: `{payload['time_granularity']}`",
        f"- pit_safe: `{payload['pit_safe']}`",
        f"- coverage_ready: `{payload['coverage_ready']}`",
        f"- research_ready: `{payload['research_ready']}`",
        f"- direction_vote_allowed: `{payload['direction_vote_allowed']}`",
        f"- intent_aggregation_allowed: `{payload['intent_aggregation_allowed']}`",
        f"- strategy_wiring_allowed: `{payload['strategy_wiring_allowed']}`",
        f"- risk_overlay_wiring_allowed: `{payload['risk_overlay_wiring_allowed']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
        f"- next_action: `{payload['next_action']}`",
        f"- audit_path: `{payload['audit_path']}`",
        f"- features_path: `{payload['features_path']}`",
        "",
        "## Audit Summary",
        "",
    ]
    for key, value in payload["audit_summary"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Top Feature Candidates", ""])
    for item in payload["top_feature_candidates"]:
        lines.append(
            f"- {item['name']} -> {item['target']}: rolling_rank_ic_mean=`{item['rolling_rank_ic_mean']}`, "
            f"ablation_delta=`{item['ablation_delta']}`, hit_rate=`{item['hit_rate']}`"
        )
    lines.extend(["", "## Recommendation", ""])
    for action in payload["recommendation"]["next_actions"]:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def _top_feature_candidates(audit_payload: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    factors = [dict(item) for item in audit_payload.get("factors", []) if item.get("blocked_by") is None]
    factors.sort(
        key=lambda item: (
            abs(float(item.get("rolling_rank_ic_mean", 0.0) or 0.0)),
            abs(float(item.get("ablation_delta", 0.0) or 0.0)),
        ),
        reverse=True,
    )
    keys = [
        "name",
        "target",
        "observations",
        "rolling_rank_ic_mean",
        "ablation_delta",
        "hit_rate",
        "signal_sign",
        "max_abs_factor_correlation",
    ]
    return [{key: item.get(key) for key in keys} for item in factors[:limit]]


def _next_action(research_ready: bool, blocked_by: str | None) -> str:
    if research_ready:
        return "freeze_research_feature_set_for_pressure_context_replay"
    if blocked_by == "insufficient_train_observations":
        return "backfill_more_train_windows_before_strategy_wiring"
    return "keep_as_observation_only_until_factor_gate_passes"


def _admission_reason(research_ready: bool, blocked_by: str | None) -> str:
    if research_ready:
        return (
            "Strict train-only audit found enough coverage and admissible factor-target candidates, "
            "but the source remains research-only until independence and candidate replay checks pass."
        )
    return f"Large option flow remains observation-only because admission is blocked by {blocked_by}."


def _next_actions(research_ready: bool, blocked_by: str | None) -> list[str]:
    if research_ready:
        return [
            "freeze_top_large_option_flow_factors",
            "run_source_independence_audit_against_existing_state_features",
            "run_replay_pressure_context_attribution_before_intent_or_risk_wiring",
            "do_not_use_as_primary_truth_or_direct_direction_signal",
        ]
    if blocked_by == "insufficient_train_observations":
        return ["backfill_more_train_windows", "rerun_strict_train_only_factor_audit"]
    return ["keep_collection_running", "rerun_factor_audit_after_more_data"]
