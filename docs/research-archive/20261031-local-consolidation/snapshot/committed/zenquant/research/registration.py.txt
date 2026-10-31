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
SUPPORTED_TREND_LAYERS = frozenset({"time_space_shadow", "trend_participation"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_PROVIDER_RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")
_BOOTSTRAP_REGISTRATIONS = MappingProxyType(
    {
        "feature/trend-registration-time-space-hypothesis": (
            "hypothesis",
            "config/research/time_space_shadow_hypothesis_v1.json",
        ),
        "feature/trend-registration-trend-participation-hypothesis": (
            "hypothesis",
            "config/research/trend_participation_hypothesis_v1.json",
        ),
        "feature/trend-registration-time-space-execution": (
            "execution",
            "config/research/registrations/time_space_shadow_execution_v1.json",
        ),
        "feature/trend-registration-trend-participation-execution": (
            "execution",
            "config/research/registrations/trend_participation_execution_v1.json",
        ),
    }
)
_ALLOWED_REGISTRATION_SELECTIONS = frozenset(_BOOTSTRAP_REGISTRATIONS.values())
_EXECUTION_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "layer",
        "hypothesis_sha256",
        "formula_sha256",
        "config_sha256",
        "reused_window",
        "fixed_end_cutoff_ns",
        "sources",
        "source_set_sha256",
        "accounting_kernel",
        "construction_identity",
        "forward_checkpoint_rule",
        "workspace_state",
    }
)
_FORBIDDEN_EXECUTION_KEYS = frozenset({"direction", "pnl", "signal"})


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
    provider_run_id: int | str
    commit_sha: str
    remote_identity: str
    received_at_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_sha256, str) or not _SHA256_RE.fullmatch(self.manifest_sha256):
            raise RegistrationError("receipt manifest hash is not canonical SHA-256")
        provider_run_id = _canonical_provider_run_id(self.provider_run_id)
        object.__setattr__(self, "provider_run_id", provider_run_id)
        if not isinstance(self.commit_sha, str) or not _GIT_SHA_RE.fullmatch(self.commit_sha):
            raise RegistrationError("receipt commit SHA is not canonical Git SHA")
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


@dataclass(frozen=True, slots=True)
class ExecutionManifest:
    """Validated identity fields from one local execution manifest."""

    schema_version: str
    layer: str
    hypothesis_sha256: str
    payload: Mapping[str, Any]
    sha256: str


def _canonical_provider_run_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise RegistrationError("provider run id is invalid")
    text = str(value)
    if not _PROVIDER_RUN_ID_RE.fullmatch(text):
        raise RegistrationError("provider run id is not canonical")
    return text


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


def resolve_bootstrap_registration(branch_name: str) -> tuple[str, str]:
    """Return the one manifest registration permitted for a bootstrap push."""

    try:
        return _BOOTSTRAP_REGISTRATIONS[branch_name]
    except KeyError as exc:
        raise RegistrationError("unmapped bootstrap branch") from exc


def validate_registration_selection(manifest_kind: str, manifest_path: str) -> tuple[str, str]:
    """Allow only the four immutable kind/path registrations."""

    selection = (manifest_kind, manifest_path)
    if selection not in _ALLOWED_REGISTRATION_SELECTIONS:
        raise RegistrationError("unsupported registration manifest")
    return selection


def resolve_manual_registration(github_ref: str, manifest_kind: str, manifest_path: str) -> tuple[str, str]:
    """Resolve a post-merge manual registration from the default branch only."""

    if github_ref != "refs/heads/main":
        raise RegistrationError("manual registration requires refs/heads/main")
    return validate_registration_selection(manifest_kind, manifest_path)


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


def _provider_identity(provider: Mapping[str, Any]) -> tuple[str, str, str]:
    try:
        provider_run_id = provider["id"]
        commit_sha = provider["head_sha"]
        repository = provider["repository"]
    except KeyError as exc:
        raise RegistrationError("provider payload is missing required identity") from exc
    provider_run_id = _canonical_provider_run_id(provider_run_id)
    if not isinstance(commit_sha, str) or not _GIT_SHA_RE.fullmatch(commit_sha):
        raise RegistrationError("provider head SHA is not canonical Git SHA")
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

    if not isinstance(commit_sha, str) or not _GIT_SHA_RE.fullmatch(commit_sha):
        raise RegistrationError("commit SHA is not canonical Git SHA")
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
    RegistrationReceipt(manifest_sha256, provider_run_id, provider_commit_sha, remote_identity, received_at_ns)
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
            _canonical_provider_run_id(envelope["provider_run_id"]),
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
    return RegistrationReceipt(
        manifest_sha256,
        provider_run_id,
        provider_commit_sha,
        remote_identity,
        received_at_ns,
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain_json(item) for item in value]
    return value


