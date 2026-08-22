"""Deterministic acceptance-summary policy tests for the frozen Quant V2 matrix."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

from zenquant.research.architecture_experiment import (
    ArchitectureStage,
    load_architecture_experiment_manifest,
)
from zenquant.research.quant_v2_acceptance import (
    AcceptanceValidationError,
    build_quant_v2_acceptance_summary,
    canonical_acceptance_summary_bytes,
)


def _episode(index: int, net_pnl: float, *, closed: bool = True) -> dict[str, object]:
    return {
        "episode_id": f"episode-{index}",
        "symbol": "BTCUSDT_PERP",
        "side": 1 if index % 2 else -1,
        "opened_at_ns": index * 10,
        "closed_at_ns": index * 10 + 5 if closed else None,
        "entry_price": 100.0,
        "exit_price": 101.0 if closed else None,
        "max_abs_qty": 1.0,
        "remaining_qty": 0.0 if closed else 1.0,
        "gross_realized_pnl": net_pnl,
        "fees": 0.0,
        "funding_pnl": 0.0,
        "net_pnl": net_pnl,
        "mfe_pnl": max(0.0, net_pnl),
        "mae_pnl": min(0.0, net_pnl),
        "unrealized_pnl": 0.0,
        "fill_count": 2 if closed else 1,
        "closed": closed,
        "add_count": 0,
        "reduce_count": 1 if closed else 0,
        "holding_duration_ns": 5 if closed else 0,
    }


def _execution_metrics() -> dict[str, object]:
    return {
        "status": "OBSERVED",
        "target_tracking_error": 0.0,
        "time_to_target_bars": 0.0,
        "time_to_target_status": "OBSERVED_ALL_IMMEDIATE",
        "immediate_target_reached_rate": 1.0,
        "right_censored_target_rate": 0.0,
        "implementation_shortfall": 1.0,
        "slippage": 0.25,
        "target_overshoot": 0,
        "unexpected_reversal": 0,
        "executor_created_exposure": 0,
        "episode_profit_factor": 1.2,
        "episode_profit_factor_status": "DEFINED",
        "position_switches_per_day": 0.5,
    }


def _later_stage(stage: str, *, net_15bp: float = 100.0) -> dict[str, object]:
    execution = {
        "cutoff_time_ns": 100,
        "decision_id": f"{stage}-decision",
        "approved_exposure": 0.5,
        "approved_target_qty": 0.5,
        "filled_exposure_before": 0.0,
        "filled_exposure_after": 0.5,
        "filled_position_before": 0.0,
        "filled_position_after": 0.5,
        "target_delta_exposure": 0.5,
        "reference_price": 100.0,
        "actual_fill_price": 100.01,
        "filled_qty": 0.5,
        "fee": 0.02,
        "actual_slippage": 0.01,
        "actual_implementation_shortfall": 0.03,
        "funding_pnl": 0.0,
        "target_tracking_error": 0.0,
        "time_to_target_bars": 0,
        "reduce_only": False,
        "safety_reasons": [],
        "forward_return_1": 0.01,
        "forward_return_3": 0.02,
        "forward_return_6": 0.03,
        "forward_return_1_available_at_ns": 200,
    }
    payload: dict[str, object] = {
        "stage": stage,
        "direction_hit_rate": 0.6,
        "cost_stress": {
            "0": {"net_pnl": 120.0, "max_drawdown": 0.10},
            "15": {"net_pnl": net_15bp, "max_drawdown": 0.11},
        },
        "execution_metrics": _execution_metrics(),
        "position_episodes": [_episode(1, 12.0), _episode(2, -10.0), _episode(3, 1_000.0, closed=False)],
        "episode_ledger": {
            "status": "OBSERVED",
            "closed_episode_count": 2,
            "open_episode_count": 1,
            "episode_profit_factor": 1.2,
            "episode_profit_factor_status": "DEFINED",
            "gross_profit": 12.0,
            "gross_loss": 10.0,
        },
        "attribution_rows": [{"execution": execution}],
    }
    if stage == ArchitectureStage.A5_SHADOW.value:
        payload["cost_shadow"] = {"blocks_order": False}
    return payload


def _direction_stage(stage: str) -> dict[str, object]:
    return {
        "stage": stage,
        "direction_hit_rate": 0.6,
        "cost_stress": {
            "0": {"net_pnl": 200.0, "max_drawdown": 0.08},
            "15": {"net_pnl": 150.0, "max_drawdown": 0.09},
        },
    }


def _complete_raw_report() -> dict[str, object]:
    manifest = load_architecture_experiment_manifest()
    runs = []
    for window in manifest.windows:
        for seed in manifest.seeds:
            stages = {
                ArchitectureStage.A0.value: {"stage": ArchitectureStage.A0.value},
                ArchitectureStage.A1_SIGN.value: _direction_stage(ArchitectureStage.A1_SIGN.value),
                ArchitectureStage.A1_SCORE.value: _direction_stage(ArchitectureStage.A1_SCORE.value),
                ArchitectureStage.A2.value: _later_stage(ArchitectureStage.A2.value),
                ArchitectureStage.A3.value: _later_stage(ArchitectureStage.A3.value),
                ArchitectureStage.A4.value: _later_stage(ArchitectureStage.A4.value),
                ArchitectureStage.A5_SHADOW.value: _later_stage(ArchitectureStage.A5_SHADOW.value),
            }
            runs.append(
                {
                    "window": window.label,
                    "seed": seed,
                    "identities": {
                        "formula_hashes": ["formula-v1"],
                        "alpha_config_hashes": ["alpha-v1"],
                    },
                    "stages": stages,
                    "options_shadow": {"status": "SUFFICIENT"},
                }
            )
    return {
        "schema_version": "architecture_attribution.v4",
        "stages": [stage.value for stage in ArchitectureStage],
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
        "options_shadow_stability": {
            "status": "SUFFICIENT",
            "coverage_status": "SUFFICIENT",
            "stability_status": "STABLE",
            "required_sufficient_seeds_per_oos_window": 4,
            "oos_window_seed_counts": {"late_dev_2025": 5},
            "oos_window_sufficient_seed_counts": {"late_dev_2025": 5},
            "quality_stability": {"CONFIRMING": {"status": "STABLE"}},
            "activation_recommendation": None,
        },
        "runs": runs,
    }


def _build(report: dict[str, object]) -> dict[str, object]:
    return build_quant_v2_acceptance_summary(
        report,
        source_artifact="raw.json",
        source_sha256="a" * 64,
        source_size_bytes=123,
    )


def _load_cli_module():
    path = Path(__file__).parent.parent / "scripts" / "build_quant_v2_acceptance_summary.py"
    spec = importlib.util.spec_from_file_location("build_quant_v2_acceptance_summary", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acceptance_summary_bytes_are_deterministic_and_matrix_is_exact():
    report = _complete_raw_report()

    first = _build(report)
    second = _build(deepcopy(report))

    assert canonical_acceptance_summary_bytes(first) == canonical_acceptance_summary_bytes(second)
    assert first["matrix"]["observed_runs"] == 30
    assert first["matrix"]["complete"] is True
    assert first["identities"]["formula_identity_set_count"] == 1
    assert first["gates"]["late_dev_positive_extra_15bp"]["passed"] is True
    assert first["gates"]["train_a5_episode_pf_at_least_1"]["passed"] is True
    assert first["gates"]["execution_purity"]["passed"] is True
    assert first["gates"]["options_zero_execution_influence"]["passed"] is True
    assert first["gates"]["hostile_window_risk_controlled"]["status"] == "NOT_EVALUABLE"
    assert first["verdict"] == "BLOCKED_PAUSE_PROMOTION_AND_TRAINING"
    assert first["blocked_by"] == ["hostile_window_gate_not_frozen_in_manifest"]


def test_acceptance_gate_logic_blocks_late_dev_and_tampered_episode_pf():
    report = _complete_raw_report()
    late = next(run for run in report["runs"] if run["window"] == "late_dev_2025" and run["seed"] == 42)
    late["stages"]["A5_SHADOW"]["cost_stress"]["15"]["net_pnl"] = -1.0

    summary = _build(report)

    assert summary["gates"]["late_dev_positive_extra_15bp"]["passed"] is False
    assert "late_dev_negative_under_extra_15bp" in summary["blocked_by"]

    train = next(run for run in report["runs"] if run["window"] == "train_full" and run["seed"] == 42)
    train["stages"]["A5_SHADOW"]["execution_metrics"]["episode_profit_factor"] = 99.0
    with pytest.raises(AcceptanceValidationError, match="episode ledger"):
        _build(report)


def test_acceptance_summary_rejects_missing_or_duplicate_matrix_identity():
    missing = _complete_raw_report()
    missing["runs"].pop()
    with pytest.raises(AcceptanceValidationError, match="matrix"):
        _build(missing)

    duplicate = _complete_raw_report()
    duplicate["runs"][-1] = deepcopy(duplicate["runs"][0])
    with pytest.raises(AcceptanceValidationError, match="matrix"):
        _build(duplicate)


def test_acceptance_cli_writes_canonical_bytes_and_returns_blocked_exit(tmp_path):
    module = _load_cli_module()
    source = tmp_path / "architecture_attribution.json"
    output = tmp_path / "acceptance_summary.json"
    source.write_text(json.dumps(_complete_raw_report(), indent=2, sort_keys=True), encoding="utf-8")

    exit_code = module.main(["--input", str(source), "--output", str(output)])
    summary = json.loads(output.read_bytes())

    assert exit_code == 1
    assert output.read_bytes() == canonical_acceptance_summary_bytes(summary)
    assert summary["source_artifact"] == str(source)
