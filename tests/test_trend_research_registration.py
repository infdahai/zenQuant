"""Immutable registration and evidence-role contracts for trend research."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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
    validate_registration_receipts,
    verify_github_receipt,
)


def _receipt(received_at_ns: int) -> RegistrationReceipt:
    return RegistrationReceipt("a" * 64, "b" * 40, "example/repo", received_at_ns)


def _manifest_payload() -> dict[str, object]:
    return {
        "schema_version": "trend-research-hypothesis.v1",
        "layer": "test",
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
    head_sha: str = "abc123",
    remote_identity: str = "example/repo",
    created_at: str = "2026-08-24T08:00:00Z",
) -> dict[str, object]:
    return {
        "id": run_id,
        "head_sha": head_sha,
        "repository": {"full_name": remote_identity},
        "created_at": created_at,
    }


def _envelope(*, commit: str = "abc123") -> dict[str, object]:
    return {
        "provider_run_id": 17,
        "commit_sha": commit,
        "remote_identity": "example/repo",
        "manifest_sha256": "a" * 64,
        "received_at_ns": 1787558400000000000,
    }


def test_receipt_uses_provider_created_at_not_local_git_time(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    envelope = build_registration_envelope(manifest_path, "abc123", _github_run())
    assert envelope["received_at_ns"] == 1787558400000000000


def test_verifier_rejects_changed_commit_or_manifest() -> None:
    with pytest.raises(RegistrationError, match="provider identity mismatch"):
        verify_github_receipt(_envelope(commit="a"), _github_run(head_sha="b"))


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
            "abc123",
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


def _write_manifest(tmp_path: Path, payload: dict[str, object] | None = None) -> Path:
    path = tmp_path / "hypothesis.json"
    path.write_text(json.dumps(payload or _manifest_payload()), encoding="utf-8")
    return path


def test_canonical_sha256_is_stable_for_mapping_order() -> None:
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


def test_forward_start_uses_later_receipt_plus_24h_and_ceil_4h() -> None:
    receipts = (_receipt(100), _receipt(200))
    expected = ((200 + DAY_NS + FOUR_HOURS_NS - 1) // FOUR_HOURS_NS) * FOUR_HOURS_NS
    assert (
        derive_forward_start_ns(
            receipts,
            expected_manifest_sha256="a" * 64,
            expected_remote_identity="example/repo",
        )
        == expected
    )


def test_forward_start_requires_exactly_two_receipts() -> None:
    with pytest.raises(RegistrationError, match="two trusted receipts"):
        derive_forward_start_ns(
            (_receipt(100),),
            expected_manifest_sha256="a" * 64,
            expected_remote_identity="example/repo",
        )


def test_receipts_bind_to_expected_manifest_and_remote_before_forward_start() -> None:
    receipts = (_receipt(100), _receipt(200))
    assert (
        validate_registration_receipts(
            receipts,
            expected_manifest_sha256="a" * 64,
            expected_remote_identity="example/repo",
        )
        == receipts
    )


@pytest.mark.parametrize(
    ("manifest_sha256", "remote_identity", "message"),
    [
        ("c" * 64, "example/repo", "manifest hash mismatch"),
        ("a" * 64, "other/repo", "remote identity mismatch"),
    ],
)
def test_receipt_binding_rejects_manifest_or_remote_mismatch(
    manifest_sha256: str, remote_identity: str, message: str
) -> None:
    receipt = RegistrationReceipt(manifest_sha256, "b" * 40, remote_identity, 100)
    with pytest.raises(RegistrationError, match=message):
        validate_registration_receipts(
            (receipt,),
            expected_manifest_sha256="a" * 64,
            expected_remote_identity="example/repo",
        )


def test_forward_start_requires_receipt_binding() -> None:
    with pytest.raises(RegistrationError, match="receipt binding is required"):
        derive_forward_start_ns((_receipt(100), _receipt(200)))


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
