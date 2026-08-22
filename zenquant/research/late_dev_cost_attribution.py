"""Validation and canonical envelope for late-development attribution inputs.

This module deliberately performs no replay or strategy computation.  It
freezes the identity and ordering of the existing architecture experiment so
later diagnostic layers can only consume a complete, point-in-time artifact.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from .architecture_experiment import (
    ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION,
    ArchitectureStage,
    load_architecture_experiment_manifest,
)

LATE_DEV_ATTRIBUTION_SCHEMA_VERSION = "quant-v2-late-dev-cost-attribution.v1"


class EvidenceStatus(StrEnum):
    """Evidence completeness labels used by later attribution layers."""

    CONSISTENT = "CONSISTENT"
    MIXED = "MIXED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    UNATTRIBUTABLE = "UNATTRIBUTABLE"


class LateDevAttributionValidationError(ValueError):
    """The raw architecture report is incomplete or not frozen."""


def build_late_dev_cost_attribution(
    report: Mapping[str, Any],
    *,
    source_artifact: str,
    source_sha256: str,
    source_size_bytes: int,
) -> dict[str, Any]:
    """Validate a frozen report and return the empty diagnostic envelope."""
    if not isinstance(report, Mapping):
        raise LateDevAttributionValidationError("raw report must be a JSON object")
    try:
        manifest = load_architecture_experiment_manifest()
        _validate_source_identity(source_sha256=source_sha256, source_size_bytes=source_size_bytes)
        runs = _validate_report_envelope(report, manifest=manifest)
    except LateDevAttributionValidationError:
        raise
    except (KeyError, OverflowError, TypeError, ValueError) as exc:
        raise LateDevAttributionValidationError("raw report contains an invalid or incomplete nested value") from exc
    return {
        "schema_version": LATE_DEV_ATTRIBUTION_SCHEMA_VERSION,
        "source": {
            "artifact": source_artifact,
            "sha256": source_sha256,
            "size_bytes": source_size_bytes,
        },
        "manifest_sha256": manifest.sha256,
        "validated_run_count": len(runs),
        "seed_stage_bridges": [],
        "episode_attribution": [],
        "evidence": {},
        "research_only_non_promotable": True,
    }


def canonical_late_dev_attribution_bytes(report: Mapping[str, Any]) -> bytes:
    """Return the deterministic JSON representation of an attribution report."""
    return json.dumps(report, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")


def _validate_source_identity(*, source_sha256: str, source_size_bytes: int) -> None:
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise LateDevAttributionValidationError("source SHA-256 must be a lowercase hexadecimal digest")
    if isinstance(source_size_bytes, bool) or not isinstance(source_size_bytes, int) or source_size_bytes <= 0:
        raise LateDevAttributionValidationError("source artifact must not be empty")


def _validate_report_envelope(report: Mapping[str, Any], *, manifest: Any) -> list[Mapping[str, Any]]:
    if report.get("schema_version") != ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION:
        raise LateDevAttributionValidationError(
            f"raw report schema must be {ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION!r}"
        )

    payload = _mapping(report.get("manifest"), "manifest")
    expected_windows = [
        {
            "label": window.label,
            "start_ns": window.start_ns,
            "end_ns": window.end_ns,
            "role": window.role,
        }
        for window in manifest.windows
    ]
    expected_manifest = {
        "sha256": manifest.sha256,
        "candidate_params_sha256": manifest.candidate_params_sha256,
        "profile_sha256": manifest.profile_sha256,
        "windows": expected_windows,
        "seeds": list(manifest.seeds),
        "cost_stress_bps": list(manifest.cost_stress_bps),
    }
    for key, expected in expected_manifest.items():
        if payload.get(key) != expected:
            raise LateDevAttributionValidationError(f"raw report manifest identity mismatch: {key}")
    if "candidate_source_sha256" in payload and payload["candidate_source_sha256"] != manifest.candidate_source_sha256:
        raise LateDevAttributionValidationError("raw report manifest identity mismatch: candidate_source_sha256")

    oos_windows = [window["label"] for window in expected_windows if window["role"] == "OOS"]
    if oos_windows != ["late_dev_2025"]:
        raise LateDevAttributionValidationError("frozen report requires exactly one OOS window named late_dev_2025")

    expected_stages = [stage.value for stage in ArchitectureStage]
    if report.get("stages") != expected_stages:
        raise LateDevAttributionValidationError("raw report stage order does not match the frozen architecture")

    raw_runs = report.get("runs")
    if not isinstance(raw_runs, list) or not all(isinstance(run, Mapping) for run in raw_runs):
        raise LateDevAttributionValidationError("raw report runs must be a list of objects")
    runs = list(raw_runs)
    try:
        observed = [(str(run.get("window")), int(run.get("seed", -1))) for run in runs]
    except (TypeError, ValueError, OverflowError) as exc:
        raise LateDevAttributionValidationError("raw report matrix mismatch") from exc
    expected = list(manifest.expected_run_keys)
    if observed != expected or len(observed) != len(set(observed)):
        raise LateDevAttributionValidationError("raw report matrix mismatch")
    for run in runs:
        stages = _mapping(run.get("stages"), "run stages")
        if list(stages) != expected_stages:
            raise LateDevAttributionValidationError("run stage order does not match the frozen architecture")
        if not all(isinstance(stage_payload, Mapping) for stage_payload in stages.values()):
            raise LateDevAttributionValidationError("run stage payloads must be JSON objects")
    return runs


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LateDevAttributionValidationError(f"raw report {name} must be a JSON object")
    return value
