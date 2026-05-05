"""Mature PaulWei progression readiness checks.

This module is intentionally read-only. It turns existing research artifacts into
a deterministic go/no-go decision for the next research step, without opening
test access or mutating strategy parameters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MATURE_TARGET_POSITION_SWITCHES_PER_30D = 1.6807
MAX_POSITION_SWITCHES_PER_30D = 3.0
MIN_PROTECTIVE_REDUCE_SHARE = 0.75


def _safe_float(value: Any) -> float:
    return float(value or 0.0)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _status_blocker(payload: dict[str, Any], key: str) -> str | None:
    status = _as_dict(payload.get(key))
    blocker = status.get("blocked_by")
    return str(blocker) if blocker else None


def build_data_engineering_summary(readiness_payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize whether current state data can support promotion-grade training."""

    overall_blockers = [str(item) for item in _as_list(readiness_payload.get("overall_blockers"))]
    source_blockers = {
        "deribit": _status_blocker(readiness_payload, "deribit_status"),
        "binance_liquidation": _status_blocker(readiness_payload, "binance_liquidation_status"),
        "hyperliquid": _status_blocker(readiness_payload, "hyperliquid_status"),
        "merged_liquidation": _status_blocker(readiness_payload, "merged_liquidation_status"),
    }
    source_blockers = {key: value for key, value in source_blockers.items() if value}
    deribit = _as_dict(readiness_payload.get("deribit_status"))
    merged = _as_dict(readiness_payload.get("merged_liquidation_status"))
    hyperliquid = _as_dict(readiness_payload.get("hyperliquid_status"))
    return {
        "overall_blockers": overall_blockers,
        "source_blockers": source_blockers,
        "deribit_observation_ready": bool(deribit.get("observation_ready")),
        "deribit_research_ready": bool(deribit.get("research_ready")),
        "merged_liquidation_research_ready_min": bool(merged.get("research_ready_min")),
        "merged_liquidation_promotion_ready": bool(merged.get("promotion_ready")),
        "hyperliquid_promotion_ready": bool(hyperliquid.get("promotion_ready")),
        "promotion_ready": not overall_blockers,
        "next_actions": sorted(
            {
                str(status.get("next_action"))
                for status in (
                    _as_dict(readiness_payload.get("deribit_status")),
                    _as_dict(readiness_payload.get("deribit_block_flow_status")),
                    _as_dict(readiness_payload.get("binance_liquidation_status")),
                    _as_dict(readiness_payload.get("hyperliquid_status")),
                    _as_dict(readiness_payload.get("merged_liquidation_status")),
                )
                if status.get("next_action")
            }
        ),
    }


