from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    path = Path("scripts/analyze_baseline_gap_attribution.py")
    spec = importlib.util.spec_from_file_location("analyze_baseline_gap_attribution", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load analyze_baseline_gap_attribution")
    module = importlib.util.module_from_spec(spec)
    sys.modules["analyze_baseline_gap_attribution"] = module
    spec.loader.exec_module(module)
    return module


def test_baseline_gap_attribution_flags_churn_and_fee_gap() -> None:
    module = _load_module()
    payload = {
        "window_reports": [
            {
                "variant": "current",
                "window_label": "dev",
                "metrics": {
                    "sharpe": 1.2,
                    "net_pnl": 100.0,
                    "max_drawdown": 0.2,
                    "profit_factor": 0.8,
                    "trade_count": 12,
                    "turnover_per_day": 10.0,
                    "fee_drag_pct": 0.02,
                },
                "decision_attribution_ledger": {
                    "micro_state_summary": {
                        "chop_neutral": {
                            "bars": 2,
                            "estimated_equity_pnl": 1.0,
                            "fees": 2.0,
                            "turnover_notional": 100.0,
                            "target_actions": {"flip": 1},
                            "execution_actions": {"flip": 1},
                        }
                    },
                    "playbook_summary": {
                        "RANGE_LIGHT": {
                            "bars": 2,
                            "estimated_equity_pnl": 1.0,
                            "fees": 2.0,
                            "turnover_notional": 100.0,
                            "target_actions": {"flip": 1},
                            "execution_actions": {"flip": 1},
                        }
                    },
                    "top_reason_codes_by_micro_state": {"chop_neutral": {"NO_DIRECTION": 2}},
                },
            },
            {
                "variant": "paulwei_baseline",
                "window_label": "dev",
                "metrics": {
                    "sharpe": 1.0,
                    "net_pnl": 80.0,
                    "max_drawdown": 0.1,
                    "profit_factor": 0.9,
                    "trade_count": 10,
                    "turnover_per_day": 8.0,
                    "fee_drag_pct": 0.01,
                },
                "decision_attribution_ledger": {
                    "micro_state_summary": {
                        "chop_neutral": {
                            "bars": 2,
                            "estimated_equity_pnl": 2.0,
                            "fees": 1.0,
                            "turnover_notional": 60.0,
                            "target_actions": {"hold": 1},
                            "execution_actions": {"unchanged": 1},
                        }
                    },
                    "playbook_summary": {
                        "RANGE_LIGHT": {
                            "bars": 2,
                            "estimated_equity_pnl": 2.0,
                            "fees": 1.0,
                            "turnover_notional": 60.0,
                            "target_actions": {"hold": 1},
                            "execution_actions": {"unchanged": 1},
                        }
                    },
                    "top_reason_codes_by_micro_state": {"chop_neutral": {"NO_DIRECTION": 1}},
                },
            },
        ]
    }

    result = module.build_baseline_gap_attribution(payload)

    assert result["split_contract"]["test_evaluated"] is False
    assert result["aggregate"]["metric_delta_current_minus_baseline"]["trade_count"] == 2
    assert "current_trade_count_above_baseline" in result["verdict"]["reason_codes"]
    assert "current_fee_drag_above_baseline" in result["verdict"]["reason_codes"]
    assert result["window_comparisons"][0]["micro_state_deltas"]["chop_neutral"]["turnover_delta"] == 40.0
    assert result["window_comparisons"][0]["playbook_deltas"]["RANGE_LIGHT"]["turnover_delta"] == 40.0


def test_baseline_gap_attribution_blocks_negative_net_pnl_even_when_churn_improves() -> None:
    module = _load_module()
    payload = {
        "window_reports": [
            {
                "variant": "current",
                "window_label": "dev",
                "metrics": {
                    "net_pnl": 70.0,
                    "profit_factor": 1.1,
                    "trade_count": 8,
                    "turnover_per_day": 6.0,
                    "fee_drag_pct": 0.005,
                },
                "decision_attribution_ledger": {"micro_state_summary": {}, "playbook_summary": {}},
            },
            {
                "variant": "paulwei_baseline",
                "window_label": "dev",
                "metrics": {
                    "net_pnl": 80.0,
                    "profit_factor": 1.0,
                    "trade_count": 10,
                    "turnover_per_day": 8.0,
                    "fee_drag_pct": 0.006,
                },
                "decision_attribution_ledger": {"micro_state_summary": {}, "playbook_summary": {}},
            },
        ]
    }

    result = module.build_baseline_gap_attribution(payload)

    assert "current_net_pnl_below_baseline" in result["verdict"]["reason_codes"]
    assert result["verdict"]["next_action"] == "repair_entry_exit_quality_before_expanding_search"
