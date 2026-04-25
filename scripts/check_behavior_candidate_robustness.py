#!/usr/bin/env python3
"""Run robustness-only validation for one behavior candidate.

This is intentionally not a trainer. It runs fixed-seed domain randomization
for current / paulwei_baseline / candidate, then fails closed unless the
candidate remains within the research risk-tolerant robustness contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktestConfig
from zenquant.historical.perturbation import PerturbationConfig
from zenquant.research.comparison import (
    behavior_candidate_existence_variant_specs,
    compare_variants,
    default_comparison_windows,
)
from zenquant.research.splits import default_btc_split_spec

DEFAULT_CANDIDATE = "squeeze_conditional_cooldown_dd060_b1"
DEFAULT_SEEDS = (42, 123, 456, 789, 1024)
MIN_PASSING_SEEDS = 4
HARD_DRAWDOWN_CAP = 0.20
MAX_WORST_HOSTILE_SHARPE_REGRESSION = 0.10


def _safe_float(value: Any) -> float:
    return float(value or 0.0)


def _runs(payload: dict[str, Any], variant: str) -> list[dict[str, Any]]:
    return [item for item in payload.get("window_reports", []) if item.get("variant") == variant]


def _window_metrics(run: dict[str, Any]) -> dict[str, float]:
    metrics = run.get("metrics") or {}
    return {
        "sharpe": round(_safe_float(metrics.get("sharpe")), 4),
        "max_drawdown": round(_safe_float(metrics.get("max_drawdown")), 6),
        "net_pnl": round(_safe_float(metrics.get("net_pnl")), 6),
        "turnover_per_day": round(_safe_float(metrics.get("turnover_per_day")), 4),
        "fee_drag_pct": round(_safe_float(metrics.get("fee_drag_pct")), 6),
        "profit_factor": round(_safe_float(metrics.get("profit_factor")), 6),
    }


def _variant_summary(payload: dict[str, Any], variant: str) -> dict[str, Any]:
    runs = _runs(payload, variant)
    dev_run = next((item for item in runs if item.get("window_label") == "dev"), {})
    hostile_runs = [item for item in runs if item.get("window_label") != "dev"]
    hostile_sharpes = [_safe_float((item.get("metrics") or {}).get("sharpe")) for item in hostile_runs]
    hostile_drawdowns = [_safe_float((item.get("metrics") or {}).get("max_drawdown")) for item in hostile_runs]
    return {
        "variant": variant,
        "dev": _window_metrics(dev_run),
        "worst_hostile_sharpe": round(min(hostile_sharpes), 4) if hostile_sharpes else 0.0,
        "max_hostile_drawdown": round(max(hostile_drawdowns), 6) if hostile_drawdowns else 0.0,
        "negative_hostile_windows": sum(1 for value in hostile_sharpes if value < 0),
        "hostile_windows": {str(item.get("window_label")): _window_metrics(item) for item in hostile_runs},
    }


def _micro_state_summary(run: dict[str, Any]) -> dict[str, Any]:
    ledger = run.get("decision_attribution_ledger") or {}
    return {
        "micro_state_summary": ledger.get("micro_state_summary") or {},
        "top_reason_codes_by_micro_state": ledger.get("top_reason_codes_by_micro_state") or {},
    }


def _seed_result(seed_payload: dict[str, Any], *, candidate_name: str) -> dict[str, Any]:
    seed = int(seed_payload["seed"])
    comparison = seed_payload["comparison_payload"]
    current = _variant_summary(comparison, "current")
    paulwei = _variant_summary(comparison, "paulwei_baseline")
    candidate = _variant_summary(comparison, candidate_name)

    failures: list[str] = []
    candidate_dev_dd = candidate["dev"]["max_drawdown"]
    if candidate_dev_dd >= HARD_DRAWDOWN_CAP or candidate["max_hostile_drawdown"] >= HARD_DRAWDOWN_CAP:
        failures.append("drawdown_exceeds_20pct_research_cap")

    if candidate["negative_hostile_windows"] > current["negative_hostile_windows"]:
        failures.append("negative_hostile_windows_increased")

    worst_hostile_sharpe_gap = round(candidate["worst_hostile_sharpe"] - current["worst_hostile_sharpe"], 4)
    if worst_hostile_sharpe_gap < -MAX_WORST_HOSTILE_SHARPE_REGRESSION:
        failures.append("worst_hostile_sharpe_regressed_beyond_tolerance")

    turnover_below_current = candidate["dev"]["turnover_per_day"] < current["dev"]["turnover_per_day"]
    if not turnover_below_current:
        failures.append("turnover_not_below_current")

    fee_drag_not_structurally_worse = candidate["dev"]["fee_drag_pct"] <= max(
        current["dev"]["fee_drag_pct"] * 1.15,
        current["dev"]["fee_drag_pct"] + 0.002,
    )
    if not fee_drag_not_structurally_worse:
        failures.append("fee_drag_structurally_worse")

    hostile_pnl_or_dd_improved = False
    for label, candidate_metrics in candidate["hostile_windows"].items():
        current_metrics = current["hostile_windows"].get(label) or {}
        if candidate_metrics["net_pnl"] > _safe_float(current_metrics.get("net_pnl")):
            hostile_pnl_or_dd_improved = True
        if candidate_metrics["max_drawdown"] <= _safe_float(current_metrics.get("max_drawdown")):
            hostile_pnl_or_dd_improved = True

    if not hostile_pnl_or_dd_improved:
        failures.append("hostile_pnl_or_drawdown_not_improved")

    attribution_available = all(
        bool(item.get("decision_attribution_ledger")) for item in comparison.get("window_reports", [])
    )
    if not attribution_available:
        failures.append("attribution_ledger_missing")

    attribution_summary = {
        f"{item.get('variant')}::{item.get('window_label')}": _micro_state_summary(item)
        for item in comparison.get("window_reports", [])
    }
    return {
        "seed": seed,
        "passed": not failures,
        "rejection_reasons": failures,
        "diagnostics": {
            "hard_drawdown_cap": HARD_DRAWDOWN_CAP,
            "max_allowed_worst_hostile_sharpe_regression": MAX_WORST_HOSTILE_SHARPE_REGRESSION,
            "worst_hostile_sharpe_gap_vs_current": worst_hostile_sharpe_gap,
            "turnover_below_current": turnover_below_current,
            "fee_drag_not_structurally_worse": fee_drag_not_structurally_worse,
            "hostile_pnl_or_drawdown_improved": hostile_pnl_or_dd_improved,
            "attribution_available": attribution_available,
        },
        "variant_window_summary": {
            "current": current,
            "paulwei_baseline": paulwei,
            candidate_name: candidate,
        },
        "attribution_summary": attribution_summary,
    }


def build_robustness_verdict(
    seed_payloads: list[dict[str, Any]],
    *,
    candidate_name: str = DEFAULT_CANDIDATE,
) -> dict[str, Any]:
    seed_results = [_seed_result(seed_payload, candidate_name=candidate_name) for seed_payload in seed_payloads]
    passing_seeds = sum(1 for item in seed_results if item["passed"])
    turnover_below_current_seeds = sum(1 for item in seed_results if item["diagnostics"]["turnover_below_current"])
    hostile_improvement_seeds = sum(
        1 for item in seed_results if item["diagnostics"]["hostile_pnl_or_drawdown_improved"]
    )
    attribution_available = all(item["diagnostics"]["attribution_available"] for item in seed_results)

    failures: list[str] = []
    if passing_seeds < MIN_PASSING_SEEDS:
        failures.append("insufficient_seed_passes")
    if turnover_below_current_seeds < MIN_PASSING_SEEDS:
        failures.append("turnover_not_below_current_in_4_of_5_seeds")
    if hostile_improvement_seeds < 3:
        failures.append("hostile_pnl_or_drawdown_not_improved_in_majority")
    if not attribution_available:
        failures.append("attribution_ledger_missing")

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_variant": candidate_name,
        "train_search_executed": False,
        "optuna_search_executed": False,
        "full_training_executed": False,
        "full_training_allowed": False,
        "robustness_gate_passed": not failures,
        "next_stage_allowed": "frozen_acceptance_review" if not failures else "none",
        "gate": {
            "min_passing_seeds": MIN_PASSING_SEEDS,
            "hard_drawdown_cap": HARD_DRAWDOWN_CAP,
            "max_worst_hostile_sharpe_regression": MAX_WORST_HOSTILE_SHARPE_REGRESSION,
            "required_turnover_below_current_seeds": MIN_PASSING_SEEDS,
            "required_hostile_improvement_seeds": 3,
        },
        "seed_count": len(seed_results),
        "passing_seeds": passing_seeds,
        "turnover_below_current_seeds": turnover_below_current_seeds,
        "hostile_improvement_seeds": hostile_improvement_seeds,
        "attribution_ledger_required": True,
        "attribution_ledger_available": attribution_available,
        "seed_results": seed_results,
        "terminal_recommendation": "robustness_gate_passed_open_frozen_acceptance_review"
        if not failures
        else "reject_robustness_return_to_attribution",
        "rejection_reasons": failures,
    }


def _markdown(verdict: dict[str, Any]) -> str:
    lines = [
        "# Behavior Candidate Robustness Check",
        "",
        f"- candidate_variant: `{verdict['candidate_variant']}`",
        f"- terminal_recommendation: `{verdict['terminal_recommendation']}`",
        f"- passing_seeds: `{verdict['passing_seeds']}/{verdict['seed_count']}`",
        f"- turnover_below_current_seeds: `{verdict['turnover_below_current_seeds']}/{verdict['seed_count']}`",
        f"- hostile_improvement_seeds: `{verdict['hostile_improvement_seeds']}/{verdict['seed_count']}`",
        f"- rejection_reasons: `{verdict['rejection_reasons']}`",
        "",
        "This check does not run Optuna or full training.",
        "",
        "## Seeds",
        "",
    ]
    for result in verdict["seed_results"]:
        lines.extend(
            [
                f"### seed `{result['seed']}`",
                "",
                f"- passed: `{result['passed']}`",
                f"- rejection_reasons: `{result['rejection_reasons']}`",
                f"- worst_hostile_sharpe_gap_vs_current: "
                f"`{result['diagnostics']['worst_hostile_sharpe_gap_vs_current']}`",
                "",
            ]
        )
    return "\n".join(lines)


def _parse_seeds(value: str) -> tuple[int, ...]:
    seeds = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    return seeds


def _perturbation(seed: int) -> PerturbationConfig:
    return PerturbationConfig(seed=seed, price_noise_pct=0.001, volume_noise_pct=0.05, bar_dropout_pct=0.02)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run robustness-only validation for one behavior candidate")
    parser.add_argument("--candidate", choices=(DEFAULT_CANDIDATE,), default=DEFAULT_CANDIDATE)
    parser.add_argument("--seeds", type=_parse_seeds, default=DEFAULT_SEEDS)
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", default="results/behavior_candidate_robustness")
    parser.add_argument("--window-workers", type=int, default=1)
    args = parser.parse_args()

    split_spec = default_btc_split_spec()
    windows = default_comparison_windows(split_spec, "dev")
    output_dir = Path(args.output_dir) / args.candidate
    output_dir.mkdir(parents=True, exist_ok=True)

    seed_payloads: list[dict[str, Any]] = []
    for seed in args.seeds:
        perturbation = _perturbation(seed)
        payload = compare_variants(
            HistoricalBacktestConfig(
                root_dir=Path(args.root),
                symbol=args.symbol,
                portfolio_symbol=f"{args.symbol}_PERP",
                perturbation=perturbation,
            ),
            variants=behavior_candidate_existence_variant_specs(args.candidate),
            windows=windows,
            walk_forward_windows=[],
            split_spec=split_spec,
            comparison_split="dev",
            window_workers=args.window_workers,
            include_attribution_ledger=True,
        )
        seed_payload = {
            "seed": seed,
            "perturbation": asdict(perturbation),
            "comparison_payload": payload,
        }
        seed_payloads.append(seed_payload)
        (output_dir / f"comparison_seed_{seed}.json").write_text(json.dumps(seed_payload, indent=2), encoding="utf-8")

    verdict = build_robustness_verdict(seed_payloads, candidate_name=args.candidate)
    (output_dir / "robustness.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    (output_dir / "robustness.md").write_text(_markdown(verdict), encoding="utf-8")

    print(f"Saved robustness verdict to {output_dir / 'robustness.json'}")


if __name__ == "__main__":
    main()
