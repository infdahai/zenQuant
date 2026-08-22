"""Fail-closed stage provenance comparison for repaired Quant V2 reports."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from .legacy_accounting_validity import ValidityState

STAGE_PROVENANCE_SCHEMA_VERSION = "legacy-accounting-stage-provenance.v1"
_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_RISK_SNAPSHOT_ASOF_STATUS = "ASOF"
_RISK_DONOR_FIELDS = (
    "ts_ns",
    "equity",
    "high_water_mark",
    "drawdown",
    "lev_cap",
    "reduce_only_mode",
    "flags",
)
_INPUT_ROW_FIELDS = (
    "alpha_id",
    "cutoff_time_ns",
    "regime",
    "raw_score",
    "frozen_confidence",
    "forward_return",
    "formula_hash",
    "alpha_config_hash",
    "forward_return_available_at_ns",
    "available_at_ns",
    "annualized_volatility",
    "risk_regime",
    "frozen_regime_multiplier",
    "exchange_min_quantity",
    "reduce_only",
    "safety_reasons",
    "risk_snapshot_status",
    "risk_snapshot_ts_ns",
    "risk_donor",
    "risk_donor_sha256",
    "estimated_fee_bps",
    "estimated_slippage_bps",
    "reference_price",
    "actual_fill_price",
    "forward_return_3",
    "forward_return_6",
    "directional_state",
    "volatility_state",
    "positioning_state",
    "quality_state",
    "quality_flags",
)
_ATTRIBUTION_ROW_FIELDS = (
    "alpha_id",
    "cutoff_time_ns",
    "regime",
    "input_row",
    "input_row_sha256",
    "risk_donor",
    "risk_donor_sha256",
    "safety_reasons",
    "approved_exposure",
    "exposure",
    "forward_return",
    "gross_pnl",
    "approved_target_gross_pnl",
    "turnover_notional",
    "funding_pnl",
    "baseline_execution_cost",
    "execution_net_pnl",
    "risk_snapshot_status",
    "risk_snapshot_ts_ns",
    "execution",
)
_EXECUTION_FIELDS = (
    "cutoff_time_ns",
    "decision_id",
    "approved_exposure",
    "approved_target_qty",
    "filled_exposure_before",
    "filled_exposure_after",
    "filled_position_before",
    "filled_position_after",
    "target_delta_exposure",
    "reference_price",
    "actual_fill_price",
    "filled_qty",
    "fee",
    "actual_slippage",
    "actual_implementation_shortfall",
    "funding_pnl",
    "target_tracking_error",
    "time_to_target_bars",
    "reduce_only",
    "safety_reasons",
    "forward_return_1",
    "forward_return_3",
    "forward_return_6",
    "forward_return_1_available_at_ns",
)
_EPISODE_FIELDS = (
    "episode_id",
    "symbol",
    "side",
    "opened_at_ns",
    "closed_at_ns",
    "entry_price",
    "exit_price",
    "max_abs_qty",
    "remaining_qty",
    "gross_realized_pnl",
    "fees",
    "funding_pnl",
    "net_pnl",
    "mfe_pnl",
    "mae_pnl",
    "unrealized_pnl",
    "fill_count",
    "closed",
    "add_count",
    "reduce_count",
    "holding_duration_ns",
)
_EPISODE_LEDGER_FIELDS = (
    "status",
    "closed_episode_count",
    "open_episode_count",
    "episode_profit_factor",
    "episode_profit_factor_status",
    "gross_profit",
    "gross_loss",
)
_COST_SHADOW_FIELDS = (
    "target_delta",
    "estimated_fee",
    "estimated_slippage",
    "round_trip_cost",
    "breakeven_move",
    "actual_implementation_shortfall",
    "forward_return_1",
    "forward_return_3",
    "forward_return_6",
    "blocks_order",
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
    elif legacy_sha256 == repaired_sha256:
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
    _require_fields(
        stage_payload,
        ("pnl_basis", "gross_pnl", "turnover_notional", "cost_stress", "position_episodes", "episode_ledger"),
        label=f"{stage} stage",
    )
    rows = stage_payload.get("attribution_rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("attribution_rows must be a non-empty list")
    extracted_rows = [_extract_attribution_row(row, index=index, stage=stage) for index, row in enumerate(rows)]
    episodes = stage_payload["position_episodes"]
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("position_episodes must be a non-empty list")
    episode_ledger = stage_payload["episode_ledger"]
    if not isinstance(episode_ledger, Mapping):
        raise ValueError("episode_ledger must be an object")
    _require_fields(episode_ledger, _EPISODE_LEDGER_FIELDS, label="episode_ledger")
    return {
        "stage": stage,
        "rows": extracted_rows,
        "pnl_basis": stage_payload["pnl_basis"],
        "gross_pnl": stage_payload["gross_pnl"],
        "turnover_notional": stage_payload["turnover_notional"],
        "cost_stress": stage_payload["cost_stress"],
        "episodes": [_extract_episode(episode, index=index) for index, episode in enumerate(episodes)],
        "episode_ledger": {field: episode_ledger[field] for field in _EPISODE_LEDGER_FIELDS},
    }


def _extract_attribution_row(row: object, *, index: int, stage: str) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError(f"attribution row {index} must be an object")
    _require_fields(row, _ATTRIBUTION_ROW_FIELDS, label=f"attribution row {index}")
    donor = row.get("risk_donor")
    if not isinstance(donor, Mapping):
        raise ValueError(f"attribution row {index} risk_donor must be an object")
    donor_payload = _validated_risk_donor(
        donor,
        declared_sha256=row["risk_donor_sha256"],
        label=f"attribution row {index}",
    )
    input_row = row["input_row"]
    if not isinstance(input_row, Mapping):
        raise ValueError(f"attribution row {index} input_row must be an object")
    _require_exact_fields(input_row, _INPUT_ROW_FIELDS, label=f"attribution row {index} input_row")
    input_row_sha256 = _validated_sha256(row["input_row_sha256"], label=f"attribution row {index} input row")
    if _canonical_payload_sha256(input_row) != input_row_sha256:
        raise ValueError(f"attribution row {index} input_row_sha256 does not match input_row")
    input_donor = input_row["risk_donor"]
    if (
        not isinstance(input_donor, Mapping)
        or not _canonical_values_equal(input_donor, donor_payload)
        or input_row["risk_donor_sha256"] != row["risk_donor_sha256"]
    ):
        raise ValueError(f"attribution row {index} input donor provenance does not match attribution row")
    if (
        row["risk_snapshot_status"] != _RISK_SNAPSHOT_ASOF_STATUS
        or input_row["risk_snapshot_status"] != _RISK_SNAPSHOT_ASOF_STATUS
    ):
        raise ValueError(f"attribution row {index} risk_snapshot_status must be ASOF")
    snapshot_ts_ns = row["risk_snapshot_ts_ns"]
    if (
        isinstance(snapshot_ts_ns, bool)
        or not isinstance(snapshot_ts_ns, int)
        or not _canonical_values_equal(snapshot_ts_ns, donor_payload["ts_ns"])
    ):
        raise ValueError(f"attribution row {index} risk_snapshot_ts_ns does not match risk_donor ts_ns")
    execution = row.get("execution")
    if not isinstance(execution, Mapping):
        raise ValueError(f"attribution row {index} execution must be an object")
    _require_fields(execution, _EXECUTION_FIELDS, label=f"attribution row {index} execution")
    row_safety_reasons = _sorted_strings(
        row["safety_reasons"],
        f"attribution row {index} safety_reasons",
    )
    execution_safety_reasons = _sorted_strings(
        execution["safety_reasons"],
        f"attribution row {index} execution safety_reasons",
    )
    mirrored_values = (
        ("alpha_id", row["alpha_id"], input_row["alpha_id"]),
        ("cutoff_time_ns", row["cutoff_time_ns"], input_row["cutoff_time_ns"]),
        ("execution cutoff_time_ns", row["cutoff_time_ns"], execution["cutoff_time_ns"]),
        ("approved_exposure", row["approved_exposure"], execution["approved_exposure"]),
        ("filled_exposure_after", row["exposure"], execution["filled_exposure_after"]),
        ("funding_pnl", row["funding_pnl"], execution["funding_pnl"]),
        ("risk_snapshot_status", row["risk_snapshot_status"], input_row["risk_snapshot_status"]),
        ("risk_snapshot_ts_ns", row["risk_snapshot_ts_ns"], input_row["risk_snapshot_ts_ns"]),
        ("safety_reasons", row_safety_reasons, execution_safety_reasons),
    )
    for field, row_value, source_value in mirrored_values:
        if not _canonical_values_equal(row_value, source_value):
            raise ValueError(f"attribution row {index} {field} does not match its source evidence")
    extracted = {
        field: row[field] for field in _ATTRIBUTION_ROW_FIELDS if field not in {"input_row", "risk_donor", "execution"}
    }
    extracted.update(
        {
            "input_row": {field: input_row[field] for field in _INPUT_ROW_FIELDS},
            "risk_donor": donor_payload,
            "safety_reasons": row_safety_reasons,
            "execution": {field: execution[field] for field in _EXECUTION_FIELDS},
        }
    )
    extracted["execution"]["safety_reasons"] = execution_safety_reasons
    if stage == "A5_SHADOW":
        cost_shadow = row.get("cost_shadow")
        if not isinstance(cost_shadow, Mapping):
            raise ValueError(f"attribution row {index} cost_shadow must be an object")
        _require_fields(cost_shadow, _COST_SHADOW_FIELDS, label=f"attribution row {index} cost_shadow")
        extracted["cost_shadow"] = {field: cost_shadow[field] for field in _COST_SHADOW_FIELDS}
    return extracted


def _validated_risk_donor(
    donor: Mapping[str, Any],
    *,
    declared_sha256: object,
    label: str,
) -> dict[str, Any]:
    _require_exact_fields(donor, _RISK_DONOR_FIELDS, label=f"{label} risk_donor")
    ts_ns = donor["ts_ns"]
    if isinstance(ts_ns, bool) or not isinstance(ts_ns, int):
        raise ValueError(f"{label} risk_donor ts_ns must be an integer")
    for field in ("equity", "high_water_mark", "drawdown", "lev_cap"):
        value = donor[field]
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
            raise ValueError(f"{label} risk_donor {field} must be a finite number")
    if not isinstance(donor["reduce_only_mode"], bool):
        raise ValueError(f"{label} risk_donor reduce_only_mode must be a boolean")
    flags = _sorted_strings(donor["flags"], f"{label} risk_donor flags")
    if donor["flags"] != flags:
        raise ValueError(f"{label} risk_donor flags must be sorted")
    payload = {
        "ts_ns": ts_ns,
        "equity": donor["equity"],
        "high_water_mark": donor["high_water_mark"],
        "drawdown": donor["drawdown"],
        "lev_cap": donor["lev_cap"],
        "reduce_only_mode": donor["reduce_only_mode"],
        "flags": flags,
    }
    digest = _validated_sha256(declared_sha256, label=f"{label} risk donor")
    if _canonical_payload_sha256(payload) != digest:
        raise ValueError(f"{label} risk_donor_sha256 does not match risk_donor")
    return payload


def _extract_episode(episode: object, *, index: int) -> dict[str, Any]:
    if not isinstance(episode, Mapping):
        raise ValueError(f"position episode {index} must be an object")
    _require_fields(episode, _EPISODE_FIELDS, label=f"position episode {index}")
    return {field: episode[field] for field in _EPISODE_FIELDS}


def _require_fields(value: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        raise ValueError(f"{label} is missing required fields {missing!r}")


def _require_exact_fields(value: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    expected = set(fields)
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} fields mismatch: missing={sorted(expected - actual)!r} extra={sorted(actual - expected)!r}"
        )


def _validated_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} SHA-256 must be sha256:<64 lowercase hex>")
    return value


def _sorted_strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string list")
    return sorted(value)


def _canonical_values_equal(left: object, right: object) -> bool:
    return _canonical_payload_bytes(left) == _canonical_payload_bytes(right)


def _canonical_payload_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_payload_sha256(payload: object) -> str:
    encoded = _canonical_payload_bytes(payload)
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
