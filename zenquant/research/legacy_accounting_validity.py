"""Fail-closed governance for performance evidence from legacy accounting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

ACCOUNTING_VALIDITY_SCHEMA_VERSION = "legacy-accounting-validity.v1"
DEFAULT_ACCOUNTING_VALIDITY_REGISTRY = Path("config/research/legacy_accounting_validity_v1.json")


class ValidityState(StrEnum):
    """Global validity states for historical performance evidence."""

    CONFIRMED_AFFECTED = "CONFIRMED_AFFECTED"
    UNVERIFIED_LEGACY = "UNVERIFIED_LEGACY"
    PROVEN_UNAFFECTED = "PROVEN_UNAFFECTED"
    SUPERSEDED_REPAIRED = "SUPERSEDED_REPAIRED"


class AccountingValidityRegistryError(ValueError):
    """The validity registry is malformed or internally ambiguous."""


class InvalidPerformanceEvidenceError(ValueError):
    """The requested performance evidence is not valid for use."""


@dataclass(frozen=True, slots=True)
class AccountingValidityRecord:
    """One immutable artifact validity classification."""

    artifact_path: str
    source_sha256: str
    selector: str
    state: ValidityState
    reason: str
    metric_families: tuple[str, ...]
    stages: tuple[str, ...]
    superseding_artifact: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-ready record representation."""
        return {
            "artifact_path": self.artifact_path,
            "source_sha256": self.source_sha256,
            "selector": self.selector,
            "state": self.state.value,
            "reason": self.reason,
            "metric_families": list(self.metric_families),
            "stages": list(self.stages),
            "superseding_artifact": self.superseding_artifact,
        }


@dataclass(frozen=True, slots=True)
class AccountingValidityRegistry:
    """Validated immutable collection of accounting validity records."""

    schema_version: str
    records: tuple[AccountingValidityRecord, ...]

    def records_for_artifact(self, artifact_path: str) -> tuple[AccountingValidityRecord, ...]:
        """Return records whose normalized artifact path matches the input."""
        normalized = _normalize_artifact_path(artifact_path)
        return tuple(record for record in self.records if record.artifact_path == normalized)


def load_accounting_validity_registry(
    path: Path = DEFAULT_ACCOUNTING_VALIDITY_REGISTRY,
) -> AccountingValidityRegistry:
    """Load and strictly validate an accounting validity registry."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise AccountingValidityRegistryError(f"unable to load accounting validity registry:{path}") from exc
    if not isinstance(payload, dict):
        raise AccountingValidityRegistryError("accounting validity registry must be a JSON object")
    if payload.get("schema_version") != ACCOUNTING_VALIDITY_SCHEMA_VERSION:
        raise AccountingValidityRegistryError(f"schema_version must be {ACCOUNTING_VALIDITY_SCHEMA_VERSION!r}")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise AccountingValidityRegistryError("records must be a non-empty list")

    records: list[AccountingValidityRecord] = []
    validity_keys: set[tuple[str, str, str]] = set()
    artifact_digests: dict[str, str] = {}
    for index, raw_record in enumerate(raw_records):
        record = _parse_record(raw_record, index=index)
        prior_digest = artifact_digests.setdefault(record.artifact_path, record.source_sha256)
        if prior_digest != record.source_sha256:
            raise AccountingValidityRegistryError(f"conflicting source_sha256 for artifact:{record.artifact_path}")
        for stage in record.stages:
            for metric_family in record.metric_families:
                key = (record.artifact_path, stage, metric_family)
                if key in validity_keys:
                    raise AccountingValidityRegistryError(f"duplicate validity key:{':'.join(key)}")
                validity_keys.add(key)
        records.append(record)
    return AccountingValidityRegistry(schema_version=ACCOUNTING_VALIDITY_SCHEMA_VERSION, records=tuple(records))


def assert_metric_family_allowed(
    registry: AccountingValidityRegistry,
    *,
    artifact_path: str,
    source_sha256: str | None,
    metric_family: str,
    stage: str | None,
) -> None:
    """Reject explicitly invalid evidence or a changed registered source."""
    records = registry.records_for_artifact(artifact_path)
    if not records:
        return
    expected_sha256 = records[0].source_sha256
    if source_sha256 != expected_sha256:
        raise InvalidPerformanceEvidenceError(
            f"source_sha256_mismatch:{_normalize_artifact_path(artifact_path)}:"
            f"expected={expected_sha256}:actual={source_sha256}"
        )
    matching = [
        record
        for record in records
        if metric_family in record.metric_families and stage is not None and stage in record.stages
    ]
    if not matching:
        return
    record = matching[0]
    if record.state in {ValidityState.CONFIRMED_AFFECTED, ValidityState.UNVERIFIED_LEGACY}:
        raise InvalidPerformanceEvidenceError(
            f"invalid_performance_evidence:{record.state.value}:{record.artifact_path}:"
            f"stage={stage}:metric_family={metric_family}:reason={record.reason}"
        )


def _parse_record(raw_record: object, *, index: int) -> AccountingValidityRecord:
    if not isinstance(raw_record, dict):
        raise AccountingValidityRegistryError(f"record {index} must be a JSON object")
    artifact_path = _required_string(raw_record.get("artifact_path"), "artifact_path", index=index)
    source_sha256 = _required_string(raw_record.get("source_sha256"), "source_sha256", index=index)
    if not _is_sha256(source_sha256):
        raise AccountingValidityRegistryError(f"record {index} source_sha256 must be lowercase hexadecimal")
    selector = _required_string(raw_record.get("selector"), "selector", index=index)
    reason = _required_string(raw_record.get("reason"), "reason", index=index)
    try:
        state = ValidityState(raw_record.get("state"))
    except (TypeError, ValueError) as exc:
        raise AccountingValidityRegistryError(f"record {index} has invalid validity state") from exc
    metric_families = _required_string_tuple(raw_record.get("metric_families"), "metric_families", index=index)
    stages = _required_string_tuple(raw_record.get("stages"), "stages", index=index)
    superseding_artifact = raw_record.get("superseding_artifact")
    if superseding_artifact is not None and (
        not isinstance(superseding_artifact, str) or not superseding_artifact.strip()
    ):
        raise AccountingValidityRegistryError(f"record {index} superseding_artifact must be null or non-empty")
    if state is ValidityState.SUPERSEDED_REPAIRED and superseding_artifact is None:
        raise AccountingValidityRegistryError(
            f"record {index} superseding_artifact is required for SUPERSEDED_REPAIRED"
        )
    return AccountingValidityRecord(
        artifact_path=_normalize_artifact_path(artifact_path),
        source_sha256=source_sha256,
        selector=selector,
        state=state,
        reason=reason,
        metric_families=metric_families,
        stages=stages,
        superseding_artifact=superseding_artifact,
    )


def _required_string(value: object, field: str, *, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AccountingValidityRegistryError(f"record {index} {field} must be non-empty")
    return value


def _required_string_tuple(value: object, field: str, *, index: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise AccountingValidityRegistryError(f"record {index} {field} must be a non-empty list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise AccountingValidityRegistryError(f"record {index} {field} entries must be non-empty strings")
    return tuple(value)


def _normalize_artifact_path(value: str) -> str:
    return Path(value).as_posix()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _reject_json_constant(value: str) -> None:
    raise AccountingValidityRegistryError(f"forbidden JSON constant:{value}")
