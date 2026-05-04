"""Research-only pressure context attribution for frozen top factors."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

PRESSURE_CONTEXT_SCHEMA_VERSION = "pressure_context_attribution.v1"
PRESSURE_CONTEXT_ROLE = "state_context_pressure_diagnostic"
REDUCE_FLIP_ACTIONS = {"reduce", "flip"}


def build_pressure_context_attribution(
    *,
    attribution_payload: dict[str, Any],
    admission_payload: dict[str, Any],
    independence_payload: dict[str, Any],
    features_path: Path | None = None,
    features: pd.DataFrame | None = None,
    top_n: int = 8,
    max_lag_hours: float = 4.25,
    min_context_rows: int = 30,
    min_high_pressure_event_count: int = 10,
    high_quantile: float = 0.75,
    low_quantile: float = 0.25,
) -> dict[str, Any]:
    """Explain replay reduce/flip cadence with frozen factors without mutating strategy state."""

    frozen_factors = _frozen_factors(admission_payload, limit=top_n)
    feature_frame = _load_features(features=features, features_path=features_path)
    blockers = _contract_blockers(admission_payload, independence_payload)
    if feature_frame.empty:
        blockers.append("pressure_feature_frame_missing")
    if not frozen_factors:
        blockers.append("frozen_factor_set_empty")

    window_summaries: list[dict[str, Any]] = []
    for run in _iter_variant_runs(attribution_payload):
        window_summaries.append(
            _summarize_variant_run(
                run,
                feature_frame=feature_frame,
                frozen_factors=frozen_factors,
                max_lag_hours=max_lag_hours,
                min_context_rows=min_context_rows,
                min_high_pressure_event_count=min_high_pressure_event_count,
                high_quantile=high_quantile,
                low_quantile=low_quantile,
            )
        )

    if not window_summaries:
        blockers.append("decision_attribution_ledger_missing")
    if not any(item["feature_overlap_rows"] >= min_context_rows for item in window_summaries):
        blockers.append("insufficient_pressure_context_overlap")

    aggregate = _aggregate_summaries(window_summaries)
    replay_explains_cadence = _replay_explains_cadence(
        aggregate,
        min_high_pressure_event_count=min_high_pressure_event_count,
    )
    if not replay_explains_cadence:
        blockers.append("reduce_flip_cadence_not_explained")

    return {
        "schema_version": PRESSURE_CONTEXT_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "source_name": str(admission_payload.get("source_name") or "unknown"),
        "role": PRESSURE_CONTEXT_ROLE,
        "evaluation_role": "development",
        "test_evaluated": False,
        "train_search_executed": False,
        "optuna_search_executed": False,
        "strategy_mutation_allowed": False,
        "direction_vote_allowed": False,
        "intent_aggregation_allowed": False,
        "position_sizing_allowed": False,
        "risk_overlay_wiring_allowed": False,
        "execution_wiring_allowed": False,
        "allowed_outputs": ["training_observation", "candidate_explanation", "replay_attribution"],
        "promotion_truth_allowed": False,
        "features_path": str(features_path or admission_payload.get("features_path") or ""),
        "frozen_factor_count": len(frozen_factors),
        "frozen_factors": frozen_factors,
        "context_gate": {
            "max_lag_hours": max_lag_hours,
            "min_context_rows": min_context_rows,
            "min_high_pressure_event_count": min_high_pressure_event_count,
            "high_quantile": high_quantile,
            "low_quantile": low_quantile,
            "min_high_pressure_lift": 1.25,
            "min_high_pressure_event_coverage": 0.15,
        },
        "contract": {
            "admission_research_ready": bool(admission_payload.get("research_ready")),
            "independence_blocked_by": independence_payload.get("blocked_by"),
            "allowed_in_pressure_context": bool(independence_payload.get("allowed_in_pressure_context")),
            "allowed_in_state_features": bool(independence_payload.get("allowed_in_state_features")),
            "use_for_research_only": bool(admission_payload.get("use_for_research_only", True)),
        },
        "window_summaries": window_summaries,
        "aggregate": aggregate,
        "replay_explains_reduce_flip_cadence": replay_explains_cadence,
        "blocked_by": sorted(set(blockers)) or None,
        "next_action": _next_action(blockers, replay_explains_cadence),
    }


def build_pressure_context_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Pressure Context Attribution",
        "",
        f"- created_at: `{payload['created_at']}`",
        f"- source_name: `{payload['source_name']}`",
        f"- role: `{payload['role']}`",
        f"- test_evaluated: `{payload['test_evaluated']}`",
        f"- strategy_mutation_allowed: `{payload['strategy_mutation_allowed']}`",
        f"- direction_vote_allowed: `{payload['direction_vote_allowed']}`",
        f"- intent_aggregation_allowed: `{payload['intent_aggregation_allowed']}`",
        f"- risk_overlay_wiring_allowed: `{payload['risk_overlay_wiring_allowed']}`",
        f"- replay_explains_reduce_flip_cadence: `{payload['replay_explains_reduce_flip_cadence']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
        f"- next_action: `{payload['next_action']}`",
        f"- features_path: `{payload['features_path']}`",
        "",
        "## Frozen Factors",
        "",
    ]
    for factor in payload["frozen_factors"]:
        lines.append(
            f"- {factor['name']} -> {factor['target']}: sign=`{factor['signal_sign']}`, "
            f"rank_ic=`{factor.get('rolling_rank_ic_mean')}`, ablation=`{factor.get('ablation_delta')}`"
        )
    lines.extend(["", "## Aggregate Matches", ""])
    for match in payload["aggregate"].get("top_matches", [])[:12]:
        lines.append(
            f"- {match['variant']} / {match['window_label']} / {match['factor']}: "
            f"target_lift=`{match['target_reduce_flip_high_lift']}`, "
            f"execution_lift=`{match['execution_reduce_flip_high_lift']}`, "
            f"coverage=`{match['reduce_flip_event_coverage']}`"
        )
    lines.extend(["", "## Window Coverage", ""])
    for item in payload["window_summaries"]:
        lines.append(
            f"- {item['variant']} {item['window_label']}: rows=`{item['decision_rows']}`, "
            f"feature_overlap_rows=`{item['feature_overlap_rows']}`, "
            f"target_reduce_flip_events=`{item['target_reduce_flip_events']}`, "
            f"execution_reduce_flip_events=`{item['execution_reduce_flip_events']}`"
        )
    return "\n".join(lines) + "\n"


def _frozen_factors(admission_payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    factors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in admission_payload.get("top_feature_candidates") or []:
        name = str(item.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        factors.append(
            {
                "name": name,
                "target": item.get("target"),
                "signal_sign": _safe_float(item.get("signal_sign"), default=1.0) or 1.0,
                "rolling_rank_ic_mean": item.get("rolling_rank_ic_mean"),
                "ablation_delta": item.get("ablation_delta"),
                "hit_rate": item.get("hit_rate"),
            }
        )
        if len(factors) >= limit:
            break
    return factors


def _load_features(*, features: pd.DataFrame | None, features_path: Path | None) -> pd.DataFrame:
    if features is not None:
        frame = features.copy()
    elif features_path is not None and features_path.exists():
        frame = pd.read_parquet(features_path)
    else:
        return pd.DataFrame()
    if "bar_close_ts" not in frame.columns:
        return pd.DataFrame()
    frame = frame.copy()
    frame["bar_close_ts"] = pd.to_datetime(frame["bar_close_ts"], utc=True).astype("datetime64[ns, UTC]")
    return frame.sort_values("bar_close_ts").drop_duplicates(subset=["bar_close_ts"])


def _contract_blockers(admission_payload: dict[str, Any], independence_payload: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not admission_payload.get("research_ready"):
        blockers.append("admission_not_research_ready")
    if independence_payload.get("blocked_by"):
        blockers.append(f"independence_{independence_payload['blocked_by']}")
    if not independence_payload.get("allowed_in_pressure_context", False):
        blockers.append("pressure_context_not_allowed")
    return blockers


def _iter_variant_runs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for window in payload.get("windows") or []:
        label = str(window.get("window") or window.get("window_label") or "")
        for variant in window.get("variants") or []:
            ledger = variant.get("decision_attribution_ledger") or {}
            runs.append(
                {
                    "window_label": label,
                    "variant": str(variant.get("variant") or ""),
                    "ledger": ledger,
                    "metrics": variant.get("metrics") or {},
                }
            )
    for run in payload.get("window_reports") or []:
        runs.append(
            {
                "window_label": str(run.get("window_label") or ""),
                "variant": str(run.get("variant") or ""),
                "ledger": run.get("decision_attribution_ledger") or {},
                "metrics": run.get("metrics") or {},
            }
        )
    return runs


def _summarize_variant_run(
    run: dict[str, Any],
    *,
    feature_frame: pd.DataFrame,
    frozen_factors: list[dict[str, Any]],
    max_lag_hours: float,
    min_context_rows: int,
    min_high_pressure_event_count: int,
    high_quantile: float,
    low_quantile: float,
) -> dict[str, Any]:
    decision_rows = list((run.get("ledger") or {}).get("decision_rows") or [])
    attached = _attach_pressure_features(
        decision_rows,
        feature_frame=feature_frame,
        factor_names=[item["name"] for item in frozen_factors],
        max_lag_hours=max_lag_hours,
    )
    factor_summaries = [
        _factor_summary(
            attached,
            factor=factor,
            min_context_rows=min_context_rows,
            min_high_pressure_event_count=min_high_pressure_event_count,
            high_quantile=high_quantile,
            low_quantile=low_quantile,
        )
        for factor in frozen_factors
    ]
    factor_summaries.sort(key=_factor_summary_sort_key, reverse=True)
    return {
        "window_label": run["window_label"],
        "variant": run["variant"],
        "metrics": _metrics_summary(run.get("metrics") or {}),
        "decision_rows": len(decision_rows),
        "feature_overlap_rows": int(attached["pressure_context_available"].sum()) if not attached.empty else 0,
        "target_reduce_flip_events": int(attached["target_reduce_flip_event"].sum()) if not attached.empty else 0,
        "execution_reduce_flip_events": int(attached["execution_reduce_flip_event"].sum()) if not attached.empty else 0,
        "factor_summaries": factor_summaries,
        "top_factor_matches": factor_summaries[:5],
    }


def _attach_pressure_features(
    rows: list[dict[str, Any]],
    *,
    feature_frame: pd.DataFrame,
    factor_names: list[str],
    max_lag_hours: float,
) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).copy()
    if "cutoff_time_ns" not in frame.columns:
        return pd.DataFrame()
    frame["decision_ts"] = pd.to_datetime(
        pd.to_numeric(frame["cutoff_time_ns"], errors="coerce"),
        unit="ns",
        utc=True,
    ).astype("datetime64[ns, UTC]")
    frame = frame.dropna(subset=["decision_ts"]).sort_values("decision_ts")
    usable_factors = [name for name in factor_names if name in feature_frame.columns]
    if not usable_factors:
        frame["pressure_context_available"] = False
        frame["target_reduce_flip_event"] = frame.apply(_target_reduce_flip_event, axis=1)
        frame["execution_reduce_flip_event"] = frame.apply(_execution_reduce_flip_event, axis=1)
        return frame
    features = feature_frame[["bar_close_ts", *usable_factors]].sort_values("bar_close_ts")
    merged = pd.merge_asof(
        frame,
        features,
        left_on="decision_ts",
        right_on="bar_close_ts",
        direction="backward",
        tolerance=pd.Timedelta(hours=max_lag_hours),
    )
    merged["pressure_context_available"] = merged["bar_close_ts"].notna()
    merged["target_reduce_flip_event"] = merged.apply(_target_reduce_flip_event, axis=1)
    merged["execution_reduce_flip_event"] = merged.apply(_execution_reduce_flip_event, axis=1)
    return merged


def _factor_summary(
    frame: pd.DataFrame,
    *,
    factor: dict[str, Any],
    min_context_rows: int,
    min_high_pressure_event_count: int,
    high_quantile: float,
    low_quantile: float,
) -> dict[str, Any]:
    name = factor["name"]
    if frame.empty or name not in frame.columns:
        return _empty_factor_summary(factor, blocked_by="factor_missing_from_context_features")
    values = pd.to_numeric(frame[name], errors="coerce") * _safe_float(factor.get("signal_sign"), default=1.0)
    valid = frame[values.notna() & frame["pressure_context_available"]].copy()
    values = values.loc[valid.index]
    if len(valid) < min_context_rows:
        return _empty_factor_summary(
            factor,
            observations=len(valid),
            blocked_by="insufficient_pressure_context_rows",
        )

    high_threshold = float(values.quantile(high_quantile))
    low_threshold = float(values.quantile(low_quantile))
    high_mask = values >= high_threshold
    low_mask = values <= low_threshold
    target_events = valid["target_reduce_flip_event"].astype(bool)
    execution_events = valid["execution_reduce_flip_event"].astype(bool)
    combined_events = target_events | execution_events
    high_event_count = int(combined_events[high_mask].sum())
    total_event_count = int(combined_events.sum())
    blocked_by = None if high_event_count >= min_high_pressure_event_count else "insufficient_high_pressure_events"
    return {
        "factor": name,
        "target": factor.get("target"),
        "signal_sign": factor.get("signal_sign"),
        "observations": len(valid),
        "high_threshold": _round(high_threshold),
        "low_threshold": _round(low_threshold),
        "target_reduce_flip_base_rate": _rate(target_events),
        "target_reduce_flip_event_count": int(target_events.sum()),
        "target_reduce_flip_high_event_count": int(target_events[high_mask].sum()),
        "target_reduce_flip_high_rate": _rate(target_events[high_mask]),
        "target_reduce_flip_low_rate": _rate(target_events[low_mask]),
        "target_reduce_flip_high_lift": _lift(_rate(target_events[high_mask]), _rate(target_events)),
        "execution_reduce_flip_base_rate": _rate(execution_events),
        "execution_reduce_flip_event_count": int(execution_events.sum()),
        "execution_reduce_flip_high_event_count": int(execution_events[high_mask].sum()),
        "execution_reduce_flip_high_rate": _rate(execution_events[high_mask]),
        "execution_reduce_flip_low_rate": _rate(execution_events[low_mask]),
        "execution_reduce_flip_high_lift": _lift(_rate(execution_events[high_mask]), _rate(execution_events)),
        "combined_reduce_flip_event_count": total_event_count,
        "high_pressure_reduce_flip_event_count": high_event_count,
        "reduce_flip_event_coverage": _event_coverage(combined_events, high_mask),
        "high_pressure_rows": int(high_mask.sum()),
        "low_pressure_rows": int(low_mask.sum()),
        "blocked_by": blocked_by,
    }


def _empty_factor_summary(
    factor: dict[str, Any],
    *,
    observations: int = 0,
    blocked_by: str,
) -> dict[str, Any]:
    return {
        "factor": factor["name"],
        "target": factor.get("target"),
        "signal_sign": factor.get("signal_sign"),
        "observations": observations,
        "target_reduce_flip_high_lift": None,
        "execution_reduce_flip_high_lift": None,
        "reduce_flip_event_coverage": 0.0,
        "blocked_by": blocked_by,
    }


def _aggregate_summaries(window_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    top_matches: list[dict[str, Any]] = []
    total_rows = 0
    overlap_rows = 0
    for window in window_summaries:
        total_rows += int(window.get("decision_rows", 0) or 0)
        overlap_rows += int(window.get("feature_overlap_rows", 0) or 0)
        for summary in window.get("factor_summaries") or []:
            if summary.get("blocked_by") is not None:
                continue
            top_matches.append(
                {
                    "window_label": window["window_label"],
                    "variant": window["variant"],
                    "factor": summary["factor"],
                    "target_reduce_flip_high_lift": summary.get("target_reduce_flip_high_lift"),
                    "execution_reduce_flip_high_lift": summary.get("execution_reduce_flip_high_lift"),
                    "reduce_flip_event_coverage": summary.get("reduce_flip_event_coverage"),
                    "high_pressure_reduce_flip_event_count": summary.get("high_pressure_reduce_flip_event_count"),
                    "observations": summary.get("observations"),
                }
            )
    top_matches.sort(key=_match_sort_key, reverse=True)
    return {
        "window_count": len(window_summaries),
        "decision_rows": total_rows,
        "feature_overlap_rows": overlap_rows,
        "feature_overlap_rate": _round(overlap_rows / total_rows) if total_rows else 0.0,
        "top_matches": top_matches[:20],
    }


def _replay_explains_cadence(aggregate: dict[str, Any], *, min_high_pressure_event_count: int) -> bool:
    for match in aggregate.get("top_matches") or []:
        coverage = _safe_float(match.get("reduce_flip_event_coverage"))
        target_lift = _safe_float(match.get("target_reduce_flip_high_lift"))
        execution_lift = _safe_float(match.get("execution_reduce_flip_high_lift"))
        high_event_count = int(match.get("high_pressure_reduce_flip_event_count", 0) or 0)
        if (
            high_event_count >= min_high_pressure_event_count
            and coverage >= 0.15
            and max(target_lift, execution_lift) >= 1.25
        ):
            return True
    return False


def _next_action(blockers: list[str], replay_explains_cadence: bool) -> str:
    hard_blockers = {
        "admission_not_research_ready",
        "pressure_context_not_allowed",
        "pressure_feature_frame_missing",
        "frozen_factor_set_empty",
        "decision_attribution_ledger_missing",
        "insufficient_pressure_context_overlap",
    }
    if any(blocker in hard_blockers or blocker.startswith("independence_") for blocker in blockers):
        return "repair_pressure_context_inputs_before_replay_review"
    if replay_explains_cadence:
        return "review_pressure_context_for_explanation_only_do_not_wire"
    return "keep_as_frozen_context_observation_and_collect_more_replay_evidence"


def _target_reduce_flip_event(row: Any) -> bool:
    return str(row.get("target_action") or "") in REDUCE_FLIP_ACTIONS


def _execution_reduce_flip_event(row: Any) -> bool:
    actions = row.get("execution_actions") or []
    if isinstance(actions, str):
        actions = [actions]
    return any(str(action) in REDUCE_FLIP_ACTIONS for action in actions)


def _rate(values: pd.Series) -> float:
    if len(values) == 0:
        return 0.0
    return _round(float(values.mean()))


def _lift(rate: float, base: float) -> float | None:
    if base <= 0.0:
        return None
    return _round(rate / base)


def _event_coverage(events: pd.Series, mask: pd.Series) -> float:
    total = int(events.sum())
    if total <= 0:
        return 0.0
    return _round(int(events[mask].sum()) / total)


def _factor_summary_sort_key(summary: dict[str, Any]) -> tuple[float, float, int]:
    return (
        max(
            _safe_float(summary.get("target_reduce_flip_high_lift")),
            _safe_float(summary.get("execution_reduce_flip_high_lift")),
        ),
        _safe_float(summary.get("reduce_flip_event_coverage")),
        int(summary.get("observations", 0) or 0),
    )


def _match_sort_key(match: dict[str, Any]) -> tuple[float, float, int]:
    return (
        max(
            _safe_float(match.get("target_reduce_flip_high_lift")),
            _safe_float(match.get("execution_reduce_flip_high_lift")),
        ),
        _safe_float(match.get("reduce_flip_event_coverage")),
        int(match.get("observations", 0) or 0),
    )


def _metrics_summary(metrics: dict[str, Any]) -> dict[str, float]:
    return {
        "sharpe": _round(_safe_float(metrics.get("sharpe"))),
        "net_pnl": _round(_safe_float(metrics.get("net_pnl"))),
        "max_drawdown": _round(_safe_float(metrics.get("max_drawdown"))),
        "trade_count": _round(_safe_float(metrics.get("trade_count"))),
        "fee_drag_pct": _round(_safe_float(metrics.get("fee_drag_pct"))),
        "turnover_per_day": _round(_safe_float(metrics.get("turnover_per_day"))),
    }


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _round(value: float | None, *, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)
