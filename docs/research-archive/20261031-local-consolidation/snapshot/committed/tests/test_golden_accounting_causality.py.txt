from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path("scripts/probe_golden_accounting_causality.py")


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("probe_golden_accounting_causality", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_captures_exact_gate_c_fixture_and_semantic_ledgers() -> None:
    probe = _load_probe_module()

    trace = probe.run_source_probe(Path.cwd())

    assert trace["fixture"]["test_source_sha256"] == (
        "be9ffbe0bf33183500c8e579ef96b8a6bfd3019d33b9ac26ee6078f6638ca748"
    )
    assert trace["fixture"]["cwd_isolated"] is True
    assert trace["fixture"]["row_counts"] == {"daily": 90, "four_hour": 540, "funding": 270, "open_interest": 540}
    assert trace["fees"]
    assert trace["reports"]
    assert trace["targets"]
    assert trace["risk_snapshots"]
    assert trace["exits"]
    assert trace["semantic_result"]["position_episodes"]
    assert "decision_snapshots" in trace["semantic_result"]
    assert trace["semantic_result"]["cycle_context"]
    assert trace["semantic_result"]["terminated_early"] is False
    assert trace["semantic_result"]["termination_reason"] is None


def test_comparison_attributes_fee_clamp_before_downstream_divergence() -> None:
    probe = _load_probe_module()
    common_fixture = {"input_sha256": "sha256:frozen", "test_source_sha256": "sha256:test"}
    report = {
        "transaction_time_ns": 10,
        "fee": 2.0,
        "filled_qty": 3.0,
        "avg_price": 40.0,
        "side": 1,
    }
    common_fee = {
        "index": 0,
        "report": report,
        "fee": 2.0,
        "before": {"cash": -20.0, "equity": 100.0, "high_water_mark": 100.0, "positions": {"BTC": 3.0}},
    }
    baseline = {
        "fixture": common_fixture,
        "fees": [
            {
                **common_fee,
                "after": {"cash": 0.0, "equity": 120.0, "high_water_mark": 120.0, "positions": {"BTC": 3.0}},
            }
        ],
        "risk_snapshots": [{"ts_ns": 20, "equity": 120.0, "flags": ["OK"]}],
        "targets": [{"cutoff_time_ns": 20, "target_qty": 4.0}],
        "reports": [report, {**report, "transaction_time_ns": 20, "filled_qty": 4.0}],
        "exits": [{"cutoff_time_ns": 30, "exit_type": "TRAILING"}],
        "semantic_result": {"metrics": {"net_pnl": 20.0}},
    }
    repaired = {
        "fixture": common_fixture,
        "fees": [
            {
                **common_fee,
                "after": {"cash": -22.0, "equity": 98.0, "high_water_mark": 100.0, "positions": {"BTC": 3.0}},
            }
        ],
        "risk_snapshots": [{"ts_ns": 20, "equity": 98.0, "flags": ["DRAWDOWN"]}],
        "targets": [{"cutoff_time_ns": 20, "target_qty": 2.0}],
        "reports": [report, {**report, "transaction_time_ns": 20, "filled_qty": 2.0}],
        "exits": [{"cutoff_time_ns": 30, "exit_type": "TIME_DECAY"}],
        "semantic_result": {"metrics": {"net_pnl": -2.0}},
    }

    comparison = probe.compare_source_traces(baseline, repaired)

    assert comparison["causal_verdict"] == "FEE_CLAMP_IS_FIRST_DIVERGENCE"
    assert comparison["first_fee_divergence"]["baseline"]["after"]["cash"] == 0.0
    assert comparison["first_fee_divergence"]["repaired"]["after"]["cash"] == -22.0
    assert comparison["first_risk_divergence"]["baseline"]["ts_ns"] == 20
    assert comparison["first_target_divergence"]["repaired"]["target_qty"] == 2.0
    assert comparison["first_report_divergence"]["index"] == 1
    assert comparison["first_exit_divergence"]["repaired"]["exit_type"] == "TIME_DECAY"


def test_comparison_attributes_canonical_mark_as_first_accounting_divergence() -> None:
    probe = _load_probe_module()
    common_fixture = {"input_sha256": "sha256:frozen", "test_source_sha256": "sha256:test"}
    report = {
        "transaction_time_ns": 10,
        "fee": 2.0,
        "filled_qty": 3.0,
        "avg_price": 40.0,
        "side": 1,
    }
    common_state = {
        "cash": 50.0,
        "positions": {"BTC": 3.0},
        "latest_risk_snapshot": None,
    }
    baseline = {
        "fixture": common_fixture,
        "fees": [
            {
                "index": 0,
                "report": report,
                "fee": 2.0,
                "before": {
                    **common_state,
                    "equity": 100.0,
                    "high_water_mark": 100.0,
                    "marks": {"BTC": 40.0},
                },
                "after": {
                    **common_state,
                    "cash": 48.0,
                    "equity": 98.0,
                    "high_water_mark": 100.0,
                    "marks": {"BTC": 40.0},
                },
            }
        ],
        "risk_snapshots": [{"ts_ns": 20, "equity": 100.0, "flags": ["OK"]}],
        "targets": [{"cutoff_time_ns": 20, "target_qty": 3.0}],
        "reports": [report],
        "exits": [{"cutoff_time_ns": 20, "exit_type": "NONE"}],
        "semantic_result": {"metrics": {"net_pnl": 0.0}},
    }
    repaired = {
        **baseline,
        "fees": [
            {
                "index": 0,
                "report": report,
                "fee": 2.0,
                "before": {
                    **common_state,
                    "equity": 97.0,
                    "high_water_mark": 100.0,
                    "marks": {"BTC": 39.0},
                },
                "after": {
                    **common_state,
                    "cash": 48.0,
                    "equity": 95.0,
                    "high_water_mark": 100.0,
                    "marks": {"BTC": 39.0},
                },
            }
        ],
    }

    comparison = probe.compare_source_traces(baseline, repaired)

    assert comparison["causal_verdict"] == "ACCOUNTING_MARK_STATE_IS_FIRST_DIVERGENCE"
    assert comparison["fixture_identity_equal"] is True
    assert comparison["first_fee_divergence"]["baseline"]["report"] == report
    assert comparison["first_fee_divergence"]["baseline"]["before"]["cash"] == 50.0
    assert comparison["first_fee_divergence"]["repaired"]["before"]["cash"] == 50.0
    assert comparison["first_fee_divergence"]["baseline"]["before"]["marks"] == {"BTC": 40.0}
    assert comparison["first_fee_divergence"]["repaired"]["before"]["marks"] == {"BTC": 39.0}
