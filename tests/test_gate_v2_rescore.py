from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from zenquant.research.legacy_accounting_validity import InvalidPerformanceEvidenceError


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
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    real_read_json = script._read_json

    def guarded_read_json(path: Path):
        if path == source:
            pytest.fail("candidate parameter JSON was loaded before validity rejection")
        return real_read_json(path)

    monkeypatch.setattr(script, "_read_json", guarded_read_json)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(InvalidPerformanceEvidenceError, match="CONFIRMED_AFFECTED"):
        script.load_candidate_manifest(manifest, validity_registry_path=registry)


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
