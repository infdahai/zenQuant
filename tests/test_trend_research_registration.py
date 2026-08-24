"""Immutable registration and evidence-role contracts for trend research."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zenquant.research.registration import (
    DAY_NS,
    FOUR_HOURS_NS,
    EvidenceRole,
    RegistrationError,
    RegistrationReceipt,
    canonical_sha256,
    classify_evidence_role,
    derive_forward_start_ns,
    load_hypothesis_manifest,
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
            {"id": "spot", "fallback": "NONE", "status": "REQUIRED"},
        ],
        "fallback": "NONE",
        "seeds": [42, 123, 456, 789, 1024],
        "training": False,
        "parameter_search": False,
        "policy": {"formula": "frozen"},
    }


def _write_manifest(tmp_path: Path, payload: dict[str, object] | None = None) -> Path:
    path = tmp_path / "hypothesis.json"
    path.write_text(json.dumps(payload or _manifest_payload()), encoding="utf-8")
    return path


def test_canonical_sha256_is_stable_for_mapping_order() -> None:
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


def test_forward_start_uses_later_receipt_plus_24h_and_ceil_4h() -> None:
    receipts = (_receipt(100), _receipt(200))
    expected = ((200 + DAY_NS + FOUR_HOURS_NS - 1) // FOUR_HOURS_NS) * FOUR_HOURS_NS
    assert derive_forward_start_ns(receipts) == expected


def test_forward_start_requires_exactly_two_receipts() -> None:
    with pytest.raises(RegistrationError, match="two trusted receipts"):
        derive_forward_start_ns((_receipt(100),))


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
