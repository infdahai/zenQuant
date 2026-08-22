from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from zenquant.domain import Timeframe
from zenquant.research.gate_v2 import build_campaign_verdict, evaluate_seed


def _load_rescore_script():
    path = Path("scripts/run_gate_v2_rescore.py")
    spec = importlib.util.spec_from_file_location("gate_v2_rescore_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RESCORE_SCRIPT = _load_rescore_script()
load_candidate_manifest = RESCORE_SCRIPT.load_candidate_manifest


DAY_NS = 86_400_000_000_000


def _report(
    variant: str,
    window_label: str,
    *,
    days: int = 365,
    net_pnl: float = 10_000.0,
    sharpe: float = 1.0,
    max_drawdown: float = 0.10,
    profit_factor: float = 1.4,
    turnover_per_day: float = 1_000.0,
    trade_count: int = 100,
    flip_count: int = 10,
    reduce_count: int = 40,
    reduce_profit_factor: float = 1.5,
) -> dict:
    return {
        "variant": variant,
        "window_label": window_label,
        "metrics": {
            "start_ns": 0,
            "end_ns": days * DAY_NS,
            "net_pnl": net_pnl,
            "gross_pnl": net_pnl + 1_000.0,
            "total_fees": 1_000.0,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "profit_factor": profit_factor,
            "fee_drag_pct": 0.05,
            "turnover_per_day": turnover_per_day,
            "trade_count": trade_count,
            "action_trade_counts": {
                "add": 10,
                "entry": 20,
                "exit": 20,
                "flip": flip_count,
                "reduce": reduce_count,
            },
            "action_profit_factor": {"reduce": reduce_profit_factor},
        },
    }


def _seed_payload(seed: int, *, candidate: str = "candidate") -> dict:
    reports = [
        _report(
            candidate,
            "train_full",
            days=1_461,
            net_pnl=100_000.0,
            turnover_per_day=10_000.0,
        ),
        *[_report(candidate, str(year)) for year in (2020, 2021, 2022, 2023)],
        _report(candidate, "late_dev_2025"),
    ]
    return {"seed": seed, "comparison_payload": {"window_reports": reports}}


def _candidate_result(payload: dict, candidate: str = "candidate") -> dict:
    return evaluate_seed(payload, candidate_names=[candidate])["candidates"][candidate]


def test_gate_v2_accepts_four_of_five_clean_seeds() -> None:
    payloads = [_seed_payload(seed) for seed in (42, 123, 456, 789, 1024)]
    payloads[-1]["comparison_payload"]["window_reports"][1]["metrics"]["max_drawdown"] = 0.25

    verdict = build_campaign_verdict(payloads, candidate_names=["candidate"])

    assert verdict["candidates"]["candidate"]["passing_seeds"] == 4
    assert verdict["candidates"]["candidate"]["gate_passed"] is True
    assert verdict["terminal_recommendation"] == "bounded_search_allowed"


def test_gate_v2_rejects_unbounded_equity_multiple() -> None:
    payload = _seed_payload(42)
    payload["comparison_payload"]["window_reports"][0]["metrics"]["net_pnl"] = 1_000_000.0

    result = _candidate_result(payload)

    assert result["passed"] is False
    assert "capacity_model_required_for_equity_multiple_above_10" in result["hard_blockers"]


def test_gate_v2_rejects_two_negative_regime_windows() -> None:
    payload = _seed_payload(42)
    reports = payload["comparison_payload"]["window_reports"]
    reports[1]["metrics"]["net_pnl"] = -1_000.0
    reports[2]["metrics"]["net_pnl"] = -2_000.0

    result = _candidate_result(payload)

    assert result["passed"] is False
    assert "more_than_one_negative_regime_window" in result["hard_blockers"]


def test_gate_v2_applies_fifteen_bp_turnover_cost() -> None:
    payload = _seed_payload(42)

    result = _candidate_result(payload)

    train = result["windows"]["train_full"]
    assert train["cost_stress"]["15"]["additional_cost"] == 21_915.0
    assert train["cost_stress"]["15"]["stressed_net_pnl"] == 78_085.0


def test_gate_v2_keeps_one_to_five_daily_switches_soft() -> None:
    payload = _seed_payload(42)
    train = payload["comparison_payload"]["window_reports"][0]
    train["metrics"]["end_ns"] = 10 * DAY_NS
    train["metrics"]["action_trade_counts"]["flip"] = 20

    result = _candidate_result(payload)

    assert "position_switches_between_1_and_5_per_day" in result["soft_flags"]
    assert "position_switches_above_5_per_day" not in result["hard_blockers"]


def test_gate_v2_rejects_more_than_five_daily_switches() -> None:
    payload = _seed_payload(42)
    train = payload["comparison_payload"]["window_reports"][0]
    train["metrics"]["end_ns"] = 10 * DAY_NS
    train["metrics"]["action_trade_counts"]["flip"] = 51

    result = _candidate_result(payload)

    assert result["passed"] is False
    assert "position_switches_above_5_per_day" in result["hard_blockers"]


def test_gate_v2_fails_closed_on_missing_required_metrics() -> None:
    payload = deepcopy(_seed_payload(42))
    del payload["comparison_payload"]["window_reports"][0]["metrics"]["turnover_per_day"]

    result = _candidate_result(payload)

    assert result["passed"] is False
    assert "train_full:missing_required_metric:turnover_per_day" in result["hard_blockers"]


def test_gate_v2_rejects_zero_trade_windows_explicitly() -> None:
    payload = _seed_payload(42)
    payload["comparison_payload"]["window_reports"][1]["metrics"]["trade_count"] = 0

    result = _candidate_result(payload)

    assert result["passed"] is False
    assert "2020:zero_trade_window" in result["hard_blockers"]


def _write_manifest_fixture(tmp_path: Path, source_payload: dict, *, duplicate: bool = False) -> Path:
    source = tmp_path / "source.json"
    source.write_text(json.dumps(source_payload), encoding="utf-8")
    entries = [
        {"name": "paulwei_baseline", "kind": "builtin"},
        {
            "name": "ranked_candidate",
            "kind": "ranked_params",
            "source": str(source),
            "selector": "train_candidate_diagnostics.top_train_trial_diagnostics",
            "rank": 2,
        },
    ]
    if duplicate:
        entries.append({"name": "ranked_candidate", "kind": "builtin"})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"candidates": entries}), encoding="utf-8")
    return manifest


