#!/usr/bin/env python3
"""Audit and freeze trend-research inputs without computing a signal."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd

from zenquant.data.spot_daily import audit_spot_daily, normalize_spot_daily_csvs
from zenquant.data.trend_market_tail import (
    FUNDING_COLUMNS,
    FUNDING_SOURCE_ID,
    PERP_COLUMNS,
    PERP_SOURCE_ID,
    MarketTailError,
    MarketTailInputs,
    replay_market_tail_provenance,
)
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
_HYPOTHESIS_LAYERS: Final = {
    "time_space_shadow_hypothesis_v1.json": "time_space_shadow",
    "trend_participation_hypothesis_v1.json": "trend_participation",
}
_OUTPUT_FILES: Final = {
    "time_space_shadow": "time_space_shadow_execution_v1.json",
    "trend_participation": "trend_participation_execution_v1.json",
}
_FORBIDDEN_EXECUTION_KEYS: Final = frozenset({"direction", "pnl", "signal"})
_CONSTRUCTION_PATHS: Final = (
    "scripts/freeze_trend_research_inputs.py",
    "scripts/prepare_trend_market_tail.py",
    "zenquant/data/spot_daily.py",
    "zenquant/data/trend_market_tail.py",
    "zenquant/domain/accounting.py",
    "zenquant/execution/router.py",
    "zenquant/research/accounting_kernel_identity.py",
    "zenquant/research/registration.py",
    "zenquant/risk/guard.py",
)


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
    perp_source_path: Path
    funding_source_path: Path
    market_tail_provenance_path: Path
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
        object.__setattr__(self, "perp_source_path", Path(self.perp_source_path))
        object.__setattr__(self, "funding_source_path", Path(self.funding_source_path))
        object.__setattr__(self, "market_tail_provenance_path", Path(self.market_tail_provenance_path))
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
        paths.extend((self.perp_source_path, self.funding_source_path))
        paths.append(self.market_tail_provenance_path)
        paths.extend(self.repository_root / relative for relative in _CONSTRUCTION_PATHS)
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
            value_columns=("open", "high", "low", "close", "volume"),
            value_contract="PERPETUAL_OHLCV",
        ),
        "BINANCE_PERPETUAL_BTCUSDT_FUNDING": _audit_regular_parquet(
            inputs.funding_path,
            source_id="BINANCE_PERPETUAL_BTCUSDT_FUNDING",
            timestamp_column="funding_time_ns",
            interval_ns=EIGHT_HOURS_NS,
            start_ns=inputs.reused_start_ns,
            end_ns=inputs.reused_end_ns,
            cutoff_column="funding_time_ns",
            value_columns=("funding_rate",),
            value_contract="FUNDING_RATE",
        ),
    }
    blockers = sorted(source_id for source_id, audit in audits.items() if audit["status"] != "READY")
    if blockers:
        raise InputFreezeError(f"REQUIRED sources not ready: {blockers}")
    if before != _snapshot_inputs(inputs.all_input_paths()):
        raise InputFreezeError("input mutated during freeze")

    market_tail_provenance = _validate_market_tail_provenance(inputs)
    for source_id in ("BINANCE_PERPETUAL_BTCUSDT_5M", "BINANCE_PERPETUAL_BTCUSDT_FUNDING"):
        audits[source_id]["market_tail_provenance"] = market_tail_provenance

    sources = {key: audits[key] for key in sorted(audits)}
    construction_identity = _construction_identity(inputs.repository_root)
    try:
        accounting_kernel = build_accounting_kernel_identity(repository_root=inputs.repository_root)
    except ValueError as exc:
        raise InputFreezeError(f"accounting kernel identity is unavailable: {exc}") from exc
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
        "accounting_kernel": accounting_kernel,
        "construction_identity": construction_identity,
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
    if market_tail_provenance != _validate_market_tail_provenance(inputs):
        raise InputFreezeError("market-tail provenance mutated during freeze")
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
    if manifest.layer != _HYPOTHESIS_LAYERS[path.name]:
        raise InputFreezeError("hypothesis filename does not match its frozen layer")
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
        warmup_start_ns = inputs.reused_start_ns - 85 * 86_400_000_000_000
        complete_audit = audit_spot_daily(inputs.spot_1d_path, warmup_start_ns, inputs.reused_end_ns)
        warmup_audit = audit_spot_daily(inputs.spot_1d_path, warmup_start_ns, inputs.reused_start_ns)
        diagnostic_audit = audit_spot_daily(
            inputs.spot_1d_path,
            inputs.reused_start_ns,
            inputs.reused_end_ns,
        )
        spot_frame = pd.read_parquet(
            inputs.spot_1d_path,
            columns=["open_time_ns", "close_time_ns", "close"],
        )
        if not pd.api.types.is_integer_dtype(spot_frame["open_time_ns"].dtype):
            return _not_ready(source_id, "spot open_time_ns must use an integer nanosecond dtype")
        spot_times = spot_frame["open_time_ns"]
        pre_start_count = int((spot_times < warmup_start_ns).sum())
        post_end_count = int((spot_times >= inputs.reused_end_ns).sum())
        required = spot_frame[(spot_times >= warmup_start_ns) & (spot_times < inputs.reused_end_ns)]
        closes = pd.to_numeric(required["close"], errors="coerce")
        valid_closes = bool(closes.notna().all()) and all(
            math.isfinite(float(value)) and float(value) > 0 for value in closes
        )
        warmup_count = int(((spot_times >= warmup_start_ns) & (spot_times < inputs.reused_start_ns)).sum())
        diagnostic_count = int(((spot_times >= inputs.reused_start_ns) & (spot_times < inputs.reused_end_ns)).sum())
        diagnostic_rows = spot_frame[(spot_times >= inputs.reused_start_ns) & (spot_times < inputs.reused_end_ns)]
        with tempfile.TemporaryDirectory(prefix="trend-spot-lineage-") as temporary_directory:
            reconstructed_path = Path(temporary_directory) / "data.parquet"
            reconstructed = normalize_spot_daily_csvs(
                inputs.spot_raw_paths,
                reconstructed_path,
                start_open_ns=warmup_start_ns,
                end_open_ns=inputs.reused_end_ns,
            )
            reconstructed_frame = pd.read_parquet(reconstructed_path)
            supplied_frame = pd.read_parquet(inputs.spot_1d_path)
            content_matches = reconstructed_frame.equals(supplied_frame)
            identity_matches = reconstructed.normalized_sha256 == _sha256_file(inputs.spot_1d_path)
        normalizer_path = inputs.repository_root / "zenquant/data/spot_daily.py"
        ready = (
            complete_audit.status == "READY"
            and warmup_audit.status == "READY"
            and diagnostic_audit.status == "READY"
            and complete_audit.last_close_ns == inputs.reused_end_ns - 1
            and warmup_count == 85
            and valid_closes
            and pre_start_count == 0
            and post_end_count == 0
            and content_matches
            and identity_matches
        )
        return {
            "requirement": "REQUIRED",
            "fallback": "NONE",
            "status": "READY" if ready else "INSUFFICIENT_DATA_SPOT_CONTRACT",
            "raw_sha256": raw_sha256,
            "normalizer_identity": {
                "callable": "zenquant.data.spot_daily.normalize_spot_daily_csvs",
                "sha256": _sha256_file(normalizer_path),
            },
            "normalized_path": str(inputs.spot_1d_path),
            "normalized_sha256": complete_audit.sha256,
            "last_cutoff_ns": complete_audit.last_close_ns,
            "lineage": {
                "status": "VERIFIED" if content_matches and identity_matches else "MISMATCH",
                "reconstructed_sha256": reconstructed.normalized_sha256,
            },
            "warmup": {
                "start_ns": warmup_start_ns,
                "end_ns": inputs.reused_start_ns,
                "required_closes": 85,
                "observed_closes": warmup_count,
                "status": ("READY" if warmup_audit.status == "READY" and warmup_count == 85 else "INSUFFICIENT_DATA"),
            },
            "coverage": {
                "start_ns": inputs.reused_start_ns,
                "end_ns": inputs.reused_end_ns,
                "first_open_ns": int(diagnostic_rows["open_time_ns"].min()),
                "last_close_ns": int(diagnostic_rows["close_time_ns"].max()),
                "row_count": diagnostic_count,
                "missing_count": len(diagnostic_audit.missing_open_ns),
                "duplicate_count": len(diagnostic_audit.duplicate_open_ns),
                "pre_start_count": pre_start_count,
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
    value_columns: tuple[str, ...],
    value_contract: str,
) -> dict[str, Any]:
    if path is None:
        return _not_ready(source_id, "missing path")
    try:
        digest = _sha256_file(path)
        frame = pd.read_parquet(
            path,
            columns=sorted({timestamp_column, cutoff_column, *value_columns}),
        )
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
        pre_start_count = sum(value < start_ns for value in observed_all)
        expected_row_count = (end_ns - start_ns) // interval_ns
        invalid_cutoff_count = 0
        if cutoff_column != timestamp_column:
            valid_deltas = {interval_ns - 1, interval_ns - 1_000_000}
            invalid_cutoff_count = sum(
                int(cutoff) - int(timestamp) not in valid_deltas
                for timestamp, cutoff in zip(timestamps, cutoffs, strict=True)
            )
        values_ready, invalid_value_count = _validate_source_values(frame, value_contract)
        ready = (
            missing_count == 0
            and duplicate_count == 0
            and off_grid_count == 0
            and post_end_count == 0
            and pre_start_count == 0
            and len(frame) == expected_row_count
            and invalid_cutoff_count == 0
            and values_ready
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
                "expected_row_count": expected_row_count,
                "window_row_count": len(observed_window),
                "missing_count": missing_count,
                "duplicate_count": duplicate_count,
                "off_grid_count": off_grid_count,
                "post_end_count": post_end_count,
                "pre_start_count": pre_start_count,
                "invalid_cutoff_count": invalid_cutoff_count,
                "invalid_value_count": invalid_value_count,
            },
        }
    except (OSError, ValueError, KeyError) as exc:
        return _not_ready(source_id, str(exc))


def _validate_market_tail_provenance(inputs: FreezeInputs) -> dict[str, Any]:
    path = inputs.market_tail_provenance_path
    try:
        digest = _sha256_file(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("root must be an object")
        if payload.get("schema_version") != "trend-market-tail-provenance.v1":
            raise ValueError("schema_version mismatch")
        if payload.get("symbol") != "BTCUSDT":
            raise ValueError("symbol mismatch")
        if payload.get("window") != [inputs.reused_start_ns, inputs.reused_end_ns]:
            raise ValueError("window mismatch")
        output_paths = {PERP_SOURCE_ID: inputs.perp_5m_path, FUNDING_SOURCE_ID: inputs.funding_path}
        source_paths = {PERP_SOURCE_ID: inputs.perp_source_path, FUNDING_SOURCE_ID: inputs.funding_source_path}
        outputs = payload.get("outputs")
        sources = payload.get("sources")
        if not isinstance(outputs, dict) or set(outputs) != set(output_paths):
            raise ValueError("output source IDs mismatch")
        if not isinstance(sources, dict) or set(sources) != set(source_paths):
            raise ValueError("canonical source IDs mismatch")
        expected_counts = {
            PERP_SOURCE_ID: (inputs.reused_end_ns - inputs.reused_start_ns) // FIVE_MINUTES_NS,
            FUNDING_SOURCE_ID: (inputs.reused_end_ns - inputs.reused_start_ns) // EIGHT_HOURS_NS,
        }
        expected_columns = {PERP_SOURCE_ID: list(PERP_COLUMNS), FUNDING_SOURCE_ID: list(FUNDING_COLUMNS)}
        for source_id, supplied_path in output_paths.items():
            if supplied_path is None:
                raise ValueError(f"missing output for {source_id}")
            output = outputs[source_id]
            if not isinstance(output, dict):
                raise ValueError(f"invalid output envelope for {source_id}")
            if (
                output.get("path") != str(supplied_path)
                or Path(str(output.get("path"))).resolve() != supplied_path.resolve()
            ):
                raise ValueError(f"output path mismatch for {source_id}")
            if output.get("sha256") != _sha256_file(supplied_path):
                raise ValueError(f"output digest mismatch for {source_id}")
            actual_row_count = len(pd.read_parquet(supplied_path))
            if (
                output.get("row_count") != actual_row_count
                or output.get("expected_row_count") != expected_counts[source_id]
                or actual_row_count != expected_counts[source_id]
                or output.get("columns") != expected_columns[source_id]
            ):
                raise ValueError(f"invalid output row count for {source_id}")
        for source_id, source_path in source_paths.items():
            source = sources[source_id]
            if not isinstance(source, dict):
                raise ValueError(f"invalid canonical source envelope for {source_id}")
            if source.get("path") != str(source_path) or source.get("resolved_path") != str(source_path.resolve()):
                raise ValueError(f"canonical source path mismatch for {source_id}")
            if source.get("sha256") != _sha256_file(source_path):
                raise ValueError(f"canonical source digest mismatch for {source_id}")
        normalizer = payload.get("normalizer")
        repository = payload.get("repository")
        if not isinstance(normalizer, dict) or not _is_sha256(normalizer.get("sha256")):
            raise ValueError("normalizer identity mismatch")
        expected_normalizer_sha = _sha256_file(inputs.repository_root / "zenquant/data/trend_market_tail.py")
        if (
            normalizer.get("callable") != "zenquant.data.trend_market_tail.prepare_market_tail"
            or normalizer.get("sha256") != expected_normalizer_sha
        ):
            raise ValueError("normalizer identity mismatch")
        current_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=inputs.repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if (
            not isinstance(repository, dict)
            or not _is_git_sha(repository.get("head"))
            or repository.get("head") != current_head
        ):
            raise ValueError("repository identity mismatch")
        replay_inputs = MarketTailInputs(
            perp_source_path=inputs.perp_source_path,
            funding_source_path=inputs.funding_source_path,
            output_root=inputs.perp_5m_path.parent.parent,
            start_ns=inputs.reused_start_ns,
            end_ns=inputs.reused_end_ns,
            repository_root=inputs.repository_root,
        )
        replayed = replay_market_tail_provenance(payload, replay_inputs)
        pd.testing.assert_frame_equal(
            replayed[PERP_SOURCE_ID], pd.read_parquet(inputs.perp_5m_path), check_dtype=True, check_like=False
        )
        pd.testing.assert_frame_equal(
            replayed[FUNDING_SOURCE_ID], pd.read_parquet(inputs.funding_path), check_dtype=True, check_like=False
        )
    except (AssertionError, MarketTailError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InputFreezeError(f"market-tail provenance is invalid: {exc}") from exc
    return {
        "path": str(path),
        "sha256": digest,
        "schema_version": payload["schema_version"],
        "normalizer": payload["normalizer"],
        "repository": payload["repository"],
    }


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _is_git_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_source_values(frame: pd.DataFrame, contract: str) -> tuple[bool, int]:
    if contract == "FUNDING_RATE":
        rates = pd.to_numeric(frame["funding_rate"], errors="coerce")
        invalid = sum(not math.isfinite(float(value)) for value in rates)
        return invalid == 0, invalid
    if contract != "PERPETUAL_OHLCV":
        raise InputFreezeError(f"unknown source value contract: {contract}")
    numeric = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    invalid = 0
    for row in numeric.itertuples(index=False):
        open_price, high, low, close, volume = (float(value) for value in row)
        if (
            not all(math.isfinite(value) for value in (open_price, high, low, close, volume))
            or min(open_price, high, low, close) <= 0
            or volume < 0
            or high < max(open_price, close)
            or low > min(open_price, close)
            or high < low
        ):
            invalid += 1
    return invalid == 0, invalid


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


def _construction_identity(repository_root: Path) -> dict[str, Any]:
    files = [
        {
            "path": relative,
            "sha256": _sha256_file(repository_root / relative),
        }
        for relative in _CONSTRUCTION_PATHS
    ]
    identity = {"files": files}
    return {**identity, "sha256": _prefixed_digest(identity)}


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
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=repository_root,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InputFreezeError("unable to bind dirty/untracked workspace state") from exc
    entries = _parse_workspace_entries(repository_root, status)
    identity = {"head": head, "entries": entries}
    return {**identity, "sha256": _prefixed_digest(identity)}


def _parse_workspace_entries(repository_root: Path, payload: bytes) -> list[dict[str, str | None]]:
    tokens = payload.split(b"\0")
    entries: list[dict[str, str | None]] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        record = tokens[index]
        index += 1
        if len(record) < 4 or record[2:3] != b" ":
            raise InputFreezeError("unable to parse dirty/untracked workspace state")
        status = record[:2].decode("ascii")
        path = record[3:].decode("utf-8", errors="surrogateescape")
        entry: dict[str, str | None] = {
            "status": status,
            "path": path,
            "content_sha256": _workspace_content_sha256(repository_root / path),
        }
        if "R" in status or "C" in status:
            if index >= len(tokens) or not tokens[index]:
                raise InputFreezeError("unable to parse renamed workspace entry")
            entry["original_path"] = tokens[index].decode("utf-8", errors="surrogateescape")
            index += 1
        entries.append(entry)
    return sorted(entries, key=lambda item: (str(item["path"]), str(item["status"])))


def _workspace_content_sha256(path: Path) -> str | None:
    if path.is_symlink():
        return hashlib.sha256(os.readlink(path).encode("utf-8", errors="surrogateescape")).hexdigest()
    if path.is_file():
        return _sha256_file(path)
    if not path.exists():
        return None
    raise InputFreezeError(f"workspace entry is not a file: {path}")


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


def _validate_cli_canonical_sources(
    repository_root: Path,
    perp_source: Path,
    funding_source: Path,
) -> None:
    """Reject substituted canonical sources before the freezer reads any bytes."""

    expected_sources = {
        PERP_SOURCE_ID: repository_root / "data/raw/binance/klines/BTCUSDT/5m/data.parquet",
        FUNDING_SOURCE_ID: repository_root / "data/raw/binance/funding/BTCUSDT/data.parquet",
    }
    supplied_sources = {
        PERP_SOURCE_ID: Path(perp_source),
        FUNDING_SOURCE_ID: Path(funding_source),
    }
    for source_id, expected_path in expected_sources.items():
        supplied_path = supplied_sources[source_id]
        if supplied_path.is_symlink():
            raise InputFreezeError(f"canonical source leaf may not be a symlink: {source_id}")
        if supplied_path.resolve() != expected_path.resolve():
            raise InputFreezeError(f"canonical source path mismatch: {source_id}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spot-1d", required=True, type=Path)
    parser.add_argument("--spot-raw", required=True, action="append", type=Path)
    parser.add_argument("--perp-5m", required=True, type=Path)
    parser.add_argument("--perp-source", required=True, type=Path)
    parser.add_argument("--funding", required=True, type=Path)
    parser.add_argument("--funding-source", required=True, type=Path)
    parser.add_argument("--market-tail-provenance", required=True, type=Path)
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
    try:
        _validate_cli_canonical_sources(repository_root, args.perp_source, args.funding_source)
    except InputFreezeError as exc:
        parser.error(str(exc))
    common = {
        "spot_1d_path": args.spot_1d,
        "spot_raw_paths": tuple(args.spot_raw),
        "perp_5m_path": args.perp_5m,
        "funding_path": args.funding,
        "perp_source_path": args.perp_source,
        "funding_source_path": args.funding_source,
        "market_tail_provenance_path": args.market_tail_provenance,
        "reused_start_ns": REUSED_START_NS,
        "reused_end_ns": REUSED_END_NS,
        "repository_root": repository_root,
    }
    try:
        freeze_inputs = tuple(
            FreezeInputs(hypothesis_path=path, **common) for path in (args.time_space_hypothesis, args.trend_hypothesis)
        )
        all_input_paths = tuple(dict.fromkeys(path for inputs in freeze_inputs for path in inputs.all_input_paths()))
        before = _snapshot_inputs(all_input_paths)
        workspace_before = _workspace_state(repository_root)
        manifests = tuple(build_execution_manifest(inputs) for inputs in freeze_inputs)
        after = _snapshot_inputs(all_input_paths)
        if before != after:
            raise InputFreezeError("input mutated during multi-manifest freeze")
        if workspace_before != _workspace_state(repository_root):
            raise InputFreezeError("workspace mutated during multi-manifest freeze")
        if manifests[0]["source_set_sha256"] != manifests[1]["source_set_sha256"]:
            raise InputFreezeError("layers observed different source identities")
        for identity_key in ("accounting_kernel", "construction_identity", "workspace_state"):
            if manifests[0][identity_key] != manifests[1][identity_key]:
                raise InputFreezeError(f"layers observed different {identity_key}")
        payloads = {_OUTPUT_FILES[str(payload["layer"])]: payload for payload in manifests}
        if set(payloads) != set(_OUTPUT_FILES.values()):
            raise InputFreezeError("the two exact research layers are required")
        _publish_atomic_directory(args.output_dir, payloads)
    except InputFreezeError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
