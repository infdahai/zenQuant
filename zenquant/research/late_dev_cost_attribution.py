"""Validation and canonical envelope for late-development attribution inputs.

This module deliberately performs no replay or strategy computation.  It
freezes the identity and ordering of the existing architecture experiment so
later diagnostic layers can only consume a complete, point-in-time artifact.
"""

from __future__ import annotations

import json
import math
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
    """Validate a frozen report and return deterministic accounting diagnostics."""
    if not isinstance(report, Mapping):
        raise LateDevAttributionValidationError("raw report must be a JSON object")
    try:
        manifest = load_architecture_experiment_manifest()
        _validate_source_identity(source_sha256=source_sha256, source_size_bytes=source_size_bytes)
        runs = _validate_report_envelope(report, manifest=manifest)
        late_dev_runs = [run for run in runs if run["window"] == "late_dev_2025"]
        bridge_stages = (
            ArchitectureStage.A1_SIGN.value,
            ArchitectureStage.A1_SCORE.value,
            ArchitectureStage.A5_SHADOW.value,
        )
        seed_stage_bridges = [
            _build_seed_stage_bridge(run, stage_name) for run in late_dev_runs for stage_name in bridge_stages
        ]
        episode_attribution = []
        for run in late_dev_runs:
            summary = _summarize_episodes(_stage(run, ArchitectureStage.A5_SHADOW.value))
            summary["seed"] = int(run["seed"])
            summary["stage"] = ArchitectureStage.A5_SHADOW.value
            episode_attribution.append(summary)
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
        "seed_stage_bridges": seed_stage_bridges,
        "episode_attribution": episode_attribution,
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
        "candidate_source_sha256": manifest.candidate_source_sha256,
        "candidate_params_sha256": manifest.candidate_params_sha256,
        "profile_sha256": manifest.profile_sha256,
        "windows": expected_windows,
        "seeds": list(manifest.seeds),
        "cost_stress_bps": list(manifest.cost_stress_bps),
    }
    for key, expected in expected_manifest.items():
        if payload.get(key) != expected:
            raise LateDevAttributionValidationError(f"raw report manifest identity mismatch: {key}")

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
        for expected_stage, stage_payload in stages.items():
            if not isinstance(stage_payload, Mapping):
                raise LateDevAttributionValidationError("run stage payloads must be JSON objects")
            if stage_payload.get("stage") != expected_stage:
                raise LateDevAttributionValidationError("run stage payload declaration does not match its key")
    return runs


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LateDevAttributionValidationError(f"raw report {name} must be a JSON object")
    return value


def _build_seed_stage_bridge(run: Mapping[str, Any], stage_name: str) -> dict[str, Any]:
    """Reconcile a frozen seed-stage baseline against its extra-15bp result."""
    stage = _stage(run, stage_name)
    baseline = _finite(stage["cost_stress"]["0"]["net_pnl"], "baseline net PnL")
    stressed = _finite(stage["cost_stress"]["15"]["net_pnl"], "15bp net PnL")
    turnover = _finite(stage["turnover_notional"], "turnover notional")
    additional_cost = turnover * 15.0 / 10_000.0
    error = stressed - (baseline - additional_cost)
    if not math.isclose(error, 0.0, rel_tol=0.0, abs_tol=1e-6):
        raise LateDevAttributionValidationError("15bp bridge does not reconcile")
    return {
        "seed": int(run["seed"]),
        "stage": stage_name,
        "baseline_net_pnl": baseline,
        "turnover_notional": turnover,
        "additional_15bp_cost": additional_cost,
        "extra_15bp_net_pnl": stressed,
        "reconciliation_error": error,
    }


