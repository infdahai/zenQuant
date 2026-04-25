#!/usr/bin/env python3
"""Analyze whether a candidate damages current's chop_neutral edge.

The script reads existing comparison/robustness artifacts. It does not run
Optuna, full training, or any replay by default.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.attribution import build_attribution_precheck_verdict

DEFAULT_INPUT = Path(
    "results/behavior_candidate_robustness/squeeze_conditional_cooldown_dd060_b1/comparison_seed_123.json"
)
DEFAULT_CANDIDATE = "squeeze_conditional_cooldown_dd060_b1"


def _load_comparison_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "comparison_payload" in payload:
        return payload["comparison_payload"]
    return payload


def _filter_window(payload: dict[str, Any], window_label: str | None) -> dict[str, Any]:
    if not window_label:
        return payload
    filtered = dict(payload)
    filtered["window_reports"] = [
        item for item in payload.get("window_reports", []) if item.get("window_label") == window_label
    ]
    return filtered


def _markdown(verdict: dict[str, Any]) -> str:
    aggregate = verdict["chop_neutral_edge_ledger"]["aggregate"]
    lines = [
        "# Chop Neutral Edge Precheck",
        "",
        f"- candidate_variant: `{verdict['candidate_variant']}`",
        f"- terminal_recommendation: `{verdict['terminal_recommendation']}`",
        f"- candidate_existence_allowed: `{verdict['candidate_existence_allowed']}`",
        f"- rejection_reasons: `{verdict['rejection_reasons']}`",
        f"- chop_pnl_delta: `{aggregate['chop_pnl_delta']}`",
        f"- squeeze_pnl_delta: `{aggregate['squeeze_pnl_delta']}`",
        f"- profitable_chop_changed_bars: `{aggregate['profitable_chop_changed_bars']}`",
        f"- turnover_not_below_current_windows: `{aggregate['turnover_not_below_current_windows']}`",
        "",
        "This precheck reads existing artifacts only and does not run training.",
        "",
        "## Windows",
        "",
    ]
    for item in verdict["chop_neutral_edge_ledger"]["window_comparisons"]:
        chop = item["chop_neutral"]
        squeeze = item["squeeze_continuation"]
        lines.extend(
            [
                f"### {item['window_label']}",
                "",
                f"- chop_pnl_delta: `{chop['pnl_delta']}`",
                f"- squeeze_pnl_delta: `{squeeze['pnl_delta']}`",
                f"- changed_profitable_bars: `{len(chop['changed_profitable_bars'])}`",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze candidate impact on current's chop_neutral edge")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    parser.add_argument("--baseline", default="current")
    parser.add_argument("--window-label", default="2023-08")
    parser.add_argument("--output-dir", type=Path, default=Path("results/chop_neutral_edge"))
    args = parser.parse_args()

    comparison_payload = _filter_window(_load_comparison_payload(args.input), args.window_label)
    verdict = build_attribution_precheck_verdict(
        comparison_payload,
        candidate_name=args.candidate,
        baseline_name=args.baseline,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "chop_neutral_edge.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    (args.output_dir / "chop_neutral_edge.md").write_text(_markdown(verdict), encoding="utf-8")

    print(f"Saved chop neutral edge precheck to {args.output_dir / 'chop_neutral_edge.json'}")


if __name__ == "__main__":
    main()
