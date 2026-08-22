"""Fail-closed stage provenance comparison for repaired Quant V2 reports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .legacy_accounting_validity import ValidityState

STAGE_PROVENANCE_SCHEMA_VERSION = "legacy-accounting-stage-provenance.v1"
_RISK_DONOR_FIELDS = (
    "ts_ns",
    "equity",
    "high_water_mark",
    "drawdown",
    "lev_cap",
    "reduce_only_mode",
    "flags",
)
_ROW_PNL_FIELDS = (
    "gross_pnl",
    "approved_target_gross_pnl",
    "funding_pnl",
    "baseline_execution_cost",
    "execution_net_pnl",
)
_EPISODE_PNL_FIELDS = (
    "gross_realized_pnl",
    "fees",
    "funding_pnl",
    "net_pnl",
    "unrealized_pnl",
)
_StageKey = tuple[str, int, str]


def audit_stage_provenance(
    legacy_report: Mapping[str, Any],
    repaired_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify exact window/seed/stage evidence without assuming independence."""
    legacy_stages = _index_stages(legacy_report, label="legacy")
    repaired_stages = _index_stages(repaired_report, label="repaired")
    records = [
        _audit_stage(key, legacy_stages.get(key), repaired_stages.get(key))
        for key in sorted(set(legacy_stages) | set(repaired_stages))
    ]
    counts = {state.value: 0 for state in ValidityState}
    for record in records:
        counts[record["state"]] += 1
    return {
        "schema_version": STAGE_PROVENANCE_SCHEMA_VERSION,
        "records": records,
        "counts": counts,
    }


def _audit_stage(
    key: _StageKey,
    legacy_stage: Mapping[str, Any] | None,
    repaired_stage: Mapping[str, Any] | None,
) -> dict[str, Any]:
    legacy_payload, legacy_error = _try_extract_stage_payload(legacy_stage, stage=key[2])
    repaired_payload, repaired_error = _try_extract_stage_payload(repaired_stage, stage=key[2])
    legacy_sha256 = _canonical_payload_sha256(legacy_payload) if legacy_payload is not None else None
    repaired_sha256 = _canonical_payload_sha256(repaired_payload) if repaired_payload is not None else None

    if legacy_stage is None or repaired_stage is None:
        state = ValidityState.UNVERIFIED_LEGACY
        reason = "stage_key_missing"
    elif legacy_error is not None or repaired_error is not None:
        state = ValidityState.UNVERIFIED_LEGACY
        reason = "stage_provenance_incomplete"
    elif legacy_payload == repaired_payload:
        state = ValidityState.PROVEN_UNAFFECTED
        reason = "exact_extracted_stage_payload_match"
    else:
        state = ValidityState.CONFIRMED_AFFECTED
        reason = "extracted_stage_payload_changed"

    return {
        "window": key[0],
        "seed": key[1],
        "stage": key[2],
        "state": state.value,
        "reason": reason,
        "legacy_stage_payload_sha256": legacy_sha256,
        "repaired_stage_payload_sha256": repaired_sha256,
        "legacy_error": legacy_error,
        "repaired_error": repaired_error,
    }


def _index_stages(report: Mapping[str, Any], *, label: str) -> dict[_StageKey, Mapping[str, Any]]:
    runs = report.get("runs")
    if not isinstance(runs, list):
        raise ValueError(f"{label} report runs must be a list")
    indexed: dict[_StageKey, Mapping[str, Any]] = {}
    for run_index, run in enumerate(runs):
        if not isinstance(run, Mapping):
            raise ValueError(f"{label} report run {run_index} must be an object")
        window = run.get("window")
        seed = run.get("seed")
        stages = run.get("stages")
        if not isinstance(window, str) or not window:
            raise ValueError(f"{label} report run {run_index} requires a window")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"{label} report run {run_index} requires an integer seed")
        if not isinstance(stages, Mapping):
            raise ValueError(f"{label} report run {run_index} stages must be an object")
        for stage, payload in stages.items():
            if not isinstance(stage, str) or not stage or not isinstance(payload, Mapping):
                raise ValueError(f"{label} report run {run_index} contains an invalid stage payload")
            key = (window, seed, stage)
            if key in indexed:
                raise ValueError(f"{label} report contains duplicate stage key {key!r}")
            indexed[key] = payload
    return indexed


