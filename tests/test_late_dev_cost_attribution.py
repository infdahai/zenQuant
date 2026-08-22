"""Contract tests for the frozen late-development attribution envelope."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from zenquant.research.architecture_experiment import (
    ArchitectureStage,
    load_architecture_experiment_manifest,
)
from zenquant.research.late_dev_cost_attribution import (
    LateDevAttributionValidationError,
    _classify_evidence,
    build_late_dev_cost_attribution,
    build_regime_objective_hypothesis,
    canonical_late_dev_attribution_bytes,
)

CLI = Path(__file__).parent.parent / "scripts" / "analyze_quant_v2_late_dev_cost.py"


def _run_cli(source: Path, output_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "--input", str(source), "--output-dir", str(output_dir)],
        text=True,
        capture_output=True,
        check=False,
    )


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("late_dev_cost_cli_test", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cost_stage(stage: str, *, include_episodes: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "stage": stage,
        "direction_hit_rate": 0.6,
        "turnover_notional": 10_000.0,
        "cost_stress": {
            "0": {"net_pnl": 120.0},
            "15": {"net_pnl": 105.0},
        },
        "attribution_rows": [
            {
                "regime": "HOSTILE",
                "gross_pnl": -10.0,
                "turnover_notional": 4_000.0,
                "baseline_execution_cost": 1.0,
                "funding_pnl": -1.0,
                "execution": {
                    "filled_position_before": 0.0,
                    "filled_position_after": 1.0,
                    "actual_implementation_shortfall": 1.0,
                },
            },
            {
                "regime": "NORMAL",
                "gross_pnl": 20.0,
                "turnover_notional": 6_000.0,
                "baseline_execution_cost": 2.0,
                "funding_pnl": 0.0,
                "execution": {
                    "filled_position_before": 1.0,
                    "filled_position_after": 2.0,
                    "actual_implementation_shortfall": 2.0,
                },
            },
            {
                "regime": "NORMAL",
                "gross_pnl": 0.0,
                "turnover_notional": 0.0,
                "baseline_execution_cost": 0.0,
                "funding_pnl": 0.0,
                "execution": {
                    "filled_position_before": 2.0,
                    "filled_position_after": 1.0,
                    "actual_implementation_shortfall": 3.0,
                },
            },
            {
                "regime": "NORMAL",
                "gross_pnl": 0.0,
                "turnover_notional": 0.0,
                "baseline_execution_cost": 0.0,
                "funding_pnl": 0.0,
                "execution": {
                    "filled_position_before": 1.0,
                    "filled_position_after": 0.0,
                    "actual_implementation_shortfall": 4.0,
                },
            },
            {
                "regime": "NORMAL",
                "gross_pnl": 0.0,
                "turnover_notional": 0.0,
                "baseline_execution_cost": 0.0,
                "funding_pnl": 0.0,
                "execution": {
                    "filled_position_before": 1.0,
                    "filled_position_after": -1.0,
                    "actual_implementation_shortfall": 5.0,
                },
            },
        ],
    }
    if include_episodes:
        payload["position_episodes"] = [
            {
                "episode_id": "long-closed",
                "symbol": "BTCUSDT_PERP",
                "side": 1,
                "opened_at_ns": 1,
                "closed_at_ns": 6,
                "entry_price": 100.0,
                "exit_price": 106.0,
                "max_abs_qty": 1.0,
                "remaining_qty": 0.0,
                "gross_realized_pnl": 6.0,
                "fees": 1.0,
                "funding_pnl": 0.0,
                "net_pnl": 5.0,
                "mfe_pnl": 7.0,
                "mae_pnl": -2.0,
                "unrealized_pnl": 0.0,
                "fill_count": 3,
                "closed": True,
                "add_count": 1,
                "reduce_count": 2,
                "holding_duration_ns": 5,
            },
            {
                "episode_id": "short-closed",
                "symbol": "BTCUSDT_PERP",
                "side": -1,
                "opened_at_ns": 7,
                "closed_at_ns": 13,
                "entry_price": 100.0,
                "exit_price": 102.0,
                "max_abs_qty": 1.0,
                "remaining_qty": 0.0,
                "gross_realized_pnl": -2.0,
                "fees": 1.0,
                "funding_pnl": 0.0,
                "net_pnl": -3.0,
                "mfe_pnl": 2.0,
                "mae_pnl": -4.0,
                "unrealized_pnl": 0.0,
                "fill_count": 2,
                "closed": True,
                "add_count": 0,
                "reduce_count": 1,
                "holding_duration_ns": 6,
            },
            {
                "episode_id": "long-open",
                "symbol": "BTCUSDT_PERP",
                "side": 1,
                "opened_at_ns": 14,
                "closed_at_ns": None,
                "entry_price": 100.0,
                "exit_price": None,
                "max_abs_qty": 1.0,
                "remaining_qty": 1.0,
                "gross_realized_pnl": 1_000.0,
                "fees": 1_000.0,
                "funding_pnl": 1_000.0,
                "net_pnl": 1_000.0,
                "mfe_pnl": 1_000.0,
                "mae_pnl": -1_000.0,
                "unrealized_pnl": 7.0,
                "fill_count": 99,
                "closed": False,
                "add_count": 99,
                "reduce_count": 99,
                "holding_duration_ns": 0,
            },
        ]
        payload["episode_ledger"] = {
            "status": "OBSERVED",
            "closed_episode_count": 2,
            "open_episode_count": 1,
        }
    return payload


def _complete_raw_report(
    *,
    direction_hit_rate: float = 0.6,
    a1_sign_15bp: float = 105.0,
    regime_localized_seed_count: int = 5,
) -> dict[str, object]:
    """Build a complete frozen matrix from the checked-in manifest.

    The stage payloads are intentionally literal rather than borrowed from
    another test module: this fixture tests the envelope, not strategy logic.
    """
    manifest = load_architecture_experiment_manifest()
    stage_order = [stage.value for stage in ArchitectureStage]
    runs: list[dict[str, object]] = []
    for window in manifest.windows:
        for seed in manifest.seeds:
            runs.append(
                {
                    "window": window.label,
                    "seed": seed,
                    "identities": {
                        "formula_hashes": ["formula-v1"],
                        "alpha_config_hashes": ["alpha-v1"],
                    },
                    "stages": {
                        stage: (
                            _cost_stage(stage, include_episodes=stage == ArchitectureStage.A5_SHADOW.value)
                            if stage
                            in {
                                ArchitectureStage.A1_SIGN.value,
                                ArchitectureStage.A1_SCORE.value,
                                ArchitectureStage.A5_SHADOW.value,
                            }
                            else {"stage": stage, "literal": True}
                        )
                        for stage in stage_order
                    },
                }
            )
    report: dict[str, object] = {
        "schema_version": "architecture_attribution.v4",
        "stages": stage_order,
        "manifest": {
            "sha256": manifest.sha256,
            "candidate_source_sha256": manifest.candidate_source_sha256,
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
        "runs": runs,
    }
    late_dev_runs = [run for run in runs if run["window"] == "late_dev_2025"]
    for index, run in enumerate(late_dev_runs):
        a1_sign = run["stages"][ArchitectureStage.A1_SIGN.value]  # type: ignore[index]
        a1_sign["direction_hit_rate"] = direction_hit_rate
        a1_sign["cost_stress"]["15"]["net_pnl"] = a1_sign_15bp
        a1_sign["turnover_notional"] = (120.0 - a1_sign_15bp) * 10_000.0 / 15.0
        if index >= regime_localized_seed_count:
            a5_rows = run["stages"][ArchitectureStage.A5_SHADOW.value]["attribution_rows"]  # type: ignore[index]
            a5_rows[1]["gross_pnl"] = -20.0
    return report


def _build(report: dict[str, object]) -> dict[str, object]:
    return build_late_dev_cost_attribution(
        report,
        source_artifact="architecture_attribution.json",
        source_sha256="a" * 64,
        source_size_bytes=123,
    )


def test_build_report_rejects_duplicate_or_missing_frozen_matrix():
    report = _complete_raw_report()
    report["runs"].pop()  # type: ignore[union-attr]
    with pytest.raises(LateDevAttributionValidationError, match="matrix"):
        _build(report)


def test_canonical_bytes_are_deterministic():
    first = _build(_complete_raw_report())
    second = _build(deepcopy(_complete_raw_report()))

    assert canonical_late_dev_attribution_bytes(first) == canonical_late_dev_attribution_bytes(second)
    assert b'"created_at"' not in canonical_late_dev_attribution_bytes(first)


def test_build_report_requires_frozen_candidate_source_identity():
    report = _complete_raw_report()
    report["manifest"].pop("candidate_source_sha256")  # type: ignore[union-attr]

    with pytest.raises(LateDevAttributionValidationError, match="candidate_source_sha256"):
        _build(report)


def test_build_report_rejects_stage_payload_declared_under_wrong_key():
    report = _complete_raw_report()
    first_run = report["runs"][0]  # type: ignore[index]
    first_run["stages"]["A1_SIGN"]["stage"] = "A5_SHADOW"  # type: ignore[index]

    with pytest.raises(LateDevAttributionValidationError, match="stage"):
        _build(report)


def test_seed_bridge_reconciles_baseline_to_extra_15bp():
    bridge = _build(_complete_raw_report())["seed_stage_bridges"][0]  # type: ignore[index]

    assert bridge == {
        "seed": 42,
        "stage": "A1_SIGN",
        "baseline_net_pnl": 120.0,
        "turnover_notional": 10_000.0,
        "additional_15bp_cost": 15.0,
        "extra_15bp_net_pnl": 105.0,
        "reconciliation_error": 0.0,
    }


def test_seed_bridge_rejects_a_non_reconciling_15bp_total():
    report = _complete_raw_report()
    late_dev = next(run for run in report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    late_dev["stages"]["A5_SHADOW"]["cost_stress"]["15"]["net_pnl"] = 104.0  # type: ignore[index]

    with pytest.raises(LateDevAttributionValidationError, match="15bp bridge does not reconcile"):
        _build(report)


def test_open_episode_is_visible_but_excluded_from_closed_episode_pnl():
    episodes = _build(_complete_raw_report())["episode_attribution"][0]  # type: ignore[index]

    assert episodes["closed_episode_count"] == 2
    assert episodes["open_episode_count"] == 1
    assert episodes["closed_net_pnl"] == 2.0
    assert episodes["open_unrealized_pnl"] == 7.0
    assert episodes["closed_totals"] == {
        "gross_realized_pnl": 4.0,
        "fees": 2.0,
        "funding_pnl": 0.0,
        "net_pnl": 2.0,
        "mfe_pnl": 9.0,
        "mae_pnl": -6.0,
        "holding_duration_ns": 11,
        "fill_count": 5,
        "add_count": 1,
        "reduce_count": 3,
    }
    assert episodes["by_side"] == {
        "LONG": {
            "closed_episode_count": 1,
            "gross_realized_pnl": 6.0,
            "fees": 1.0,
            "funding_pnl": 0.0,
            "net_pnl": 5.0,
            "mfe_pnl": 7.0,
            "mae_pnl": -2.0,
            "holding_duration_ns": 5,
            "fill_count": 3,
            "add_count": 1,
            "reduce_count": 2,
        },
        "SHORT": {
            "closed_episode_count": 1,
            "gross_realized_pnl": -2.0,
            "fees": 1.0,
            "funding_pnl": 0.0,
            "net_pnl": -3.0,
            "mfe_pnl": 2.0,
            "mae_pnl": -4.0,
            "holding_duration_ns": 6,
            "fill_count": 2,
            "add_count": 0,
            "reduce_count": 1,
        },
    }
    assert episodes["negative_tail_share"] == 1.0


def test_execution_and_explicit_regime_groups_are_accounted_without_playbook_inference():
    episodes = _build(_complete_raw_report())["episode_attribution"][0]  # type: ignore[index]

    assert episodes["regime_groups"] == {
        "HOSTILE": {
            "row_count": 1,
            "gross_pnl": -10.0,
            "turnover_notional": 4_000.0,
            "baseline_execution_cost": 1.0,
            "funding_pnl": -1.0,
            "additional_15bp_cost": 6.0,
            "net_pnl_15bp": -18.0,
        },
        "NORMAL": {
            "row_count": 4,
            "gross_pnl": 20.0,
            "turnover_notional": 6_000.0,
            "baseline_execution_cost": 2.0,
            "funding_pnl": 0.0,
            "additional_15bp_cost": 9.0,
            "net_pnl_15bp": 9.0,
        },
    }
    assert episodes["playbook_groups"] == {"status": "INSUFFICIENT_SAMPLE", "groups": {}}
    assert episodes["execution_actions"] == {
        "entry": 1,
        "exit": 1,
        "flip": 1,
        "add": 1,
        "reduce": 1,
        "unchanged": 0,
        "implementation_shortfall_observation_count": 5,
        "implementation_shortfall": 15.0,
    }


def test_episode_ledger_count_mismatch_fails_closed():
    report = _complete_raw_report()
    late_dev = next(run for run in report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    late_dev["stages"]["A5_SHADOW"]["episode_ledger"]["closed_episode_count"] = 3  # type: ignore[index]

    with pytest.raises(LateDevAttributionValidationError, match="episode ledger closed episode count mismatch"):
        _build(report)


def test_not_observed_empty_episode_ledger_remains_distinct_from_observed_empty_ledger():
    report = _complete_raw_report()
    late_dev = next(run for run in report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    stage = late_dev["stages"]["A5_SHADOW"]  # type: ignore[index]
    stage["position_episodes"] = []
    stage["episode_ledger"] = {
        "status": "NOT_OBSERVED",
        "closed_episode_count": 0,
        "open_episode_count": 0,
    }

    summary = _build(report)["episode_attribution"][0]  # type: ignore[index]

    assert summary["episode_ledger_status"] == "NOT_OBSERVED"
    assert summary["closed_episode_count"] == 0
    assert summary["open_episode_count"] == 0

    observed_report = deepcopy(report)
    observed_late_dev = next(run for run in observed_report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    observed_late_dev["stages"]["A5_SHADOW"]["episode_ledger"]["status"] = "OBSERVED"  # type: ignore[index]

    observed_summary = _build(observed_report)["episode_attribution"][0]  # type: ignore[index]
    assert observed_summary["episode_ledger_status"] == "OBSERVED"


def test_global_direction_failure_stops_donor_training():
    result = _build(_complete_raw_report(direction_hit_rate=0.47, a1_sign_15bp=-10.0))

    assert result["evidence"]["directional_edge"]["status"] == "CONSISTENT"  # type: ignore[index]
    assert result["terminal_recommendation"] == "stop_donor_training"
    assert build_regime_objective_hypothesis(result) is None


def test_regime_localized_failure_prepares_redesign_at_four_of_five():
    result = _build(_complete_raw_report(regime_localized_seed_count=4))

    assert result["evidence"]["regime_localization"]["supporting_seed_count"] == 4  # type: ignore[index]
    assert result["terminal_recommendation"] == "prepare_regime_objective_redesign"


def test_three_of_five_is_mixed_and_cannot_open_redesign():
    result = _build(_complete_raw_report(regime_localized_seed_count=3))

    assert result["evidence"]["regime_localization"]["status"] == "MIXED"  # type: ignore[index]
    assert result["terminal_recommendation"] == "insufficient_evidence_no_strategy_change"


def test_episode_and_shortfall_sample_floors_are_family_local():
    evidence = _classify_evidence(
        [
            {
                "seed": seed,
                "directional_edge_support": True,
                "confidence_no_incremental_edge_support": False,
                "cost_flip_support": False,
                "regime_localization_support": False,
                "closed_episode_count": 19,
                "negative_tail_share": 1.0,
                "implementation_shortfall_observation_count": 19,
                "implementation_shortfall": 100.0,
                "a5_baseline_net_pnl": -100.0,
            }
            for seed in (42, 1729, 2718, 3141, 9001)
        ]
    )

    assert evidence["directional_edge"]["status"] == "CONSISTENT"
    assert evidence["episode_tail"]["status"] == "INSUFFICIENT_SAMPLE"
    assert evidence["execution_shortfall"]["status"] == "INSUFFICIENT_SAMPLE"


def test_execution_shortfall_does_not_divide_a_non_loss_baseline():
    evidence = _classify_evidence(
        [
            {
                "seed": seed,
                "directional_edge_support": False,
                "confidence_no_incremental_edge_support": False,
                "cost_flip_support": False,
                "regime_localization_support": False,
                "closed_episode_count": 20,
                "negative_tail_share": 0.0,
                "implementation_shortfall_observation_count": 20,
                "implementation_shortfall": 100.0,
                "a5_baseline_net_pnl": 50.0,
            }
            for seed in (42, 1729, 2718, 3141, 9001)
        ]
    )

    assert evidence["execution_shortfall"]["status"] == "UNATTRIBUTABLE"


def test_non_loss_shortfall_below_floor_is_unattributable_not_insufficient_sample():
    evidence = _classify_evidence(
        [
            {
                "seed": seed,
                "directional_edge_support": False,
                "confidence_no_incremental_edge_support": False,
                "cost_flip_support": False,
                "regime_localization_support": False,
                "closed_episode_count": 20,
                "negative_tail_share": 0.0,
                "implementation_shortfall_observation_count": 19,
                "implementation_shortfall": 100.0,
                "a5_baseline_net_pnl": 50.0,
            }
            for seed in (42, 1729, 2718, 3141, 9001)
        ]
    )

    assert evidence["execution_shortfall"]["status"] == "UNATTRIBUTABLE"
    assert evidence["execution_shortfall"]["insufficient_sample_seeds"] == []


def test_four_loss_seed_shortfalls_remain_consistent_with_one_non_loss_seed_below_floor():
    evidence = _classify_evidence(
        [
            {
                "seed": seed,
                "directional_edge_support": False,
                "confidence_no_incremental_edge_support": False,
                "cost_flip_support": False,
                "regime_localization_support": False,
                "closed_episode_count": 20,
                "negative_tail_share": 0.0,
                "implementation_shortfall_observation_count": 20,
                "implementation_shortfall": 25.0,
                "a5_baseline_net_pnl": -100.0,
                "execution_shortfall_support": True,
            }
            for seed in (42, 1729, 2718, 3141)
        ]
        + [
            {
                "seed": 9001,
                "directional_edge_support": False,
                "confidence_no_incremental_edge_support": False,
                "cost_flip_support": False,
                "regime_localization_support": False,
                "closed_episode_count": 20,
                "negative_tail_share": 0.0,
                "implementation_shortfall_observation_count": 0,
                "implementation_shortfall": 0.0,
                "a5_baseline_net_pnl": 50.0,
                "execution_shortfall_support": False,
            }
        ]
    )

    assert evidence["execution_shortfall"]["status"] == "CONSISTENT"
    assert evidence["execution_shortfall"]["supporting_seed_count"] == 4
    assert evidence["execution_shortfall"]["inapplicable_seeds"] == [9001]


def test_missing_episode_collection_does_not_mask_a_present_nonfinite_hit_rate():
    report = _complete_raw_report()
    late_dev = next(run for run in report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    late_dev["stages"][ArchitectureStage.A5_SHADOW.value].pop("position_episodes")  # type: ignore[index]
    late_dev["stages"][ArchitectureStage.A1_SIGN.value]["direction_hit_rate"] = float("nan")  # type: ignore[index]

    with pytest.raises(LateDevAttributionValidationError, match="A1_SIGN direction hit rate"):
        _build(report)


def test_present_non_mapping_cost_stress_is_invalid_not_structurally_incomplete():
    report = _complete_raw_report()
    late_dev = next(run for run in report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    late_dev["stages"][ArchitectureStage.A1_SIGN.value]["cost_stress"] = []  # type: ignore[index]

    with pytest.raises(LateDevAttributionValidationError, match="A1_SIGN cost stress"):
        _build(report)


def test_present_cost_stress_mapping_missing_15bp_entry_is_invalid():
    report = _complete_raw_report()
    late_dev = next(run for run in report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    late_dev["stages"][ArchitectureStage.A1_SIGN.value]["cost_stress"].pop("15")  # type: ignore[index]

    with pytest.raises(LateDevAttributionValidationError, match="A1_SIGN cost stress"):
        _build(report)


@pytest.mark.parametrize("fifteen_bp_entry", [[], {}], ids=["non_mapping", "missing_net_pnl"])
def test_present_15bp_cost_stress_entry_requires_a_mapping_net_pnl(fifteen_bp_entry: object):
    report = _complete_raw_report()
    late_dev = next(run for run in report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    late_dev["stages"][ArchitectureStage.A1_SIGN.value]["cost_stress"]["15"] = fifteen_bp_entry  # type: ignore[index]

    with pytest.raises(LateDevAttributionValidationError, match="A1_SIGN cost stress"):
        _build(report)


def test_confidence_failure_requires_equal_direction_hit_rates_and_nonpositive_score_pnl():
    report = _complete_raw_report()
    for run in report["runs"]:  # type: ignore[index]
        if run["window"] != "late_dev_2025":
            continue
        score = run["stages"][ArchitectureStage.A1_SCORE.value]  # type: ignore[index]
        score["direction_hit_rate"] = 0.6
        score["cost_stress"]["15"]["net_pnl"] = -10.0
        score["turnover_notional"] = 130.0 * 10_000.0 / 15.0

    result = _build(report)

    assert result["evidence"]["confidence_no_incremental_edge"] == {  # type: ignore[index]
        "status": "CONSISTENT",
        "supporting_seed_count": 5,
        "supporting_seeds": [42, 123, 456, 789, 1024],
        "insufficient_sample_seeds": [],
        "inapplicable_seeds": [],
    }


def test_cost_flip_requires_positive_baseline_and_nonpositive_15bp_pnl():
    report = _complete_raw_report()
    for run in report["runs"]:  # type: ignore[index]
        if run["window"] != "late_dev_2025":
            continue
        a5 = run["stages"][ArchitectureStage.A5_SHADOW.value]  # type: ignore[index]
        a5["cost_stress"]["15"]["net_pnl"] = 0.0
        a5["turnover_notional"] = 80_000.0

    result = _build(report)

    assert result["evidence"]["cost_flip"]["status"] == "CONSISTENT"  # type: ignore[index]
    assert result["evidence"]["cost_flip"]["supporting_seed_count"] == 5  # type: ignore[index]


def test_missing_a5_episode_collection_is_insufficient_not_invalid():
    report = _complete_raw_report(regime_localized_seed_count=4)
    late_dev = next(run for run in report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    late_dev["stages"][ArchitectureStage.A5_SHADOW.value].pop("position_episodes")  # type: ignore[index]

    result = _build(report)

    assert result["evidence"]["required_inputs_incomplete"] is True  # type: ignore[index]
    assert result["evidence"]["regime_localization"]["status"] == "INSUFFICIENT_SAMPLE"  # type: ignore[index]
    assert result["terminal_recommendation"] == "insufficient_evidence_no_strategy_change"
    assert result["promotion_state"] == "BLOCKED_PAUSE_PROMOTION_AND_TRAINING"


def test_regime_hypothesis_is_single_family_research_only_record():
    result = _build(_complete_raw_report(regime_localized_seed_count=4))

    hypothesis = build_regime_objective_hypothesis(result)

    assert hypothesis == {
        "schema_version": "quant-v2-regime-objective-hypothesis.v1",
        "research_only_non_promotable": True,
        "promotion_state": "BLOCKED_PAUSE_PROMOTION_AND_TRAINING",
        "failure_family": "regime_localization",
        "market_behavior_hypothesis": (
            "Late-development loss is concentrated in one observed regime while the other observed regimes "
            "remain net positive after the fixed 15bp turnover cost allocation."
        ),
        "excluded_prior_exploration_families": [
            "threshold sweeping",
            "leverage expansion",
            "donor continuation",
        ],
        "oos_only_falsification_gate": (
            "A separately authorized out-of-sample-only evaluation falsifies this hypothesis unless the same "
            "localized loss pattern recurs in at least four of five perturbation seeds."
        ),
    }


def test_cli_writes_complete_stop_donor_reports_without_hypothesis(tmp_path: Path):
    """A complete directional failure must publish diagnostics but no redesign artifact."""
    source = tmp_path / "architecture_attribution.json"
    source.write_text(
        json.dumps(_complete_raw_report(direction_hit_rate=0.47, a1_sign_15bp=-10.0)),
        encoding="utf-8",
    )

    completed = _run_cli(source, tmp_path / "out")

    output_dir = tmp_path / "out"
    report_path = output_dir / "late_dev_cost_attribution.json"
    markdown_path = output_dir / "late_dev_cost_attribution.md"
    assert completed.returncode == 0, completed.stderr
    assert json.loads(report_path.read_text(encoding="utf-8"))["terminal_recommendation"] == "stop_donor_training"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Identities" in markdown
    assert "## Five-seed bridges" in markdown
    assert "## Evidence" in markdown
    assert "## Regime, action, and episode summaries" in markdown
    assert "## Limitations" in markdown
    assert "stop_donor_training" in markdown
    assert not (output_dir / "regime_objective_redesign_from_late_dev.json").exists()
    assert not list(output_dir.glob("*.tmp"))


def test_cli_writes_hypothesis_only_for_regime_objective_redesign(tmp_path: Path):
    """A four-of-five localized failure is the sole hypothesis-publication path."""
    source = tmp_path / "architecture_attribution.json"
    source.write_text(json.dumps(_complete_raw_report(regime_localized_seed_count=4)), encoding="utf-8")

    completed = _run_cli(source, tmp_path / "out")

    hypothesis_path = tmp_path / "out" / "regime_objective_redesign_from_late_dev.json"
    assert completed.returncode == 0, completed.stderr
    hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
    assert hypothesis["failure_family"] == "regime_localization"
    assert hypothesis["excluded_prior_exploration_families"] == [
        "threshold sweeping",
        "leverage expansion",
        "donor continuation",
    ]
    assert "out-of-sample-only" in hypothesis["oos_only_falsification_gate"]
    assert "grid" not in json.dumps(hypothesis).lower()
    assert "command" not in json.dumps(hypothesis).lower()


def test_cli_removes_stale_hypothesis_when_recommendation_changes(tmp_path: Path):
    """A prior redesign artifact must not survive a later stop-donor diagnosis."""
    redesign_source = tmp_path / "redesign.json"
    redesign_source.write_text(json.dumps(_complete_raw_report(regime_localized_seed_count=4)), encoding="utf-8")
    stop_source = tmp_path / "stop.json"
    stop_source.write_text(
        json.dumps(_complete_raw_report(direction_hit_rate=0.47, a1_sign_15bp=-10.0)),
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    assert _run_cli(redesign_source, output_dir).returncode == 0
    assert (output_dir / "regime_objective_redesign_from_late_dev.json").exists()

    completed = _run_cli(stop_source, output_dir)

    assert completed.returncode == 0, completed.stderr
    assert not (output_dir / "regime_objective_redesign_from_late_dev.json").exists()


def test_cli_insufficient_valid_input_writes_reports_and_returns_one(tmp_path: Path):
    """Missing required diagnostics is valid evidence, not an invalid input error."""
    report = _complete_raw_report()
    late_dev = next(run for run in report["runs"] if run["window"] == "late_dev_2025")  # type: ignore[index]
    late_dev["stages"][ArchitectureStage.A5_SHADOW.value].pop("position_episodes")  # type: ignore[index]
    source = tmp_path / "architecture_attribution.json"
    source.write_text(json.dumps(report), encoding="utf-8")

    completed = _run_cli(source, tmp_path / "out")

    report_path = tmp_path / "out" / "late_dev_cost_attribution.json"
    assert completed.returncode == 1, completed.stderr
    assert json.loads(report_path.read_text(encoding="utf-8"))["terminal_recommendation"] == (
        "insufficient_evidence_no_strategy_change"
    )
    assert (tmp_path / "out" / "late_dev_cost_attribution.md").exists()
    assert not (tmp_path / "out" / "regime_objective_redesign_from_late_dev.json").exists()


@pytest.mark.parametrize("contents", ["{}", "not json"], ids=["invalid_envelope", "malformed_json"])
def test_cli_invalid_input_writes_nothing_and_returns_two(tmp_path: Path, contents: str):
    """Invalid raw input must not create an output directory or a partial report."""
    source = tmp_path / "bad.json"
    source.write_text(contents, encoding="utf-8")

    completed = _run_cli(source, tmp_path / "out")

    assert completed.returncode == 2
    assert not (tmp_path / "out").exists()
    assert "invalid input" in completed.stderr.lower()


def test_cli_records_exact_source_identity_and_publishes_only_complete_files(tmp_path: Path):
    """Published diagnostics must retain the bytes they diagnose and leave no staging files."""
    source = tmp_path / "architecture_attribution.json"
    source_bytes = json.dumps(_complete_raw_report(), indent=1).encode("utf-8")
    source.write_bytes(source_bytes)

    completed = _run_cli(source, tmp_path / "out")

    report = json.loads((tmp_path / "out" / "late_dev_cost_attribution.json").read_text(encoding="utf-8"))
    assert completed.returncode == 0, completed.stderr
    assert report["source"] == {
        "artifact": str(source),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "size_bytes": len(source_bytes),
    }
    assert not list((tmp_path / "out").glob("*.tmp"))


def test_cli_read_input_hashes_and_decodes_the_same_single_byte_sequence(monkeypatch: pytest.MonkeyPatch):
    """The source identity must describe precisely the bytes decoded into the report."""
    cli_module = _load_cli_module()
    source_bytes = json.dumps(_complete_raw_report()).encode("utf-8")
    calls = 0

    def read_bytes_once(_: Path) -> bytes:
        nonlocal calls
        calls += 1
        return source_bytes

    monkeypatch.setattr(Path, "read_bytes", read_bytes_once)

    report, source_sha256, source_size_bytes = cli_module._read_input(Path("ignored-by-controlled-read.json"))

    assert calls == 1
    assert report == json.loads(source_bytes)
    assert source_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert source_size_bytes == len(source_bytes)


def test_cli_atomic_publish_cleans_partial_temp_file_after_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A failed temp write must not leave a partial artifact beside a report."""
    cli_module = _load_cli_module()
    original_write_bytes = Path.write_bytes

    def write_partial_temp_then_fail(path: Path, content: bytes) -> int:
        if path.suffix == ".tmp":
            original_write_bytes(path, b"partial")
            raise OSError("simulated partial temporary write")
        return original_write_bytes(path, content)

    monkeypatch.setattr(Path, "write_bytes", write_partial_temp_then_fail)

    with pytest.raises(OSError, match="partial temporary write"):
        cli_module._publish_atomically(tmp_path / "out", {"late_dev_cost_attribution.json": b"complete"})

    assert not list((tmp_path / "out").glob("*.tmp"))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_cli_rejects_nonfinite_json_constants_without_writing_artifacts(tmp_path: Path, constant: str):
    """Non-finite JSON constants are invalid even when placed in an ignored raw field."""
    report = _complete_raw_report()
    report["ignored_nonfinite_probe"] = float(constant.replace("Infinity", "inf").replace("NaN", "nan"))
    source = tmp_path / "architecture_attribution.json"
    source.write_text(json.dumps(report), encoding="utf-8")

    completed = _run_cli(source, tmp_path / "out")

    assert completed.returncode == 2
    assert not (tmp_path / "out").exists()
    assert "invalid input" in completed.stderr.lower()
