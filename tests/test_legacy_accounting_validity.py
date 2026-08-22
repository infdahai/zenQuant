from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from zenquant.research.legacy_accounting_validity import (
    AccountingValidityRegistryError,
    InvalidPerformanceEvidenceError,
    ValidityState,
    assert_metric_family_allowed,
    load_accounting_validity_registry,
)


def _record(
    *,
    state: str = "CONFIRMED_AFFECTED",
    artifact_path: str = "results/source.json",
    source_sha256: str = "a" * 64,
    selector: str = "train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
    metric_families: list[str] | None = None,
    stages: list[str] | None = None,
    reason: str = "legacy_fee_cash_floor_can_create_equity",
    superseding_artifact: str | None = None,
    ledger_formula_hash: str = "sha256:" + "c" * 64,
    decision_at: str = "2026-08-23T00:00:00Z",
    approving_decision: str = "legacy-accounting-integrity-plan-task-3",
) -> dict[str, object]:
    return {
        "artifact_path": artifact_path,
        "source_sha256": source_sha256,
        "selector": selector,
        "state": state,
        "reason": reason,
        "metric_families": metric_families or ["promotion_gate"],
        "stages": stages or ["A0"],
        "superseding_artifact": superseding_artifact,
        "ledger_formula_hash": ledger_formula_hash,
        "decision_at": decision_at,
        "approving_decision": approving_decision,
    }


def _write_registry(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"schema_version": "legacy-accounting-validity.v1", "records": records}),
        encoding="utf-8",
    )
    return path


def test_registry_rejects_unknown_validity_state(tmp_path: Path) -> None:
    path = _write_registry(tmp_path, [_record(state="MAYBE_AFFECTED")])

    with pytest.raises(AccountingValidityRegistryError, match="invalid validity state"):
        load_accounting_validity_registry(path)


def test_registry_rejects_duplicate_artifact_stage_metric_key(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path,
        [
            _record(metric_families=["equity", "return"]),
            _record(metric_families=["return"], stages=["A0", "A1_SIGN"]),
        ],
    )

    with pytest.raises(
        AccountingValidityRegistryError,
        match=r"duplicate validity key:results/source\.json:A0:return",
    ):
        load_accounting_validity_registry(path)


def test_registry_rejects_ambiguous_same_bytes_selector_policy(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path,
        [
            _record(artifact_path="results/first.json"),
            _record(artifact_path="results/copy.json", state="PROVEN_UNAFFECTED"),
        ],
    )

    with pytest.raises(AccountingValidityRegistryError, match="duplicate source validity key"):
        load_accounting_validity_registry(path)


def test_registry_rejects_missing_reason(tmp_path: Path) -> None:
    path = _write_registry(tmp_path, [_record(reason="")])

    with pytest.raises(AccountingValidityRegistryError, match="reason must be non-empty"):
        load_accounting_validity_registry(path)


@pytest.mark.parametrize("field", ["ledger_formula_hash", "decision_at", "approving_decision"])
def test_registry_rejects_missing_governance_identity(tmp_path: Path, field: str) -> None:
    record = _record()
    record.pop(field)

    with pytest.raises(AccountingValidityRegistryError, match=field):
        load_accounting_validity_registry(_write_registry(tmp_path, [record]))


@pytest.mark.parametrize("state", [ValidityState.CONFIRMED_AFFECTED, ValidityState.UNVERIFIED_LEGACY])
def test_invalid_legacy_states_reject_performance_evidence(tmp_path: Path, state: ValidityState) -> None:
    registry = load_accounting_validity_registry(_write_registry(tmp_path, [_record(state=state.value)]))

    with pytest.raises(InvalidPerformanceEvidenceError, match=state.value):
        assert_metric_family_allowed(
            registry,
            artifact_path="results/source.json",
            source_sha256="a" * 64,
            metric_family="promotion_gate",
            stage="A0",
            selector="train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
        )


def test_original_assert_interface_uses_only_unambiguous_selector(tmp_path: Path) -> None:
    registry = load_accounting_validity_registry(_write_registry(tmp_path, [_record()]))

    with pytest.raises(InvalidPerformanceEvidenceError, match="CONFIRMED_AFFECTED"):
        assert_metric_family_allowed(
            registry,
            artifact_path="results/source.json",
            source_sha256="a" * 64,
            metric_family="promotion_gate",
            stage="A0",
        )


@pytest.mark.parametrize(
    ("state", "superseding_artifact"),
    [
        (ValidityState.PROVEN_UNAFFECTED, None),
        (ValidityState.SUPERSEDED_REPAIRED, "results/repaired/source.json"),
    ],
)
def test_valid_or_repaired_states_allow_performance_evidence(
    tmp_path: Path,
    state: ValidityState,
    superseding_artifact: str | None,
) -> None:
    registry = load_accounting_validity_registry(
        _write_registry(
            tmp_path,
            [_record(state=state.value, superseding_artifact=superseding_artifact)],
        )
    )

    assert_metric_family_allowed(
        registry,
        artifact_path="results/source.json",
        source_sha256="a" * 64,
        metric_family="promotion_gate",
        stage="A0",
        selector="train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
    )


