"""Fail-closed stage provenance tests for repaired Quant V2 reports."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from zenquant.research.architecture_experiment import ArchitectureStage
from zenquant.research.legacy_accounting_provenance import audit_stage_provenance
from zenquant.research.legacy_accounting_validity import ValidityState

_REQUIRED_ATTRIBUTION_FIELDS = (
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
)
_REQUIRED_EXECUTION_FIELDS = (
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
_REQUIRED_EPISODE_FIELDS = (
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
_REQUIRED_COST_SHADOW_FIELDS = (
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


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _risk_donor(*, flags: list[str] | None = None) -> dict[str, object]:
    return {
        "ts_ns": 100,
        "equity": 100_000.0,
        "high_water_mark": 101_000.0,
        "drawdown": 0.009900990099009901,
        "lev_cap": 1.0,
        "reduce_only_mode": False,
        "flags": sorted(flags or []),
    }


def _input_row(stage: str, donor: dict[str, object], donor_sha256: str) -> dict[str, object]:
    return {
        "alpha_id": f"{stage}-alpha-100",
        "cutoff_time_ns": 100,
        "regime": "TREND",
        "raw_score": 0.5,
        "frozen_confidence": 0.5,
        "forward_return": 0.01,
        "formula_hash": "formula-v1",
        "alpha_config_hash": "alpha-v1",
        "forward_return_available_at_ns": 200,
        "available_at_ns": 100,
        "annualized_volatility": 0.2,
        "risk_regime": "TRADABLE",
        "frozen_regime_multiplier": 1.0,
        "exchange_min_quantity": 0.001,
        "reduce_only": False,
        "safety_reasons": [],
        "risk_snapshot_status": "ASOF",
        "risk_snapshot_ts_ns": 100,
        "risk_donor": donor,
        "risk_donor_sha256": donor_sha256,
        "estimated_fee_bps": 4.0,
        "estimated_slippage_bps": 1.5,
        "reference_price": 100.0,
        "actual_fill_price": None,
        "forward_return_3": 0.02,
        "forward_return_6": 0.03,
        "directional_state": "UPTREND",
        "volatility_state": "NORMAL",
        "positioning_state": "HEALTHY",
        "quality_state": "TRADABLE",
        "quality_flags": [],
    }


def _execution(stage: str) -> dict[str, object]:
    return {
        "cutoff_time_ns": 100,
        "decision_id": f"{stage}-decision-100",
        "approved_exposure": 0.5,
        "approved_target_qty": 5.0,
        "filled_exposure_before": 0.2,
        "filled_exposure_after": 0.4,
        "filled_position_before": 2.0,
        "filled_position_after": 4.0,
        "target_delta_exposure": 0.3,
        "reference_price": 100.0,
        "actual_fill_price": 100.1,
        "filled_qty": 2.0,
        "fee": 0.08,
        "actual_slippage": 0.2,
        "actual_implementation_shortfall": 0.28,
        "funding_pnl": 0.25,
        "target_tracking_error": 0.1,
        "time_to_target_bars": 0,
        "reduce_only": False,
        "safety_reasons": [],
        "forward_return_1": 0.01,
        "forward_return_3": 0.02,
        "forward_return_6": 0.03,
        "forward_return_1_available_at_ns": 200,
    }


def _episode(stage: str) -> dict[str, object]:
    return {
        "episode_id": f"{stage}-episode-1",
        "symbol": "BTCUSDT_PERP",
        "side": 1,
        "opened_at_ns": 100,
        "closed_at_ns": 200,
        "entry_price": 100.0,
        "exit_price": 101.0,
        "max_abs_qty": 4.0,
        "remaining_qty": 0.0,
        "gross_realized_pnl": 4.0,
        "fees": 0.08,
        "funding_pnl": 0.25,
        "net_pnl": 4.17,
        "mfe_pnl": 5.0,
        "mae_pnl": -1.0,
        "unrealized_pnl": 0.0,
        "fill_count": 2,
        "closed": True,
        "add_count": 0,
        "reduce_count": 1,
        "holding_duration_ns": 100,
    }


def _stage(stage: str) -> dict[str, object]:
    donor = _risk_donor()
    donor_sha256 = _canonical_sha256(donor)
    input_row = _input_row(stage, donor, donor_sha256)
    attribution_row: dict[str, object] = {
        "alpha_id": f"{stage}-alpha-100",
        "cutoff_time_ns": 100,
        "regime": "TREND",
        "input_row": input_row,
        "input_row_sha256": _canonical_sha256(input_row),
        "risk_donor_sha256": donor_sha256,
        "risk_donor": donor,
        "safety_reasons": [],
        "approved_exposure": 0.5,
        "exposure": 0.4,
        "forward_return": 0.01,
        "gross_pnl": 4.0,
        "approved_target_gross_pnl": 5.0,
        "turnover_notional": 200.2,
        "funding_pnl": 0.25,
        "baseline_execution_cost": 0.28,
        "execution_net_pnl": 3.97,
        "risk_snapshot_status": "ASOF",
        "risk_snapshot_ts_ns": 100,
        "execution": _execution(stage),
    }
    if stage == ArchitectureStage.A5_SHADOW.value:
        attribution_row["cost_shadow"] = {
            "target_delta": 0.3,
            "estimated_fee": 0.12,
            "estimated_slippage": 0.045,
            "round_trip_cost": 0.33,
            "breakeven_move": 0.0011,
            "actual_implementation_shortfall": 0.28,
            "forward_return_1": 0.01,
            "forward_return_3": 0.02,
            "forward_return_6": 0.03,
            "blocks_order": False,
        }
    return {
        "stage": stage,
        "pnl_basis": "ACTUAL_FILLED_EXPOSURE",
        "gross_pnl": 12.0,
        "turnover_notional": 200.2,
        "cost_stress": {
            "0": {"net_pnl": 11.0},
            "15": {"net_pnl": 9.5},
        },
        "attribution_rows": [attribution_row],
        "position_episodes": [_episode(stage)],
        "episode_ledger": {
            "status": "OBSERVED",
            "closed_episode_count": 1,
            "open_episode_count": 0,
            "episode_profit_factor": None,
            "episode_profit_factor_status": "NO_LOSSES",
            "gross_profit": 4.17,
            "gross_loss": 0.0,
        },
    }


def _report() -> dict[str, object]:
    stages = {stage.value: _stage(stage.value) for stage in ArchitectureStage}
    return {
        "schema_version": "architecture_attribution.v4",
        "manifest": {"sha256": "sha256:frozen-manifest"},
        "validity": {"state": "legacy-envelope"},
        "runs": [{"window": "late_dev_2025", "seed": 42, "stages": stages}],
    }


def _states(audit: dict[str, object]) -> dict[tuple[str, int, str], str]:
    return {(record["window"], record["seed"], record["stage"]): record["state"] for record in audit["records"]}


def _replace_donor(row: dict[str, object], donor: dict[str, object], *, donor_sha256: str | None = None) -> None:
    resolved_sha256 = donor_sha256 or _canonical_sha256(donor)
    row["risk_donor"] = donor
    row["risk_donor_sha256"] = resolved_sha256
    input_row = row["input_row"]
    input_row["risk_donor"] = donor
    input_row["risk_donor_sha256"] = resolved_sha256
    row["input_row_sha256"] = _canonical_sha256(input_row)


def _replace_snapshot_identity(row: dict[str, object], *, status: str, ts_ns: object) -> None:
    row["risk_snapshot_status"] = status
    row["risk_snapshot_ts_ns"] = ts_ns
    input_row = row["input_row"]
    input_row["risk_snapshot_status"] = status
    input_row["risk_snapshot_ts_ns"] = ts_ns
    row["input_row_sha256"] = _canonical_sha256(input_row)


def test_identical_extracted_stage_payloads_are_proven_unaffected_despite_envelope_change():
    legacy = _report()
    repaired = deepcopy(legacy)
    repaired["schema_version"] = "architecture_attribution.repaired"
    repaired["manifest"] = {"sha256": "sha256:repaired-envelope"}
    repaired["validity"] = {"state": "repaired-envelope"}

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states == {
        ("late_dev_2025", 42, stage.value): ValidityState.PROVEN_UNAFFECTED.value for stage in ArchitectureStage
    }


@pytest.mark.parametrize(
    ("stage", "path", "changed_value"),
    [
        pytest.param(
            "A5_SHADOW",
            ("attribution_rows", 0, "cost_shadow", "blocks_order"),
            0,
            id="false-to-zero",
        ),
        pytest.param("A4", ("position_episodes", 0, "closed"), 1, id="true-to-one"),
        pytest.param("A3", ("position_episodes", 0, "fill_count"), 2.0, id="integer-to-equal-float"),
        pytest.param("A2", ("position_episodes", 0, "add_count"), False, id="zero-to-false"),
    ],
)
def test_canonical_bool_numeric_payload_difference_is_confirmed_affected(stage, path, changed_value):
    legacy = _report()
    repaired = deepcopy(legacy)
    target = repaired["runs"][0]["stages"][stage]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = changed_value

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, stage)] == ValidityState.CONFIRMED_AFFECTED.value


def test_snapshot_timestamp_must_exactly_match_validated_donor_timestamp():
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        row = report["runs"][0]["stages"]["A2"]["attribution_rows"][0]
        _replace_snapshot_identity(row, status="ASOF", ts_ns=999)

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


def test_no_prior_snapshot_status_with_non_null_donor_is_unverified():
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        row = report["runs"][0]["stages"]["A2"]["attribution_rows"][0]
        _replace_snapshot_identity(row, status="NO_PRIOR_SNAPSHOT_NEUTRAL", ts_ns=100)

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


def test_python_equal_input_attribution_and_execution_identities_are_unverified():
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        row = report["runs"][0]["stages"]["A2"]["attribution_rows"][0]
        row["cutoff_time_ns"] = False
        row["input_row"]["cutoff_time_ns"] = 0
        row["execution"]["cutoff_time_ns"] = 0
        row["input_row_sha256"] = _canonical_sha256(row["input_row"])

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


def test_changed_risk_donor_flags_confirm_the_dependent_stage_is_affected():
    legacy = _report()
    repaired = deepcopy(legacy)
    repaired_row = repaired["runs"][0]["stages"]["A2"]["attribution_rows"][0]
    _replace_donor(repaired_row, _risk_donor(flags=["PORTFOLIO_KILL"]))

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.CONFIRMED_AFFECTED.value
    assert states[("late_dev_2025", 42, "A1_SCORE")] == ValidityState.PROVEN_UNAFFECTED.value


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("approved_exposure", 0.75),
        ("filled_exposure_after", 0.75),
        ("gross_pnl", -4.0),
    ],
)
def test_changed_approved_filled_or_pnl_value_is_confirmed_affected(field, changed_value):
    legacy = _report()
    repaired = deepcopy(legacy)
    repaired_row = repaired["runs"][0]["stages"]["A3"]["attribution_rows"][0]
    if field == "approved_exposure":
        repaired_row["approved_exposure"] = changed_value
        repaired_row["execution"]["approved_exposure"] = changed_value
    elif field == "filled_exposure_after":
        repaired_row["exposure"] = changed_value
        repaired_row["execution"]["filled_exposure_after"] = changed_value
    else:
        repaired_row[field] = changed_value

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A3")] == ValidityState.CONFIRMED_AFFECTED.value


def test_missing_legacy_donor_hash_is_unverified_even_when_other_values_match():
    legacy = _report()
    repaired = deepcopy(legacy)
    del legacy["runs"][0]["stages"]["A4"]["attribution_rows"][0]["risk_donor_sha256"]

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A4")] == ValidityState.UNVERIFIED_LEGACY.value


def test_missing_legacy_row_pnl_is_unverified_instead_of_inferred_unchanged():
    legacy = _report()
    repaired = deepcopy(legacy)
    del legacy["runs"][0]["stages"]["A4"]["attribution_rows"][0]["gross_pnl"]

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A4")] == ValidityState.UNVERIFIED_LEGACY.value


def test_all_null_donor_with_non_sha_identity_is_unverified():
    legacy = _report()
    repaired = deepcopy(legacy)
    null_donor = {
        "ts_ns": None,
        "equity": None,
        "high_water_mark": None,
        "drawdown": None,
        "lev_cap": None,
        "reduce_only_mode": None,
        "flags": [],
    }
    for report in (legacy, repaired):
        row = report["runs"][0]["stages"]["A2"]["attribution_rows"][0]
        _replace_donor(row, null_donor, donor_sha256="not-a-sha")

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


def test_all_null_donor_with_matching_hash_is_still_unverified():
    legacy = _report()
    repaired = deepcopy(legacy)
    null_donor = {
        "ts_ns": None,
        "equity": None,
        "high_water_mark": None,
        "drawdown": None,
        "lev_cap": None,
        "reduce_only_mode": None,
        "flags": [],
    }
    for report in (legacy, repaired):
        row = report["runs"][0]["stages"]["A2"]["attribution_rows"][0]
        _replace_donor(row, null_donor)

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


def test_valid_looking_nonmatching_donor_hash_is_unverified():
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        row = report["runs"][0]["stages"]["A2"]["attribution_rows"][0]
        _replace_donor(row, row["risk_donor"], donor_sha256=f"sha256:{'0' * 64}")

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


def test_valid_looking_nonmatching_input_hash_is_unverified():
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        row = report["runs"][0]["stages"]["A2"]["attribution_rows"][0]
        row["input_row_sha256"] = f"sha256:{'0' * 64}"

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


def test_input_hash_without_canonical_input_payload_is_unverified():
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        del report["runs"][0]["stages"]["A2"]["attribution_rows"][0]["input_row"]

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


@pytest.mark.parametrize("field", _REQUIRED_ATTRIBUTION_FIELDS)
def test_missing_required_attribution_field_in_both_reports_is_unverified(field):
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        del report["runs"][0]["stages"]["A2"]["attribution_rows"][0][field]

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


@pytest.mark.parametrize("field", _REQUIRED_EXECUTION_FIELDS)
def test_missing_required_execution_field_in_both_reports_is_unverified(field):
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        del report["runs"][0]["stages"]["A3"]["attribution_rows"][0]["execution"][field]

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A3")] == ValidityState.UNVERIFIED_LEGACY.value


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("approved_exposure", 0.75),
        ("exposure", 0.75),
        ("safety_reasons", ["KILL"]),
        ("funding_pnl", 0.5),
    ],
)
def test_internally_inconsistent_row_and_execution_evidence_is_unverified(field, changed_value):
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        report["runs"][0]["stages"]["A3"]["attribution_rows"][0][field] = changed_value

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A3")] == ValidityState.UNVERIFIED_LEGACY.value


@pytest.mark.parametrize("field", _REQUIRED_EPISODE_FIELDS)
def test_missing_required_episode_field_in_both_reports_is_unverified(field):
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        del report["runs"][0]["stages"]["A4"]["position_episodes"][0][field]

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A4")] == ValidityState.UNVERIFIED_LEGACY.value


@pytest.mark.parametrize("field", _REQUIRED_COST_SHADOW_FIELDS)
def test_missing_required_a5_cost_shadow_field_in_both_reports_is_unverified(field):
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        del report["runs"][0]["stages"]["A5_SHADOW"]["attribution_rows"][0]["cost_shadow"][field]

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A5_SHADOW")] == ValidityState.UNVERIFIED_LEGACY.value


@pytest.mark.parametrize("collection", ["attribution_rows", "position_episodes"])
def test_empty_required_stage_collection_in_both_reports_is_unverified(collection):
    legacy = _report()
    repaired = deepcopy(legacy)
    for report in (legacy, repaired):
        report["runs"][0]["stages"]["A2"][collection] = []

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.UNVERIFIED_LEGACY.value


def test_missing_exact_stage_key_is_unverified_legacy():
    legacy = _report()
    repaired = deepcopy(legacy)
    del legacy["runs"][0]["stages"]["A5_SHADOW"]

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A5_SHADOW")] == ValidityState.UNVERIFIED_LEGACY.value
