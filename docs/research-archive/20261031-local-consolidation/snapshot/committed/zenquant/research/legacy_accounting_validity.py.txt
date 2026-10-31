"""Fail-closed governance for performance evidence from legacy accounting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

ACCOUNTING_VALIDITY_SCHEMA_VERSION = "legacy-accounting-validity.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACCOUNTING_VALIDITY_REGISTRY = REPOSITORY_ROOT / "config/research/legacy_accounting_validity_v1.json"


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
    ledger_formula_hash: str
    decision_at: str
    approving_decision: str

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
            "ledger_formula_hash": self.ledger_formula_hash,
            "decision_at": self.decision_at,
            "approving_decision": self.approving_decision,
        }


@dataclass(frozen=True, slots=True)
class AccountingValidityRegistry:
    """Validated immutable collection of accounting validity records."""

    schema_version: str
    records: tuple[AccountingValidityRecord, ...]
    repository_root: Path

    def records_for_artifact(self, artifact_path: str) -> tuple[AccountingValidityRecord, ...]:
        """Return records whose normalized artifact path matches the input."""
        normalized = _canonical_artifact_identity(artifact_path, repository_root=self.repository_root)
        return tuple(record for record in self.records if record.artifact_path == normalized)

    def records_for_sha256(self, source_sha256: str) -> tuple[AccountingValidityRecord, ...]:
        """Return every record governing the exact source bytes."""
        return tuple(record for record in self.records if record.source_sha256 == source_sha256)


def load_accounting_validity_registry(
    path: Path = DEFAULT_ACCOUNTING_VALIDITY_REGISTRY,
    *,
    repository_root: Path = REPOSITORY_ROOT,
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
    repository_root = repository_root.resolve()
    validity_keys: set[tuple[str, str, str, str]] = set()
    source_validity_keys: set[tuple[str, str, str, str]] = set()
    artifact_digests: dict[str, str] = {}
    for index, raw_record in enumerate(raw_records):
        record = _parse_record(raw_record, index=index, repository_root=repository_root)
        prior_digest = artifact_digests.setdefault(record.artifact_path, record.source_sha256)
        if prior_digest != record.source_sha256:
            raise AccountingValidityRegistryError(f"conflicting source_sha256 for artifact:{record.artifact_path}")
        for stage in record.stages:
            for metric_family in record.metric_families:
                key = (record.artifact_path, record.selector, stage, metric_family)
                if key in validity_keys:
                    raise AccountingValidityRegistryError(
                        f"duplicate validity key:{record.artifact_path}:{stage}:{metric_family}:"
                        f"selector={record.selector}"
                    )
                validity_keys.add(key)
                source_key = (record.source_sha256, record.selector, stage, metric_family)
                if source_key in source_validity_keys:
                    raise AccountingValidityRegistryError(
                        f"duplicate source validity key:{record.source_sha256}:{stage}:{metric_family}:"
                        f"selector={record.selector}"
                    )
                source_validity_keys.add(source_key)
        records.append(record)
    return AccountingValidityRegistry(
        schema_version=ACCOUNTING_VALIDITY_SCHEMA_VERSION,
        records=tuple(records),
        repository_root=repository_root,
    )


def assert_metric_family_allowed(
    registry: AccountingValidityRegistry,
    *,
    artifact_path: str,
    source_sha256: str | None,
    metric_family: str,
    stage: str | None,
    selector: str | None = None,
) -> None:
    """Reject explicitly invalid evidence or a changed registered source."""
    path_records = registry.records_for_artifact(artifact_path)
    if path_records and source_sha256 not in {record.source_sha256 for record in path_records}:
        expected = sorted({record.source_sha256 for record in path_records})
        raise InvalidPerformanceEvidenceError(
            f"source_sha256_mismatch:{_canonical_artifact_identity(artifact_path, repository_root=registry.repository_root)}:"
            f"expected={expected}:actual={source_sha256}"
        )
    digest_records = registry.records_for_sha256(source_sha256) if source_sha256 is not None else ()
    records = tuple(dict.fromkeys((*path_records, *digest_records)))
    if not records:
        raise InvalidPerformanceEvidenceError(
            f"artifact_unclassified:"
            f"{_canonical_artifact_identity(artifact_path, repository_root=registry.repository_root)}:"
            f"source_sha256={source_sha256}"
        )
    metric_records = [record for record in records if metric_family in record.metric_families]
    if not metric_records:
        raise InvalidPerformanceEvidenceError(
            f"metric_family_unclassified:{artifact_path}:metric_family={metric_family}"
        )
    if selector is None:
        selectors = {record.selector for record in metric_records}
        if len(selectors) != 1:
            raise InvalidPerformanceEvidenceError(
                f"selector_required:"
                f"{_canonical_artifact_identity(artifact_path, repository_root=registry.repository_root)}:"
                f"metric_family={metric_family}"
            )
        selector = selectors.pop()
    selector_records = [record for record in metric_records if record.selector == selector]
    if not selector_records:
        raise InvalidPerformanceEvidenceError(
            f"selector_unclassified:{artifact_path}:selector={selector}:metric_family={metric_family}"
        )
    if stage is None:
        raise InvalidPerformanceEvidenceError(
            f"stage_required:{artifact_path}:selector={selector}:metric_family={metric_family}"
        )
    matching = [record for record in selector_records if stage in record.stages]
    if not matching:
        raise InvalidPerformanceEvidenceError(
            f"stage_unclassified:{artifact_path}:stage={stage}:selector={selector}:metric_family={metric_family}"
        )
    record = matching[0]
    if record.state in {ValidityState.CONFIRMED_AFFECTED, ValidityState.UNVERIFIED_LEGACY}:
        raise InvalidPerformanceEvidenceError(
            f"invalid_performance_evidence:{record.state.value}:{record.artifact_path}:"
            f"stage={stage}:metric_family={metric_family}:reason={record.reason}"
        )


def _parse_record(
    raw_record: object,
    *,
    index: int,
    repository_root: Path,
) -> AccountingValidityRecord:
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
    ledger_formula_hash = _required_string(
        raw_record.get("ledger_formula_hash"),
        "ledger_formula_hash",
        index=index,
    )
    if not _is_formula_hash(ledger_formula_hash):
        raise AccountingValidityRegistryError(f"record {index} ledger_formula_hash must be sha256:<lowercase hex>")
    decision_at = _required_string(raw_record.get("decision_at"), "decision_at", index=index)
    _validate_decision_at(decision_at, index=index)
    approving_decision = _required_string(
        raw_record.get("approving_decision"),
        "approving_decision",
        index=index,
    )
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
        artifact_path=_canonical_artifact_identity(artifact_path, repository_root=repository_root),
        source_sha256=source_sha256,
        selector=selector,
        state=state,
        reason=reason,
        metric_families=metric_families,
        stages=stages,
        superseding_artifact=superseding_artifact,
        ledger_formula_hash=ledger_formula_hash,
        decision_at=decision_at,
        approving_decision=approving_decision,
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


def _canonical_artifact_identity(value: str, *, repository_root: Path) -> str:
    path = Path(value)
    resolved = (path if path.is_absolute() else repository_root / path).resolve(strict=False)
    try:
        return resolved.relative_to(repository_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _is_formula_hash(value: str) -> bool:
    return value.startswith("sha256:") and _is_sha256(value.removeprefix("sha256:"))


def _validate_decision_at(value: str, *, index: int) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AccountingValidityRegistryError(f"record {index} decision_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise AccountingValidityRegistryError(f"record {index} decision_at must include a timezone")


def _reject_json_constant(value: str) -> None:
    raise AccountingValidityRegistryError(f"forbidden JSON constant:{value}")
