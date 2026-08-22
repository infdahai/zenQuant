"""Contract tests for the frozen late-development attribution envelope."""

from __future__ import annotations

from copy import deepcopy

import pytest

from zenquant.research.architecture_experiment import (
    ArchitectureStage,
    load_architecture_experiment_manifest,
)
from zenquant.research.late_dev_cost_attribution import (
    LateDevAttributionValidationError,
    build_late_dev_cost_attribution,
    canonical_late_dev_attribution_bytes,
)


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


def _complete_raw_report() -> dict[str, object]:
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
    return {
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
