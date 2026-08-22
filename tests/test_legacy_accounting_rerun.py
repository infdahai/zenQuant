from __future__ import annotations

import errno
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from zenquant.research.architecture_experiment import ArchitectureStage
from zenquant.research.legacy_accounting_validity import ValidityState

FORMULA_HASH = "sha256:82af23a08c68a94641e1d7769ecdf9619059dddbeb531dd3703cc4d2ff809f4a"


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
    assert "must not already exist" in " ".join(parser.format_help().split())
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
    assert summary["resource_behavior"]["legacy_sha256_stream_passes"] == 2
    assert summary["resource_behavior"]["publication"] == (
        "temporary_sibling_directory_then_atomic_noreplace_directory_rename"
    )


def test_existing_output_dir_fails_before_compute_and_preserves_last_good_outputs(tmp_path, monkeypatch):
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

    def forbidden_runner(*, root, manifest, window, seed):
        del root, manifest, window, seed
        raise AssertionError("existing output must fail before compute")

    monkeypatch.setattr(module, "run_frozen_matrix_entry", forbidden_runner)

    with pytest.raises(FileExistsError, match="new versioned output directory"):
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


def test_cli_reports_existing_output_as_clean_invalid_input(tmp_path):
    legacy_input = tmp_path / "legacy.json"
    legacy_input.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "accounting-v1"
    output_dir.mkdir()
    script = Path(__file__).parent.parent / "scripts" / "rerun_legacy_accounting_baseline.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(tmp_path / "data"),
            "--legacy-input",
            str(legacy_input),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "new versioned output directory" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_publisher_refuses_an_existing_empty_output_directory(tmp_path):
    module = _load_cli_module()
    output_dir = tmp_path / "accounting"
    staging_dir = tmp_path / ".accounting.rerun-ready"
    output_dir.mkdir()
    staging_dir.mkdir()
    for name in module.OUTPUT_FILENAMES:
        (staging_dir / name).write_bytes(f"new:{name}".encode())

    with pytest.raises(FileExistsError, match="new versioned output directory"):
        module._publish_staged_outputs(staging_dir=staging_dir, output_dir=output_dir)

    assert not list(output_dir.iterdir())
    assert {path.name for path in staging_dir.iterdir()} == set(module.OUTPUT_FILENAMES)


@pytest.mark.parametrize("collision_errno", [errno.EEXIST, errno.ENOTEMPTY])
def test_publisher_race_preserves_concurrently_created_destination(tmp_path, monkeypatch, collision_errno):
    module = _load_cli_module()
    output_dir = tmp_path / "accounting"
    staging_dir = tmp_path / ".accounting.rerun-ready"
    staging_dir.mkdir()
    for name in module.OUTPUT_FILENAMES:
        (staging_dir / name).write_bytes(f"new:{name}".encode())

    def race_at_atomic_boundary(source, destination):
        assert source == staging_dir
        assert destination == output_dir
        destination.mkdir()
        (destination / "concurrent-last-good").write_bytes(b"preserve-me")
        raise OSError(collision_errno, "destination exists", destination)

    monkeypatch.setattr(module, "_atomic_rename_directory_noreplace", race_at_atomic_boundary)

    with pytest.raises(FileExistsError, match="new versioned output directory") as exc_info:
        module._publish_staged_outputs(staging_dir=staging_dir, output_dir=output_dir)

    assert exc_info.value.errno == errno.EEXIST
    assert (output_dir / "concurrent-last-good").read_bytes() == b"preserve-me"
    assert {path.name for path in staging_dir.iterdir()} == set(module.OUTPUT_FILENAMES)


def test_publisher_uses_controlled_atomic_noreplace_primitive(tmp_path, monkeypatch):
    module = _load_cli_module()
    output_dir = tmp_path / "accounting"
    staging_dir = tmp_path / ".accounting.rerun-ready"
    staging_dir.mkdir()
    for name in module.OUTPUT_FILENAMES:
        (staging_dir / name).write_bytes(f"new:{name}".encode())
    calls = []

    def controlled_atomic_noreplace(source, destination):
        calls.append((source, destination))
        os.rename(source, destination)

    monkeypatch.setattr(module, "_atomic_rename_directory_noreplace", controlled_atomic_noreplace)

    module._publish_staged_outputs(staging_dir=staging_dir, output_dir=output_dir)

    assert calls == [(staging_dir, output_dir)]
    assert not staging_dir.exists()
    assert {path.name for path in output_dir.iterdir()} == set(module.OUTPUT_FILENAMES)


def test_atomic_noreplace_fails_closed_on_unsupported_platform(tmp_path, monkeypatch):
    module = _load_cli_module()
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    monkeypatch.setattr(module.sys, "platform", "unsupported")

    def unsafe_fallback(*args, **kwargs):
        raise AssertionError("must not fall back to unsafe os.rename")

    monkeypatch.setattr(module.os, "rename", unsafe_fallback)

    with pytest.raises(OSError) as exc_info:
        module._atomic_rename_directory_noreplace(source, destination)

    assert exc_info.value.errno == errno.ENOTSUP
    assert source.is_dir()
    assert not destination.exists()


def test_alias_protection_uses_samefile_for_existing_paths(tmp_path):
    module = _load_cli_module()
    legacy_input = tmp_path / "legacy.json"
    legacy_input.write_text("{}", encoding="utf-8")
    output_alias = tmp_path / "legacy-alias"
    output_alias.symlink_to(legacy_input)

    with pytest.raises(ValueError, match="alias"):
        module._assert_safe_paths(legacy_input, output_alias)