def _safe_source_payload() -> dict:
    return {
        "evaluation_role": "development",
        "test_evaluated": False,
        "test_comparison_payload": None,
        "train_candidate_diagnostics": {
            "top_train_trial_diagnostics": [
                {"params": {"max_leverage": 1.0}},
                {"params": {"max_leverage": 2.0}},
            ]
        },
    }


def test_manifest_loads_ranked_params_from_train_dev_report(tmp_path: Path) -> None:
    records = load_candidate_manifest(_write_manifest_fixture(tmp_path, _safe_source_payload()))

    assert [record.name for record in records] == ["paulwei_baseline", "ranked_candidate"]
    assert records[0].params is None
    assert records[1].params == {"max_leverage": 2.0}


def test_manifest_rejects_test_evaluated_source(tmp_path: Path) -> None:
    payload = _safe_source_payload()
    payload["test_evaluated"] = True

    with pytest.raises(ValueError, match="frozen_test_source_rejected"):
        load_candidate_manifest(_write_manifest_fixture(tmp_path, payload))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evaluation_role", "acceptance"),
        ("test_comparison_payload", {"window_reports": []}),
    ],
)
def test_manifest_rejects_acceptance_or_test_comparison_payload(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload = _safe_source_payload()
    payload[field] = value

    with pytest.raises(ValueError, match="frozen_test_source_rejected"):
        load_candidate_manifest(_write_manifest_fixture(tmp_path, payload))


def test_manifest_requires_unique_candidate_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate_candidate_name:ranked_candidate"):
        load_candidate_manifest(_write_manifest_fixture(tmp_path, _safe_source_payload(), duplicate=True))


def test_windows_include_train_years_and_late_dev_but_not_test() -> None:
    windows = RESCORE_SCRIPT.gate_v2_windows()

    assert [window.label for window in windows] == [
        "train_full",
        "2020",
        "2021",
        "2022",
        "2023",
        "late_dev_2025",
    ]
    test_start_ns = RESCORE_SCRIPT.default_btc_split_spec().test.start_ns
    assert all(window.end_ns is not None and window.end_ns < test_start_ns for window in windows)


def test_perturbations_are_exactly_the_five_fixed_seeds() -> None:
    perturbations = RESCORE_SCRIPT.gate_v2_perturbations()

    assert [item.seed for item in perturbations] == [42, 123, 456, 789, 1024]
    assert {(item.price_noise_pct, item.volume_noise_pct, item.bar_dropout_pct) for item in perturbations} == {
        (0.001, 0.05, 0.02)
    }


def test_base_config_replays_the_authoritative_training_timeframe_contract() -> None:
    config = RESCORE_SCRIPT.build_base_config(root_dir=Path("data/raw/binance"), symbol="BTCUSDT")

    assert config.execution_timeframe == Timeframe.TF_5M
    assert config.context_timeframe == Timeframe.TF_1H
    assert config.strategy_config is not None
    assert config.strategy_config.execution_timeframe == Timeframe.TF_5M


def test_report_declares_no_training_and_no_test_access() -> None:
    report = RESCORE_SCRIPT.finalize_report(
        {
            "seed_count": 5,
            "candidates": {},
            "seed_results": [],
            "terminal_recommendation": "design_regime_router_before_more_training",
        },
        candidate_names=["paulwei_baseline"],
    )

    assert report["train_search_executed"] is False
    assert report["optuna_search_executed"] is False
    assert report["rl_search_executed"] is False
    assert report["full_training_executed"] is False
    assert report["test_evaluated"] is False
    assert report["test_access_allowed"] is False
    assert report["candidate_names"] == ["paulwei_baseline"]


def test_markdown_contains_terminal_recommendation_and_candidate_matrix() -> None:
    report = RESCORE_SCRIPT.finalize_report(
        {
            "seed_count": 5,
            "min_passing_seeds": 4,
            "candidates": {
                "candidate": {
                    "passing_seeds": 3,
                    "seed_count": 5,
                    "gate_passed": False,
                    "hard_blocker_counts": {"worst_regime_sharpe_below_minus_2": 5},
                    "soft_flag_counts": {},
                }
            },
            "seed_results": [],
            "terminal_recommendation": "design_regime_router_before_more_training",
        },
        candidate_names=["candidate"],
    )

    markdown = RESCORE_SCRIPT.render_markdown(report)

    assert "design_regime_router_before_more_training" in markdown
    assert "| candidate | 3/5 | false |" in markdown.lower()