def _validate_sha_fields(value: Any, *, path: str = "manifest") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if "sha256" in key_text.lower() and not isinstance(item, Mapping | list | tuple):
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
    return validate_hypothesis_manifest_payload(payload)


def validate_hypothesis_manifest_payload(payload: Any) -> HypothesisManifest:
    """Validate and freeze one in-memory hypothesis manifest payload."""

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
    if not isinstance(payload["layer"], str) or payload["layer"] not in SUPPORTED_TREND_LAYERS:
        raise RegistrationError("unsupported hypothesis layer")
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


def _require_exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RegistrationError(f"{label} keys do not match execution manifest schema")
    return value


def _require_prefixed_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _PREFIXED_SHA256_RE.fullmatch(value):
        raise RegistrationError(f"{label} is not canonical prefixed SHA-256")
    return value


def _prefixed_payload_sha256(value: Mapping[str, Any]) -> str:
    return f"sha256:{canonical_sha256(value)}"


def _all_mapping_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return {str(key) for key in value} | set().union(*(_all_mapping_keys(item) for item in value.values()), set())
    if isinstance(value, list | tuple):
        return set().union(*(_all_mapping_keys(item) for item in value), set())
    return set()


def _validate_recursive_sha_fields(value: Any, *, path: str = "execution") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if "sha256" in key_text.lower() and not isinstance(item, Mapping | list | tuple):
                if item is None and key_text == "content_sha256":
                    continue
                if not isinstance(item, str) or not (_SHA256_RE.fullmatch(item) or _PREFIXED_SHA256_RE.fullmatch(item)):
                    raise RegistrationError(f"{path}.{key_text} is not canonical SHA-256")
            _validate_recursive_sha_fields(item, path=f"{path}.{key_text}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _validate_recursive_sha_fields(item, path=f"{path}[{index}]")


def _validate_execution_sources(value: Any) -> Mapping[str, Any]:
    sources = _require_exact_keys(value, frozenset(REQUIRED_SOURCE_IDS), "execution sources")
    spot_keys = frozenset(
        {
            "coverage",
            "fallback",
            "last_cutoff_ns",
            "lineage",
            "normalized_path",
            "normalized_sha256",
            "normalizer_identity",
            "raw_sha256",
            "requirement",
            "status",
            "warmup",
        }
    )
    derivative_keys = frozenset(
        {"coverage", "fallback", "last_cutoff_ns", "market_tail_provenance", "path", "requirement", "sha256", "status"}
    )
    spot_coverage_keys = frozenset(
        {
            "duplicate_count",
            "end_ns",
            "first_open_ns",
            "last_close_ns",
            "missing_count",
            "post_end_count",
            "pre_start_count",
            "row_count",
            "start_ns",
        }
    )
    derivative_coverage_keys = frozenset(
        {
            "duplicate_count",
            "expected_row_count",
            "first_event_ns",
            "invalid_cutoff_count",
            "invalid_value_count",
            "last_event_ns",
            "missing_count",
            "off_grid_count",
            "post_end_count",
            "pre_start_count",
            "row_count",
            "window_row_count",
        }
    )
    for source_id, item in sources.items():
        source = _require_exact_keys(
            item,
            spot_keys if source_id == "BINANCE_SPOT_BTCUSDT_1D" else derivative_keys,
            f"execution source {source_id}",
        )
        if (source["status"], source["requirement"], source["fallback"]) != ("READY", "REQUIRED", "NONE"):
            raise RegistrationError(f"execution source {source_id} is not READY/REQUIRED/NONE")
        _require_exact_keys(
            source["coverage"],
            spot_coverage_keys if source_id == "BINANCE_SPOT_BTCUSDT_1D" else derivative_coverage_keys,
            f"execution source {source_id} coverage",
        )
        if source_id == "BINANCE_SPOT_BTCUSDT_1D":
            _require_exact_keys(source["lineage"], frozenset({"reconstructed_sha256", "status"}), "spot lineage")
            if source["lineage"]["status"] != "VERIFIED":
                raise RegistrationError("spot lineage is not verified")
            _require_exact_keys(source["normalizer_identity"], frozenset({"callable", "sha256"}), "spot normalizer")
            _require_exact_keys(
                source["warmup"],
                frozenset({"end_ns", "observed_closes", "required_closes", "start_ns", "status"}),
                "spot warmup",
            )
            if source["warmup"]["status"] != "READY":
                raise RegistrationError("spot warmup is not ready")
            if not isinstance(source["raw_sha256"], list) or not source["raw_sha256"]:
                raise RegistrationError("spot raw identities are required")
            for raw in source["raw_sha256"]:
                _require_exact_keys(raw, frozenset({"path", "sha256"}), "spot raw identity")
        else:
            provenance = _require_exact_keys(
                source["market_tail_provenance"],
                frozenset({"normalizer", "path", "repository", "schema_version", "sha256"}),
                "market-tail provenance",
            )
            if provenance["schema_version"] != "trend-market-tail-provenance.v1":
                raise RegistrationError("market-tail provenance schema is unsupported")
            _require_exact_keys(provenance["normalizer"], frozenset({"callable", "sha256"}), "market-tail normalizer")
            repository = _require_exact_keys(provenance["repository"], frozenset({"head"}), "market-tail repository")
            if not isinstance(repository["head"], str) or not _GIT_SHA_RE.fullmatch(repository["head"]):
                raise RegistrationError("market-tail repository head is not canonical Git SHA")
    return sources


def _validate_identity_block(
    value: Any,
    *,
    expected_keys: frozenset[str],
    digest_keys: tuple[str, ...],
    label: str,
) -> Mapping[str, Any]:
    block = _require_exact_keys(value, expected_keys, label)
    expected_digest = _prefixed_payload_sha256({key: block[key] for key in digest_keys})
    if block["sha256"] != expected_digest:
        raise RegistrationError(f"{label} hash mismatch")
    return block


def validate_execution_manifest_payload(payload: Mapping[str, Any]) -> None:
    """Validate the exact fail-closed execution-manifest v1 schema."""

    _require_exact_keys(payload, _EXECUTION_MANIFEST_KEYS, "execution manifest")
    if _FORBIDDEN_EXECUTION_KEYS.intersection(_all_mapping_keys(payload)):
        raise RegistrationError("execution manifest contains forbidden result fields")
    if payload["reused_window"] != [REUSED_START_NS, REUSED_END_NS]:
        raise RegistrationError("execution reused window is not frozen")
    if payload["fixed_end_cutoff_ns"] != REUSED_END_NS:
        raise RegistrationError("execution manifest end cutoff is not frozen")
    if payload["forward_checkpoint_rule"] != "later_trusted_receipt_plus_24h_ceil_confirmed_4h":
        raise RegistrationError("execution forward checkpoint rule is not frozen")
    for key in ("formula_sha256", "config_sha256", "source_set_sha256"):
        _require_prefixed_sha256(payload[key], f"execution {key}")
    if payload["config_sha256"] != f"sha256:{payload['hypothesis_sha256']}":
        raise RegistrationError("execution config hash does not bind hypothesis")
    sources = _validate_execution_sources(payload["sources"])
    if payload["source_set_sha256"] != _prefixed_payload_sha256(sources):
        raise RegistrationError("execution source set hash mismatch")
    construction = _validate_identity_block(
        payload["construction_identity"],
        expected_keys=frozenset({"files", "sha256"}),
        digest_keys=("files",),
        label="construction identity",
    )
    if not isinstance(construction["files"], list) or not construction["files"]:
        raise RegistrationError("construction identity files are required")
    for item in construction["files"]:
        _require_exact_keys(item, frozenset({"path", "sha256"}), "construction file")
    accounting = _validate_identity_block(
        payload["accounting_kernel"],
        expected_keys=frozenset({"schema_version", "components", "sha256"}),
        digest_keys=("schema_version", "components"),
        label="accounting kernel",
    )
    if accounting["schema_version"] != "accounting-kernel-identity.v1":
        raise RegistrationError("accounting kernel schema is unsupported")
    if not isinstance(accounting["components"], list) or not accounting["components"]:
        raise RegistrationError("accounting kernel components are required")
    for item in accounting["components"]:
        _require_exact_keys(item, frozenset({"identity", "semantic_sha256"}), "accounting component")
    workspace = _validate_identity_block(
        payload["workspace_state"],
        expected_keys=frozenset({"head", "entries", "sha256"}),
        digest_keys=("head", "entries"),
        label="workspace state",
    )
    if not isinstance(workspace["head"], str) or not _GIT_SHA_RE.fullmatch(workspace["head"]):
        raise RegistrationError("workspace head is not canonical Git SHA")
    if not isinstance(workspace["entries"], list):
        raise RegistrationError("workspace entries are invalid")
    for item in workspace["entries"]:
        if (
            not isinstance(item, Mapping)
            or not set(item).issubset({"status", "path", "content_sha256", "original_path"})
            or not {"status", "path", "content_sha256"}.issubset(item)
        ):
            raise RegistrationError("workspace entry schema is invalid")
    _validate_recursive_sha_fields(payload)


def load_execution_manifest(path: Path) -> ExecutionManifest:
    """Build a validated descriptor from one local execution manifest."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"cannot load execution manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise RegistrationError("execution manifest must be a JSON object")
    validate_execution_manifest_payload(payload)
    if payload["schema_version"] != "trend-research-execution-manifest.v1":
        raise RegistrationError("unsupported execution manifest schema")
    if not isinstance(payload["layer"], str) or payload["layer"] not in SUPPORTED_TREND_LAYERS:
        raise RegistrationError("unsupported execution layer")
    hypothesis_sha256 = payload["hypothesis_sha256"]
    if not isinstance(hypothesis_sha256, str) or not _SHA256_RE.fullmatch(hypothesis_sha256):
        raise RegistrationError("execution hypothesis hash is not canonical SHA-256")
    return ExecutionManifest(
        schema_version=payload["schema_version"],
        layer=payload["layer"],
        hypothesis_sha256=hypothesis_sha256,
        payload=_freeze(payload),
        sha256=canonical_sha256(payload),
    )


def validate_registration_receipt_pair(
    hypothesis: HypothesisManifest,
    execution: ExecutionManifest,
    receipts: Sequence[RegistrationReceipt],
    *,
    expected_remote_identity: str,
    expected_hypothesis_provider_run_id: int | str,
    expected_hypothesis_commit_sha: str,
    expected_execution_provider_run_id: int | str,
    expected_execution_commit_sha: str,
) -> tuple[RegistrationReceipt, RegistrationReceipt]:
    """Bind validated local manifests to two distinct authoritative runs."""

    if not isinstance(hypothesis, HypothesisManifest):
        raise RegistrationError("hypothesis descriptor is invalid")
    if not isinstance(execution, ExecutionManifest):
        raise RegistrationError("execution descriptor is invalid")
    try:
        hypothesis_payload = _plain_json(hypothesis.payload)
        validated_hypothesis = validate_hypothesis_manifest_payload(hypothesis_payload)
    except (RegistrationError, TypeError, ValueError) as exc:
        raise RegistrationError("hypothesis descriptor is invalid") from exc
    if validated_hypothesis != hypothesis:
        raise RegistrationError("hypothesis descriptor fields or hash are inconsistent")
    try:
        execution_payload = _plain_json(execution.payload)
        if not isinstance(execution_payload, dict):
            raise RegistrationError("execution payload is not an object")
        validate_execution_manifest_payload(execution_payload)
    except (RegistrationError, TypeError, ValueError) as exc:
        raise RegistrationError("execution descriptor is invalid") from exc
    expected_execution_fields = (
        execution_payload["schema_version"],
        execution_payload["layer"],
        execution_payload["hypothesis_sha256"],
        canonical_sha256(execution_payload),
    )
    observed_execution_fields = (
        execution.schema_version,
        execution.layer,
        execution.hypothesis_sha256,
        execution.sha256,
    )
    if observed_execution_fields != expected_execution_fields:
        raise RegistrationError("execution descriptor fields or hash are inconsistent")
    if hypothesis.layer != execution.layer:
        raise RegistrationError("hypothesis and execution must describe the same layer")
    if execution.hypothesis_sha256 != hypothesis.sha256:
        raise RegistrationError("execution manifest does not bind hypothesis")
    formula_fields = ("formula", "thresholds", "costs", "robustness", "gates", "policy", "authority")
    formula_payload = {
        field: _plain_json(hypothesis.payload[field]) for field in formula_fields if field in hypothesis.payload
    }
    if execution.payload["formula_sha256"] != _prefixed_payload_sha256(formula_payload):
        raise RegistrationError("execution formula hash does not bind hypothesis")
    if hypothesis.sha256 == execution.sha256:
        raise RegistrationError("hypothesis and execution manifest hashes must be distinct")
    if not isinstance(expected_remote_identity, str) or not expected_remote_identity:
        raise RegistrationError("expected remote identity is required")
    expected_runs = (
        _canonical_provider_run_id(expected_hypothesis_provider_run_id),
        _canonical_provider_run_id(expected_execution_provider_run_id),
    )
    expected_commits = (expected_hypothesis_commit_sha, expected_execution_commit_sha)
    if any(not isinstance(value, str) or not _GIT_SHA_RE.fullmatch(value) for value in expected_commits):
        raise RegistrationError("expected commit SHA is not canonical Git SHA")
    if expected_runs[0] == expected_runs[1]:
        raise RegistrationError("expected provider runs must be distinct")

    bound = tuple(receipts)
    if len(bound) != 2:
        raise RegistrationError("two trusted receipts are required")
    for role, receipt in zip(("hypothesis", "execution"), bound, strict=True):
        if not isinstance(receipt, RegistrationReceipt):
            raise RegistrationError(f"{role} registration receipt has an invalid type")
    expected_hashes = (hypothesis.sha256, execution.sha256)
    if (bound[0].manifest_sha256, bound[1].manifest_sha256) == tuple(reversed(expected_hashes)):
        raise RegistrationError("registration receipts must be ordered hypothesis and execution")
    if bound[0].provider_run_id == bound[1].provider_run_id:
        raise RegistrationError("receipt provider runs must be distinct")
    for role, receipt, expected_hash, expected_run, expected_commit in zip(
        ("hypothesis", "execution"),
        bound,
        expected_hashes,
        expected_runs,
        expected_commits,
        strict=True,
    ):
        if receipt.manifest_sha256 != expected_hash:
            raise RegistrationError(f"{role} receipt manifest hash mismatch")
        if receipt.remote_identity != expected_remote_identity:
            raise RegistrationError(f"{role} receipt remote identity mismatch")
        if (receipt.provider_run_id, receipt.commit_sha) != (expected_run, expected_commit):
            raise RegistrationError(f"{role} receipt provider identity mismatch")
    return bound


def derive_forward_start_ns(
    hypothesis: HypothesisManifest | None,
    execution: ExecutionManifest | None,
    receipts: Sequence[RegistrationReceipt],
    *,
    expected_remote_identity: str | None = None,
    expected_hypothesis_provider_run_id: int | str | None = None,
    expected_hypothesis_commit_sha: str | None = None,
    expected_execution_provider_run_id: int | str | None = None,
    expected_execution_commit_sha: str | None = None,
) -> int:
    """Return the first confirmed 4h cutoff after trusted registration + 24h."""

    if (
        hypothesis is None
        or execution is None
        or expected_remote_identity is None
        or expected_hypothesis_provider_run_id is None
        or expected_hypothesis_commit_sha is None
        or expected_execution_provider_run_id is None
        or expected_execution_commit_sha is None
    ):
        raise RegistrationError("receipt binding is required before deriving forward start")
    bound_receipts = validate_registration_receipt_pair(
        hypothesis,
        execution,
        receipts,
        expected_remote_identity=expected_remote_identity,
        expected_hypothesis_provider_run_id=expected_hypothesis_provider_run_id,
        expected_hypothesis_commit_sha=expected_hypothesis_commit_sha,
        expected_execution_provider_run_id=expected_execution_provider_run_id,
        expected_execution_commit_sha=expected_execution_commit_sha,
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
