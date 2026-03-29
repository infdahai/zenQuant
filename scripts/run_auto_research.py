#!/usr/bin/env python3
"""Run an AutoResearch campaign from the command line.

Usage:
    # Quick campaign (3 leverage values, no walk-forward, 5 robustness seeds)
    uv run python scripts/run_auto_research.py --quick

    # Full campaign (27 combos, 3 WF splits, 5 robustness seeds)
    uv run python scripts/run_auto_research.py --full

    # Custom search space
    uv run python scripts/run_auto_research.py \
        --bull-leverage 2.5,3.0,3.5 \
        --preserve-capital-risk 0.55,0.60,0.65,0.70 \
        --exit-transition-risk 0.80,0.85,0.90 \
        --label my_experiment

    # Skip robustness (faster iteration)
    uv run python scripts/run_auto_research.py --quick --no-robustness
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from zenquant.research.auto_research import (
    Campaign,
    CampaignConfig,
    SearchSpace,
    WalkForwardSplit,
)


def _parse_float_list(value: str) -> list[float]:
    return [float(v.strip()) for v in value.split(",")]


def _parse_int_list(value: str) -> list[int]:
    return [int(v.strip()) for v in value.split(",")]


def _build_quick_config(args: argparse.Namespace) -> CampaignConfig:
    """Quick campaign: small search space, no walk-forward."""
    return CampaignConfig(
        data_root=Path(args.data_root),
        symbol=args.symbol,
        search_space=SearchSpace(
            strategy_params={
                "bull_leverage": args.bull_leverage or [2.5, 3.0, 3.5],
            },
        ),
        wf_splits=[],  # skip walk-forward
        top_n_for_validation=3,
        robustness_seeds=[] if args.no_robustness else [42, 123, 456, 789, 1024],
        campaign_label=args.label or "quick",
        output_dir=Path(args.output_dir),
        verbose=not args.quiet,
    )


def _build_full_config(args: argparse.Namespace) -> CampaignConfig:
    """Full campaign: grid search + walk-forward + robustness."""
    # Build walk-forward splits (3 × 90-day train + 90-day validation)
    now = datetime.now()
    splits: list[WalkForwardSplit] = []
    base = now - timedelta(days=365)
    for i in range(3):
        train_start = base + timedelta(days=i * 90)
        train_end = train_start + timedelta(days=90)
        val_start = train_end
        val_end = val_start + timedelta(days=90)
        splits.append(WalkForwardSplit(
            train_start_ns=int(train_start.timestamp() * 1e9),
            train_end_ns=int(train_end.timestamp() * 1e9),
            val_start_ns=int(val_start.timestamp() * 1e9),
            val_end_ns=int(val_end.timestamp() * 1e9),
        ))

    return CampaignConfig(
        data_root=Path(args.data_root),
        symbol=args.symbol,
        search_space=SearchSpace(
            strategy_params={
                "preserve_capital_risk": args.preserve_capital_risk or [0.55, 0.60, 0.65, 0.70],
                "exit_transition_risk": args.exit_transition_risk or [0.80, 0.85, 0.90],
                "bull_leverage": args.bull_leverage or [2.5, 3.0, 3.5],
            },
        ),
        wf_splits=splits,
        top_n_for_validation=args.top_n,
        robustness_seeds=[] if args.no_robustness else [42, 123, 456, 789, 1024],
        campaign_label=args.label or "full",
        output_dir=Path(args.output_dir),
        verbose=not args.quiet,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an AutoResearch optimization campaign",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--quick", action="store_true", help="Quick campaign (sweep only)")
    parser.add_argument("--full", action="store_true", help="Full campaign (sweep + WF + robustness)")
    parser.add_argument("--data-root", default="data/raw/binance", help="Historical data root")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol to optimize")
    parser.add_argument("--output-dir", default="results/campaigns", help="Campaign output directory")
    parser.add_argument("--label", default="", help="Campaign label")
    parser.add_argument("--top-n", type=int, default=3, help="Top N candidates for validation")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    parser.add_argument("--no-robustness", action="store_true", help="Skip robustness testing")

    # Custom search space
    parser.add_argument("--bull-leverage", type=_parse_float_list, help="Comma-separated leverage values")
    parser.add_argument("--preserve-capital-risk", type=_parse_float_list, help="Comma-separated risk values")
    parser.add_argument("--exit-transition-risk", type=_parse_float_list, help="Comma-separated risk values")

    args = parser.parse_args()

    if not args.quick and not args.full:
        parser.error("Must specify --quick or --full")

    config = _build_quick_config(args) if args.quick else _build_full_config(args)
    campaign = Campaign(config)
    report = campaign.run()

    # Summary
    print("\n" + "=" * 80)
    print("CAMPAIGN COMPLETE")
    print("=" * 80)
    print(f"Campaign ID:  {report.campaign_id}")
    print(f"Runtime:      {report.runtime_s:.1f}s")
    print(f"Sweep:        {report.sweep.total_combinations} combinations")
    print(f"Validations:  {len(report.validations)}")
    print(f"Robustness:   {len(report.robustness)} candidates tested")

    if report.verdicts:
        print("\nVerdicts:")
        for params, verdict in report.verdicts:
            print(f"  {json.dumps(params)} → {verdict.decision}")
            for reason in verdict.reasons:
                print(f"    {reason}")

    if report.best_params:
        print(f"\nBest parameters: {json.dumps(report.best_params, indent=2)}")
        print(f"Decision: {report.best_verdict.decision if report.best_verdict else 'N/A'}")

    print(f"\nResults saved to: {campaign._campaign_dir}")


if __name__ == "__main__":
    main()
