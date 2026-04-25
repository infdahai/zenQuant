#!/usr/bin/env python3
"""Run the minimal execution/inventory architecture falsification check."""

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
    build_markdown_summary,
    compare_variants,
    default_comparison_windows,
    execution_inventory_rethink_variant_specs,
)
from zenquant.research.splits import default_btc_split_spec


def _safe_float(value: Any) -> float:
    return float(value or 0.0)


def _dev_window_label(split_spec) -> str:
    return split_spec.dev.name


def _find_run(payload: dict[str, Any], *, variant: str, window_label: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in payload.get("window_reports", [])
            if item.get("variant") == variant and item.get("window_label") == window_label
        ),
        None,
    )


def _variant_summary(payload: dict[str, Any], *, variant: str, dev_window_label: str) -> dict[str, Any]:
    variant_runs = [item for item in payload.get("window_reports", []) if item.get("variant") == variant]
    dev_run = _find_run(payload, variant=variant, window_label=dev_window_label) or {}
    weak_run = _find_run(payload, variant=variant, window_label="2023-08") or {}
    validation = next(
        (item for item in payload.get("validation_reports", []) if item.get("variant") == variant),
        {},
    )
    worst_dev_sharpe = min(
        (_safe_float((item.get("metrics") or {}).get("sharpe")) for item in variant_runs),
        default=0.0,
    )
    return {
        "variant": variant,
        "execution_architecture_profile": next(
            (
                item.get("execution_architecture_profile")
                for item in payload.get("variants", [])
                if item.get("name") == variant
            ),
            None,
        ),
        "dev_sharpe": round(_safe_float((dev_run.get("metrics") or {}).get("sharpe")), 4),
        "weak_window_sharpe": round(_safe_float((weak_run.get("metrics") or {}).get("sharpe")), 4),
        "worst_dev_sharpe": round(worst_dev_sharpe, 4),
        "turnover_per_day": round(_safe_float((dev_run.get("metrics") or {}).get("turnover_per_day")), 4),
        "fee_drag_pct": round(_safe_float((dev_run.get("metrics") or {}).get("fee_drag_pct")), 6),
        "profit_factor": round(_safe_float((dev_run.get("metrics") or {}).get("profit_factor")), 6),
        "avg_test_sharpe": round(_safe_float(validation.get("avg_test_sharpe")), 4),
        "worst_test_sharpe": round(_safe_float(validation.get("worst_test_sharpe")), 4),
    }


