"""Fail-closed contracts for registering the two BTC trend research layers.

The hypothesis manifest is deliberately a content-addressed, immutable input.
Registration receipts are kept separate from it: provider timestamps and
identities must never become part of the manifest hash.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

DAY_NS = 86_400_000_000_000
FOUR_HOURS_NS = 14_400_000_000_000
REUSED_START_NS = 1_767_225_600_000_000_000
REUSED_END_NS = 1_787_529_600_000_000_000
EXPECTED_SEEDS = (42, 123, 456, 789, 1024)
REQUIRED_SOURCE_IDS = (
    "BINANCE_SPOT_BTCUSDT_1D",
    "BINANCE_PERPETUAL_BTCUSDT_5M",
    "BINANCE_PERPETUAL_BTCUSDT_FUNDING",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RegistrationError(ValueError):
    """Raised when a research registration contract cannot be trusted."""


class EvidenceRole(StrEnum):
    """The mutually exclusive role of an event in research evidence."""

    DISCOVERY_ONLY = "DISCOVERY_ONLY"
    TEST_REUSED_DIAGNOSTIC = "TEST_REUSED_DIAGNOSTIC"
    EXCLUDED_EMBARGO = "EXCLUDED_EMBARGO"
    FORWARD_CONFIRMATION = "FORWARD_CONFIRMATION"


@dataclass(frozen=True, slots=True)
class RegistrationReceipt:
    """Trusted provider receipt for one immutable registration record."""

    manifest_sha256: str
    commit_sha: str
    remote_identity: str
    received_at_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_sha256, str) or not _SHA256_RE.fullmatch(self.manifest_sha256):
            raise RegistrationError("receipt manifest hash is not canonical SHA-256")
        if not isinstance(self.remote_identity, str) or not self.remote_identity:
            raise RegistrationError("receipt remote identity is required")
        if isinstance(self.received_at_ns, bool) or not isinstance(self.received_at_ns, int) or self.received_at_ns < 0:
            raise RegistrationError("receipt timestamp must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class HypothesisManifest:
    """Validated, frozen hypothesis policy and its canonical content digest."""

    schema_version: str
    layer: str
    asset: str
    active_signal_source: str
    reused_start_ns: int
    reused_end_ns: int
    source_requirements: tuple[Mapping[str, Any], ...]
    fallback: str
    seeds: tuple[int, ...]
    training: bool
    parameter_search: bool
    payload: Mapping[str, Any]
    sha256: str


_ALLOWED_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "layer",
        "asset",
        "active_signal_source",
        "reused_window",
        "source_requirements",
        "fallback",
        "seeds",
        "training",
        "parameter_search",
        # Research-specific formula and gate sections are frozen as opaque
        # values here; later layers interpret them without changing identity.
        "formula",
        "thresholds",
        "costs",
        "robustness",
        "gates",
        "policy",
        "authority",
        "diagnostics",
        "execution",
        "formula_sha256",
        "config_sha256",
        "source_set_sha256",
    }
)
_SOURCE_KEYS = frozenset({"id", "requirement", "fallback"})


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    """Return the SHA-256 of a deterministic JSON representation."""

    if not isinstance(payload, Mapping):
        raise TypeError("canonical SHA-256 requires a mapping")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RegistrationError("manifest payload is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def iso8601_to_ns(value: str) -> int:
    """Convert an offset-aware ISO-8601 provider timestamp to epoch nanoseconds."""

    if not isinstance(value, str):
        raise RegistrationError("provider timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistrationError("provider timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise RegistrationError("provider timestamp must include a timezone")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = parsed.astimezone(UTC) - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _provider_identity(provider: Mapping[str, Any]) -> tuple[Any, str, str]:
    try:
        provider_run_id = provider["id"]
        commit_sha = provider["head_sha"]
        repository = provider["repository"]
    except KeyError as exc:
        raise RegistrationError("provider payload is missing required identity") from exc
    if isinstance(provider_run_id, bool) or not isinstance(provider_run_id, (int, str)):
        raise RegistrationError("provider run id is invalid")
    if not isinstance(commit_sha, str) or not commit_sha:
        raise RegistrationError("provider head SHA is required")
    if not isinstance(repository, Mapping):
        raise RegistrationError("provider repository identity is required")
    remote_identity = repository.get("full_name")
    if not isinstance(remote_identity, str) or not remote_identity:
        raise RegistrationError("provider repository identity is required")
    return provider_run_id, commit_sha, remote_identity


def build_registration_envelope(
    manifest_path: Path | str,
    commit_sha: str,
    provider_run: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind one checked-in manifest to its authoritative provider run identity."""

    if not isinstance(commit_sha, str) or not commit_sha:
        raise RegistrationError("commit SHA is required")
    path = Path(manifest_path)
    try:
        manifest_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"cannot load registration manifest: {path}") from exc
    if not isinstance(manifest_payload, Mapping):
        raise RegistrationError("registration manifest must be a JSON object")

    provider_run_id, provider_commit_sha, remote_identity = _provider_identity(provider_run)
    if provider_commit_sha != commit_sha:
        raise RegistrationError("provider identity mismatch")
    received_at_ns = iso8601_to_ns(str(provider_run.get("created_at")))
    manifest_sha256 = canonical_sha256(manifest_payload)
    RegistrationReceipt(manifest_sha256, provider_commit_sha, remote_identity, received_at_ns)
    return {
        "provider_run_id": provider_run_id,
        "commit_sha": provider_commit_sha,
        "remote_identity": remote_identity,
        "manifest_sha256": manifest_sha256,
        "received_at_ns": received_at_ns,
    }