def _try_extract_stage_payload(
    stage_payload: Mapping[str, Any] | None,
    *,
    stage: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if stage_payload is None:
        return None, "missing_stage_key"
    try:
        return _extract_stage_payload(stage_payload, stage=stage), None
    except (KeyError, TypeError, ValueError) as exc:
        return None, str(exc)


def _extract_stage_payload(stage_payload: Mapping[str, Any], *, stage: str) -> dict[str, Any]:
    if stage_payload.get("stage") != stage:
        raise ValueError("stage declaration does not match its key")
    rows = stage_payload.get("attribution_rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("attribution_rows must be a non-empty list")
    extracted_rows = [_extract_attribution_row(row, index=index) for index, row in enumerate(rows)]
    stage_pnl: dict[str, Any] = {}
    if "gross_pnl" in stage_payload:
        stage_pnl["gross_pnl"] = stage_payload["gross_pnl"]
    if "cost_stress" in stage_payload:
        stage_pnl["cost_stress"] = stage_payload["cost_stress"]
    if not stage_pnl:
        raise ValueError("stage PnL evidence is missing")
    episodes = stage_payload.get("position_episodes", [])
    if not isinstance(episodes, list):
        raise ValueError("position_episodes must be a list when present")
    return {
        "stage": stage,
        "rows": extracted_rows,
        "stage_pnl": stage_pnl,
        "episode_pnl": [_extract_episode_pnl(episode, index=index) for index, episode in enumerate(episodes)],
    }


def _extract_attribution_row(row: object, *, index: int) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError(f"attribution row {index} must be an object")
    input_row_sha256 = _required_string(row, "input_row_sha256", index=index)
    risk_donor_sha256 = _required_string(row, "risk_donor_sha256", index=index)
    donor = row.get("risk_donor")
    if not isinstance(donor, Mapping):
        raise ValueError(f"attribution row {index} risk_donor must be an object")
    donor_payload = {field: donor[field] for field in _RISK_DONOR_FIELDS}
    donor_payload["flags"] = _sorted_strings(donor_payload["flags"], f"attribution row {index} donor flags")
    execution = row.get("execution")
    if execution is not None and not isinstance(execution, Mapping):
        raise ValueError(f"attribution row {index} execution must be an object")
    execution = execution or {}
    if "gross_pnl" not in row:
        raise ValueError(f"attribution row {index} requires gross_pnl")
    return {
        "input_row_sha256": input_row_sha256,
        "risk_donor_sha256": risk_donor_sha256,
        "risk_donor": donor_payload,
        "safety_reasons": _sorted_strings(
            row.get("safety_reasons", ()),
            f"attribution row {index} safety_reasons",
        ),
        "execution_safety_reasons": _sorted_strings(
            execution.get("safety_reasons", ()),
            f"attribution row {index} execution safety_reasons",
        ),
        "approved_exposure": row["approved_exposure"],
        "execution_approved_exposure": execution.get("approved_exposure"),
        "filled_exposure": row["exposure"],
        "filled_exposure_before": execution.get("filled_exposure_before"),
        "filled_exposure_after": execution.get("filled_exposure_after"),
        "pnl": {field: row[field] for field in _ROW_PNL_FIELDS if field in row},
    }


def _extract_episode_pnl(episode: object, *, index: int) -> dict[str, Any]:
    if not isinstance(episode, Mapping):
        raise ValueError(f"position episode {index} must be an object")
    return {field: episode[field] for field in _EPISODE_PNL_FIELDS if field in episode}


def _required_string(row: Mapping[str, Any], field: str, *, index: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"attribution row {index} requires {field}")
    return value


def _sorted_strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string list")
    return sorted(value)


def _canonical_payload_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
