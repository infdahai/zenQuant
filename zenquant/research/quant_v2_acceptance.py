"""Deterministic acceptance policy for the frozen Quant V2 experiment report."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from zenquant.domain import PositionEpisode

from .architecture_experiment import (
    ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION,
    ArchitectureStage,
    load_architecture_experiment_manifest,
)
from .gate_v3 import ProfitFactorStatus, calculate_episode_profit_factor

ACCEPTANCE_SUMMARY_SCHEMA_VERSION = "quant-v2-acceptance.v1"
ACCEPT_VERDICT = "ACCEPT_CONTINUE_TRAINING"
BLOCKED_VERDICT = "BLOCKED_PAUSE_PROMOTION_AND_TRAINING"
LATER_STAGES = (
    ArchitectureStage.A2.value,
    ArchitectureStage.A3.value,
    ArchitectureStage.A4.value,
    ArchitectureStage.A5_SHADOW.value,
)
PURITY_FIELDS = (
    "executor_created_exposure",
    "target_overshoot",
    "unexpected_reversal",
)


class AcceptanceValidationError(ValueError):
    """The raw integrated report is incomplete, inconsistent, or not frozen."""


def build_quant_v2_acceptance_summary(
    report: Any,
    *,
    source_artifact: str,
    source_sha256: str,
    source_size_bytes: int,
) -> dict[str, Any]:
    """Validate the exact 30-run report and derive the frozen product verdict."""
    if not isinstance(report, Mapping):
        raise AcceptanceValidationError("raw report must be a JSON object")
    try:
        return _build_quant_v2_acceptance_summary(
            report,
            source_artifact=source_artifact,
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
        )
    except AcceptanceValidationError:
        raise
    except (KeyError, OverflowError, TypeError, ValueError) as exc:
        raise AcceptanceValidationError("raw report contains an invalid or incomplete nested value") from exc


def _build_quant_v2_acceptance_summary(
    report: Mapping[str, Any],
    *,
    source_artifact: str,
    source_sha256: str,
    source_size_bytes: int,
) -> dict[str, Any]:
    manifest = load_architecture_experiment_manifest()
    _validate_source_identity(source_sha256=source_sha256, source_size_bytes=source_size_bytes)
    _validate_report_manifest(report, manifest=manifest)
    runs = _validate_matrix(report, manifest=manifest)
    formula_identities, alpha_identities = _validate_run_identities_and_ledgers(runs)
    options_zero_influence = _a4_a5_execution_material_equal(runs)

    runs_by_key = {(str(run["window"]), int(run["seed"])): run for run in runs}
    train_label = next(window.label for window in manifest.windows if window.role == "TRAIN")
    oos_labels = tuple(window.label for window in manifest.windows if window.role == "OOS")
    if len(oos_labels) != 1:
        raise AcceptanceValidationError("frozen acceptance policy requires exactly one OOS window")
    late_dev_label = oos_labels[0]

    seed_results: list[dict[str, Any]] = []
    all_purity = dict.fromkeys(PURITY_FIELDS, 0)
    all_a5_switches: list[float] = []
    for seed in manifest.seeds:
        seed_runs = [runs_by_key[(window.label, seed)] for window in manifest.windows]
        a5_stages = [_stage(run, ArchitectureStage.A5_SHADOW.value) for run in seed_runs]
        max_mdd = max(_metric(stage, "cost_stress", "0", "max_drawdown") for stage in a5_stages)
        max_switches = max(_metric(stage, "execution_metrics", "position_switches_per_day") for stage in a5_stages)
        all_a5_switches.extend(_metric(stage, "execution_metrics", "position_switches_per_day") for stage in a5_stages)
        train_stage = _stage(runs_by_key[(train_label, seed)], ArchitectureStage.A5_SHADOW.value)
        train_pf = _metric(train_stage, "execution_metrics", "episode_profit_factor")
        train_pf_passed = _episode_pf_gate_passed(train_stage)
        late_stage = _stage(runs_by_key[(late_dev_label, seed)], ArchitectureStage.A5_SHADOW.value)
        late_15bp = _metric(late_stage, "cost_stress", "15", "net_pnl")
        seed_purity = dict.fromkeys(PURITY_FIELDS, 0)
        for run in seed_runs:
            for stage_name in LATER_STAGES:
                metrics = _mapping(_stage(run, stage_name).get("execution_metrics"), "execution_metrics")
                for field in PURITY_FIELDS:
                    value = _int_metric(metrics, field)
                    seed_purity[field] += value
                    all_purity[field] += value
        passed = (
            max_mdd < 0.20
            and train_pf_passed
            and late_15bp > 0.0
            and max_switches <= 2.0
            and all(value == 0 for value in seed_purity.values())
        )
        seed_results.append(
            {
                "seed": seed,
                "max_a5_baseline_mdd": max_mdd,
                "train_a5_episode_pf": train_pf,
                "late_dev_a5_extra_15bp_net_pnl": late_15bp,
                "max_a5_switches_per_day": max_switches,
                "purity": seed_purity,
                "passed": passed,
            }
        )

    late_runs = [runs_by_key[(late_dev_label, seed)] for seed in manifest.seeds]
    late_a5_15bp = [
        _metric(_stage(run, ArchitectureStage.A5_SHADOW.value), "cost_stress", "15", "net_pnl") for run in late_runs
    ]
    train_stages = [
        _stage(runs_by_key[(train_label, seed)], ArchitectureStage.A5_SHADOW.value) for seed in manifest.seeds
    ]
    train_pf_passes = [_episode_pf_gate_passed(stage) for stage in train_stages]
    numeric_train_pf = [
        value
        for stage in train_stages
        if (value := _metric(stage, "execution_metrics", "episode_profit_factor", allow_none=True)) is not None
    ]
    seed_pass_count = sum(1 for item in seed_results if item["passed"])
    options_summary = _validate_options_shadow_stability(
        report.get("options_shadow_stability"),
        oos_labels=oos_labels,
        expected_seed_count=len(manifest.seeds),
    )

    gates: dict[str, Any] = {
        "all_seed_a5_mdd_below_20pct": {
            "passed": all(item["max_a5_baseline_mdd"] < 0.20 for item in seed_results),
            "max": max(item["max_a5_baseline_mdd"] for item in seed_results),
        },
        "train_a5_episode_pf_at_least_1": {
            "passed": all(train_pf_passes),
            "min": min(numeric_train_pf) if numeric_train_pf else None,
        },
        "late_dev_positive_extra_15bp": {
            "passed": all(value > 0.0 for value in late_a5_15bp),
            "range": [min(late_a5_15bp), max(late_a5_15bp)],
        },
        "hostile_window_risk_controlled": {
            "passed": False,
            "status": "NOT_EVALUABLE",
            "reason": "frozen manifest has no explicit hostile window or quantitative hostile threshold",
        },
        "switch_cadence": {
            "passed": all(value <= 2.0 for value in all_a5_switches),
            "range": [
                min(item["max_a5_switches_per_day"] for item in seed_results),
                max(item["max_a5_switches_per_day"] for item in seed_results),
            ],
            "severity": _cadence_severity(max(all_a5_switches)),
            "absolute_over_5_count": sum(1 for value in all_a5_switches if value > 5.0),
        },
        "execution_purity": {
            "passed": all(value == 0 for value in all_purity.values()),
            "totals": all_purity,
        },
        "options_zero_execution_influence": {
            "passed": options_zero_influence,
            "evidence": "raw A4/A5 execution-material equality and non-blocking A5 cost-shadow rows",
        },
        "robustness_4_of_5": {
            "passed": seed_pass_count >= 4,
            "passing_seed_count": seed_pass_count,
            "required": 4,
        },
    }

    blocked_by: list[str] = []
    if not gates["all_seed_a5_mdd_below_20pct"]["passed"]:
        blocked_by.append("a5_mdd_not_below_20pct")
    if not gates["train_a5_episode_pf_at_least_1"]["passed"]:
        blocked_by.append("train_a5_episode_pf_below_1")
    if not gates["late_dev_positive_extra_15bp"]["passed"]:
        blocked_by.append("late_dev_negative_under_extra_15bp")
    if not gates["robustness_4_of_5"]["passed"]:
        blocked_by.append("robustness_below_4_of_5")
    blocked_by.append("hostile_window_gate_not_frozen_in_manifest")
    if not gates["switch_cadence"]["passed"]:
        blocked_by.append("switch_cadence_above_2_per_day")
    if not gates["execution_purity"]["passed"]:
        blocked_by.append("execution_purity_violation")
    if not gates["options_zero_execution_influence"]["passed"]:
        blocked_by.append("options_shadow_changed_execution")
    if options_summary.get("status") != "SUFFICIENT":
        blocked_by.append("options_shadow_oos_overlap_insufficient")

    return {
        "schema_version": ACCEPTANCE_SUMMARY_SCHEMA_VERSION,
        "source_artifact": source_artifact,
        "source_sha256": source_sha256,
        "source_size_bytes": source_size_bytes,
        "matrix": {
            "complete": True,
            "expected_runs": len(manifest.expected_run_keys),
            "observed_runs": len(runs),
            "windows": [window.label for window in manifest.windows],
            "seeds": list(manifest.seeds),
        },
        "identities": {
            "manifest_sha256": manifest.sha256,
            "candidate_params_sha256": manifest.candidate_params_sha256,
            "profile_sha256": manifest.profile_sha256,
            "formula_identity_set_count": len(formula_identities),
            "alpha_config_identity_set_count": len(alpha_identities),
        },
        "gates": gates,
        "seed_results": seed_results,
        "direction_attribution_late_dev": {
            stage_name: _late_direction_range(late_runs, stage_name=stage_name)
            for stage_name in (
                ArchitectureStage.A1_SIGN.value,
                ArchitectureStage.A1_SCORE.value,
                ArchitectureStage.A5_SHADOW.value,
            )
        },
        "options_shadow": options_summary,
        "blocked_by": blocked_by,
        "verdict": BLOCKED_VERDICT if blocked_by else ACCEPT_VERDICT,
    }


def canonical_acceptance_summary_bytes(summary: Mapping[str, Any]) -> bytes:
    """Return the sole canonical on-disk representation of the compact verdict."""
    return json.dumps(summary, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")


def acceptance_exit_code(summary: Mapping[str, Any]) -> int:
    """Use a non-zero outcome for a valid experiment that blocks promotion."""
    return 0 if summary.get("verdict") == ACCEPT_VERDICT else 1


def _validate_source_identity(*, source_sha256: str, source_size_bytes: int) -> None:
    if len(source_sha256) != 64 or any(character not in "0123456789abcdef" for character in source_sha256):
        raise AcceptanceValidationError("source SHA-256 must be a lowercase hexadecimal digest")
    if source_size_bytes <= 0:
        raise AcceptanceValidationError("source artifact must not be empty")


def _validate_report_manifest(report: Mapping[str, Any], *, manifest: Any) -> None:
    if report.get("schema_version") != ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION:
        raise AcceptanceValidationError(f"raw report schema must be {ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION!r}")
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
    expected = {
        "sha256": manifest.sha256,
        "candidate_params_sha256": manifest.candidate_params_sha256,
        "profile_sha256": manifest.profile_sha256,
        "windows": expected_windows,
        "seeds": list(manifest.seeds),
        "cost_stress_bps": list(manifest.cost_stress_bps),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise AcceptanceValidationError(f"raw report manifest identity mismatch: {key}")
    if report.get("stages") != [stage.value for stage in ArchitectureStage]:
        raise AcceptanceValidationError("raw report stage order does not match the frozen architecture")


def _validate_matrix(report: Mapping[str, Any], *, manifest: Any) -> list[Mapping[str, Any]]:
    raw_runs = report.get("runs")
    if not isinstance(raw_runs, list) or not all(isinstance(run, Mapping) for run in raw_runs):
        raise AcceptanceValidationError("raw report runs must be a list of objects")
    observed = [(str(run.get("window")), int(run.get("seed", -1))) for run in raw_runs]
    expected = list(manifest.expected_run_keys)
    if len(observed) != len(set(observed)) or observed != expected:
        raise AcceptanceValidationError(f"raw report matrix mismatch: expected={expected!r} observed={observed!r}")
    return list(raw_runs)


def _validate_run_identities_and_ledgers(
    runs: list[Mapping[str, Any]],
) -> tuple[set[str], set[str]]:
    formula_identities: set[str] = set()
    alpha_identities: set[str] = set()
    expected_stages = {stage.value for stage in ArchitectureStage}
    for run in runs:
        stages = _mapping(run.get("stages"), "stages")
        if set(stages) != expected_stages:
            raise AcceptanceValidationError("run does not contain all seven frozen stages")
        identities = _mapping(run.get("identities"), "identities")
        formulas = identities.get("formula_hashes")
        alpha_configs = identities.get("alpha_config_hashes")
        if (
            not isinstance(formulas, list)
            or len(formulas) != 1
            or not all(isinstance(item, str) and item for item in formulas)
        ):
            raise AcceptanceValidationError("run formula identity must contain exactly one non-empty hash")
        if (
            not isinstance(alpha_configs, list)
            or len(alpha_configs) != 1
            or not all(isinstance(item, str) and item for item in alpha_configs)
        ):
            raise AcceptanceValidationError("run alpha config identity must contain exactly one non-empty hash")
        formula_identities.update(formulas)
        alpha_identities.update(alpha_configs)
        for stage_name in LATER_STAGES:
            _validate_episode_pf(_stage(run, stage_name), stage_name=stage_name)
    if len(formula_identities) != 1 or len(alpha_identities) != 1:
        raise AcceptanceValidationError("frozen matrix contains divergent formula or alpha config identities")
    return formula_identities, alpha_identities


def _validate_episode_pf(stage: Mapping[str, Any], *, stage_name: str) -> None:
    payload = stage.get("position_episodes")
    if not isinstance(payload, list):
        raise AcceptanceValidationError(f"{stage_name} position episode ledger is missing")
    try:
        episodes = tuple(PositionEpisode(**item) for item in payload)
    except (TypeError, ValueError) as exc:
        raise AcceptanceValidationError(f"{stage_name} position episode ledger is invalid") from exc
    pf = calculate_episode_profit_factor(episodes)
    metrics = _mapping(stage.get("execution_metrics"), "execution_metrics")
    ledger = _mapping(stage.get("episode_ledger"), "episode_ledger")
    if metrics.get("status") != "OBSERVED" or ledger.get("status") != "OBSERVED":
        raise AcceptanceValidationError(f"{stage_name} episode ledger is not observed")
    if (
        metrics.get("episode_profit_factor_status") != pf.status.value
        or not _optional_number_equal(metrics.get("episode_profit_factor"), pf.value)
        or ledger.get("episode_profit_factor_status") != pf.status.value
        or not _optional_number_equal(ledger.get("episode_profit_factor"), pf.value)
        or ledger.get("closed_episode_count") != pf.closed_episode_count
        or ledger.get("open_episode_count") != sum(1 for episode in episodes if not episode.closed)
        or not _optional_number_equal(ledger.get("gross_profit"), pf.gross_profit)
        or not _optional_number_equal(ledger.get("gross_loss"), pf.gross_loss)
    ):
        raise AcceptanceValidationError(f"{stage_name} episode ledger does not reproduce reported PF")


def _validate_options_shadow_stability(
    value: Any,
    *,
    oos_labels: tuple[str, ...],
    expected_seed_count: int,
) -> dict[str, Any]:
    payload = _mapping(value, "options_shadow_stability")
    _choice(payload.get("status"), name="options status", allowed={"SUFFICIENT", "INSUFFICIENT_SAMPLE"})
    _choice(
        payload.get("coverage_status"),
        name="options coverage status",
        allowed={"SUFFICIENT", "INSUFFICIENT_SAMPLE"},
    )
    _choice(
        payload.get("stability_status"),
        name="options stability status",
        allowed={"STABLE", "UNSTABLE", "INSUFFICIENT_SAMPLE"},
    )
    required_seeds = payload.get("required_sufficient_seeds_per_oos_window")
    if isinstance(required_seeds, bool) or not isinstance(required_seeds, int) or required_seeds <= 0:
        raise AcceptanceValidationError("options required sufficient seed count must be a positive integer")
    seed_counts = _validated_count_mapping(
        payload.get("oos_window_seed_counts"),
        name="options OOS seed counts",
        expected_keys=set(oos_labels),
        maximum=expected_seed_count,
    )
    sufficient_counts = _validated_count_mapping(
        payload.get("oos_window_sufficient_seed_counts"),
        name="options OOS sufficient seed counts",
        expected_keys=set(oos_labels),
        maximum=expected_seed_count,
    )
    if any(sufficient_counts[label] > seed_counts[label] for label in oos_labels):
        raise AcceptanceValidationError("options sufficient seed count exceeds observed seed count")
    quality = _mapping(payload.get("quality_stability"), "options quality stability")
    for label, diagnostic in quality.items():
        if not isinstance(label, str) or not label:
            raise AcceptanceValidationError("options quality labels must be non-empty strings")
        item = _mapping(diagnostic, f"options quality stability {label}")
        _choice(
            item.get("status"),
            name=f"options quality stability {label} status",
            allowed={"STABLE", "UNSTABLE", "INSUFFICIENT_SAMPLE"},
        )
    if payload.get("activation_recommendation") is not None:
        raise AcceptanceValidationError("options activation recommendation must remain null in shadow mode")
    return dict(payload)


def _validated_count_mapping(
    value: Any,
    *,
    name: str,
    expected_keys: set[str],
    maximum: int,
) -> dict[str, int]:
    payload = _mapping(value, name)
    if set(payload) != expected_keys:
        raise AcceptanceValidationError(f"{name} keys do not match frozen OOS windows")
    counts: dict[str, int] = {}
    for key, count in payload.items():
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= maximum:
            raise AcceptanceValidationError(f"{name} values must be bounded non-negative integers")
        counts[str(key)] = count
    return counts


def _choice(value: Any, *, name: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise AcceptanceValidationError(f"{name} is invalid")
    return value


def _episode_pf_gate_passed(stage: Mapping[str, Any]) -> bool:
    ledger = _mapping(stage.get("episode_ledger"), "episode_ledger")
    status = ledger.get("episode_profit_factor_status")
    if status == ProfitFactorStatus.NO_LOSS_EPISODES.value:
        return int(ledger.get("closed_episode_count", 0)) >= 2
    value = ledger.get("episode_profit_factor")
    return status == ProfitFactorStatus.DEFINED.value and isinstance(value, int | float) and float(value) >= 1.0


def _a4_a5_execution_material_equal(runs: list[Mapping[str, Any]]) -> bool:
    for run in runs:
        a4 = _stage(run, ArchitectureStage.A4.value)
        a5 = _stage(run, ArchitectureStage.A5_SHADOW.value)
        a4_rows = a4.get("attribution_rows")
        a5_rows = a5.get("attribution_rows")
        if not isinstance(a4_rows, list) or not isinstance(a5_rows, list) or len(a4_rows) != len(a5_rows):
            return False
        for a4_row, a5_row in zip(a4_rows, a5_rows, strict=True):
            if not isinstance(a4_row, Mapping) or not isinstance(a5_row, Mapping):
                return False
            a4_execution = _mapping(a4_row.get("execution"), "A4 execution")
            a5_execution = _mapping(a5_row.get("execution"), "A5 execution")
            a4_material = {key: value for key, value in a4_execution.items() if key != "decision_id"}
            a5_material = {key: value for key, value in a5_execution.items() if key != "decision_id"}
            if a4_material != a5_material:
                return False
        shadow = _mapping(a5.get("cost_shadow"), "A5 cost_shadow")
        if shadow.get("blocks_order") is not False:
            return False
    return True


def _late_direction_range(runs: list[Mapping[str, Any]], *, stage_name: str) -> dict[str, list[float]]:
    stages = [_stage(run, stage_name) for run in runs]
    baseline = [_metric(stage, "cost_stress", "0", "net_pnl") for stage in stages]
    stressed = [_metric(stage, "cost_stress", "15", "net_pnl") for stage in stages]
    hit_rates = [_metric(stage, "direction_hit_rate") for stage in stages]
    return {
        "direction_hit_rate_range": [min(hit_rates), max(hit_rates)],
        "baseline_net_pnl_range": [min(baseline), max(baseline)],
        "extra_15bp_net_pnl_range": [min(stressed), max(stressed)],
    }


def _stage(run: Mapping[str, Any], stage_name: str) -> Mapping[str, Any]:
    stages = _mapping(run.get("stages"), "stages")
    return _mapping(stages.get(stage_name), stage_name)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AcceptanceValidationError(f"{name} must be an object")
    return value


def _metric(payload: Mapping[str, Any], *path: str, allow_none: bool = False) -> float | None:
    value: Any = payload
    for key in path:
        value = _mapping(value, ".".join(path)).get(key)
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise AcceptanceValidationError(f"metric {'.'.join(path)} must be finite")
    return float(value)


def _int_metric(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AcceptanceValidationError(f"execution purity metric {key} must be a non-negative integer")
    return value


def _optional_number_equal(left: Any, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or not isinstance(left, int | float):
        return False
    return math.isclose(float(left), right, rel_tol=0.0, abs_tol=1e-12)


def _cadence_severity(max_switches_per_day: float) -> str:
    if max_switches_per_day <= 1.0:
        return "NONE"
    if max_switches_per_day <= 2.0:
        return "SOFT"
    return "HARD"
