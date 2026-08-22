"""Pure Gate v2 scoring for fixed train/dev candidate replays."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

STARTING_EQUITY = 100_000.0
HARD_DRAWDOWN_CAP = 0.20
MIN_WORST_REGIME_SHARPE = -2.0
MIN_PROFIT_FACTOR_AFTER_FEE = 1.0
MIN_REDUCE_PROFIT_FACTOR = 1.0
MAX_POSITION_SWITCHES_PER_DAY = 5.0
SOFT_POSITION_SWITCHES_PER_DAY = 1.0
MAX_EQUITY_MULTIPLE_WITHOUT_CAPACITY_MODEL = 10.0
MIN_PASSING_SEEDS = 4
REQUIRED_WINDOWS = ("train_full", "2020", "2021", "2022", "2023", "late_dev_2025")
REGIME_WINDOWS = ("2020", "2021", "2022", "2023")
DEFAULT_COST_BPS = (0.0, 5.0, 10.0, 15.0)
REQUIRED_METRICS = (
    "start_ns",
    "end_ns",
    "net_pnl",
    "sharpe",
    "max_drawdown",
    "profit_factor",
    "fee_drag_pct",
    "turnover_per_day",
    "trade_count",
    "action_trade_counts",
    "action_profit_factor",
)


def _cost_key(cost_bps: float) -> str:
    return str(int(cost_bps)) if float(cost_bps).is_integer() else str(cost_bps)


def _window_summary(metrics: dict[str, Any], cost_bps: Sequence[float]) -> dict[str, Any]:
    duration_days = max(1.0, (float(metrics["end_ns"]) - float(metrics["start_ns"])) / 86_400_000_000_000)
    action_counts = metrics["action_trade_counts"]
    flip_count = float(action_counts.get("flip", 0.0) or 0.0)
    reduce_count = float(action_counts.get("reduce", 0.0) or 0.0)
    trade_count = float(metrics["trade_count"])
    net_pnl = float(metrics["net_pnl"])
    turnover_notional = float(metrics["turnover_per_day"]) * duration_days
    cost_stress: dict[str, dict[str, float]] = {}
    for extra_cost_bps in cost_bps:
        additional_cost = turnover_notional * float(extra_cost_bps) / 10_000.0
        cost_stress[_cost_key(float(extra_cost_bps))] = {
            "additional_cost": round(additional_cost, 6),
            "stressed_net_pnl": round(net_pnl - additional_cost, 6),
        }
    protective_denominator = reduce_count + flip_count
    return {
        "duration_days": round(duration_days, 6),
        "net_pnl": net_pnl,
        "sharpe": float(metrics["sharpe"]),
        "max_drawdown": float(metrics["max_drawdown"]),
        "profit_factor": float(metrics["profit_factor"]),
        "profit_factor_after_fee": round(
            float(metrics["profit_factor"]) * max(0.0, 1.0 - float(metrics["fee_drag_pct"])),
            6,
        ),
        "reduce_action_profit_factor": float(metrics["action_profit_factor"].get("reduce", 0.0) or 0.0),
        "reduce_action_count": reduce_count,
        "trade_count_per_day": round(trade_count / duration_days, 6),
        "position_switches_per_day": round(flip_count / duration_days, 6),
        "protective_reduce_share": round(reduce_count / protective_denominator, 6)
        if protective_denominator > 0
        else 0.0,
        "equity_multiple": round((STARTING_EQUITY + net_pnl) / STARTING_EQUITY, 6),
        "cost_stress": cost_stress,
    }


def _candidate_seed_result(
    reports: Sequence[dict[str, Any]],
    *,
    candidate_name: str,
    cost_bps: Sequence[float],
) -> dict[str, Any]:
    hard_blockers: list[str] = []
    soft_flags: list[str] = []
    by_label = {
        str(report.get("window_label")): report for report in reports if report.get("variant") == candidate_name
    }
    windows: dict[str, dict[str, Any]] = {}

    for label in REQUIRED_WINDOWS:
        report = by_label.get(label)
        if report is None:
            hard_blockers.append(f"missing_required_window:{label}")
            continue
        if report.get("comparison_error"):
            hard_blockers.append(f"{label}:comparison_error")
            continue
        metrics = report.get("metrics")
        if not isinstance(metrics, dict):
            hard_blockers.append(f"{label}:missing_metrics")
            continue
        missing = [field for field in REQUIRED_METRICS if field not in metrics]
        if missing:
            hard_blockers.extend(f"{label}:missing_required_metric:{field}" for field in missing)
            continue
        windows[label] = _window_summary(metrics, cost_bps)
        if float(metrics["trade_count"]) <= 0.0:
            hard_blockers.append(f"{label}:zero_trade_window")

    if len(windows) == len(REQUIRED_WINDOWS):
        if any(window["max_drawdown"] >= HARD_DRAWDOWN_CAP for window in windows.values()):
            hard_blockers.append("drawdown_at_or_above_20pct")

        negative_regime_windows = sum(1 for label in REGIME_WINDOWS if windows[label]["net_pnl"] <= 0.0)
        if negative_regime_windows > 1:
            hard_blockers.append("more_than_one_negative_regime_window")

        if min(windows[label]["sharpe"] for label in (*REGIME_WINDOWS, "late_dev_2025")) < MIN_WORST_REGIME_SHARPE:
            hard_blockers.append("worst_regime_sharpe_below_minus_2")
        if windows["late_dev_2025"]["net_pnl"] <= 0.0:
            hard_blockers.append("late_dev_net_pnl_not_positive")

        train = windows["train_full"]
        if train["profit_factor_after_fee"] < MIN_PROFIT_FACTOR_AFTER_FEE:
            hard_blockers.append("train_profit_factor_after_fee_below_1")
        if train["reduce_action_count"] > 0 and train["reduce_action_profit_factor"] < MIN_REDUCE_PROFIT_FACTOR:
            hard_blockers.append("train_reduce_action_profit_factor_below_1")
        if train["equity_multiple"] > MAX_EQUITY_MULTIPLE_WITHOUT_CAPACITY_MODEL:
            hard_blockers.append("capacity_model_required_for_equity_multiple_above_10")

        switch_rate = train["position_switches_per_day"]
        if switch_rate > MAX_POSITION_SWITCHES_PER_DAY:
            hard_blockers.append("position_switches_above_5_per_day")
        elif switch_rate > SOFT_POSITION_SWITCHES_PER_DAY:
            soft_flags.append("position_switches_between_1_and_5_per_day")
        if train["trade_count_per_day"] > 1.0:
            soft_flags.append("total_fills_above_1_per_day")
        if train["protective_reduce_share"] < 0.75:
            soft_flags.append("protective_reduce_share_below_0_75")

        max_cost_key = _cost_key(max(float(value) for value in cost_bps))
        if train["cost_stress"][max_cost_key]["stressed_net_pnl"] <= 0.0:
            hard_blockers.append(f"train_not_positive_under_{max_cost_key}bp_extra_cost")
        if windows["late_dev_2025"]["cost_stress"][max_cost_key]["stressed_net_pnl"] <= 0.0:
            hard_blockers.append(f"late_dev_not_positive_under_{max_cost_key}bp_extra_cost")

    return {
        "candidate": candidate_name,
        "passed": not hard_blockers,
        "hard_blockers": list(dict.fromkeys(hard_blockers)),
        "soft_flags": list(dict.fromkeys(soft_flags)),
        "windows": windows,
    }


def evaluate_seed(
    payload: dict[str, Any],
    *,
    candidate_names: Sequence[str],
    cost_bps: Sequence[float] = DEFAULT_COST_BPS,
) -> dict[str, Any]:
    """Evaluate every requested candidate for one deterministic seed."""
    comparison_payload = payload.get("comparison_payload") or {}
    reports = comparison_payload.get("window_reports") or []
    return {
        "seed": int(payload["seed"]),
        "candidates": {
            name: _candidate_seed_result(reports, candidate_name=name, cost_bps=cost_bps) for name in candidate_names
        },
    }


def build_campaign_verdict(
    seed_payloads: Sequence[dict[str, Any]],
    *,
    candidate_names: Sequence[str],
) -> dict[str, Any]:
    """Aggregate fixed-seed results into the Gate v2 campaign verdict."""
    seed_results = [evaluate_seed(payload, candidate_names=candidate_names) for payload in seed_payloads]
    candidates: dict[str, dict[str, Any]] = {}
    for name in candidate_names:
        results = [seed_result["candidates"][name] for seed_result in seed_results]
        passing_seeds = sum(1 for result in results if result["passed"])
        candidates[name] = {
            "passing_seeds": passing_seeds,
            "seed_count": len(results),
            "gate_passed": passing_seeds >= MIN_PASSING_SEEDS,
            "hard_blocker_counts": _count_values(result["hard_blockers"] for result in results),
            "soft_flag_counts": _count_values(result["soft_flags"] for result in results),
        }
    any_candidate_passed = any(candidate["gate_passed"] for candidate in candidates.values())
    return {
        "seed_count": len(seed_results),
        "min_passing_seeds": MIN_PASSING_SEEDS,
        "candidates": candidates,
        "seed_results": seed_results,
        "terminal_recommendation": "bounded_search_allowed"
        if any_candidate_passed
        else "design_regime_router_before_more_training",
    }


def _count_values(groups: Sequence[Sequence[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for group in groups:
        for value in group:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
