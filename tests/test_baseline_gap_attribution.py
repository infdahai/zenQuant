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


def test_baseline_gap_attribution_builds_strength_profile_from_gap_and_public_behavior() -> None:
    module = _load_module()
    payload = {
        "window_reports": [
            {
                "variant": "current",
                "window_label": "dev",
                "metrics": {
                    "sharpe": 1.4,
                    "net_pnl": 120.0,
                    "max_drawdown": 0.08,
                    "profit_factor": 0.8,
                    "trade_count": 12,
                    "turnover_per_day": 20.0,
                    "fee_drag_pct": 0.01,
                },
                "decision_attribution_ledger": {
                    "micro_state_summary": {
                        "chop_neutral": {
                            "bars": 5,
                            "estimated_equity_pnl": 9.0,
                            "fees": 2.0,
                            "turnover_notional": 100.0,
                        }
                    },
                    "playbook_summary": {
                        "RANGE_LIGHT": {
                            "bars": 5,
                            "estimated_equity_pnl": 11.0,
                            "fees": 2.0,
                            "turnover_notional": 100.0,
                        }
                    },
                },
            },
            {
                "variant": "paulwei_baseline",
                "window_label": "dev",
                "metrics": {
                    "sharpe": 1.0,
                    "net_pnl": 100.0,
                    "max_drawdown": 0.1,
                    "profit_factor": 0.9,
                    "trade_count": 10,
                    "turnover_per_day": 12.0,
                    "fee_drag_pct": 0.01,
                },
                "decision_attribution_ledger": {
                    "micro_state_summary": {
                        "chop_neutral": {
                            "bars": 5,
                            "estimated_equity_pnl": 4.0,
                            "fees": 1.0,
                            "turnover_notional": 50.0,
                        }
                    },
                    "playbook_summary": {
                        "RANGE_LIGHT": {
                            "bars": 5,
                            "estimated_equity_pnl": 4.0,
                            "fees": 1.0,
                            "turnover_notional": 50.0,
                        }
                    },
                },
            },
        ]
    }

    result = module.build_baseline_gap_attribution(
        payload,
        paulwei_behavior_profile={
            "btc_notional_share": 0.84,
            "position_switches_per_30d": 2.4,
            "protective_exit_profile": {"protective_reduce_share": 0.89},
            "recovery_profile": {"kill_recovery_style": "gradual_ramp_after_cooldown", "median_recovery_days": 9.0},
        },
        paulwei_episode_stats={
            "available": True,
            "closed_episodes": 20,
            "win_rate": 0.6,
            "median_duration_hours": 12.0,
            "median_adds_per_episode": 10.0,
            "median_reduces_per_episode": 9.0,
            "side_profile": {
                "long": {"win_rate": 0.65},
                "short": {"win_rate": 0.55},
            },
        },
    )

    current_codes = {item["code"] for item in result["strength_profile"]["current_strengths"]}
    paulwei_codes = {item["code"] for item in result["strength_profile"]["paulwei_baseline_strengths"]}

    assert {"higher_net_pnl", "higher_sharpe", "lower_drawdown", "range_light_pnl_engine"} <= current_codes
    assert {"lower_trade_count", "lower_turnover", "higher_profit_factor"} <= paulwei_codes
    assert {"protective_reduce_bias", "staged_build_and_unwind"} <= paulwei_codes
    assert (
        "do_not_broadly_gate_range_light_or_chop_neutral_intent" in result["strength_profile"]["research_implications"]
    )


def test_build_paulwei_episode_stats_reads_closed_symbol_cadence(tmp_path: Path) -> None:
    module = _load_module()
    catalog = tmp_path / "episodes.csv"
    catalog.write_text(
        "\n".join(
            [
                "symbol,side,closed,estimated_return_bps,duration_hours,add_events,reduce_events,entry_notional,flip_exit,flip_entry",
                "XBTUSD,long,True,100,12,10,8,1000,False,False",
                "XBTUSD,short,True,-20,4,2,3,500,True,True",
                "ETHUSD,long,True,300,20,15,10,700,False,False",
                "XBTUSD,long,False,200,6,1,1,300,False,False",
            ]
        ),
        encoding="utf-8",
    )

    stats = module.build_paulwei_episode_stats(catalog)

    assert stats["available"] is True
    assert stats["closed_episodes"] == 2
    assert stats["win_rate"] == 0.5
    assert stats["median_return_bps"] == 40.0
    assert stats["side_profile"]["long"]["episodes"] == 1
