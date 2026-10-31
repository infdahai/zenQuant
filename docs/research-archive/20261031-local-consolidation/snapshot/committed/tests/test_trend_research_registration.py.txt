"""Immutable registration and evidence-role contracts for trend research."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest
import yaml

from zenquant.research import registration as registration_contracts
from zenquant.research.registration import (
    DAY_NS,
    FOUR_HOURS_NS,
    EvidenceRole,
    RegistrationError,
    RegistrationReceipt,
    build_registration_envelope,
    canonical_sha256,
    classify_evidence_role,
    derive_forward_start_ns,
    load_hypothesis_manifest,
    resolve_bootstrap_registration,
    resolve_manual_registration,
    verify_github_receipt,
)

_COMMIT_A = "b" * 40
_COMMIT_B = "d" * 40


def _receipt(
    received_at_ns: int,
    *,
    manifest_sha256: str = "a" * 64,
    provider_run_id: int | str = 17,
    commit_sha: str = _COMMIT_A,
    remote_identity: str = "example/repo",
) -> RegistrationReceipt:
    return RegistrationReceipt(
        manifest_sha256,
        provider_run_id,
        commit_sha,
        remote_identity,
        received_at_ns,
    )


def _manifest_payload() -> dict[str, object]:
    return {
        "schema_version": "trend-research-hypothesis.v1",
        "layer": "time_space_shadow",
        "asset": "BTC",
        "active_signal_source": "BINANCE_SPOT_BTCUSDT_1D",
        "reused_window": [1767225600000000000, 1787529600000000000],
        "source_requirements": [
            {
                "id": "BINANCE_SPOT_BTCUSDT_1D",
                "requirement": "REQUIRED",
                "fallback": "NONE",
            },
            {
                "id": "BINANCE_PERPETUAL_BTCUSDT_5M",
                "requirement": "REQUIRED",
                "fallback": "NONE",
            },
            {
                "id": "BINANCE_PERPETUAL_BTCUSDT_FUNDING",
                "requirement": "REQUIRED",
                "fallback": "NONE",
            },
        ],
        "fallback": "NONE",
        "seeds": [42, 123, 456, 789, 1024],
        "training": False,
        "parameter_search": False,
        "policy": {"formula": "frozen"},
    }


def _github_run(
    *,
    run_id: int = 17,
    head_sha: str = _COMMIT_A,
    remote_identity: str = "example/repo",
    created_at: str = "2026-08-24T08:00:00Z",
) -> dict[str, object]:
    return {
        "id": run_id,
        "head_sha": head_sha,
        "repository": {"full_name": remote_identity},
        "created_at": created_at,
    }


def _envelope(*, commit: str = _COMMIT_A) -> dict[str, object]:
    return {
        "provider_run_id": 17,
        "commit_sha": commit,
        "remote_identity": "example/repo",
        "manifest_sha256": "a" * 64,
        "received_at_ns": 1787558400000000000,
    }


def test_receipt_uses_provider_created_at_not_local_git_time(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    envelope = build_registration_envelope(manifest_path, _COMMIT_A, _github_run())
    assert envelope["received_at_ns"] == 1787558400000000000
    assert envelope["provider_run_id"] == "17"


def test_verified_receipt_retains_authoritative_provider_identity(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    provider = _github_run()
    envelope = build_registration_envelope(manifest_path, _COMMIT_A, provider)
    receipt = verify_github_receipt(envelope, provider)
    assert (receipt.provider_run_id, receipt.commit_sha) == ("17", _COMMIT_A)


def test_verifier_accepts_legacy_numeric_provider_run_id() -> None:
    receipt = verify_github_receipt(_envelope(), _github_run())
    assert receipt.provider_run_id == "17"


def test_receipt_retains_canonical_provider_run_and_commit_identity() -> None:
    receipt = _receipt(100, provider_run_id=17)
    assert receipt.provider_run_id == "17"
    assert receipt.commit_sha == _COMMIT_A


@pytest.mark.parametrize("provider_run_id", [True, 0, -1, "", "0", "017", "+17", "run-17"])
def test_receipt_rejects_noncanonical_provider_run_id(provider_run_id: object) -> None:
    with pytest.raises(RegistrationError, match="provider run id"):
        _receipt(100, provider_run_id=provider_run_id)  # type: ignore[arg-type]


@pytest.mark.parametrize("commit_sha", ["abc123", "A" * 40, "g" * 40, "a" * 39, "a" * 41])
def test_receipt_rejects_noncanonical_commit_sha(commit_sha: str) -> None:
    with pytest.raises(RegistrationError, match="commit SHA"):
        _receipt(100, commit_sha=commit_sha)


def test_verifier_rejects_changed_commit_or_manifest() -> None:
    with pytest.raises(RegistrationError, match="provider identity mismatch"):
        verify_github_receipt(_envelope(commit="a" * 40), _github_run(head_sha="c" * 40))


@pytest.mark.parametrize(
    ("branch_name", "manifest_kind", "manifest_path"),
    [
        (
            "feature/trend-registration-time-space-hypothesis",
            "hypothesis",
            "config/research/time_space_shadow_hypothesis_v1.json",
        ),
        (
            "feature/trend-registration-trend-participation-hypothesis",
            "hypothesis",
            "config/research/trend_participation_hypothesis_v1.json",
        ),
        (
            "feature/trend-registration-time-space-execution",
            "execution",
            "config/research/registrations/time_space_shadow_execution_v1.json",
        ),
        (
            "feature/trend-registration-trend-participation-execution",
            "execution",
            "config/research/registrations/trend_participation_execution_v1.json",
        ),
    ],
)
def test_bootstrap_branch_selects_its_exact_registration_manifest(
    branch_name: str, manifest_kind: str, manifest_path: str
) -> None:
    assert resolve_bootstrap_registration(branch_name) == (manifest_kind, manifest_path)


def test_bootstrap_branch_rejects_arbitrary_ref_or_path_selection() -> None:
    with pytest.raises(RegistrationError, match="unmapped bootstrap branch"):
        resolve_bootstrap_registration("codex/late-dev-cost-attribution")


@pytest.mark.parametrize(
    ("manifest_kind", "manifest_path"),
    [
        ("hypothesis", "config/research/time_space_shadow_hypothesis_v1.json"),
        ("hypothesis", "config/research/trend_participation_hypothesis_v1.json"),
        ("execution", "config/research/registrations/time_space_shadow_execution_v1.json"),
        ("execution", "config/research/registrations/trend_participation_execution_v1.json"),
    ],
)
def test_manual_main_dispatch_accepts_only_exact_registration_pairs(manifest_kind: str, manifest_path: str) -> None:
    assert resolve_manual_registration("refs/heads/main", manifest_kind, manifest_path) == (
        manifest_kind,
        manifest_path,
    )


@pytest.mark.parametrize(
    ("manifest_kind", "manifest_path"),
    [
        ("execution", "config/research/time_space_shadow_hypothesis_v1.json"),
        ("hypothesis", "config/research/registrations/time_space_shadow_execution_v1.json"),
        ("hypothesis", "config/research/legacy_accounting_rerun_v1.json"),
    ],
)
def test_manual_main_dispatch_rejects_mismatched_or_other_tracked_paths(manifest_kind: str, manifest_path: str) -> None:
    with pytest.raises(RegistrationError, match="unsupported registration manifest"):
        resolve_manual_registration("refs/heads/main", manifest_kind, manifest_path)


def test_manual_dispatch_rejects_any_non_main_ref() -> None:
    with pytest.raises(RegistrationError, match="requires refs/heads/main"):
        resolve_manual_registration(
            "refs/heads/codex/late-dev-cost-attribution",
            "hypothesis",
            "config/research/time_space_shadow_hypothesis_v1.json",
        )


def test_workflow_triggers_only_the_four_bootstrap_push_refs_and_dispatch() -> None:
    workflow = yaml.load(
        Path(".github/workflows/trend-research-registration.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert set(workflow["on"]) == {"push", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == [
        "feature/trend-registration-time-space-hypothesis",
        "feature/trend-registration-trend-participation-hypothesis",
        "feature/trend-registration-time-space-execution",
        "feature/trend-registration-trend-participation-execution",
    ]


def test_dependency_free_cli_selects_bootstrap_manifest(tmp_path: Path) -> None:
    github_env = tmp_path / "github.env"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "scripts/build_trend_registration_receipt.py",
            "select-bootstrap",
            "--branch-name",
            "feature/trend-registration-time-space-hypothesis",
            "--github-env",
            str(github_env),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert github_env.read_text(encoding="utf-8") == (
        "MANIFEST_KIND=hypothesis\nMANIFEST_PATH=config/research/time_space_shadow_hypothesis_v1.json\n"
    )


def test_dependency_free_cli_builds_receipt(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    provider_path = tmp_path / "provider-run.json"
    provider_path.write_text(json.dumps(_github_run()), encoding="utf-8")
    output_path = tmp_path / "registration-receipt.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "scripts/build_trend_registration_receipt.py",
            "build",
            "--manifest-path",
            str(manifest_path),
            "--commit-sha",
            _COMMIT_A,
            "--provider-run-json",
            str(provider_path),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output_path.read_text(encoding="utf-8"))["received_at_ns"] == 1787558400000000000


def _run_dependency_free_registration_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", "scripts/build_trend_registration_receipt.py", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_dependency_free_cli_selects_valid_manual_registration(tmp_path: Path) -> None:
    github_env = tmp_path / "github.env"
    result = _run_dependency_free_registration_cli(
        "select-manual",
        "--github-ref",
        "refs/heads/main",
        "--manifest-kind",
        "hypothesis",
        "--manifest-path",
        "config/research/trend_participation_hypothesis_v1.json",
        "--github-env",
        str(github_env),
    )
    assert result.returncode == 0, result.stderr
    assert github_env.read_text(encoding="utf-8") == (
        "MANIFEST_KIND=hypothesis\nMANIFEST_PATH=config/research/trend_participation_hypothesis_v1.json\n"
    )


@pytest.mark.parametrize(
    ("github_ref", "manifest_kind", "manifest_path"),
    [
        ("refs/heads/feature/other", "hypothesis", "config/research/time_space_shadow_hypothesis_v1.json"),
        ("refs/heads/main", "execution", "config/research/time_space_shadow_hypothesis_v1.json"),
    ],
)
def test_dependency_free_cli_rejects_invalid_manual_registration(
    tmp_path: Path, github_ref: str, manifest_kind: str, manifest_path: str
) -> None:
    result = _run_dependency_free_registration_cli(
        "select-manual",
        "--github-ref",
        github_ref,
        "--manifest-kind",
        manifest_kind,
        "--manifest-path",
        manifest_path,
        "--github-env",
        str(tmp_path / "github.env"),
    )
    assert result.returncode != 0


def test_dependency_free_cli_validates_exact_registration_pair() -> None:
    result = _run_dependency_free_registration_cli(
        "validate",
        "--manifest-kind",
        "execution",
        "--manifest-path",
        "config/research/registrations/trend_participation_execution_v1.json",
    )
    assert result.returncode == 0, result.stderr


def test_dependency_free_cli_rejects_invalid_registration_pair() -> None:
    result = _run_dependency_free_registration_cli(
        "validate",
        "--manifest-kind",
        "hypothesis",
        "--manifest-path",
        "config/research/registrations/trend_participation_execution_v1.json",
    )
    assert result.returncode != 0


def _write_manifest(tmp_path: Path, payload: dict[str, object] | None = None) -> Path:
    path = tmp_path / "hypothesis.json"
    path.write_text(json.dumps(payload or _manifest_payload()), encoding="utf-8")
    return path


def _descriptor_pair(
    tmp_path: Path,
    *,
    execution_layer: str = "time_space_shadow",
    execution_hypothesis_sha256: str | None = None,
    execution_formula_sha256: str | None = None,
) -> tuple[object, object]:
    hypothesis = load_hypothesis_manifest(_write_manifest(tmp_path))
    payload = json.loads(
        Path("config/research/registrations/time_space_shadow_execution_v1.json").read_text(encoding="utf-8")
    )
    payload["layer"] = execution_layer
    payload["hypothesis_sha256"] = execution_hypothesis_sha256 or hypothesis.sha256
    payload["config_sha256"] = f"sha256:{payload['hypothesis_sha256']}"
    formula_fields = ("formula", "thresholds", "costs", "robustness", "gates", "policy", "authority")
    formula_payload = {key: _manifest_payload()[key] for key in formula_fields if key in _manifest_payload()}
    payload["formula_sha256"] = execution_formula_sha256 or f"sha256:{canonical_sha256(formula_payload)}"
    path = tmp_path / "execution.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    execution = registration_contracts.load_execution_manifest(path)
    return hypothesis, execution


def test_canonical_sha256_is_stable_for_mapping_order() -> None:
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


def test_hypothesis_descriptor_rejects_unknown_layer(tmp_path: Path) -> None:
    payload = _manifest_payload()
    payload["layer"] = "arbitrary"
    with pytest.raises(RegistrationError, match="unsupported hypothesis layer"):
        load_hypothesis_manifest(_write_manifest(tmp_path, payload))


def test_execution_descriptor_is_built_from_validated_local_manifest(tmp_path: Path) -> None:
    source = Path("config/research/registrations/time_space_shadow_execution_v1.json")
    path = tmp_path / "execution.json"
    path.write_bytes(source.read_bytes())
    execution = registration_contracts.load_execution_manifest(path)
    assert execution.schema_version == "trend-research-execution-manifest.v1"
    assert execution.layer == "time_space_shadow"
    assert execution.sha256 == canonical_sha256(json.loads(path.read_text()))


@pytest.mark.parametrize("layer", ["time_space_shadow", "trend_participation"])
def test_execution_descriptor_loads_frozen_checked_in_manifest(layer: str) -> None:
    hypothesis = load_hypothesis_manifest(Path(f"config/research/{layer}_hypothesis_v1.json"))
    execution = registration_contracts.load_execution_manifest(
        Path(f"config/research/registrations/{layer}_execution_v1.json")
    )
    assert execution.layer == hypothesis.layer
    assert execution.hypothesis_sha256 == hypothesis.sha256


@pytest.mark.parametrize(
    "payload",
    [
        {"arbitrary": "json"},
        {
            "schema_version": "trend-research-hypothesis.v1",
            "layer": "test",
            "hypothesis_sha256": "a" * 64,
        },
        {
            "schema_version": "trend-research-execution-manifest.v1",
            "layer": "time_space_shadow",
        },
        {
            "schema_version": "trend-research-execution-manifest.v1",
            "layer": "arbitrary",
            "hypothesis_sha256": "a" * 64,
            "fixed_end_cutoff_ns": 1787529600000000000,
        },
        {
            "schema_version": "trend-research-execution-manifest.v1",
            "layer": "time_space_shadow",
            "hypothesis_sha256": "a" * 64,
            "fixed_end_cutoff_ns": 1787529600000000000,
        },
    ],
)
def test_execution_descriptor_rejects_arbitrary_or_wrong_kind_manifest(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    path = tmp_path / "execution.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RegistrationError):
        registration_contracts.load_execution_manifest(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_top_level",
        "wrong_window",
        "wrong_checkpoint_rule",
        "missing_source",
        "source_not_ready",
        "source_not_required",
        "source_fallback",
        "source_set_hash",
        "formula_hash",
        "config_hash",
        "construction_shape",
        "construction_hash",
        "accounting_shape",
        "accounting_hash",
        "workspace_shape",
        "workspace_hash",
        "recursive_sha",
        "forbidden_result_key",
    ],
)
def test_execution_descriptor_rejects_schema_and_identity_mutations(tmp_path: Path, mutation: str) -> None:
    source = Path("config/research/registrations/time_space_shadow_execution_v1.json")
    payload = deepcopy(json.loads(source.read_text(encoding="utf-8")))
    if mutation == "unknown_top_level":
        payload["unexpected"] = 1
    elif mutation == "wrong_window":
        payload["reused_window"][1] -= 1
    elif mutation == "wrong_checkpoint_rule":
        payload["forward_checkpoint_rule"] = "self_asserted"
    elif mutation == "missing_source":
        payload["sources"].pop("BINANCE_PERPETUAL_BTCUSDT_FUNDING")
    elif mutation == "source_not_ready":
        payload["sources"]["BINANCE_SPOT_BTCUSDT_1D"]["status"] = "MISSING"
    elif mutation == "source_not_required":
        payload["sources"]["BINANCE_SPOT_BTCUSDT_1D"]["requirement"] = "OPTIONAL"
    elif mutation == "source_fallback":
        payload["sources"]["BINANCE_SPOT_BTCUSDT_1D"]["fallback"] = "PROXY"
    elif mutation == "source_set_hash":
        payload["source_set_sha256"] = "sha256:" + "0" * 64
    elif mutation == "formula_hash":
        payload["formula_sha256"] = "0" * 64
    elif mutation == "config_hash":
        payload["config_sha256"] = "sha256:" + "0" * 64
    elif mutation == "construction_shape":
        payload["construction_identity"].pop("files")
    elif mutation == "construction_hash":
        payload["construction_identity"]["sha256"] = "sha256:" + "0" * 64
    elif mutation == "accounting_shape":
        payload["accounting_kernel"]["schema_version"] = "arbitrary"
    elif mutation == "accounting_hash":
        payload["accounting_kernel"]["sha256"] = "sha256:" + "0" * 64
    elif mutation == "workspace_shape":
        payload["workspace_state"].pop("head")
    elif mutation == "workspace_hash":
        payload["workspace_state"]["sha256"] = "sha256:" + "0" * 64
    elif mutation == "recursive_sha":
        payload["sources"]["BINANCE_SPOT_BTCUSDT_1D"]["raw_sha256"][0]["sha256"] = "bad"
    elif mutation == "forbidden_result_key":
        payload["sources"]["BINANCE_SPOT_BTCUSDT_1D"]["pnl"] = 1
    path = tmp_path / "execution.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RegistrationError):
        registration_contracts.load_execution_manifest(path)


def test_forward_start_uses_later_receipt_plus_24h_and_ceil_4h(tmp_path: Path) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path)
    receipts = (
        _receipt(100, manifest_sha256=hypothesis.sha256, provider_run_id=17),
        _receipt(
            200,
            manifest_sha256=execution.sha256,
            provider_run_id=18,
            commit_sha=_COMMIT_B,
        ),
    )
    expected = ((200 + DAY_NS + FOUR_HOURS_NS - 1) // FOUR_HOURS_NS) * FOUR_HOURS_NS
    assert (
        derive_forward_start_ns(
            hypothesis,
            execution,
            receipts,
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )
        == expected
    )


def test_forward_start_requires_exactly_two_receipts(tmp_path: Path) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path)
    with pytest.raises(RegistrationError, match="two trusted receipts"):
        derive_forward_start_ns(
            hypothesis,
            execution,
            (_receipt(100, manifest_sha256=hypothesis.sha256),),
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


@pytest.mark.parametrize(
    ("receipt_roles", "message"),
    [
        (("execution", "hypothesis"), "ordered hypothesis and execution"),
        (("hypothesis", "hypothesis"), "execution receipt manifest hash mismatch"),
        (("hypothesis", "unexpected"), "execution receipt manifest hash mismatch"),
    ],
)
def test_registration_pair_rejects_swapped_duplicate_or_unexpected_receipts(
    tmp_path: Path, receipt_roles: tuple[str, str], message: str
) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path)
    hashes = {
        "hypothesis": hypothesis.sha256,
        "execution": execution.sha256,
        "unexpected": "f" * 64,
    }
    receipts = (
        _receipt(100, manifest_sha256=hashes[receipt_roles[0]], provider_run_id=17),
        _receipt(
            200,
            manifest_sha256=hashes[receipt_roles[1]],
            provider_run_id=18,
            commit_sha=_COMMIT_B,
        ),
    )
    with pytest.raises(RegistrationError, match=message):
        derive_forward_start_ns(
            hypothesis,
            execution,
            receipts,
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


def test_registration_pair_rejects_cross_layer_execution(tmp_path: Path) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path, execution_layer="trend_participation")
    with pytest.raises(RegistrationError, match="same layer"):
        derive_forward_start_ns(
            hypothesis,
            execution,
            (),
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


def test_registration_pair_rejects_execution_linked_to_other_hypothesis(tmp_path: Path) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path, execution_hypothesis_sha256="f" * 64)
    with pytest.raises(RegistrationError, match="does not bind hypothesis"):
        derive_forward_start_ns(
            hypothesis,
            execution,
            (),
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


def test_registration_pair_rejects_execution_with_other_formula(tmp_path: Path) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path, execution_formula_sha256="sha256:" + "0" * 64)
    with pytest.raises(RegistrationError, match="formula hash"):
        derive_forward_start_ns(
            hypothesis,
            execution,
            (),
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


def test_registration_pair_rejects_two_hypothesis_descriptors(tmp_path: Path) -> None:
    hypothesis = load_hypothesis_manifest(_write_manifest(tmp_path))
    with pytest.raises(RegistrationError, match="execution descriptor"):
        derive_forward_start_ns(
            hypothesis,
            hypothesis,
            (),
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


@pytest.mark.parametrize("mutation", ["sha", "field", "payload"])
def test_registration_pair_rejects_forged_execution_descriptor(tmp_path: Path, mutation: str) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path)
    if mutation == "sha":
        execution = replace(execution, sha256="f" * 64)
    elif mutation == "field":
        execution = replace(execution, hypothesis_sha256="f" * 64)
    else:
        payload = dict(execution.payload)
        payload["forward_checkpoint_rule"] = "forged"
        execution = replace(execution, payload=MappingProxyType(payload))
    with pytest.raises(RegistrationError, match="execution descriptor"):
        derive_forward_start_ns(
            hypothesis,
            execution,
            (),
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


@pytest.mark.parametrize("mutation", ["sha", "field", "payload"])
def test_registration_pair_rejects_forged_hypothesis_descriptor(tmp_path: Path, mutation: str) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path)
    if mutation == "sha":
        hypothesis = replace(hypothesis, sha256="f" * 64)
    elif mutation == "field":
        hypothesis = replace(hypothesis, layer="trend_participation")
    else:
        payload = dict(hypothesis.payload)
        payload["fallback"] = "PROXY"
        hypothesis = replace(hypothesis, payload=MappingProxyType(payload))
    with pytest.raises(RegistrationError, match="hypothesis descriptor"):
        derive_forward_start_ns(
            hypothesis,
            execution,
            (),
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


def test_registration_pair_rejects_same_provider_run(tmp_path: Path) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path)
    receipts = (
        _receipt(100, manifest_sha256=hypothesis.sha256, provider_run_id=17),
        _receipt(200, manifest_sha256=execution.sha256, provider_run_id="17", commit_sha=_COMMIT_B),
    )
    with pytest.raises(RegistrationError, match="provider runs must be distinct"):
        derive_forward_start_ns(
            hypothesis,
            execution,
            receipts,
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


def test_registration_pair_rejects_unexpected_provider_run_or_commit(tmp_path: Path) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path)
    receipts = (
        _receipt(100, manifest_sha256=hypothesis.sha256, provider_run_id=17),
        _receipt(200, manifest_sha256=execution.sha256, provider_run_id=18, commit_sha=_COMMIT_B),
    )
    with pytest.raises(RegistrationError, match="execution receipt provider identity mismatch"):
        derive_forward_start_ns(
            hypothesis,
            execution,
            receipts,
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=19,
            expected_execution_commit_sha="e" * 40,
        )


@pytest.mark.parametrize("mismatched_role", ["hypothesis", "execution"])
def test_registration_pair_requires_both_receipts_on_expected_remote(tmp_path: Path, mismatched_role: str) -> None:
    hypothesis, execution = _descriptor_pair(tmp_path)
    receipts = (
        _receipt(
            100,
            manifest_sha256=hypothesis.sha256,
            provider_run_id=17,
            remote_identity="other/repo" if mismatched_role == "hypothesis" else "example/repo",
        ),
        _receipt(
            200,
            manifest_sha256=execution.sha256,
            provider_run_id=18,
            commit_sha=_COMMIT_B,
            remote_identity="other/repo" if mismatched_role == "execution" else "example/repo",
        ),
    )
    with pytest.raises(RegistrationError, match=f"{mismatched_role} receipt remote identity mismatch"):
        derive_forward_start_ns(
            hypothesis,
            execution,
            receipts,
            expected_remote_identity="example/repo",
            expected_hypothesis_provider_run_id=17,
            expected_hypothesis_commit_sha=_COMMIT_A,
            expected_execution_provider_run_id=18,
            expected_execution_commit_sha=_COMMIT_B,
        )


def test_forward_start_requires_receipt_binding() -> None:
    with pytest.raises(RegistrationError, match="receipt binding is required"):
        derive_forward_start_ns(None, None, ())  # type: ignore[arg-type]


def test_backfilled_old_event_remains_reused() -> None:
    assert (
        classify_evidence_role(99, reused_start_ns=50, reused_end_ns=100, forward_start_ns=120)
        is EvidenceRole.TEST_REUSED_DIAGNOSTIC
    )


def test_pre_reused_window_is_discovery_only() -> None:
    assert (
        classify_evidence_role(49, reused_start_ns=50, reused_end_ns=100, forward_start_ns=120)
        is EvidenceRole.DISCOVERY_ONLY
    )


def test_reused_end_starts_excluded_embargo() -> None:
    assert (
        classify_evidence_role(100, reused_start_ns=50, reused_end_ns=100, forward_start_ns=120)
        is EvidenceRole.EXCLUDED_EMBARGO
    )


def test_event_at_forward_start_is_forward_confirmation() -> None:
    assert (
        classify_evidence_role(120, reused_start_ns=50, reused_end_ns=100, forward_start_ns=120)
        is EvidenceRole.FORWARD_CONFIRMATION
    )


def test_manifest_loader_returns_frozen_identity_and_common_contract(tmp_path: Path) -> None:
    manifest = load_hypothesis_manifest(_write_manifest(tmp_path))
    assert manifest.asset == "BTC"
    assert manifest.active_signal_source == "BINANCE_SPOT_BTCUSDT_1D"
    assert manifest.reused_start_ns == 1767225600000000000
    assert manifest.reused_end_ns == 1787529600000000000
    assert manifest.seeds == (42, 123, 456, 789, 1024)
    assert manifest.training is False
    assert manifest.parameter_search is False
    assert len(manifest.sha256) == 64


@pytest.mark.parametrize("name", ["time_space_shadow", "trend_participation"])
def test_checked_in_hypothesis_freezes_window_sources_and_search_policy(name: str) -> None:
    manifest = load_hypothesis_manifest(Path(f"config/research/{name}_hypothesis_v1.json"))
    assert manifest.reused_start_ns == 1767225600000000000
    assert manifest.reused_end_ns == 1787529600000000000
    assert manifest.training is False
    assert manifest.parameter_search is False
    assert manifest.seeds == (42, 123, 456, 789, 1024)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update(unexpected=True), "unknown manifest key"),
        (lambda payload: payload.update(fallback="LAST_KNOWN"), "fallback must be NONE"),
        (lambda payload: payload.update(seeds=[42]), "seeds are frozen"),
        (lambda payload: payload.update(reused_window=[1767225600000000000, 0]), "reused window"),
    ],
)
def test_manifest_loader_rejects_contract_mutations(tmp_path: Path, mutation, message: str) -> None:
    payload = _manifest_payload()
    mutation(payload)
    with pytest.raises(RegistrationError, match=message):
        load_hypothesis_manifest(_write_manifest(tmp_path, payload))


def test_manifest_loader_rejects_noncanonical_sha_strings(tmp_path: Path) -> None:
    payload = _manifest_payload()
    payload["formula_sha256"] = "A" * 64
    with pytest.raises(RegistrationError, match="canonical SHA-256"):
        load_hypothesis_manifest(_write_manifest(tmp_path, payload))


@pytest.mark.parametrize(
    "missing_id",
    [
        "BINANCE_SPOT_BTCUSDT_1D",
        "BINANCE_PERPETUAL_BTCUSDT_5M",
        "BINANCE_PERPETUAL_BTCUSDT_FUNDING",
    ],
)
def test_manifest_loader_rejects_missing_required_source(tmp_path: Path, missing_id: str) -> None:
    payload = _manifest_payload()
    payload["source_requirements"] = [source for source in payload["source_requirements"] if source["id"] != missing_id]
    with pytest.raises(RegistrationError, match="mandatory source"):
        load_hypothesis_manifest(_write_manifest(tmp_path, payload))


def test_manifest_loader_rejects_optional_source(tmp_path: Path) -> None:
    payload = _manifest_payload()
    payload["source_requirements"][0]["requirement"] = "OPTIONAL"
    with pytest.raises(RegistrationError, match="requirement must be REQUIRED"):
        load_hypothesis_manifest(_write_manifest(tmp_path, payload))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda sources: sources[0].update(extra="not-allowed"),
        lambda sources: sources[0].pop("fallback"),
        lambda sources: sources.append(
            {
                "id": "UNKNOWN_SOURCE",
                "requirement": "REQUIRED",
                "fallback": "NONE",
            }
        ),
    ],
)
def test_manifest_loader_rejects_unknown_or_malformed_source(tmp_path: Path, mutation) -> None:
    payload = _manifest_payload()
    mutation(payload["source_requirements"])
    with pytest.raises(RegistrationError, match="source"):
        load_hypothesis_manifest(_write_manifest(tmp_path, payload))