def verify_github_receipt(envelope: Mapping[str, Any], provider_payload: Mapping[str, Any]) -> RegistrationReceipt:
    """Rebuild a trusted receipt from a GitHub run API payload, never local time."""

    provider_run_id, provider_commit_sha, remote_identity = _provider_identity(provider_payload)
    try:
        expected = (
            envelope["provider_run_id"],
            envelope["commit_sha"],
            envelope["remote_identity"],
        )
        manifest_sha256 = str(envelope["manifest_sha256"])
    except KeyError as exc:
        raise RegistrationError("registration envelope is missing required identity") from exc
    observed = (provider_run_id, provider_commit_sha, remote_identity)
    if expected != observed:
        raise RegistrationError("provider identity mismatch")
    received_at_ns = iso8601_to_ns(str(provider_payload.get("created_at")))
    return RegistrationReceipt(manifest_sha256, provider_commit_sha, remote_identity, received_at_ns)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _validate_sha_fields(value: Any, *, path: str = "manifest") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if "sha256" in key_text.lower():
                if not isinstance(item, str) or not _SHA256_RE.fullmatch(item):
                    raise RegistrationError(f"{path}.{key_text} is not canonical SHA-256")
            _validate_sha_fields(item, path=f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_sha_fields(item, path=f"{path}[{index}]")


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegistrationError(f"{field} must be an integer")
    return value


def load_hypothesis_manifest(path: Path) -> HypothesisManifest:
    """Load and validate one checked-in hypothesis manifest.

    The manifest's digest is computed from its complete JSON payload.  A
    self-embedded digest is intentionally not accepted, avoiding a circular
    identity and making the returned ``sha256`` reproducible from file bytes.
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"cannot load hypothesis manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise RegistrationError("hypothesis manifest must be a JSON object")

    unknown = set(payload) - _ALLOWED_MANIFEST_KEYS
    if unknown:
        names = ", ".join(sorted(str(item) for item in unknown))
        raise RegistrationError(f"unknown manifest key(s): {names}")
    _validate_sha_fields(payload)

    required = {
        "schema_version",
        "layer",
        "asset",
        "active_signal_source",
        "reused_window",
        "source_requirements",
        "fallback",
        "seeds",
        "training",
        "parameter_search",
    }
    missing = required - set(payload)
    if missing:
        names = ", ".join(sorted(missing))
        raise RegistrationError(f"missing manifest key(s): {names}")

    if payload["schema_version"] != "trend-research-hypothesis.v1":
        raise RegistrationError("unsupported hypothesis manifest schema")
    if payload["asset"] != "BTC":
        raise RegistrationError("asset must be BTC")
    if payload["active_signal_source"] != "BINANCE_SPOT_BTCUSDT_1D":
        raise RegistrationError("active signal source must be BINANCE_SPOT_BTCUSDT_1D")
    if payload["fallback"] != "NONE":
        raise RegistrationError("fallback must be NONE")
    if payload["training"] is not False:
        raise RegistrationError("training must be false")
    if payload["parameter_search"] is not False:
        raise RegistrationError("parameter_search must be false")

    window = payload["reused_window"]
    if not isinstance(window, list) or len(window) != 2:
        raise RegistrationError("reused window must contain exactly two timestamps")
    reused_start_ns = _require_int(window[0], "reused window start")
    reused_end_ns = _require_int(window[1], "reused window end")
    if (reused_start_ns, reused_end_ns) != (REUSED_START_NS, REUSED_END_NS):
        raise RegistrationError("reused window is not the exact frozen window")

    seeds = payload["seeds"]
    if seeds != list(EXPECTED_SEEDS) or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds):
        raise RegistrationError("seeds are frozen to (42, 123, 456, 789, 1024)")

    source_requirements = payload["source_requirements"]
    if not isinstance(source_requirements, list) or not source_requirements:
        raise RegistrationError("source_requirements must be a non-empty list")
    if any(not isinstance(item, dict) for item in source_requirements):
        raise RegistrationError("source_requirements entries must be objects")
    source_ids: list[str] = []
    for item in source_requirements:
        if set(item) != _SOURCE_KEYS:
            raise RegistrationError("source schema keys must be exactly id, requirement, fallback")
        source_id = item["id"]
        if not isinstance(source_id, str) or source_id not in REQUIRED_SOURCE_IDS:
            raise RegistrationError("unknown source requirement id")
        if source_id in source_ids:
            raise RegistrationError("duplicate source requirement id")
        source_ids.append(source_id)
        if item["requirement"] != "REQUIRED":
            raise RegistrationError("source requirement must be REQUIRED")
        if item["fallback"] != "NONE":
            raise RegistrationError("source requirement fallback must be NONE")
    missing_sources = set(REQUIRED_SOURCE_IDS) - set(source_ids)
    if missing_sources:
        missing = ", ".join(sorted(missing_sources))
        raise RegistrationError(f"mandatory source inputs missing: {missing}")

    manifest_sha256 = canonical_sha256(payload)
    frozen_payload = _freeze(payload)
    return HypothesisManifest(
        schema_version=str(payload["schema_version"]),
        layer=str(payload["layer"]),
        asset=str(payload["asset"]),
        active_signal_source=str(payload["active_signal_source"]),
        reused_start_ns=reused_start_ns,
        reused_end_ns=reused_end_ns,
        source_requirements=tuple(_freeze(item) for item in source_requirements),
        fallback=str(payload["fallback"]),
        seeds=tuple(seeds),
        training=False,
        parameter_search=False,
        payload=frozen_payload,
        sha256=manifest_sha256,
    )


def validate_registration_receipts(
    receipts: Sequence[RegistrationReceipt],
    *,
    expected_manifest_sha256: str,
    expected_remote_identity: str,
) -> tuple[RegistrationReceipt, ...]:
    """Bind trusted receipts to one manifest and one remote identity."""

    if not isinstance(expected_manifest_sha256, str) or not _SHA256_RE.fullmatch(expected_manifest_sha256):
        raise RegistrationError("expected manifest hash is not canonical SHA-256")
    if not isinstance(expected_remote_identity, str) or not expected_remote_identity:
        raise RegistrationError("expected remote identity is required")
    bound = tuple(receipts)
    for receipt in bound:
        if not isinstance(receipt, RegistrationReceipt):
            raise RegistrationError("registration receipt has an invalid type")
        if receipt.manifest_sha256 != expected_manifest_sha256:
            raise RegistrationError("receipt manifest hash mismatch")
        if receipt.remote_identity != expected_remote_identity:
            raise RegistrationError("receipt remote identity mismatch")
    return bound


def derive_forward_start_ns(
    receipts: Sequence[RegistrationReceipt],
    *,
    expected_manifest_sha256: str | None = None,
    expected_remote_identity: str | None = None,
) -> int:
    """Return the first confirmed 4h cutoff after trusted registration + 24h."""

    if expected_manifest_sha256 is None or expected_remote_identity is None:
        raise RegistrationError("receipt binding is required before deriving forward start")
    if len(receipts) != 2:
        raise RegistrationError("two trusted receipts are required")
    bound_receipts = validate_registration_receipts(
        receipts,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_remote_identity=expected_remote_identity,
    )
    candidate = max(item.received_at_ns for item in bound_receipts) + DAY_NS
    return ((candidate + FOUR_HOURS_NS - 1) // FOUR_HOURS_NS) * FOUR_HOURS_NS


def classify_evidence_role(
    event_cutoff_ns: int,
    reused_start_ns: int,
    reused_end_ns: int,
    forward_start_ns: int,
) -> EvidenceRole:
    """Classify by market-event cutoff, never by ingest/backfill timestamp."""

    values = (event_cutoff_ns, reused_start_ns, reused_end_ns, forward_start_ns)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise RegistrationError("evidence cutoffs must be integers")
    if reused_start_ns > reused_end_ns or reused_end_ns > forward_start_ns:
        raise RegistrationError("evidence windows must be ordered")
    if event_cutoff_ns < reused_start_ns:
        return EvidenceRole.DISCOVERY_ONLY
    if event_cutoff_ns < reused_end_ns:
        return EvidenceRole.TEST_REUSED_DIAGNOSTIC
    if event_cutoff_ns < forward_start_ns:
        return EvidenceRole.EXCLUDED_EMBARGO
    return EvidenceRole.FORWARD_CONFIRMATION