def test_registered_artifact_sha_mismatch_fails_closed(tmp_path: Path) -> None:
    registry = load_accounting_validity_registry(
        _write_registry(tmp_path, [_record(state=ValidityState.PROVEN_UNAFFECTED.value)])
    )

    with pytest.raises(InvalidPerformanceEvidenceError, match="source_sha256_mismatch"):
        assert_metric_family_allowed(
            registry,
            artifact_path="results/source.json",
            source_sha256="b" * 64,
            metric_family="promotion_gate",
            stage="A0",
            selector="train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
        )


def test_stage_none_fails_closed_for_stage_scoped_governed_evidence(tmp_path: Path) -> None:
    registry = load_accounting_validity_registry(_write_registry(tmp_path, [_record()]))

    with pytest.raises(InvalidPerformanceEvidenceError, match="stage_required"):
        assert_metric_family_allowed(
            registry,
            artifact_path="results/source.json",
            source_sha256="a" * 64,
            metric_family="promotion_gate",
            stage=None,
            selector="train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
        )


def _load_audit_script():
    path = Path("scripts/audit_legacy_accounting_integrity.py")
    spec = importlib.util.spec_from_file_location("audit_legacy_accounting_integrity_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_audit_cli_writes_canonical_sidecar_without_mutating_source(tmp_path: Path) -> None:
    cli = _load_audit_script()
    source = tmp_path / "source.json"
    source_bytes = b'{"raw":"legacy evidence"}\n'
    source.write_bytes(source_bytes)
    registry_path = _write_registry(
        tmp_path,
        [
            _record(
                artifact_path=str(source),
                source_sha256=hashlib.sha256(source_bytes).hexdigest(),
            )
        ],
    )
    output = tmp_path / "source.validity.json"

    exit_code = cli.main(["--source", str(source), "--registry", str(registry_path), "--output", str(output)])

    sidecar = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert source.read_bytes() == source_bytes
    assert sidecar["schema_version"] == "legacy-accounting-audit.v1"
    assert sidecar["source"] == {
        "artifact_path": str(source),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "size_bytes": len(source_bytes),
    }
    assert sidecar["records"][0]["state"] == "CONFIRMED_AFFECTED"
    assert sidecar["records"][0]["ledger_formula_hash"] == "sha256:" + "c" * 64
    assert sidecar["records"][0]["decision_at"] == "2026-08-23T00:00:00Z"
    assert sidecar["records"][0]["approving_decision"] == "legacy-accounting-integrity-plan-task-3"
    assert output.read_bytes() == json.dumps(sidecar, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_audit_cli_rejects_source_as_output_without_mutating_it(tmp_path: Path) -> None:
    cli = _load_audit_script()
    source = tmp_path / "source.json"
    source_bytes = b'{"raw":"legacy evidence"}\n'
    source.write_bytes(source_bytes)
    registry_path = _write_registry(
        tmp_path,
        [
            _record(
                artifact_path=str(source),
                source_sha256=hashlib.sha256(source_bytes).hexdigest(),
            )
        ],
    )

    exit_code = cli.main(["--source", str(source), "--registry", str(registry_path), "--output", str(source)])

    assert exit_code == 2
    assert source.read_bytes() == source_bytes


def test_audit_cli_rejects_unclassified_source_without_publishing(tmp_path: Path) -> None:
    cli = _load_audit_script()
    source = tmp_path / "unclassified.json"
    source.write_bytes(b"unclassified")
    registry_path = _write_registry(tmp_path, [_record(artifact_path="results/other.json")])
    output = tmp_path / "audit.json"

    exit_code = cli.main(["--source", str(source), "--registry", str(registry_path), "--output", str(output)])

    assert exit_code == 2
    assert not output.exists()


def test_audit_cli_temp_name_cannot_alias_dot_tmp_source(tmp_path: Path) -> None:
    cli = _load_audit_script()
    output = tmp_path / "a.json"
    source = tmp_path / "a.json.tmp"
    source_bytes = b'{"raw":"legacy evidence"}\n'
    source.write_bytes(source_bytes)
    registry_path = _write_registry(
        tmp_path,
        [
            _record(
                artifact_path=str(source),
                source_sha256=hashlib.sha256(source_bytes).hexdigest(),
            )
        ],
    )

    exit_code = cli.main(["--source", str(source), "--registry", str(registry_path), "--output", str(output)])

    assert exit_code == 0
    assert source.read_bytes() == source_bytes
    assert (
        json.loads(output.read_text(encoding="utf-8"))["source"]["sha256"] == hashlib.sha256(source_bytes).hexdigest()
    )


def test_default_registry_is_loadable_outside_repository_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    registry = load_accounting_validity_registry()

    assert registry.records