def test_alias_protection_casefolds_generated_target_paths(tmp_path):
    module = _load_cli_module()
    output_dir = tmp_path / "accounting"
    output_dir.mkdir()
    legacy_input = output_dir / "SUMMARY.JSON"
    legacy_input.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="alias"):
        module._assert_safe_paths(legacy_input, output_dir)


def test_post_audit_legacy_mutation_aborts_before_publication(tmp_path, monkeypatch):
    module = _load_cli_module()
    _install_fast_frozen_runner(monkeypatch, module)
    manifest = module.load_architecture_experiment_manifest(module.FROZEN_MANIFEST_PATH)
    legacy_input = tmp_path / "legacy.json"
    legacy_input.write_text(
        json.dumps(_minimal_report(manifest, list(manifest.expected_run_keys)), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    output_dir = tmp_path / "accounting"
    real_stream_stage_validity = module._stream_stage_validity

    def mutate_after_audit(*, legacy_input, repaired_report, manifest):
        result = real_stream_stage_validity(
            legacy_input=legacy_input,
            repaired_report=repaired_report,
            manifest=manifest,
        )
        legacy_input.write_text("mutated-after-audit", encoding="utf-8")
        return result

    monkeypatch.setattr(module, "_stream_stage_validity", mutate_after_audit)

    with pytest.raises(ValueError, match="legacy source changed during rerun"):
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

    assert not output_dir.exists()
    assert not list(tmp_path.glob(".accounting.rerun-*"))


def test_legacy_run_streamer_accepts_nested_and_escaped_run_payloads(tmp_path):
    module = _load_cli_module()
    source = tmp_path / "legacy.json"
    expected_runs = [
        {
            "window": "train_full",
            "seed": 42,
            "nested": {
                "message": 'escaped quote: " and braces: {still text}',
                "items": [{"value": 1}, {"value": 2}],
            },
        },
        {"window": "2020", "seed": 123, "nested": {"message": "backslash: \\"}},
    ]
    source.write_text(
        json.dumps(
            {
                "metadata": {"nested": {"runs": [{"not": "top-level"}]}},
                "runs": expected_runs,
                "tail": {"complete": True},
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    assert list(module._iter_legacy_runs(source)) == expected_runs


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"outer": {"runs": [{"window": "nested", "seed": 1}]}}, "top-level runs array"),
        ('{\n  "runs": [\n    {"window": "x", "seed": 1}\n  ]', "top-level JSON object is incomplete"),
        ('{\n  "runs": [\n    {"window": "x", "seed": 1}\n  ]\n} trailing', "trailing content"),
        ('{\n  "runs": [\n    {"window": "x", "seed": 1}', "runs array is incomplete"),
        (
            '{\n  "runs": [\n    {"window": "x", "seed": 1}\n  ],\n  "tail": invalid\n}',
            "top-level JSON is malformed",
        ),
    ],
)
def test_legacy_run_streamer_rejects_incomplete_or_nested_only_documents(tmp_path, payload, error):
    module = _load_cli_module()
    source = tmp_path / "legacy.json"
    if isinstance(payload, dict):
        source.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    else:
        source.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        list(module._iter_legacy_runs(source))


def test_repaired_report_rejects_boolean_seed_before_matrix_comparison():
    module = _load_cli_module()
    manifest = module.load_architecture_experiment_manifest(module.FROZEN_MANIFEST_PATH)
    report = _minimal_report(manifest, list(manifest.expected_run_keys))
    report["runs"][0]["seed"] = True

    with pytest.raises(ValueError, match="integer seed"):
        module._validate_repaired_report(report, manifest=manifest)


def test_repaired_report_rejects_non_string_window_before_matrix_comparison():
    module = _load_cli_module()
    manifest = module.load_architecture_experiment_manifest(module.FROZEN_MANIFEST_PATH)
    report = _minimal_report(manifest, list(manifest.expected_run_keys))
    report["runs"][0]["window"] = 2020

    with pytest.raises(ValueError, match="window string"):
        module._validate_repaired_report(report, manifest=manifest)


@pytest.mark.parametrize(
    "formula_hash",
    [
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        "sha256:" + "e" * 64,
    ],
)
def test_repaired_report_requires_exact_frozen_formula_identity(formula_hash):
    module = _load_cli_module()
    manifest = module.load_architecture_experiment_manifest(module.FROZEN_MANIFEST_PATH)
    report = _minimal_report(manifest, list(manifest.expected_run_keys))
    report["runs"][0]["identities"]["formula_hashes"] = [formula_hash]

    with pytest.raises(ValueError, match="frozen repaired formula hash"):
        module._validate_repaired_report(report, manifest=manifest)


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


@pytest.mark.parametrize(
    ("legacy_error", "repaired_error", "expected_reason"),
    [
        ("missing legacy donor", None, "legacy_stage_provenance_incomplete"),
        (None, "missing repaired donor", "repaired_stage_provenance_incomplete"),
        (
            "missing legacy payload",
            "missing repaired payload",
            "legacy_and_repaired_stage_provenance_incomplete",
        ),
        (None, None, "stage_comparison_unverified"),
    ],
)
def test_unverified_reason_identifies_the_incomplete_side(legacy_error, repaired_error, expected_reason):
    module = _load_cli_module()
    comparison = module.build_full_system_comparison(
        [
            {
                "window": "train_full",
                "seed": 42,
                "stage": ArchitectureStage.A0.value,
                "state": ValidityState.UNVERIFIED_LEGACY.value,
                "legacy_error": legacy_error,
                "repaired_error": repaired_error,
            }
        ]
    )

    assert comparison["status"] == ValidityState.UNVERIFIED_LEGACY.value
    assert comparison["reason"] == expected_reason
