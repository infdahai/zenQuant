"""Independence audit for admitted Deribit large-option-flow factors."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

_BASELINE_COLUMNS = ["price_return_24h_z", "realized_vol_24h_z"]


def build_deribit_large_option_flow_independence_audit(
    *,
    admission_payload: dict[str, Any],
    features_path: Path,
    symbol: str = "BTCUSDT",
    max_allowed_correlation: float = 0.75,
    min_observations: int = 500,
) -> dict[str, Any]:
    candidates = list(
        dict.fromkeys(item["name"] for item in admission_payload.get("top_feature_candidates", []) if item.get("name"))
    )
    features = pd.read_parquet(features_path) if features_path.exists() else pd.DataFrame()
    missing = [name for name in candidates if name not in features.columns]
    baseline_columns = [column for column in _BASELINE_COLUMNS if column in features.columns]
    usable_columns = [name for name in candidates if name in features.columns]
    frame = (
        features[["bar_close_ts", *usable_columns, *baseline_columns]].dropna() if usable_columns else pd.DataFrame()
    )
    correlations = _candidate_correlations(frame, usable_columns, baseline_columns)
    max_abs_correlation = max(
        (abs(value) for values in correlations.values() for value in values.values()), default=0.0
    )
    observations = len(frame)
    blocked_by = None
    if not admission_payload.get("research_ready"):
        blocked_by = "admission_not_research_ready"
    elif missing:
        blocked_by = "candidate_feature_missing"
    elif observations < min_observations:
        blocked_by = "insufficient_independence_observations"
    elif max_abs_correlation >= max_allowed_correlation:
        blocked_by = "high_baseline_state_correlation"
    pressure_context_allowed = blocked_by is None
    return {
        "schema_version": "deribit_large_option_flow_independence_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_name": "deribit_large_option_flow",
        "symbol": symbol,
        "role": "state_context_pressure_diagnostic",
        "time_granularity": "4h",
        "observations": observations,
        "candidate_count": len(candidates),
        "usable_candidate_count": len(usable_columns),
        "missing_candidates": missing,
        "baseline_columns": baseline_columns,
        "candidate_baseline_correlations": correlations,
        "max_abs_correlation": round(float(max_abs_correlation), 4),
        "ablation_ready": True,
        "ablation_delta": None,
        "allowed_in_state_features": False,
        "allowed_in_pressure_context": pressure_context_allowed,
        "allowed_in_candidate_explanation": pressure_context_allowed,
        "direction_vote_allowed": False,
        "intent_aggregation_allowed": False,
        "position_sizing_allowed": False,
        "risk_overlay_wiring_allowed": False,
        "use_for_research_only": True,
        "blocked_by": blocked_by,
        "next_action": _next_action(blocked_by),
    }


def write_deribit_large_option_flow_independence_audit(
    *,
    admission_path: Path = Path("results/data_source_review/deribit_large_option_flow_admission_review.json"),
    output_dir: Path = Path("results/signal_combination"),
) -> dict[str, Any]:
    admission_payload = json.loads(admission_path.read_text(encoding="utf-8"))
    features_path = Path(str(admission_payload.get("features_path") or ""))
    payload = build_deribit_large_option_flow_independence_audit(
        admission_payload=admission_payload,
        features_path=features_path,
        symbol=str(admission_payload.get("symbol") or "BTCUSDT"),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "deribit_large_option_flow_independence_audit.json"
    md_path = output_dir / "deribit_large_option_flow_independence_audit.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(build_deribit_large_option_flow_independence_markdown(payload), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "allowed_in_state_features": payload["allowed_in_state_features"],
        "allowed_in_pressure_context": payload["allowed_in_pressure_context"],
        "blocked_by": payload["blocked_by"],
        "max_abs_correlation": payload["max_abs_correlation"],
    }


def build_deribit_large_option_flow_independence_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Deribit Large Option Flow Independence Audit",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- source_name: `{payload['source_name']}`",
        f"- role: `{payload['role']}`",
        f"- time_granularity: `{payload['time_granularity']}`",
        f"- observations: `{payload['observations']}`",
        f"- candidate_count: `{payload['candidate_count']}`",
        f"- usable_candidate_count: `{payload['usable_candidate_count']}`",
        f"- max_abs_correlation: `{payload['max_abs_correlation']}`",
        f"- allowed_in_state_features: `{payload['allowed_in_state_features']}`",
        f"- allowed_in_pressure_context: `{payload['allowed_in_pressure_context']}`",
        f"- direction_vote_allowed: `{payload['direction_vote_allowed']}`",
        f"- intent_aggregation_allowed: `{payload['intent_aggregation_allowed']}`",
        f"- risk_overlay_wiring_allowed: `{payload['risk_overlay_wiring_allowed']}`",
        f"- use_for_research_only: `{payload['use_for_research_only']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
        f"- next_action: `{payload['next_action']}`",
        "",
        "## Candidate Baseline Correlations",
        "",
    ]
    for name, values in payload["candidate_baseline_correlations"].items():
        for baseline, value in values.items():
            lines.append(f"- {name} vs {baseline}: `{value}`")
    if not payload["candidate_baseline_correlations"]:
        lines.append("- `none`")
    return "\n".join(lines) + "\n"


def _candidate_correlations(
    frame: pd.DataFrame,
    candidates: list[str],
    baseline_columns: list[str],
) -> dict[str, dict[str, float]]:
    correlations: dict[str, dict[str, float]] = {}
    for candidate in candidates:
        correlations[candidate] = {}
        for baseline in baseline_columns:
            pair = frame[[candidate, baseline]].dropna()
            if len(pair) < 10:
                correlations[candidate][baseline] = 0.0
                continue
            value = pair[candidate].rank().corr(pair[baseline].rank())
            correlations[candidate][baseline] = 0.0 if pd.isna(value) else round(float(value), 4)
    return correlations


def _next_action(blocked_by: str | None) -> str:
    if blocked_by is None:
        return "freeze_feature_set_for_pressure_context_replay"
    if blocked_by == "high_baseline_state_correlation":
        return "drop_redundant_large_option_flow_features_before_replay"
    return "repair_large_option_flow_independence_blocker"
