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
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

DAY_NS = 86_400_000_000_000
FOUR_HOURS_NS = 14_400_000_000_000
REUSED_START_NS = 1_767_225_600_000_000_000
REUSED_END_NS = 1_787_529_600_000_000_000
EXPECTED_SEEDS = (42, 123, 456, 789, 1024)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RegistrationError(ValueError):
    """Raised when a research registration contract cannot be trusted."""


class EvidenceRole(StrEnum):
    """The mutually exclusive role of an event in research evidence."""

    DISCOVERY_ONLY = "DISCOVERY_ONLY"
    TEST_REUSED_DIAGNOSTIC = "TEST_REUSED_DIAGNOSTIC"
    FORWARD_CONFIRMATION = "FORWARD_CONFIRMATION"


@dataclass(frozen=True, slots=True)
class RegistrationReceipt:
    """Trusted provider receipt for one immutable registration record."""

    manifest_sha256: str
    commit_sha: str
    remote_identity: str
    received_at_ns: int


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
    if any(item.get("fallback", item.get("fallback_id")) != "NONE" for item in source_requirements):
        raise RegistrationError("source requirement fallback must be NONE")

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


def derive_forward_start_ns(receipts: Sequence[RegistrationReceipt]) -> int:
    """Return the first confirmed 4h cutoff after trusted registration + 24h."""

    if len(receipts) != 2:
        raise RegistrationError("two trusted receipts are required")
    candidate = max(item.received_at_ns for item in receipts) + DAY_NS
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
    if event_cutoff_ns < forward_start_ns:
        return EvidenceRole.TEST_REUSED_DIAGNOSTIC
    return EvidenceRole.FORWARD_CONFIRMATION