def build_mature_behavior_contract(
    mature_baseline_payload: dict[str, Any],
    *,
    behavior_profile_payload: dict[str, Any] | None = None,
    strict_learning_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract transferable mature-style behavior targets from local PaulWei artifacts."""

    behavior_profile = _as_dict(behavior_profile_payload)
    strict_learning = _as_dict(strict_learning_payload)
    baseline_summary = _as_dict(mature_baseline_payload.get("behavior_summary"))
    protective_exit = _as_dict(behavior_profile.get("protective_exit_profile"))
    recovery_profile = _as_dict(behavior_profile.get("recovery_profile"))
    strict_modules = [
        str(item.get("module"))
        for item in _as_list(strict_learning.get("strict_learning_modules"))
        if isinstance(item, dict) and item.get("worth_absorbing")
    ]
    targets = {
        "position_switches_per_30d": {
            "target": _safe_float(
                behavior_profile.get("position_switches_per_30d")
                or baseline_summary.get("position_switches_per_30d")
                or MATURE_TARGET_POSITION_SWITCHES_PER_30D
            ),
            "max": MAX_POSITION_SWITCHES_PER_30D,
        },
        "median_adds_per_episode": {
            "target": _safe_float(
                behavior_profile.get("median_adds_per_episode") or baseline_summary.get("median_adds_per_episode")
            ),
        },
        "median_reduces_per_episode": {
            "target": _safe_float(
                behavior_profile.get("median_reduces_per_episode") or baseline_summary.get("median_reduces_per_episode")
            ),
        },
        "protective_reduce_share": {
            "target": _safe_float(protective_exit.get("protective_reduce_share")),
            "min": MIN_PROTECTIVE_REDUCE_SHARE,
        },
        "flip_exit_share": {
            "reference": _safe_float(protective_exit.get("flip_exit_share")),
        },
        "median_recovery_days": {
            "reference": _safe_float(
                recovery_profile.get("median_recovery_days") or baseline_summary.get("median_recovery_days")
            ),
        },
    }
    required_train_only_gates = [
        "train_position_switch_cadence",
        "train_flip_reduce_fee_drag",
        "train_protective_reduce_bias_weak",
        "train_drawdown_recovery_ramp",
        "train_hostile_worst_window",
    ]
    return {
        "baseline_id": mature_baseline_payload.get("baseline_id"),
        "promotion_status": mature_baseline_payload.get("promotion_status"),
        "mapping_confidence": mature_baseline_payload.get("mapping_confidence"),
        "targets": targets,
        "execution_overrides_present": bool(mature_baseline_payload.get("execution_config_overrides")),
        "risk_overrides_present": bool(mature_baseline_payload.get("risk_config_overrides")),
        "strict_learning_modules": strict_modules,
        "unsupported_dimensions": _as_list(
            _as_dict(strict_learning.get("learning_boundaries")).get("unsupported_dimensions")
            or mature_baseline_payload.get("unsupported_dimensions")
        ),
        "required_train_only_gates": required_train_only_gates,
        "ready_for_train_only_scoring": bool(
            targets["position_switches_per_30d"]["target"]
            and targets["protective_reduce_share"]["target"]
            and mature_baseline_payload.get("execution_config_overrides")
            and mature_baseline_payload.get("risk_config_overrides")
        ),
    }


def build_recent_training_summary(framework_payload: dict[str, Any]) -> dict[str, Any]:
    """Extract latest candidate-existence and smoke-training evidence."""

    verdict = _as_dict(framework_payload.get("final_verdict"))
    diagnostics = _as_dict(framework_payload.get("train_candidate_diagnostics"))
    primary = _as_dict(diagnostics.get("primary_search_diagnostics"))
    fallback = _as_dict(diagnostics.get("fallback_search_diagnostics"))
    no_tuned_reason = diagnostics.get("no_tuned_candidate_reason") or fallback.get("no_tuned_candidate_reason")
    full_training_blockers = [str(item) for item in _as_list(framework_payload.get("full_training_blockers"))]
    no_candidate = (
        no_tuned_reason == "all_negative_primary_metric"
        or "no_tuned_candidate_from_smoke" in full_training_blockers
        or framework_payload.get("empty_shortlist_reason") == "primary_and_fallback_empty"
    )
    return {
        "created_at": framework_payload.get("created_at"),
        "best_variant": framework_payload.get("best_variant") or verdict.get("best_variant"),
        "best_score": _safe_float(framework_payload.get("best_score")),
        "candidate_ready_for_test": bool(verdict.get("candidate_ready_for_test")),
        "candidate_ready_for_handoff": bool(verdict.get("candidate_ready_for_handoff")),
        "blocked_by": verdict.get("blocked_by"),
        "full_training_decision": framework_payload.get("full_training_decision"),
        "full_training_blockers": full_training_blockers,
        "empty_shortlist_reason": framework_payload.get("empty_shortlist_reason"),
        "candidate_existence_recovered": bool(
            diagnostics.get("candidate_existence_recovered") or primary.get("candidate_existence_recovered")
        ),
        "no_tuned_candidate_reason": no_tuned_reason,
        "primary_raw_trial_count": int(primary.get("raw_trial_count") or diagnostics.get("raw_trial_count") or 0),
        "primary_eligible_trade_count_trials": int(
            primary.get("eligible_trade_count_trials") or diagnostics.get("eligible_trade_count_trials") or 0
        ),
        "primary_max_train_sharpe": _safe_float(primary.get("max_train_sharpe") or diagnostics.get("max_train_sharpe")),
        "primary_max_hostile_proxy_score": _safe_float(
            primary.get("max_hostile_proxy_score") or diagnostics.get("max_hostile_proxy_score")
        ),
        "stage1_fail_fast_reasons": _as_dict(
            primary.get("stage1_fail_fast_reasons") or diagnostics.get("stage1_fail_fast_reasons")
        ),
        "no_tuned_candidate_from_latest_report": bool(no_candidate),
    }


def _decision(
    *,
    data_summary: dict[str, Any],
    behavior_contract: dict[str, Any],
    training_summary: dict[str, Any],
) -> tuple[str, bool, bool, list[str]]:
    blockers: list[str] = []
    if data_summary["overall_blockers"]:
        blockers.append("data_not_promotion_ready")
    if not behavior_contract["ready_for_train_only_scoring"]:
        blockers.append("mature_behavior_contract_incomplete")
    if training_summary["no_tuned_candidate_from_latest_report"]:
        blockers.append("no_tuned_candidate_from_latest_report")

    if "data_not_promotion_ready" in blockers:
        return "strengthen_data_engineering_before_expanded_training", False, False, blockers
    if "mature_behavior_contract_incomplete" in blockers:
        return "complete_mature_behavior_contract_before_training", False, False, blockers
    if "no_tuned_candidate_from_latest_report" in blockers:
        return "run_contract_limited_train_only_smoke", False, True, blockers
    return "prepare_promotion_readiness_review", False, False, blockers


def build_mature_progression_payload(
    *,
    data_readiness_payload: dict[str, Any],
    framework_payload: dict[str, Any],
    mature_baseline_payload: dict[str, Any],
    behavior_profile_payload: dict[str, Any] | None = None,
    strict_learning_payload: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the next-step recommendation for mature PaulWei alignment."""

    data_summary = build_data_engineering_summary(data_readiness_payload)
    behavior_contract = build_mature_behavior_contract(
        mature_baseline_payload,
        behavior_profile_payload=behavior_profile_payload,
        strict_learning_payload=strict_learning_payload,
    )
    training_summary = build_recent_training_summary(framework_payload)
    decision, expanded_training_allowed, small_smoke_allowed, blockers = _decision(
        data_summary=data_summary,
        behavior_contract=behavior_contract,
        training_summary=training_summary,
    )
    return {
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
        "decision": decision,
        "selection_scope": "research_planning_only",
        "test_access_required": False,
        "test_evaluated": False,
        "expanded_training_allowed": expanded_training_allowed,
        "small_train_only_smoke_allowed": small_smoke_allowed,
        "blockers": blockers,
        "data_engineering": data_summary,
        "mature_behavior_contract": behavior_contract,
        "recent_training": training_summary,
        "recommended_work_order": [
            "close_promotion_data_blockers",
            "codify_mature_behavior_gates",
            "run_contract_limited_train_only_smoke",
            "use_dev_as_diagnostic_only",
        ],
    }


def build_mature_progression_markdown(payload: dict[str, Any]) -> str:
    """Render a concise handoff report for the product owner."""

    data = payload["data_engineering"]
    behavior = payload["mature_behavior_contract"]
    training = payload["recent_training"]
    switch_target = behavior["targets"]["position_switches_per_30d"]["target"]
    protective_target = behavior["targets"]["protective_reduce_share"]["target"]
    lines = [
        "# Mature PaulWei Progression Readiness",
        "",
        f"- decision: `{payload['decision']}`",
        f"- expanded_training_allowed: `{payload['expanded_training_allowed']}`",
        f"- small_train_only_smoke_allowed: `{payload['small_train_only_smoke_allowed']}`",
        f"- test_evaluated: `{payload['test_evaluated']}`",
        f"- blockers: `{payload['blockers']}`",
        "",
        "## Data Engineering",
        "",
        f"- overall_blockers: `{data['overall_blockers']}`",
        f"- source_blockers: `{data['source_blockers']}`",
        f"- next_actions: `{data['next_actions']}`",
        "",
        "## Mature Behavior Contract",
        "",
        f"- baseline_id: `{behavior['baseline_id']}`",
        f"- target_position_switches_per_30d: `{switch_target}`",
        f"- protective_reduce_share_target: `{protective_target}`",
        f"- required_train_only_gates: `{behavior['required_train_only_gates']}`",
        "",
        "## Recent Training Evidence",
        "",
        f"- best_variant: `{training['best_variant']}`",
        f"- no_tuned_candidate_reason: `{training['no_tuned_candidate_reason']}`",
        f"- empty_shortlist_reason: `{training['empty_shortlist_reason']}`",
        f"- primary_raw_trial_count: `{training['primary_raw_trial_count']}`",
        f"- primary_max_train_sharpe: `{training['primary_max_train_sharpe']}`",
        f"- primary_max_hostile_proxy_score: `{training['primary_max_hostile_proxy_score']}`",
        "",
        "## Work Order",
        "",
    ]
    lines.extend(f"- `{item}`" for item in payload["recommended_work_order"])
    return "\n".join(lines) + "\n"
