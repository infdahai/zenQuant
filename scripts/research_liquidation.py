#!/usr/bin/env python3
"""Run liquidation signal research variants on the historical replay stack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.data.liquidation_pipeline import liquidation_readiness
from zenquant.historical import HistoricalBacktestConfig
from zenquant.research import (
    WindowSpec,
    build_markdown_summary,
    compare_variants,
    fixed_2023_08_window,
    liquidation_research_variant_specs,
    windows_from_worst_periods,
)


def _build_research_verdict(payload: dict, readiness: dict) -> dict:
    if not readiness["data_ready"]:
        verdicts = []
        for variant in payload["validation_reports"]:
            if variant["variant"] == "paulwei_baseline":
                continue
            verdicts.append(
                {
                    "variant": variant["variant"],
                    "promotion_candidate": False,
                    "generalization_score": 0.0,
                    "blocked_by": readiness["blocked_by"],
                    "verdict": "insufficient_data",
                }
            )
        return {
            "baseline_variant": "paulwei_baseline",
            "data_ready": False,
            "promotion_ready": False,
            "blocked_by": readiness["blocked_by"],
            "detail": readiness["detail"],
            "variant_verdicts": verdicts,
        }

    if not readiness["promotion_ready"]:
        verdicts = []
        for variant in payload["validation_reports"]:
            if variant["variant"] == "paulwei_baseline":
                continue
            verdicts.append(
                {
                    "variant": variant["variant"],
                    "promotion_candidate": False,
                    "generalization_score": 0.0,
                    "blocked_by": "not_promotion_ready",
                    "verdict": "not_promotion_ready",
                }
            )
        return {
            "baseline_variant": "paulwei_baseline",
            "data_ready": True,
            "promotion_ready": False,
            "blocked_by": "not_promotion_ready",
            "detail": readiness["detail"],
            "variant_verdicts": verdicts,
        }

    baseline_window = next(
        item
        for item in payload["window_reports"]
        if item["variant"] == "paulwei_baseline" and item["window_label"] == "overall"
    )
    baseline_validation = next(item for item in payload["validation_reports"] if item["variant"] == "paulwei_baseline")

    verdicts = []
    for variant in payload["validation_reports"]:
        if variant["variant"] == "paulwei_baseline":
            continue
        overall = next(
            item
            for item in payload["window_reports"]
            if item["variant"] == variant["variant"] and item["window_label"] == "overall"
        )
        promote = (
            variant["avg_test_sharpe"] >= baseline_validation["avg_test_sharpe"]
            and variant["worst_test_sharpe"] >= baseline_validation["worst_test_sharpe"]
            and overall["metrics"]["max_drawdown"] <= baseline_window["metrics"]["max_drawdown"] + 0.03
        )
        verdicts.append(
            {
                "variant": variant["variant"],
                "promotion_candidate": promote,
                "generalization_score": round(
                    (variant["avg_test_sharpe"] - baseline_validation["avg_test_sharpe"])
                    + (variant["avg_oos_ratio"] - baseline_validation["avg_oos_ratio"]),
                    4,
                ),
                "blocked_by": None,
                "verdict": "promote" if promote else "do_not_promote",
            }
        )
    return {
        "baseline_variant": "paulwei_baseline",
        "data_ready": True,
        "promotion_ready": True,
        "blocked_by": None,
        "detail": readiness["detail"],
        "variant_verdicts": verdicts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Research liquidation state/risk variants")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", default="results/liquidation_research")
    parser.add_argument("--liquidation-dir", default="data/raw/merged_liquidations/aggregated")
    parser.add_argument("--include-worst-periods", action="store_true")
    parser.add_argument("--skip-walk-forward", action="store_true")
    args = parser.parse_args()

    readiness = liquidation_readiness(
        root_dir=Path(args.root),
        symbol=args.symbol,
        liquidation_dir=Path(args.liquidation_dir),
    )
    readiness_payload = {
        "total_bars": readiness.total_bars,
        "nonzero_bars": readiness.nonzero_bars,
        "nonstale_bars": readiness.nonstale_bars,
        "extreme_bars": readiness.extreme_bars,
        "max_total_liq_usd": readiness.max_total_liq_usd,
        "data_ready": readiness.data_ready,
        "promotion_ready": readiness.promotion_ready,
        "blocked_by": readiness.blocked_by,
        "detail": readiness.detail,
    }

    windows = [WindowSpec(label="overall"), fixed_2023_08_window()]
    if args.include_worst_periods:
        seen = {window.label for window in windows}
        for window in windows_from_worst_periods():
            if window.label not in seen:
                windows.append(window)
                seen.add(window.label)

    payload = compare_variants(
        HistoricalBacktestConfig(
            root_dir=Path(args.root),
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
            liquidation_dir=Path(args.liquidation_dir),
        ),
        variants=liquidation_research_variant_specs(),
        windows=windows,
        walk_forward_windows=[] if args.skip_walk_forward or not readiness.promotion_ready else None,
    )
    payload["liquidation_readiness"] = readiness_payload
    payload["research_verdict"] = _build_research_verdict(payload, readiness_payload)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "liquidation_comparison.json"
    md_path = output_dir / "liquidation_comparison.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(build_markdown_summary(payload), encoding="utf-8")

    if not readiness.data_ready:
        print(f"Liquidation data not ready: blocked_by={readiness.blocked_by} detail={readiness.detail}")
    print(f"Saved JSON report to {json_path}")
    print(f"Saved Markdown summary to {md_path}")
    for verdict in payload["research_verdict"]["variant_verdicts"]:
        print(
            f"{verdict['variant']:>16} | promote={str(verdict['promotion_candidate']).lower():<5} | "
            f"generalization_score={verdict['generalization_score']:+.4f}"
        )


if __name__ == "__main__":
    main()
