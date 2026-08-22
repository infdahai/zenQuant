"""Contract tests for the frozen late-development attribution envelope."""

from __future__ import annotations

from copy import deepcopy

import pytest

from zenquant.research.architecture_experiment import (
    ArchitectureStage,
    load_architecture_experiment_manifest,
)
from zenquant.research.late_dev_cost_attribution import (
    LateDevAttributionValidationError,
    build_late_dev_cost_attribution,
    canonical_late_dev_attribution_bytes,
)


def _complete_raw_report() -> dict[str, object]:
    """Build a complete frozen matrix from the checked-in manifest.

    The stage payloads are intentionally literal rather than borrowed from
    another test module: this fixture tests the envelope, not strategy logic.
    """
    manifest = load_architecture_experiment_manifest()
    stage_order = [stage.value for stage in ArchitectureStage]
    runs: list[dict[str, object]] = []
    for window in manifest.windows:
        for seed in manifest.seeds:
            runs.append(
                {
                    "window": window.label,
                    "seed": seed,
                    "identities": {
                        "formula_hashes": ["formula-v1"],
                        "alpha_config_hashes": ["alpha-v1"],
                    },
                    "stages": {stage: {"stage": stage, "literal": True} for stage in stage_order},
                }
            )
    return {
        "schema_version": "architecture_attribution.v4",
        "stages": stage_order,
        "manifest": {
            "sha256": manifest.sha256,
            "candidate_params_sha256": manifest.candidate_params_sha256,
            "profile_sha256": manifest.profile_sha256,
            "windows": [
                {
                    "label": window.label,
                    "start_ns": window.start_ns,
                    "end_ns": window.end_ns,
                    "role": window.role,
                }
                for window in manifest.windows
            ],
            "seeds": list(manifest.seeds),
            "cost_stress_bps": list(manifest.cost_stress_bps),
        },
        "runs": runs,
    }


def _build(report: dict[str, object]) -> dict[str, object]:
    return build_late_dev_cost_attribution(
        report,
        source_artifact="architecture_attribution.json",
        source_sha256="a" * 64,
        source_size_bytes=123,
    )


def test_build_report_rejects_duplicate_or_missing_frozen_matrix():
    report = _complete_raw_report()
    report["runs"].pop()  # type: ignore[union-attr]
    with pytest.raises(LateDevAttributionValidationError, match="matrix"):
        _build(report)


def test_canonical_bytes_are_deterministic():
    first = _build(_complete_raw_report())
    second = _build(deepcopy(_complete_raw_report()))

    assert canonical_late_dev_attribution_bytes(first) == canonical_late_dev_attribution_bytes(second)
    assert b'"created_at"' not in canonical_late_dev_attribution_bytes(first)
