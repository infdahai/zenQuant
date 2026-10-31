from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from zenquant.research.legacy_accounting_validity import InvalidPerformanceEvidenceError

GOVERNANCE_FIELDS = {
    "ledger_formula_hash": "sha256:" + "c" * 64,
    "decision_at": "2026-08-23T00:00:00Z",
    "approving_decision": "legacy-accounting-integrity-plan-task-3",
}


def _load_rescore_script():
    path = Path("scripts/run_gate_v2_rescore.py")
    spec = importlib.util.spec_from_file_location("gate_v2_rescore_validity_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_august_candidate_is_rejected_before_parameter_json_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _load_rescore_script()
    source = Path("results/remediation_20260821/train_dev_smoke_daily_switch_policy/framework_optimization.json")
    absolute_source = tmp_path / source
    absolute_source.parent.mkdir(parents=True)
    source_bytes = b'{"parameters":"must not be parsed"}'
    absolute_source.write_bytes(source_bytes)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "name": "august_rank1",
                        "kind": "ranked_params",
                        "source": str(source),
                        "selector": "train_candidate_diagnostics.top_train_trial_diagnostics",
                        "rank": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "legacy-accounting-validity.v1",
                "records": [
                    {
                        "artifact_path": source.as_posix(),
                        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                        "selector": "train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
                        "state": "CONFIRMED_AFFECTED",
                        "reason": "legacy_fee_cash_floor_can_create_equity",
                        "metric_families": ["promotion_gate"],
                        "stages": ["A0"],
                        "superseding_artifact": None,
                        **GOVERNANCE_FIELDS,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    real_read_json = script._read_json

    def guarded_read_json(path: Path):
        if path == absolute_source.resolve():
            pytest.fail("candidate parameter JSON was loaded before validity rejection")
        return real_read_json(path)

    monkeypatch.setattr(script, "_read_json", guarded_read_json)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(InvalidPerformanceEvidenceError, match="CONFIRMED_AFFECTED"):
        script.load_candidate_manifest(
            manifest,
            validity_registry_path=registry,
            repository_root=tmp_path,
        )


def test_missing_candidate_source_keeps_specific_missing_error(tmp_path: Path) -> None:
    script = _load_rescore_script()
    missing = tmp_path / "missing.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "name": "missing",
                        "kind": "ranked_params",
                        "source": str(missing),
                        "selector": "top_optuna_trials",
                        "rank": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=f"candidate_source_missing:{missing}"):
        script.load_candidate_manifest(manifest, validity_registry_path=tmp_path / "absent-registry.json")


def _write_ranked_source(tmp_path: Path) -> tuple[Path, bytes]:
    source = tmp_path / "ranked.json"
    source_bytes = json.dumps(
        {
            "train_candidate_diagnostics": {
                "top_train_trial_diagnostics": [
                    {"params": {"max_leverage": 1.0}},
                    {"params": {"max_leverage": 2.0}},
                ]
            }
        }
    ).encode("utf-8")
    source.write_bytes(source_bytes)
    return source, source_bytes


def _write_manifest(tmp_path: Path, *, source: str, rank: int) -> Path:
    manifest = tmp_path / f"manifest-{rank}.json"
    manifest.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "name": f"rank-{rank}",
                        "kind": "ranked_params",
                        "source": source,
                        "selector": "train_candidate_diagnostics.top_train_trial_diagnostics",
                        "rank": rank,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _write_registry(tmp_path: Path, *, artifact_path: str, source_sha256: str, records: list[dict]) -> Path:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "legacy-accounting-validity.v1",
                "records": [
                    {
                        "artifact_path": artifact_path,
                        "source_sha256": source_sha256,
                        "reason": "reviewed",
                        "metric_families": ["promotion_gate"],
                        "stages": ["A0"],
                        "superseding_artifact": None,
                        **GOVERNANCE_FIELDS,
                        **record,
                    }
                    for record in records
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry


@pytest.mark.parametrize("alias_kind", ["dotdot", "symlink", "copy"])
def test_governed_candidate_cannot_bypass_policy_through_path_alias(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    source, source_bytes = _write_ranked_source(tmp_path)
    governed_path = source
    candidate_path: Path
    if alias_kind == "dotdot":
        candidate_path = source.parent / "alias" / ".." / source.name
    elif alias_kind == "symlink":
        candidate_path = tmp_path / "alias.json"
        candidate_path.symlink_to(source)
    else:
        candidate_path = tmp_path / "copy.json"
        candidate_path.write_bytes(source_bytes)
    registry = _write_registry(
        tmp_path,
        artifact_path=str(governed_path),
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        records=[
            {
                "selector": "train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
                "state": "CONFIRMED_AFFECTED",
            }
        ],
    )

    with pytest.raises(InvalidPerformanceEvidenceError, match="CONFIRMED_AFFECTED"):
        _load_rescore_script().load_candidate_manifest(
            _write_manifest(tmp_path, source=str(candidate_path), rank=1),
            validity_registry_path=registry,
        )


def test_rank_selector_mismatch_fails_before_source_json_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, source_bytes = _write_ranked_source(tmp_path)
    registry = _write_registry(
        tmp_path,
        artifact_path=str(source),
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        records=[
            {
                "selector": "train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
                "state": "PROVEN_UNAFFECTED",
            }
        ],
    )
    script = _load_rescore_script()
    real_read_json = script._read_json

    def guarded_read_json(path: Path):
        if path == source:
            pytest.fail("rank-mismatched source was parsed before validity rejection")
        return real_read_json(path)

    monkeypatch.setattr(script, "_read_json", guarded_read_json)

    with pytest.raises(InvalidPerformanceEvidenceError, match="selector_unclassified"):
        script.load_candidate_manifest(
            _write_manifest(tmp_path, source=str(source), rank=2),
            validity_registry_path=registry,
        )


def test_mixed_selector_validity_allows_only_proven_rank(tmp_path: Path) -> None:
    source, source_bytes = _write_ranked_source(tmp_path)
    registry = _write_registry(
        tmp_path,
        artifact_path=str(source),
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        records=[
            {
                "selector": "train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
                "state": "CONFIRMED_AFFECTED",
            },
            {
                "selector": "train_candidate_diagnostics.top_train_trial_diagnostics[1].params",
                "state": "PROVEN_UNAFFECTED",
            },
        ],
    )

    with pytest.raises(InvalidPerformanceEvidenceError, match="CONFIRMED_AFFECTED"):
        _load_rescore_script().load_candidate_manifest(
            _write_manifest(tmp_path, source=str(source), rank=1),
            validity_registry_path=registry,
        )
    records = _load_rescore_script().load_candidate_manifest(
        _write_manifest(tmp_path, source=str(source), rank=2),
        validity_registry_path=registry,
    )

    assert records[0].params == {"max_leverage": 2.0}


def test_repo_relative_candidate_and_default_registry_are_anchored_outside_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_rescore_script()
    repository_root = Path(__file__).resolve().parent.parent
    source = repository_root / "config/research/legacy_accounting_validity_v1.json"
    source_bytes = source.read_bytes()
    registry = _write_registry(
        tmp_path,
        artifact_path="config/research/legacy_accounting_validity_v1.json",
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        records=[
            {
                "selector": "train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
                "state": "CONFIRMED_AFFECTED",
            }
        ],
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    with pytest.raises(InvalidPerformanceEvidenceError, match="CONFIRMED_AFFECTED"):
        script.load_candidate_manifest(
            _write_manifest(tmp_path, source="config/research/legacy_accounting_validity_v1.json", rank=1),
            validity_registry_path=registry,
        )


def test_reserialized_ranked_artifact_cannot_bypass_unknown_identity_policy(tmp_path: Path) -> None:
    source, _source_bytes = _write_ranked_source(tmp_path)
    source.write_text(
        json.dumps(json.loads(source.read_text(encoding="utf-8")), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    registry = _write_registry(
        tmp_path,
        artifact_path=str(tmp_path / "original-august.json"),
        source_sha256="a" * 64,
        records=[
            {
                "selector": "train_candidate_diagnostics.top_train_trial_diagnostics[0].params",
                "state": "CONFIRMED_AFFECTED",
            }
        ],
    )

    with pytest.raises(InvalidPerformanceEvidenceError, match="artifact_unclassified"):
        _load_rescore_script().load_candidate_manifest(
            _write_manifest(tmp_path, source=str(source), rank=1),
            validity_registry_path=registry,
        )


def test_explicit_params_are_new_input_without_historical_ranking_evidence(tmp_path: Path) -> None:
    manifest = tmp_path / "explicit.json"
    manifest.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "name": "new-fixed-candidate",
                        "kind": "explicit_params",
                        "params": {"max_leverage": 1.25},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    records = _load_rescore_script().load_candidate_manifest(
        manifest,
        validity_registry_path=tmp_path / "does-not-need-a-registry.json",
    )

    assert records[0].kind == "explicit_params"
    assert records[0].params == {"max_leverage": 1.25}
    assert records[0].source is None
