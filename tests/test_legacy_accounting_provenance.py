"""Fail-closed stage provenance tests for repaired Quant V2 reports."""

from __future__ import annotations

from copy import deepcopy

import pytest

from zenquant.research.architecture_experiment import ArchitectureStage
from zenquant.research.legacy_accounting_provenance import audit_stage_provenance
from zenquant.research.legacy_accounting_validity import ValidityState


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


def _stage(stage: str) -> dict[str, object]:
    return {
        "stage": stage,
        "gross_pnl": 12.0,
        "cost_stress": {
            "0": {"net_pnl": 11.0},
            "15": {"net_pnl": 9.5},
        },
        "attribution_rows": [
            {
                "alpha_id": f"{stage}-alpha-100",
                "cutoff_time_ns": 100,
                "input_row_sha256": f"sha256:{stage.lower()}-input",
                "risk_donor_sha256": "sha256:donor-100",
                "risk_donor": _risk_donor(),
                "safety_reasons": [],
                "approved_exposure": 0.5,
                "exposure": 0.4,
                "gross_pnl": 4.0,
                "funding_pnl": 0.25,
                "baseline_execution_cost": 0.1,
                "execution_net_pnl": 4.15,
                "execution": {
                    "approved_exposure": 0.5,
                    "filled_exposure_before": 0.2,
                    "filled_exposure_after": 0.4,
                    "safety_reasons": [],
                    "funding_pnl": 0.25,
                },
            }
        ],
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


def test_changed_risk_donor_flags_confirm_the_dependent_stage_is_affected():
    legacy = _report()
    repaired = deepcopy(legacy)
    repaired_row = repaired["runs"][0]["stages"]["A2"]["attribution_rows"][0]
    repaired_row["risk_donor"] = _risk_donor(flags=["PORTFOLIO_KILL"])
    repaired_row["risk_donor_sha256"] = "sha256:changed-donor"

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A2")] == ValidityState.CONFIRMED_AFFECTED.value
    assert states[("late_dev_2025", 42, "A1_SCORE")] == ValidityState.PROVEN_UNAFFECTED.value


@pytest.mark.parametrize(
    ("field_path", "changed_value"),
    [
        (("approved_exposure",), 0.75),
        (("execution", "filled_exposure_after"), 0.75),
        (("gross_pnl",), -4.0),
    ],
)
def test_changed_approved_filled_or_pnl_value_is_confirmed_affected(field_path, changed_value):
    legacy = _report()
    repaired = deepcopy(legacy)
    repaired_row = repaired["runs"][0]["stages"]["A3"]["attribution_rows"][0]
    target = repaired_row
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = changed_value

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


def test_missing_exact_stage_key_is_unverified_legacy():
    legacy = _report()
    repaired = deepcopy(legacy)
    del legacy["runs"][0]["stages"]["A5_SHADOW"]

    states = _states(audit_stage_provenance(legacy, repaired))

    assert states[("late_dev_2025", 42, "A5_SHADOW")] == ValidityState.UNVERIFIED_LEGACY.value
