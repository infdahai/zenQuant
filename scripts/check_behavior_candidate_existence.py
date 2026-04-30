#!/usr/bin/env python3
"""Run one behavior-family candidate existence check.

This is intentionally not an optimizer. It compares:

  current / paulwei_baseline / one candidate

on dev and hostile windows, then fails closed unless the candidate is not
worse than both anchors on hostile robustness, drawdown, turnover, and fees.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.comparison import (
    behavior_candidate_existence_variant_specs,
    build_markdown_summary,
    compare_variants,
    default_comparison_windows,
)
from zenquant.research.splits import default_btc_split_spec

SUPPORTED_BEHAVIORS = (
    "short_restraint",
    "long_dca_cap",
    "protective_exit_speed",
    "flip_confidence",
    "vol_shock_compression",
    "ahr999_slow_risk_modifier",
    "hostile_aware_behavior_router",
    "squeeze_flip_flatten_only",
    "squeeze_flip_cooldown",
    "squeeze_conditional_cooldown",
    "squeeze_conditional_cooldown_dd025_b2",
    "squeeze_conditional_cooldown_dd040_b1",
    "squeeze_conditional_cooldown_dd060_b1",
    "squeeze_conditional_cooldown_dd060_chop_cancel_b1",
    "squeeze_toxic_only_cooldown_b1",
)


def _safe_float(value: Any) -> float:
    return float(value or 0.0)


def _runs(payload: dict[str, Any], variant: str) -> list[dict[str, Any]]:
    return [item for item in payload.get("window_reports", []) if item.get("variant") == variant]


def _run(payload: dict[str, Any], *, variant: str, window_label: str) -> dict[str, Any]:
    return next(
        (
            item
            for item in payload.get("window_reports", [])
            if item.get("variant") == variant and item.get("window_label") == window_label
        ),
        {},
    )


def _summary(payload: dict[str, Any], variant: str) -> dict[str, Any]:
    dev = _run(payload, variant=variant, window_label="dev")
    dev_metrics = dev.get("metrics") or {}
    hostile_runs = [item for item in _runs(payload, variant) if item.get("window_label") != "dev"]
    hostile_sharpes = [_safe_float((item.get("metrics") or {}).get("sharpe")) for item in hostile_runs]
    hostile_drawdowns = [_safe_float((item.get("metrics") or {}).get("max_drawdown")) for item in hostile_runs]
    return {
        "variant": variant,
        "dev_sharpe": round(_safe_float(dev_metrics.get("sharpe")), 4),
        "dev_drawdown": round(_safe_float(dev_metrics.get("max_drawdown")), 6),
        "worst_hostile_sharpe": round(min(hostile_sharpes), 4) if hostile_sharpes else 0.0,
        "max_hostile_drawdown": round(max(hostile_drawdowns), 6) if hostile_drawdowns else 0.0,
        "negative_hostile_windows": sum(1 for value in hostile_sharpes if value < 0),
        "turnover_per_day": round(_safe_float(dev_metrics.get("turnover_per_day")), 4),
        "fee_drag_pct": round(_safe_float(dev_metrics.get("fee_drag_pct")), 6),
        "profit_factor": round(_safe_float(dev_metrics.get("profit_factor")), 6),
    }


def build_candidate_existence_verdict(
    payload: dict[str, Any],
    *,
    behavior: str,
    gate_mode: str = "strict_candidate_existence",
) -> dict[str, Any]:
    candidate_name = behavior_candidate_existence_variant_specs(behavior)[-1].name
    current = _summary(payload, "current")
    paulwei = _summary(payload, "paulwei_baseline")
    candidate = _summary(payload, candidate_name)

    hostile_anchor = max(current["worst_hostile_sharpe"], paulwei["worst_hostile_sharpe"])
    negative_anchor = min(current["negative_hostile_windows"], paulwei["negative_hostile_windows"])
    drawdown_anchor = min(current["dev_drawdown"], paulwei["dev_drawdown"])
    turnover_anchor = max(current["turnover_per_day"], paulwei["turnover_per_day"])
    fee_anchor = max(current["fee_drag_pct"], paulwei["fee_drag_pct"])

    failures: list[str] = []
    attribution_available = all(
        bool(item.get("decision_attribution_ledger")) for item in payload.get("window_reports", [])
    )
    risk_tolerant = gate_mode == "risk_tolerant_candidate_existence"
    gate_diagnostics: dict[str, Any] = {"gate_mode": gate_mode}
    if risk_tolerant:
        failures.extend(
            _risk_tolerant_failures(
                payload,
                current=current,
                paulwei=paulwei,
                candidate=candidate,
                candidate_name=candidate_name,
                attribution_available=attribution_available,
                diagnostics=gate_diagnostics,
            )
        )
    else:
        if candidate["worst_hostile_sharpe"] < hostile_anchor:
            failures.append("worst_hostile_sharpe_regressed")
        if candidate["negative_hostile_windows"] > negative_anchor:
            failures.append("negative_hostile_windows_increased")
        if candidate["dev_drawdown"] > drawdown_anchor * 1.05:
            failures.append("drawdown_exceeds_5pct_baseline_band")
        if candidate["turnover_per_day"] > max(turnover_anchor * 1.20, turnover_anchor + 100.0):
            failures.append("turnover_structurally_worse")
        if candidate["fee_drag_pct"] > max(fee_anchor * 1.15, fee_anchor + 0.002):
            failures.append("fee_drag_structurally_worse")
    if not attribution_available:
        failures.append("attribution_ledger_missing")

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "behavior": behavior,
        "candidate_variant": candidate_name,
        "gate_mode": gate_mode,
        "train_search_executed": False,
        "optuna_search_executed": False,
        "full_training_allowed": (not failures) and attribution_available and not risk_tolerant,
        "robustness_allowed": (not failures) and attribution_available and risk_tolerant,
        "next_stage_allowed": "robustness_only" if risk_tolerant and not failures else "none",
        "attribution_ledger_required": True,
        "attribution_ledger_available": attribution_available,
        "gate_diagnostics": gate_diagnostics,
        "variant_window_summary": {
            "current": current,
            "paulwei_baseline": paulwei,
            candidate_name: candidate,
        },
        "terminal_recommendation": "candidate_exists_open_full_training_gate"
        if not failures and not risk_tolerant
        else "candidate_exists_open_robustness_gate"
        if not failures
        else "reject_behavior_family",
        "rejection_reasons": failures,
    }


def _risk_tolerant_failures(
    payload: dict[str, Any],
    *,
    current: dict[str, Any],
    paulwei: dict[str, Any],
    candidate: dict[str, Any],
    candidate_name: str,
    attribution_available: bool,
    diagnostics: dict[str, Any],
) -> list[str]:
    del paulwei
    failures: list[str] = []
    current_hostile = {
        item["window_label"]: item for item in _runs(payload, "current") if item.get("window_label") != "dev"
    }
    candidate_hostile = {
        item["window_label"]: item for item in _runs(payload, candidate_name) if item.get("window_label") != "dev"
    }
    hard_dd_cap = 0.20
    max_sharpe_regression = 0.10
    hostile_sharpe_gap = round(candidate["worst_hostile_sharpe"] - current["worst_hostile_sharpe"], 4)
    diagnostics["hard_drawdown_cap"] = hard_dd_cap
    diagnostics["max_allowed_worst_hostile_sharpe_regression"] = max_sharpe_regression
    diagnostics["worst_hostile_sharpe_gap_vs_current"] = hostile_sharpe_gap

    if candidate["dev_drawdown"] >= hard_dd_cap or candidate["max_hostile_drawdown"] >= hard_dd_cap:
        failures.append("drawdown_exceeds_20pct_research_cap")
    if candidate["negative_hostile_windows"] > current["negative_hostile_windows"]:
        failures.append("negative_hostile_windows_increased")
    if hostile_sharpe_gap < -max_sharpe_regression:
        failures.append("worst_hostile_sharpe_regressed_beyond_tolerance")
    if candidate["turnover_per_day"] > current["turnover_per_day"]:
        failures.append("turnover_not_below_current")

    compensation_checks = {
        "turnover_below_current": candidate["turnover_per_day"] < current["turnover_per_day"],
        "fee_drag_not_structurally_worse": candidate["fee_drag_pct"]
        <= max(current["fee_drag_pct"] * 1.15, current["fee_drag_pct"] + 0.002),
        "hostile_pnl_improved": False,
        "hostile_drawdown_not_higher": False,
    }
    for label, candidate_run in candidate_hostile.items():
        current_run = current_hostile.get(label) or {}
        candidate_metrics = candidate_run.get("metrics") or {}
        current_metrics = current_run.get("metrics") or {}
        if _safe_float(candidate_metrics.get("net_pnl")) > _safe_float(current_metrics.get("net_pnl")):
            compensation_checks["hostile_pnl_improved"] = True
        if _safe_float(candidate_metrics.get("max_drawdown")) <= _safe_float(current_metrics.get("max_drawdown")):
            compensation_checks["hostile_drawdown_not_higher"] = True

    compensation_count = sum(1 for passed in compensation_checks.values() if passed)
    diagnostics["compensation_checks"] = compensation_checks
    diagnostics["compensation_count"] = compensation_count
    diagnostics["attribution_available"] = attribution_available
    if hostile_sharpe_gap < 0 and compensation_count < 2:
        failures.append("insufficient_compensation_for_hostile_sharpe_regression")
    return failures


def _markdown(verdict: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Behavior Candidate Existence Check",
            "",
            f"- behavior: `{verdict['behavior']}`",
            f"- candidate_variant: `{verdict['candidate_variant']}`",
            f"- terminal_recommendation: `{verdict['terminal_recommendation']}`",
            f"- rejection_reasons: `{verdict['rejection_reasons']}`",
            "",
            "This check does not run Optuna or full training.",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one behavior-family candidate existence check")
    parser.add_argument("--behavior", choices=SUPPORTED_BEHAVIORS, required=True)
    parser.add_argument(
        "--gate-mode",
        choices=("strict_candidate_existence", "risk_tolerant_candidate_existence"),
        default="strict_candidate_existence",
    )
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", default="results/behavior_candidate_existence")
    args = parser.parse_args()

    split_spec = default_btc_split_spec()
    payload = compare_variants(
        HistoricalBacktestConfig(
            root_dir=Path(args.root),
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
        ),
        variants=behavior_candidate_existence_variant_specs(args.behavior),
        windows=default_comparison_windows(split_spec, "dev"),
        walk_forward_windows=[],
        split_spec=split_spec,
        comparison_split="dev",
        include_attribution_ledger=True,
    )
    verdict = build_candidate_existence_verdict(payload, behavior=args.behavior, gate_mode=args.gate_mode)

    output_dir = Path(args.output_dir) / args.behavior
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (output_dir / "comparison.md").write_text(build_markdown_summary(payload), encoding="utf-8")
    (output_dir / "candidate_existence.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    (output_dir / "candidate_existence.md").write_text(_markdown(verdict), encoding="utf-8")

    print(f"Saved candidate existence verdict to {output_dir / 'candidate_existence.json'}")


if __name__ == "__main__":
    main()
