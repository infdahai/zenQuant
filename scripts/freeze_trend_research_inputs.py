#!/usr/bin/env python3
"""Audit and freeze trend-research inputs without computing a signal."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd

from zenquant.data.spot_daily import audit_spot_daily
from zenquant.research.accounting_kernel_identity import build_accounting_kernel_identity
from zenquant.research.registration import (
    REQUIRED_SOURCE_IDS,
    REUSED_END_NS,
    REUSED_START_NS,
    HypothesisManifest,
    canonical_sha256,
    load_hypothesis_manifest,
)

FIVE_MINUTES_NS: Final = 300_000_000_000
EIGHT_HOURS_NS: Final = 28_800_000_000_000
FIXED_REUSED_START: Final = "2026-01-01T00:00:00Z"
FIXED_REUSED_END: Final = "2026-08-24T00:00:00Z"
_HYPOTHESIS_FILES: Final = frozenset(
    {
        "time_space_shadow_hypothesis_v1.json",
        "trend_participation_hypothesis_v1.json",
    }
)
_OUTPUT_FILES: Final = {
    "time_space_shadow": "time_space_shadow_execution_v1.json",
    "trend_participation": "trend_participation_execution_v1.json",
}
_FORBIDDEN_EXECUTION_KEYS: Final = frozenset({"direction", "pnl", "signal"})


class InputFreezeError(ValueError):
    """Raised when a required research input cannot be frozen safely."""


@dataclass(frozen=True, slots=True)
class FreezeInputs:
    """Explicit paths and immutable cutoffs required for one execution manifest."""

    hypothesis_path: Path
    spot_1d_path: Path
    spot_raw_paths: tuple[Path, ...]
    perp_5m_path: Path
    funding_path: Path | None
    reused_start_ns: int
    reused_end_ns: int
    repository_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "hypothesis_path", Path(self.hypothesis_path))
        object.__setattr__(self, "spot_1d_path", Path(self.spot_1d_path))
        object.__setattr__(self, "spot_raw_paths", tuple(Path(path) for path in self.spot_raw_paths))
        object.__setattr__(self, "perp_5m_path", Path(self.perp_5m_path))
        if self.funding_path is not None:
            object.__setattr__(self, "funding_path", Path(self.funding_path))
        object.__setattr__(self, "repository_root", Path(self.repository_root).resolve())

    def all_input_paths(self) -> tuple[Path, ...]:
        """Return every file whose bytes contribute to the frozen envelope."""

        paths = [
            self.hypothesis_path,
            self.spot_1d_path,
            *self.spot_raw_paths,
            self.perp_5m_path,
        ]
        if self.funding_path is not None:
            paths.append(self.funding_path)
        paths.extend(
            self.repository_root / relative
            for relative in (
                "zenquant/data/spot_daily.py",
                "zenquant/domain/accounting.py",
                "zenquant/risk/guard.py",
                "zenquant/execution/router.py",
            )
        )
        return tuple(paths)


def build_execution_manifest(inputs: FreezeInputs) -> dict[str, Any]:
    """Audit all REQUIRED inputs, bind identities, and return canonical data only."""

    _validate_frozen_window(inputs)
    before = _snapshot_inputs(inputs.all_input_paths())
    workspace_before = _workspace_state(inputs.repository_root)
    manifest = _load_checked_in_hypothesis(inputs)

    audits = {
        "BINANCE_SPOT_BTCUSDT_1D": _audit_spot(inputs),
        "BINANCE_PERPETUAL_BTCUSDT_5M": _audit_regular_parquet(
            inputs.perp_5m_path,
            source_id="BINANCE_PERPETUAL_BTCUSDT_5M",
            timestamp_column="open_time_ns",
            interval_ns=FIVE_MINUTES_NS,
            start_ns=inputs.reused_start_ns,
            end_ns=inputs.reused_end_ns,
            cutoff_column="close_time_ns",
        ),
        "BINANCE_PERPETUAL_BTCUSDT_FUNDING": _audit_regular_parquet(
            inputs.funding_path,
            source_id="BINANCE_PERPETUAL_BTCUSDT_FUNDING",
            timestamp_column="funding_time_ns",
            interval_ns=EIGHT_HOURS_NS,
            start_ns=inputs.reused_start_ns,
            end_ns=inputs.reused_end_ns,
            cutoff_column="funding_time_ns",
        ),
    }
    blockers = sorted(source_id for source_id, audit in audits.items() if audit["status"] != "READY")
    if blockers:
        raise InputFreezeError(f"REQUIRED sources not ready: {blockers}")

    sources = {key: audits[key] for key in sorted(audits)}
    payload: dict[str, Any] = {
        "schema_version": "trend-research-execution-manifest.v1",
        "layer": manifest.layer,
        "hypothesis_sha256": manifest.sha256,
        "formula_sha256": _prefixed_digest(_formula_payload(manifest)),
        "config_sha256": f"sha256:{manifest.sha256}",
        "reused_window": [inputs.reused_start_ns, inputs.reused_end_ns],
        "fixed_end_cutoff_ns": inputs.reused_end_ns,
        "sources": sources,
        "source_set_sha256": _prefixed_digest(sources),
        "accounting_kernel": build_accounting_kernel_identity(repository_root=inputs.repository_root),
        "forward_checkpoint_rule": "later_trusted_receipt_plus_24h_ceil_confirmed_4h",
        "workspace_state": workspace_before,
    }
    forbidden = _FORBIDDEN_EXECUTION_KEYS.intersection(_all_keys(payload))
    if forbidden:
        raise InputFreezeError(f"execution manifest contains forbidden result fields: {sorted(forbidden)}")

    after = _snapshot_inputs(inputs.all_input_paths())
    if before != after:
        changed = sorted(str(path) for path in before.keys() | after.keys() if before.get(path) != after.get(path))
        raise InputFreezeError(f"input mutated during freeze: {changed}")
    if workspace_before != _workspace_state(inputs.repository_root):
        raise InputFreezeError("workspace state mutated during freeze")
    return payload


def _validate_frozen_window(inputs: FreezeInputs) -> None:
    if (inputs.reused_start_ns, inputs.reused_end_ns) != (REUSED_START_NS, REUSED_END_NS):
        raise InputFreezeError("reused window must match the exact frozen hypothesis window")


def _load_checked_in_hypothesis(inputs: FreezeInputs) -> HypothesisManifest:
    expected_directory = (inputs.repository_root / "config/research").resolve()
    path = inputs.hypothesis_path.resolve()
    if path.parent != expected_directory or path.name not in _HYPOTHESIS_FILES:
        raise InputFreezeError("hypothesis must be one of the checked-in frozen manifests")
    try:
        manifest = load_hypothesis_manifest(path)
    except (OSError, ValueError) as exc:
        raise InputFreezeError(f"hypothesis identity is invalid: {exc}") from exc
    if (manifest.reused_start_ns, manifest.reused_end_ns) != (
        inputs.reused_start_ns,
        inputs.reused_end_ns,
    ):
        raise InputFreezeError("hypothesis reused window differs from freeze inputs")
    requirements = tuple(
        (str(item["id"]), str(item["requirement"]), str(item["fallback"])) for item in manifest.source_requirements
    )
    expected = tuple((source_id, "REQUIRED", "NONE") for source_id in REQUIRED_SOURCE_IDS)
    if requirements != expected:
        raise InputFreezeError("hypothesis source contract is not the exact REQUIRED/NONE set")
    return manifest


def _audit_spot(inputs: FreezeInputs) -> dict[str, Any]:
    source_id = "BINANCE_SPOT_BTCUSDT_1D"
    if not inputs.spot_raw_paths:
        return _not_ready(source_id, "missing raw spot archives")
    try:
        raw_sha256 = [
            {"path": str(path), "sha256": _sha256_file(path)}
            for path in sorted(inputs.spot_raw_paths, key=lambda item: str(item))
        ]
        if any(path.stat().st_size <= 0 for path in inputs.spot_raw_paths):
            return _not_ready(source_id, "empty raw spot archive")
        audit = audit_spot_daily(inputs.spot_1d_path, inputs.reused_start_ns, inputs.reused_end_ns)
        spot_times = pd.read_parquet(inputs.spot_1d_path, columns=["open_time_ns"])["open_time_ns"]
        post_end_count = int((spot_times >= inputs.reused_end_ns).sum())
        normalizer_path = inputs.repository_root / "zenquant/data/spot_daily.py"
        ready = audit.status == "READY" and audit.last_close_ns == inputs.reused_end_ns - 1 and post_end_count == 0
        return {
            "requirement": "REQUIRED",
            "fallback": "NONE",
            "status": (
                "READY" if ready else audit.status if audit.status != "READY" else "INSUFFICIENT_DATA_END_DRIFT"
            ),
            "raw_sha256": raw_sha256,
            "normalizer_identity": {
                "callable": "zenquant.data.spot_daily.normalize_spot_daily_csvs",
                "sha256": _sha256_file(normalizer_path),
            },
            "normalized_path": str(inputs.spot_1d_path),
            "normalized_sha256": audit.sha256,
            "last_cutoff_ns": audit.last_close_ns,
            "coverage": {
                "first_open_ns": audit.first_open_ns,
                "last_close_ns": audit.last_close_ns,
                "row_count": audit.row_count,
                "missing_count": len(audit.missing_open_ns),
                "duplicate_count": len(audit.duplicate_open_ns),
                "post_end_count": post_end_count,
            },
        }
    except (OSError, ValueError) as exc:
        return _not_ready(source_id, str(exc))


def _audit_regular_parquet(
    path: Path | None,
    *,
    source_id: str,
    timestamp_column: str,
    interval_ns: int,
    start_ns: int,
    end_ns: int,
    cutoff_column: str,
) -> dict[str, Any]:
    if path is None:
        return _not_ready(source_id, "missing path")
    try:
        digest = _sha256_file(path)
        frame = pd.read_parquet(path, columns=sorted({timestamp_column, cutoff_column}))
        if frame.empty:
            return _not_ready(source_id, "empty parquet")
        if not pd.api.types.is_integer_dtype(frame[timestamp_column].dtype) or not pd.api.types.is_integer_dtype(
            frame[cutoff_column].dtype
        ):
            return _not_ready(source_id, "timestamps must use an integer nanosecond dtype")
        timestamps = pd.to_numeric(frame[timestamp_column], errors="coerce")
        cutoffs = pd.to_numeric(frame[cutoff_column], errors="coerce")
        if timestamps.isna().any() or cutoffs.isna().any():
            return _not_ready(source_id, "non-numeric timestamp")
        observed_all = tuple(int(value) for value in timestamps)
        observed_window = tuple(value for value in observed_all if start_ns <= value < end_ns)
        counts = pd.Series(observed_all, dtype="int64").value_counts()
        duplicate_count = int((counts - 1).clip(lower=0).sum())
        expected = set(range(start_ns, end_ns, interval_ns))
        missing_count = len(expected.difference(observed_window))
        off_grid_count = sum((value - start_ns) % interval_ns != 0 for value in observed_all)
        post_end_count = sum(value >= end_ns for value in observed_all)
        invalid_cutoff_count = 0
        if cutoff_column != timestamp_column:
            valid_deltas = {interval_ns - 1, interval_ns - 1_000_000}
            invalid_cutoff_count = sum(
                int(cutoff) - int(timestamp) not in valid_deltas
                for timestamp, cutoff in zip(timestamps, cutoffs, strict=True)
            )
        ready = (
            missing_count == 0
            and duplicate_count == 0
            and off_grid_count == 0
            and post_end_count == 0
            and invalid_cutoff_count == 0
        )
        return {
            "requirement": "REQUIRED",
            "fallback": "NONE",
            "status": "READY" if ready else "INSUFFICIENT_DATA_GRID_GAPS",
            "path": str(path),
            "sha256": digest,
            "last_cutoff_ns": int(cutoffs.max()),
            "coverage": {
                "first_event_ns": min(observed_all),
                "last_event_ns": max(observed_all),
                "row_count": len(frame),
                "window_row_count": len(observed_window),
                "missing_count": missing_count,
                "duplicate_count": duplicate_count,
                "off_grid_count": off_grid_count,
                "post_end_count": post_end_count,
                "invalid_cutoff_count": invalid_cutoff_count,
            },
        }
    except (OSError, ValueError, KeyError) as exc:
        return _not_ready(source_id, str(exc))


def _not_ready(source_id: str, reason: str) -> dict[str, Any]:
    return {
        "requirement": "REQUIRED",
        "fallback": "NONE",
        "status": "INSUFFICIENT_DATA",
        "source_id": source_id,
        "reason": reason,
    }


def _formula_payload(manifest: HypothesisManifest) -> Mapping[str, Any]:
    fields = ("formula", "thresholds", "costs", "robustness", "gates", "policy", "authority")
    return {field: _plain_json(manifest.payload[field]) for field in fields if field in manifest.payload}


def _plain_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain_json(item) for item in value]
    return value


def _prefixed_digest(payload: Mapping[str, Any]) -> str:
    return f"sha256:{canonical_sha256(payload)}"


def _snapshot_inputs(paths: Sequence[Path]) -> dict[Path, str | None]:
    identities: dict[Path, str | None] = {}
    for path in paths:
        resolved = Path(path).resolve()
        try:
            identities[resolved] = _sha256_file(resolved)
        except OSError:
            identities[resolved] = None
    return identities


def _workspace_state(repository_root: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InputFreezeError("unable to bind dirty/untracked workspace state") from exc
    entries = sorted(line for line in completed.stdout.splitlines() if line)
    identity = {"entries": entries}
    return {**identity, "sha256": _prefixed_digest(identity)}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _all_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        keys = {str(key) for key in value}
        for item in value.values():
            keys.update(_all_keys(item))
        return keys
    if isinstance(value, list | tuple):
        keys: set[str] = set()
        for item in value:
            keys.update(_all_keys(item))
        return keys
    return set()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _publish_atomic_directory(output_dir: Path, payloads: Mapping[str, Mapping[str, Any]]) -> None:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise InputFreezeError(f"immutable output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        for name, payload in sorted(payloads.items()):
            (staging / name).write_text(_canonical_json(payload), encoding="utf-8")
        os.replace(staging, output_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spot-1d", required=True, type=Path)
    parser.add_argument("--spot-raw", required=True, action="append", type=Path)
    parser.add_argument("--perp-5m", required=True, type=Path)
    parser.add_argument("--funding", required=True, type=Path)
    parser.add_argument("--time-space-hypothesis", required=True, type=Path)
    parser.add_argument("--trend-hypothesis", required=True, type=Path)
    parser.add_argument("--reused-start", required=True)
    parser.add_argument("--reused-end", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.reused_start != FIXED_REUSED_START:
        parser.error(f"fixed reused start must be {FIXED_REUSED_START}")
    if args.reused_end != FIXED_REUSED_END:
        parser.error(f"fixed reused end must be {FIXED_REUSED_END}")

    repository_root = Path(__file__).resolve().parents[1]
    common = {
        "spot_1d_path": args.spot_1d,
        "spot_raw_paths": tuple(args.spot_raw),
        "perp_5m_path": args.perp_5m,
        "funding_path": args.funding,
        "reused_start_ns": REUSED_START_NS,
        "reused_end_ns": REUSED_END_NS,
        "repository_root": repository_root,
    }
    try:
        before = _snapshot_inputs(
            (
                args.time_space_hypothesis,
                args.trend_hypothesis,
                args.spot_1d,
                *args.spot_raw,
                args.perp_5m,
                args.funding,
            )
        )
        manifests = tuple(
            build_execution_manifest(FreezeInputs(hypothesis_path=path, **common))
            for path in (args.time_space_hypothesis, args.trend_hypothesis)
        )
        after = _snapshot_inputs(before)
        if before != after:
            raise InputFreezeError("input mutated during multi-manifest freeze")
        if manifests[0]["source_set_sha256"] != manifests[1]["source_set_sha256"]:
            raise InputFreezeError("layers observed different source identities")
        payloads = {_OUTPUT_FILES[str(payload["layer"])]: payload for payload in manifests}
        if set(payloads) != set(_OUTPUT_FILES.values()):
            raise InputFreezeError("the two exact research layers are required")
        _publish_atomic_directory(args.output_dir, payloads)
    except InputFreezeError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
