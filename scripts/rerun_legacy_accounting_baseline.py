#!/usr/bin/env python3
"""Rerun the exact frozen August matrix after the legacy accounting repair."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.run_architecture_experiment import (
    FIXED_NOTIONAL,
    build_architecture_attribution_report,
    run_frozen_matrix_entry,
)

from zenquant.research.architecture_experiment import (
    DEFAULT_ARCHITECTURE_MANIFEST_PATH,
    ArchitectureExperimentManifest,
    ArchitectureStage,
    load_architecture_experiment_manifest,
)
from zenquant.research.legacy_accounting_provenance import (
    STAGE_PROVENANCE_SCHEMA_VERSION,
    audit_stage_provenance,
)
from zenquant.research.legacy_accounting_validity import ValidityState

FROZEN_MANIFEST_PATH = DEFAULT_ARCHITECTURE_MANIFEST_PATH
OUTPUT_FILENAMES = (
    "fixed_event_attribution.json",
    "full_system_attribution.json",
    "stage_validity.json",
    "summary.json",
)
_JSON_HASH_CHUNK_BYTES = 1024 * 1024
_FIXED_EVENT_SCHEMA_VERSION = "legacy-accounting-fixed-event-attribution.v1"
_RERUN_SUMMARY_SCHEMA_VERSION = "legacy-accounting-rerun-summary.v1"
EXPECTED_REPAIRED_FORMULA_HASH = "sha256:82af23a08c68a94641e1d7769ecdf9619059dddbeb531dd3703cc4d2ff809f4a"
_SHA256_IDENTITY_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_RUNS_DECLARATION_PATTERN = re.compile(r'^\s*"runs"\s*:\s*\[\s*$')
_DARWIN_AT_FDCWD = -2
_DARWIN_RENAME_EXCL = 0x00000004
_LINUX_AT_FDCWD = -100
_LINUX_RENAME_NOREPLACE = 0x00000001


def build_parser() -> argparse.ArgumentParser:
    """Expose frozen I/O locations and no research/search overrides."""
    parser = argparse.ArgumentParser(description="Rerun the frozen August accounting baseline")
    parser.add_argument("--root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument(
        "--legacy-input",
        type=Path,
        default=Path("results/quant_v2_acceptance/architecture_attribution.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/accounting_integrity_20260823"),
        help="Brand-new versioned output directory; must not already exist.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = load_architecture_experiment_manifest(FROZEN_MANIFEST_PATH)
    _assert_safe_paths(args.legacy_input, args.output_dir)
    _assert_new_output_directory(args.output_dir)
    legacy_source = _source_identity(args.legacy_input)

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{args.output_dir.name}.rerun-",
            dir=args.output_dir.parent,
        )
    )
    try:
        repaired_report = _run_frozen_matrix(root=args.root, manifest=manifest)
        formula_hashes = _validate_repaired_report(repaired_report, manifest=manifest)
        repaired_report["accounting_rerun"] = {
            "schema_version": _RERUN_SUMMARY_SCHEMA_VERSION,
            "parameter_search": False,
            "manifest_sha256": manifest.sha256,
            "formula_hashes": formula_hashes,
            "legacy_source": legacy_source,
        }

        full_system_path = staging_dir / "full_system_attribution.json"
        _write_json(full_system_path, repaired_report)
        repaired_source = _source_identity(full_system_path)
        repaired_source["path"] = str(args.output_dir / full_system_path.name)

        stage_validity = _stream_stage_validity(
            legacy_input=args.legacy_input,
            repaired_report=repaired_report,
            manifest=manifest,
        )
        stage_validity.update(
            {
                "parameter_search": False,
                "manifest_sha256": manifest.sha256,
                "formula_hashes": formula_hashes,
                "sources": {"legacy": legacy_source, "repaired": repaired_source},
            }
        )
        full_system_comparison = build_full_system_comparison(stage_validity["records"])
        fixed_event = _build_fixed_event_attribution(
            manifest=manifest,
            formula_hashes=formula_hashes,
            legacy_source=legacy_source,
            repaired_source=repaired_source,
        )
        summary = _build_summary(
            manifest=manifest,
            formula_hashes=formula_hashes,
            legacy_source=legacy_source,
            repaired_source=repaired_source,
            stage_validity=stage_validity,
            fixed_event=fixed_event,
            full_system_comparison=full_system_comparison,
            observed_runs=len(repaired_report["runs"]),
        )

        _write_json(staging_dir / "fixed_event_attribution.json", fixed_event)
        _write_json(staging_dir / "stage_validity.json", stage_validity)
        _write_json(staging_dir / "summary.json", summary)
        _assert_safe_paths(args.legacy_input, args.output_dir)
        final_legacy_source = _source_identity(args.legacy_input)
        if final_legacy_source != legacy_source:
            raise ValueError("legacy source changed during rerun; refusing publication")
        _publish_staged_outputs(staging_dir=staging_dir, output_dir=args.output_dir)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    print(
        f"Saved {len(manifest.expected_run_keys)} frozen accounting runs to {args.output_dir} "
        f"(parameter_search=false, legacy={legacy_source['sha256']})"
    )
    return 0


def cli(argv: list[str] | None = None) -> int:
    """Return a clean invalid-input exit instead of publishing a traceback."""
    try:
        return main(argv)
    except (OSError, ValueError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2


def _run_frozen_matrix(
    *,
    root: Path,
    manifest: ArchitectureExperimentManifest,
) -> dict[str, Any]:
    runs: list[Any] = []
    expected_count = len(manifest.expected_run_keys)
    for index, window in enumerate(manifest.windows):
        for seed_index, seed in enumerate(manifest.seeds):
            run_number = index * len(manifest.seeds) + seed_index + 1
            print(
                f"Running frozen matrix entry {run_number}/{expected_count}: window={window.label} seed={seed}",
                flush=True,
            )
            runs.append(
                run_frozen_matrix_entry(
                    root=root,
                    manifest=manifest,
                    window=window,
                    seed=seed,
                )
            )
    report = build_architecture_attribution_report(
        runs,
        manifest=manifest,
        fixed_notional=FIXED_NOTIONAL,
    )
    if not isinstance(report, dict):
        raise ValueError("frozen architecture runner returned a non-object report")
    return report


def _validate_repaired_report(
    report: Mapping[str, Any],
    *,
    manifest: ArchitectureExperimentManifest,
) -> list[str]:
    constraints = report.get("constraints")
    if not isinstance(constraints, Mapping) or constraints.get("parameter_search") is not False:
        raise ValueError("repaired report must explicitly set parameter_search=false")
    report_manifest = report.get("manifest")
    if not isinstance(report_manifest, Mapping):
        raise ValueError("repaired report is missing its frozen manifest identity")
    exact_manifest_fields = {
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
    }
    for field, expected in exact_manifest_fields.items():
        if report_manifest.get(field) != expected:
            raise ValueError(f"repaired report manifest identity mismatch: {field}")

    raw_runs = report.get("runs")
    if not isinstance(raw_runs, list) or not all(isinstance(run, Mapping) for run in raw_runs):
        raise ValueError("repaired report runs must be a list of objects")
    observed_keys: list[tuple[str, int]] = []
    for index, run in enumerate(raw_runs):
        window = run.get("window")
        seed = run.get("seed")
        if not isinstance(window, str) or not window:
            raise ValueError(f"repaired run {index} requires an exact window string")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"repaired run {index} requires an integer seed")
        observed_keys.append((window, seed))
    if observed_keys != list(manifest.expected_run_keys):
        raise ValueError("repaired report does not contain the exact frozen matrix")
    formula_hashes = sorted({formula_hash for run in raw_runs for formula_hash in _formula_hashes_from_run(run)})
    if formula_hashes != [EXPECTED_REPAIRED_FORMULA_HASH]:
        raise ValueError("repaired report does not use the frozen repaired formula hash")
    return formula_hashes


def _formula_hashes_from_run(run: Mapping[str, Any]) -> list[str]:
    identities = run.get("identities")
    if not isinstance(identities, Mapping):
        raise ValueError("repaired run is missing identities")
    value = identities.get("formula_hashes")
    if (
        not isinstance(value, list)
        or len(value) != 1
        or not isinstance(value[0], str)
        or _SHA256_IDENTITY_PATTERN.fullmatch(value[0]) is None
        or value[0] != EXPECTED_REPAIRED_FORMULA_HASH
    ):
        raise ValueError("repaired run does not contain the exact frozen repaired formula hash")
    return value


def _stream_stage_validity(
    *,
    legacy_input: Path,
    repaired_report: Mapping[str, Any],
    manifest: ArchitectureExperimentManifest,
) -> dict[str, Any]:
    repaired_runs = repaired_report["runs"]
    repaired_by_key = {(str(run["window"]), int(run["seed"])): run for run in repaired_runs}
    remaining = dict(repaired_by_key)
    records: list[dict[str, Any]] = []
    observed_legacy_keys: set[tuple[str, int]] = set()

    for legacy_run in _iter_legacy_runs(legacy_input):
        key = _run_key(legacy_run, label="legacy")
        if key in observed_legacy_keys:
            raise ValueError(f"legacy report contains duplicate run key {key!r}")
        observed_legacy_keys.add(key)
        repaired_run = remaining.pop(key, None)
        compared = audit_stage_provenance(
            {"runs": [legacy_run]},
            {"runs": [repaired_run]} if repaired_run is not None else {"runs": []},
        )
        records.extend(compared["records"])

    for key in manifest.expected_run_keys:
        repaired_run = remaining.pop(key, None)
        if repaired_run is None:
            continue
        compared = audit_stage_provenance(
            {"runs": []},
            {"runs": [repaired_run]},
        )
        records.extend(compared["records"])

    if remaining:
        raise ValueError(f"repaired report contains unexpected run keys {sorted(remaining)!r}")
    records.sort(key=_stage_record_sort_key)
    counts = {state.value: 0 for state in ValidityState}
    for record in records:
        counts[record["state"]] += 1
    return {
        "schema_version": STAGE_PROVENANCE_SCHEMA_VERSION,
        "records": records,
        "counts": counts,
    }


def _iter_legacy_runs(path: Path) -> Iterator[dict[str, Any]]:
    """Decode one pretty-printed top-level run at a time, never the 1GB report."""
    structure = _JsonStructure()
    found_runs = False
    closed_runs = False
    collecting = False
    fragments: list[str] = []
    envelope_fragments: list[str] = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not found_runs:
                if (
                    _RUNS_DECLARATION_PATTERN.fullmatch(line.rstrip("\r\n")) is not None
                    and structure.stack == ["}"]
                    and not structure.in_string
                ):
                    found_runs = True
                    structure.consume(line)
                    if structure.stack != ["}", "]"]:
                        raise ValueError("legacy top-level runs key must open an array")
                    declaration = line.rstrip("\r\n")
                    envelope_fragments.append(f"{declaration[: declaration.rfind('[')]}[]\n")
                    continue
                envelope_fragments.append(line)
                structure.consume(line)
                continue
            if not closed_runs and not collecting:
                stripped = line.strip()
                if structure.stack == ["}", "]"] and stripped in {"]", "],"}:
                    structure.consume(line)
                    closed_runs = True
                    envelope_fragments.append(",\n" if stripped.endswith(",") else "\n")
                    continue
                if not stripped:
                    structure.consume(line)
                    continue
                if structure.stack != ["}", "]"] or not stripped.startswith("{"):
                    raise ValueError("legacy report runs array contains a non-object value")
                collecting = True
            if not closed_runs and collecting:
                fragments.append(line)
                structure.consume(line)
            else:
                if (
                    _RUNS_DECLARATION_PATTERN.fullmatch(line.rstrip("\r\n")) is not None
                    and structure.stack == ["}"]
                    and not structure.in_string
                ):
                    raise ValueError("legacy report contains duplicate top-level runs keys")
                envelope_fragments.append(line)
                structure.consume(line)
            if collecting and structure.stack == ["}", "]"] and not structure.in_string:
                encoded = "".join(fragments).rstrip()
                if encoded.endswith(","):
                    encoded = encoded[:-1]
                value = json.loads(encoded)
                if not isinstance(value, dict):
                    raise ValueError("legacy report run must be an object")
                yield value
                fragments.clear()
                collecting = False
    if not found_runs:
        raise ValueError("legacy report is missing its top-level runs array")
    if collecting or not closed_runs:
        raise ValueError("legacy report runs array is incomplete")
    if not structure.root_closed or structure.stack or structure.in_string:
        raise ValueError("legacy top-level JSON object is incomplete")
    try:
        envelope = json.loads("".join(envelope_fragments))
    except json.JSONDecodeError as exc:
        raise ValueError("legacy top-level JSON is malformed outside the streamed runs array") from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get("runs"), list):
        raise ValueError("legacy report is missing its top-level runs array")


class _JsonStructure:
    __slots__ = ("escaped", "in_string", "root_closed", "root_started", "stack")

    def __init__(self) -> None:
        self.stack: list[str] = []
        self.in_string = False
        self.escaped = False
        self.root_started = False
        self.root_closed = False

    def consume(self, text: str) -> None:
        for character in text:
            if self.root_closed:
                if not character.isspace():
                    raise ValueError("legacy report contains trailing content after top-level JSON object")
                continue
            if not self.root_started:
                if character.isspace():
                    continue
                if character != "{":
                    raise ValueError("legacy report top-level JSON value must be an object")
                self.root_started = True
                self.stack.append("}")
                continue
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif character == "\\":
                    self.escaped = True
                elif character == '"':
                    self.in_string = False
                continue
            if character == '"':
                self.in_string = True
            elif character == "{":
                self.stack.append("}")
            elif character == "[":
                self.stack.append("]")
            elif character in "}]":
                if not self.stack or self.stack[-1] != character:
                    raise ValueError("legacy report contains mismatched JSON delimiters")
                self.stack.pop()
                if not self.stack:
                    self.root_closed = True


def _run_key(run: Mapping[str, Any], *, label: str) -> tuple[str, int]:
    window = run.get("window")
    seed = run.get("seed")
    if not isinstance(window, str) or not window:
        raise ValueError(f"{label} run requires a window")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"{label} run requires an integer seed")
    return window, seed


def build_full_system_comparison(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=_stage_record_sort_key)
    first_confirmed = next(
        (record for record in ordered if record.get("state") == ValidityState.CONFIRMED_AFFECTED.value),
        None,
    )
    first_unverified = next(
        (record for record in ordered if record.get("state") == ValidityState.UNVERIFIED_LEGACY.value),
        None,
    )
    if first_confirmed is not None:
        status = ValidityState.CONFIRMED_AFFECTED.value
    elif first_unverified is not None:
        status = ValidityState.UNVERIFIED_LEGACY.value
    else:
        status = ValidityState.PROVEN_UNAFFECTED.value
    return {
        "status": status,
        "first_confirmed_divergence": _stage_key_payload(first_confirmed),
        "first_unverified_comparison": _stage_key_payload(first_unverified),
        "reason": (
            _unverified_comparison_reason(first_unverified)
            if status == ValidityState.UNVERIFIED_LEGACY.value
            else "first_exact_stage_payload_change"
            if status == ValidityState.CONFIRMED_AFFECTED.value
            else "all_extracted_stage_payloads_match"
        ),
    }


def _unverified_comparison_reason(record: Mapping[str, Any] | None) -> str:
    if record is None:
        return "stage_comparison_unverified"
    legacy_incomplete = bool(record.get("legacy_error"))
    repaired_incomplete = bool(record.get("repaired_error"))
    if legacy_incomplete and repaired_incomplete:
        return "legacy_and_repaired_stage_provenance_incomplete"
    if legacy_incomplete:
        return "legacy_stage_provenance_incomplete"
    if repaired_incomplete:
        return "repaired_stage_provenance_incomplete"
    return "stage_comparison_unverified"


def _stage_key_payload(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {field: record[field] for field in ("window", "seed", "stage")}


def _stage_record_sort_key(record: Mapping[str, Any]) -> tuple[int, int, int, str]:
    window_order = {
        "train_full": 0,
        "2020": 1,
        "2021": 2,
        "2022": 3,
        "2023": 4,
        "late_dev_2025": 5,
    }
    stage_order = {stage.value: index for index, stage in enumerate(ArchitectureStage)}
    window = str(record.get("window"))
    stage = str(record.get("stage"))
    seed = record.get("seed")
    seed_value = seed if isinstance(seed, int) and not isinstance(seed, bool) else sys.maxsize
    return (
        window_order.get(window, sys.maxsize),
        seed_value,
        stage_order.get(stage, sys.maxsize),
        stage,
    )


def _build_fixed_event_attribution(
    *,
    manifest: ArchitectureExperimentManifest,
    formula_hashes: list[str],
    legacy_source: Mapping[str, Any],
    repaired_source: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": _FIXED_EVENT_SCHEMA_VERSION,
        "status": "INSUFFICIENT_EVIDENCE",
        "legacy_state": ValidityState.UNVERIFIED_LEGACY.value,
        "reason": "legacy_artifact_has_no_replayable_fixed_event_ledger",
        "parameter_search": False,
        "manifest_sha256": manifest.sha256,
        "formula_hashes": formula_hashes,
        "sources": {"legacy": dict(legacy_source), "repaired_full_system": dict(repaired_source)},
        "event_stream_identity": None,
        "first_affected_fee": None,
        "fills_marks_and_fees_identical": None,
        "accounting_delta": None,
        "funding_and_financing": "OUT_OF_SCOPE_NOT_EQUITY_INTEGRATED",
    }


def _build_summary(
    *,
    manifest: ArchitectureExperimentManifest,
    formula_hashes: list[str],
    legacy_source: Mapping[str, Any],
    repaired_source: Mapping[str, Any],
    stage_validity: Mapping[str, Any],
    fixed_event: Mapping[str, Any],
    full_system_comparison: Mapping[str, Any],
    observed_runs: int,
) -> dict[str, Any]:
    has_evidence_gap = (
        fixed_event["status"] == "INSUFFICIENT_EVIDENCE"
        or full_system_comparison["status"] == ValidityState.UNVERIFIED_LEGACY.value
    )
    return {
        "schema_version": _RERUN_SUMMARY_SCHEMA_VERSION,
        "status": "COMPLETED_WITH_EVIDENCE_GAPS" if has_evidence_gap else "COMPLETED",
        "constraints": {"parameter_search": False},
        "matrix": {
            "expected_runs": len(manifest.expected_run_keys),
            "observed_runs": observed_runs,
        },
        "identities": {
            "manifest_sha256": manifest.sha256,
            "candidate_params_sha256": manifest.candidate_params_sha256,
            "profile_sha256": manifest.profile_sha256,
            "formula_hashes": formula_hashes,
        },
        "sources": {"legacy": dict(legacy_source), "repaired": dict(repaired_source)},
        "fixed_event": {
            "status": fixed_event["status"],
            "legacy_state": fixed_event["legacy_state"],
            "reason": fixed_event["reason"],
        },
        "full_system_comparison": dict(full_system_comparison),
        "stage_validity_counts": dict(stage_validity["counts"]),
        "resource_behavior": {
            "legacy_sha256_stream_passes": 2,
            "legacy_stage_stream_passes": 1,
            "legacy_full_json_load_count": 0,
            "legacy_peak_loaded_scope": "single_run",
            "sha256_chunk_bytes": _JSON_HASH_CHUNK_BYTES,
            "publication": "temporary_sibling_directory_then_atomic_noreplace_directory_rename",
        },
    }


def _source_identity(path: Path) -> dict[str, Any]:
    size_bytes = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_JSON_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "size_bytes": size_bytes,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as output:
        json.dump(payload, output, allow_nan=False, indent=2, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())


def _publish_staged_outputs(*, staging_dir: Path, output_dir: Path) -> None:
    missing = [name for name in OUTPUT_FILENAMES if not (staging_dir / name).is_file()]
    if missing:
        raise ValueError(f"staged rerun is incomplete: missing={missing!r}")
    _assert_new_output_directory(output_dir)
    try:
        _atomic_rename_directory_noreplace(staging_dir, output_dir)
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise _output_dir_exists_error(output_dir) from None
        raise
    _fsync_directory(output_dir.parent)


def _atomic_rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename a directory only when the destination is absent."""
    if sys.platform == "darwin":
        function_name = "renameatx_np"
        at_fdcwd = _DARWIN_AT_FDCWD
        flag = _DARWIN_RENAME_EXCL
    elif sys.platform.startswith("linux"):
        function_name = "renameat2"
        at_fdcwd = _LINUX_AT_FDCWD
        flag = _LINUX_RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unsupported on this platform")

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename_noreplace = getattr(libc, function_name)
    except AttributeError as exc:
        raise OSError(
            errno.ENOTSUP,
            f"atomic no-replace directory rename primitive {function_name} is unavailable",
        ) from exc
    rename_noreplace.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename_noreplace.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = rename_noreplace(
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(destination),
        flag,
    )
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number), os.fspath(destination))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _assert_safe_paths(legacy_input: Path, output_dir: Path) -> None:
    if _paths_alias(legacy_input, output_dir):
        raise ValueError("legacy-input must not alias output-dir")
    for name in OUTPUT_FILENAMES:
        if _paths_alias(legacy_input, output_dir / name):
            raise ValueError("legacy-input cannot alias a generated output")


def _paths_alias(first: Path, second: Path) -> bool:
    if os.path.lexists(first) and os.path.lexists(second):
        try:
            if os.path.samefile(first, second):
                return True
        except OSError:
            pass
    return str(first.resolve(strict=False)).casefold() == str(second.resolve(strict=False)).casefold()


def _assert_new_output_directory(output_dir: Path) -> None:
    if os.path.lexists(output_dir):
        raise _output_dir_exists_error(output_dir)


def _output_dir_exists_error(output_dir: Path) -> FileExistsError:
    return FileExistsError(
        errno.EEXIST,
        f"output-dir already exists; choose a new versioned output directory: {output_dir}",
        output_dir,
    )


if __name__ == "__main__":
    raise SystemExit(cli())