def _gap(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    return {
        "dev_sharpe_gap": round(candidate["dev_sharpe"] - baseline["dev_sharpe"], 4),
        "weak_window_sharpe_gap": round(candidate["weak_window_sharpe"] - baseline["weak_window_sharpe"], 4),
        "worst_dev_sharpe_gap": round(candidate["worst_dev_sharpe"] - baseline["worst_dev_sharpe"], 4),
        "turnover_per_day_gap": round(candidate["turnover_per_day"] - baseline["turnover_per_day"], 4),
        "fee_drag_pct_gap": round(candidate["fee_drag_pct"] - baseline["fee_drag_pct"], 6),
    }


def _verdict(
    *,
    candidate: dict[str, Any],
    current: dict[str, Any],
    paulwei: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    hostile_anchor_weak = max(current["weak_window_sharpe"], paulwei["weak_window_sharpe"])
    hostile_anchor_worst = max(current["worst_dev_sharpe"], paulwei["worst_dev_sharpe"])
    turnover_anchor = max(current["turnover_per_day"], paulwei["turnover_per_day"])
    fee_anchor = max(current["fee_drag_pct"], paulwei["fee_drag_pct"])
    weak_ok = candidate["weak_window_sharpe"] >= hostile_anchor_weak - 0.25
    worst_ok = candidate["worst_dev_sharpe"] >= hostile_anchor_worst - 0.25
    turnover_ok = candidate["turnover_per_day"] <= max(turnover_anchor * 1.20, turnover_anchor + 100.0)
    fee_ok = candidate["fee_drag_pct"] <= max(fee_anchor * 1.15, fee_anchor + 0.002)
    if not weak_ok:
        reasons.append("weak_window_sharpe_regressed")
    if not worst_ok:
        reasons.append("worst_dev_sharpe_regressed")
    if not turnover_ok:
        reasons.append("turnover_per_day_structurally_worse")
    if not fee_ok:
        reasons.append("fee_drag_pct_structurally_worse")
    if weak_ok and worst_ok and turnover_ok and fee_ok:
        return "promising_for_campaign", []
    return "reject_and_switch_hypothesis", reasons


def build_execution_inventory_rethink_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    split_spec = default_btc_split_spec()
    dev_label = _dev_window_label(split_spec)
    current = _variant_summary(payload, variant="current", dev_window_label=dev_label)
    paulwei = _variant_summary(payload, variant="paulwei_baseline", dev_window_label=dev_label)
    candidate = _variant_summary(payload, variant="hostile_inventory_rethink_v1", dev_window_label=dev_label)
    recommendation, rejection_reasons = _verdict(candidate=candidate, current=current, paulwei=paulwei)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "research_direction": "execution/inventory architecture rethink",
        "execution_architecture_profile": "hostile_inventory_rethink_v1",
        "single_minimal_falsification_only": True,
        "search_family_reused": False,
        "optuna_search_executed": False,
        "multi_round_loop_executed": False,
        "train_test_scope": "train_dev_only",
        "variant_window_summary": {
            "current": current,
            "paulwei_baseline": paulwei,
            "hostile_inventory_rethink_v1": candidate,
        },
        "relative_to_current": _gap(candidate, current),
        "relative_to_paulwei_baseline": _gap(candidate, paulwei),
        "terminal_recommendation": recommendation,
        "rejection_reasons": rejection_reasons,
    }


def _build_verdict_markdown(verdict: dict[str, Any]) -> str:
    candidate = verdict["variant_window_summary"]["hostile_inventory_rethink_v1"]
    return "\n".join(
        [
            "# Execution / Inventory Architecture Check",
            "",
            f"- created_at: `{verdict['created_at']}`",
            f"- execution_architecture_profile: `{verdict['execution_architecture_profile']}`",
            f"- terminal_recommendation: `{verdict['terminal_recommendation']}`",
            f"- rejection_reasons: `{verdict['rejection_reasons']}`",
            "",
            "## Candidate Summary",
            "",
            f"- dev_sharpe: `{candidate['dev_sharpe']}`",
            f"- weak_window_sharpe: `{candidate['weak_window_sharpe']}`",
            f"- worst_dev_sharpe: `{candidate['worst_dev_sharpe']}`",
            f"- turnover_per_day: `{candidate['turnover_per_day']}`",
            f"- fee_drag_pct: `{candidate['fee_drag_pct']}`",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the minimal execution/inventory architecture falsification check")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", default="results/framework_execution_inventory_rethink_v1_check")
    args = parser.parse_args()

    split_spec = default_btc_split_spec()
    payload = compare_variants(
        HistoricalBacktestConfig(
            root_dir=Path(args.root),
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
        ),
        variants=execution_inventory_rethink_variant_specs(),
        windows=default_comparison_windows(split_spec, "dev"),
        walk_forward_windows=[],
        split_spec=split_spec,
        comparison_split="dev",
    )
    verdict = build_execution_inventory_rethink_verdict(payload)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_json_path = output_dir / "comparison.json"
    comparison_md_path = output_dir / "comparison.md"
    verdict_json_path = output_dir / "execution_inventory_rethink_check.json"
    verdict_md_path = output_dir / "execution_inventory_rethink_check.md"
    comparison_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    comparison_md_path.write_text(build_markdown_summary(payload), encoding="utf-8")
    verdict_json_path.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    verdict_md_path.write_text(_build_verdict_markdown(verdict), encoding="utf-8")

    print(f"Saved JSON report to {comparison_json_path}")
    print(f"Saved Markdown summary to {comparison_md_path}")
    print(f"Saved verdict JSON to {verdict_json_path}")
    print(f"Saved verdict Markdown to {verdict_md_path}")


if __name__ == "__main__":
    main()