def _summarize_episodes(stage: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize serialized A5 episodes and explicit attribution rows only."""
    episodes = stage.get("position_episodes")
    if not isinstance(episodes, list) or not all(isinstance(episode, Mapping) for episode in episodes):
        raise LateDevAttributionValidationError("position episodes must be a list of objects")
    closed = [episode for episode in episodes if _closed(episode)]
    open_episodes = [episode for episode in episodes if not _closed(episode)]
    episode_ledger = _mapping(stage.get("episode_ledger"), "episode ledger")
    episode_ledger_status = episode_ledger.get("status")
    if episode_ledger_status not in {"OBSERVED", "NOT_OBSERVED"}:
        raise LateDevAttributionValidationError("episode ledger status must be OBSERVED or NOT_OBSERVED")
    ledger_closed_count = _nonnegative_int(
        episode_ledger.get("closed_episode_count"), "episode ledger closed episode count"
    )
    ledger_open_count = _nonnegative_int(episode_ledger.get("open_episode_count"), "episode ledger open episode count")
    if ledger_closed_count != len(closed):
        raise LateDevAttributionValidationError("episode ledger closed episode count mismatch")
    if ledger_open_count != len(open_episodes):
        raise LateDevAttributionValidationError("episode ledger open episode count mismatch")
    if episode_ledger_status == "NOT_OBSERVED" and (episodes or ledger_closed_count or ledger_open_count):
        raise LateDevAttributionValidationError("not observed episode ledger must be empty")
    closed_totals = _episode_totals(closed)
    by_side = {
        side: {"closed_episode_count": len(items), **_episode_totals(items)}
        for side, items in (
            ("LONG", [episode for episode in closed if _side(episode) == "LONG"]),
            ("SHORT", [episode for episode in closed if _side(episode) == "SHORT"]),
        )
        if items
    }
    rows = stage.get("attribution_rows")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise LateDevAttributionValidationError("attribution rows must be a list of objects")
    return {
        "episode_ledger_status": episode_ledger_status,
        "closed_episode_count": len(closed),
        "open_episode_count": len(open_episodes),
        "closed_net_pnl": closed_totals["net_pnl"],
        "open_unrealized_pnl": sum(
            _finite(episode["unrealized_pnl"], "open episode unrealized PnL") for episode in open_episodes
        ),
        "closed_totals": closed_totals,
        "by_side": by_side,
        "negative_tail_share": _negative_tail_share(closed),
        "regime_groups": _attribution_groups(rows, field="regime"),
        "playbook_groups": _playbook_groups(rows),
        "execution_actions": _execution_actions(rows),
    }


def _derive_execution_action(row: Mapping[str, Any]) -> str:
    """Classify a serialized filled-position transition without market inference."""
    before = _finite(row["filled_position_before"], "filled position before")
    after = _finite(row["filled_position_after"], "filled position after")
    if before == 0.0 and after != 0.0:
        return "entry"
    if after == 0.0:
        return "exit"
    if before * after < 0.0:
        return "flip"
    if abs(after) > abs(before):
        return "add"
    if abs(after) < abs(before):
        return "reduce"
    return "unchanged"


def _stage(run: Mapping[str, Any], stage_name: str) -> Mapping[str, Any]:
    stages = _mapping(run.get("stages"), "run stages")
    return _mapping(stages.get(stage_name), f"{stage_name} stage")


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise LateDevAttributionValidationError(f"{name} must be finite")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LateDevAttributionValidationError(f"{name} must be finite") from exc
    if not math.isfinite(numeric):
        raise LateDevAttributionValidationError(f"{name} must be finite")
    return numeric


def _closed(episode: Mapping[str, Any]) -> bool:
    closed = episode.get("closed")
    if not isinstance(closed, bool):
        raise LateDevAttributionValidationError("episode closed flag must be boolean")
    return closed


def _side(episode: Mapping[str, Any]) -> str:
    side = episode.get("side")
    if isinstance(side, bool) or side not in {-1, 1}:
        raise LateDevAttributionValidationError("episode side must be -1 or 1")
    return "LONG" if side == 1 else "SHORT"


def _episode_totals(episodes: list[Mapping[str, Any]]) -> dict[str, float | int]:
    fields = (
        "gross_realized_pnl",
        "fees",
        "funding_pnl",
        "net_pnl",
        "mfe_pnl",
        "mae_pnl",
    )
    totals: dict[str, float | int] = {
        field: sum(_finite(episode[field], f"episode {field}") for episode in episodes) for field in fields
    }
    for field in ("holding_duration_ns", "fill_count", "add_count", "reduce_count"):
        totals[field] = sum(_nonnegative_int(episode[field], f"episode {field}") for episode in episodes)
    return totals


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LateDevAttributionValidationError(f"{name} must be a non-negative integer")
    return value


def _negative_tail_share(closed: list[Mapping[str, Any]]) -> float | None:
    if not closed:
        return None
    losses = sorted(
        (_finite(episode["net_pnl"], "episode net PnL") for episode in closed),
        key=lambda pnl: max(0.0, -pnl),
        reverse=True,
    )
    losses = [max(0.0, -pnl) for pnl in losses]
    total_loss = sum(losses)
    if total_loss == 0.0:
        return 0.0
    tail_count = max(1, math.ceil(0.10 * len(closed)))
    return sum(losses[:tail_count]) / total_loss


def _attribution_groups(rows: list[Mapping[str, Any]], *, field: str) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            raise LateDevAttributionValidationError(f"attribution row {field} must be explicit")
        grouped.setdefault(value, []).append(row)
    return {value: _attribution_totals(grouped[value]) for value in sorted(grouped)}


def _attribution_totals(rows: list[Mapping[str, Any]]) -> dict[str, float | int]:
    gross_pnl = sum(_finite(row["gross_pnl"], "attribution gross PnL") for row in rows)
    turnover = sum(_finite(row["turnover_notional"], "attribution turnover notional") for row in rows)
    baseline_cost = sum(_finite(row["baseline_execution_cost"], "attribution baseline execution cost") for row in rows)
    funding_pnl = sum(_finite(row["funding_pnl"], "attribution funding PnL") for row in rows)
    additional_cost = turnover * 15.0 / 10_000.0
    return {
        "row_count": len(rows),
        "gross_pnl": gross_pnl,
        "turnover_notional": turnover,
        "baseline_execution_cost": baseline_cost,
        "funding_pnl": funding_pnl,
        "additional_15bp_cost": additional_cost,
        "net_pnl_15bp": gross_pnl + funding_pnl - baseline_cost - additional_cost,
    }


def _playbook_groups(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not any("playbook_id" in row for row in rows):
        return {"status": EvidenceStatus.INSUFFICIENT_SAMPLE.value, "groups": {}}
    return {"status": EvidenceStatus.CONSISTENT.value, "groups": _attribution_groups(rows, field="playbook_id")}


def _execution_actions(rows: list[Mapping[str, Any]]) -> dict[str, float | int]:
    actions = {action: 0 for action in ("entry", "exit", "flip", "add", "reduce", "unchanged")}
    shortfall_count = 0
    shortfall = 0.0
    for row in rows:
        execution = row.get("execution")
        if execution is None:
            continue
        execution_row = _mapping(execution, "attribution execution")
        actions[_derive_execution_action(execution_row)] += 1
        shortfall_count += 1
        shortfall += _finite(execution_row["actual_implementation_shortfall"], "actual implementation shortfall")
    return {
        **actions,
        "implementation_shortfall_observation_count": shortfall_count,
        "implementation_shortfall": shortfall,
    }
