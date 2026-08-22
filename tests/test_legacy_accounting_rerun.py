from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from zenquant.research.architecture_experiment import ArchitectureStage
from zenquant.research.legacy_accounting_validity import ValidityState

FORMULA_HASH = "sha256:" + "f" * 64


def _load_cli_module():
    path = Path(__file__).parent.parent / "scripts" / "rerun_legacy_accounting_baseline.py"
    spec = importlib.util.spec_from_file_location("rerun_legacy_accounting_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _minimal_report(manifest, entries: list[tuple[str, int]]) -> dict[str, object]:
    return {
        "schema_version": "architecture_attribution.v4",
        "constraints": {"parameter_search": False},
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
        "runs": [
            {
                "window": window,
                "seed": seed,
                "identities": {"formula_hashes": [FORMULA_HASH]},
                "stages": {stage.value: {"stage": stage.value} for stage in ArchitectureStage},
            }
            for window, seed in entries
        ],
    }


def _install_fast_frozen_runner(monkeypatch, module) -> None:
    def fake_run_frozen_matrix_entry(*, root, manifest, window, seed):
        del root, manifest
        return window.label, seed

    def fake_build_architecture_attribution_report(entries, *, manifest, fixed_notional):
        assert fixed_notional == 100_000.0
        return _minimal_report(manifest, entries)

    monkeypatch.setattr(module, "run_frozen_matrix_entry", fake_run_frozen_matrix_entry)
    monkeypatch.setattr(
        module,
        "build_architecture_attribution_report",
        fake_build_architecture_attribution_report,
    )


def test_parser_exposes_only_frozen_io_arguments():
    module = _load_cli_module()
    parser = module.build_parser()

    args = parser.parse_args(
        [
            "--root",
            "market-data",
            "--legacy-input",
            "legacy.json",
            "--output-dir",
            "rerun",
        ]
    )

    assert args.root == Path("market-data")
    assert args.legacy_input == Path("legacy.json")
    assert args.output_dir == Path("rerun")
    option_dests = {action.dest for action in parser._actions if action.option_strings and action.dest != "help"}
    assert option_dests == {"root", "legacy_input", "output_dir"}
    for forbidden in ("--seed", "--window", "--params", "--trials", "--search-space"):
        with pytest.raises(SystemExit):
            parser.parse_args([forbidden, "override"])


def test_rerun_validates_manifest_before_reading_legacy_input(tmp_path, monkeypatch):
    module = _load_cli_module()
    manifest = tmp_path / "architecture_experiment_v1.json"
    manifest.write_text('{"tampered":true}', encoding="utf-8")
    monkeypatch.setattr(module, "FROZEN_MANIFEST_PATH", manifest)

    with pytest.raises(ValueError, match="manifest SHA-256 mismatch"):
        module.main(
            [
                "--root",
                str(tmp_path / "data"),
                "--legacy-input",
                str(tmp_path / "missing.json"),
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )


def test_rerun_preserves_legacy_and_publishes_frozen_identities(tmp_path, monkeypatch):
    module = _load_cli_module()
    _install_fast_frozen_runner(monkeypatch, module)
    manifest = module.load_architecture_experiment_manifest(module.FROZEN_MANIFEST_PATH)
    entries = list(manifest.expected_run_keys)
    legacy_input = tmp_path / "legacy.json"
    legacy_bytes = json.dumps(_minimal_report(manifest, entries), indent=2, sort_keys=True).encode()
    legacy_input.write_bytes(legacy_bytes)
    output_dir = tmp_path / "accounting"

    exit_code = module.main(
        [
            "--root",
            str(tmp_path / "data"),
            "--legacy-input",
            str(legacy_input),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert legacy_input.read_bytes() == legacy_bytes
    assert {path.name for path in output_dir.iterdir()} == {
        "fixed_event_attribution.json",
        "full_system_attribution.json",
        "stage_validity.json",
        "summary.json",
    }
    full_system = json.loads((output_dir / "full_system_attribution.json").read_bytes())
    fixed_event = json.loads((output_dir / "fixed_event_attribution.json").read_bytes())
    stage_validity = json.loads((output_dir / "stage_validity.json").read_bytes())
    summary = json.loads((output_dir / "summary.json").read_bytes())

    assert len(full_system["runs"]) == 30
    assert full_system["constraints"]["parameter_search"] is False
    assert fixed_event["status"] == "INSUFFICIENT_EVIDENCE"
    assert fixed_event["legacy_state"] == ValidityState.UNVERIFIED_LEGACY.value
    assert stage_validity["counts"][ValidityState.UNVERIFIED_LEGACY.value] == 30 * len(ArchitectureStage)
    assert summary["constraints"] == {"parameter_search": False}
    assert summary["matrix"] == {"expected_runs": 30, "observed_runs": 30}
    assert summary["identities"]["manifest_sha256"] == manifest.sha256
    assert summary["identities"]["formula_hashes"] == [FORMULA_HASH]
    assert summary["full_system_comparison"]["status"] == ValidityState.UNVERIFIED_LEGACY.value
    assert summary["full_system_comparison"]["first_confirmed_divergence"] is None
    assert summary["resource_behavior"]["legacy_full_json_load_count"] == 0
    assert summary["resource_behavior"]["legacy_peak_loaded_scope"] == "single_run"


def test_failed_rerun_does_not_replace_last_good_outputs(tmp_path, monkeypatch):
    module = _load_cli_module()
    manifest = module.load_architecture_experiment_manifest(module.FROZEN_MANIFEST_PATH)
    legacy_input = tmp_path / "legacy.json"
    legacy_input.write_text(
        json.dumps(_minimal_report(manifest, list(manifest.expected_run_keys)), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    output_dir = tmp_path / "accounting"
    output_dir.mkdir()
    expected = {}
    for name in module.OUTPUT_FILENAMES:
        expected[name] = f"last-good:{name}".encode()
        (output_dir / name).write_bytes(expected[name])

    calls = 0

    def interrupted_runner(*, root, manifest, window, seed):
        nonlocal calls
        del root, manifest, window, seed
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted")
        return object()

    monkeypatch.setattr(module, "run_frozen_matrix_entry", interrupted_runner)

    with pytest.raises(RuntimeError, match="interrupted"):
        module.main(
            [
                "--root",
                str(tmp_path / "data"),
                "--legacy-input",
                str(legacy_input),
                "--output-dir",
                str(output_dir),
            ]
        )

    assert {name: (output_dir / name).read_bytes() for name in module.OUTPUT_FILENAMES} == expected
    assert not list(tmp_path.glob(".accounting.rerun-*"))


def test_failed_atomic_publication_rolls_back_every_last_good_output(tmp_path, monkeypatch):
    module = _load_cli_module()
    output_dir = tmp_path / "accounting"
    staging_dir = tmp_path / ".accounting.rerun-ready"
    output_dir.mkdir()
    staging_dir.mkdir()
    expected = {}
    for name in module.OUTPUT_FILENAMES:
        expected[name] = f"last-good:{name}".encode()
        (output_dir / name).write_bytes(expected[name])
        (staging_dir / name).write_bytes(f"new:{name}".encode())

    real_replace = os.replace
    publication_calls = 0

    def fail_second_publication(source, destination):
        nonlocal publication_calls
        if Path(source).parent == staging_dir and Path(destination).parent == output_dir:
            publication_calls += 1
            if publication_calls == 2:
                raise OSError("publication interrupted")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second_publication)

    with pytest.raises(OSError, match="publication interrupted"):
        module._publish_staged_outputs(staging_dir=staging_dir, output_dir=output_dir)

    assert {name: (output_dir / name).read_bytes() for name in module.OUTPUT_FILENAMES} == expected
    assert not list(tmp_path.glob(".accounting.rollback-*"))


def test_first_confirmed_divergence_uses_frozen_matrix_and_stage_order():
    module = _load_cli_module()
    records = [
        {
            "window": "train_full",
            "seed": 42,
            "stage": ArchitectureStage.A3.value,
            "state": ValidityState.CONFIRMED_AFFECTED.value,
        },
        {
            "window": "train_full",
            "seed": 42,
            "stage": ArchitectureStage.A2.value,
            "state": ValidityState.CONFIRMED_AFFECTED.value,
        },
    ]

    comparison = module.build_full_system_comparison(records)

    assert comparison["status"] == ValidityState.CONFIRMED_AFFECTED.value
    assert comparison["first_confirmed_divergence"] == {
        "window": "train_full",
        "seed": 42,
        "stage": ArchitectureStage.A2.value,
    }
