#!/usr/bin/env python3
"""Run the train-only regime objective gate for one fixed research candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.comparison import (
    behavior_candidate_existence_variant_specs,
    build_markdown_summary,
    compare_variants,
)
from zenquant.research.regime_objective import (
    build_regime_objective_markdown,
    build_regime_objective_verdict,
    train_regime_windows,
)
from zenquant.research.splits import default_btc_split_spec

DEFAULT_BEHAVIOR_CANDIDATE = "squeeze_conditional_cooldown_dd060_b1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run train-only regime objective candidate check")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--behavior-candidate", default=DEFAULT_BEHAVIOR_CANDIDATE)
    parser.add_argument("--output-dir", default="results/regime_objective_redesign")
    args = parser.parse_args()

    split_spec = default_btc_split_spec()
    variants = behavior_candidate_existence_variant_specs(args.behavior_candidate)
    candidate_variant = variants[-1].name
    payload = compare_variants(
        HistoricalBacktestConfig(
            root_dir=Path(args.root),
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
        ),
        variants=variants,
        windows=train_regime_windows(split_spec),
        walk_forward_windows=[],
        split_spec=split_spec,
        comparison_split="dev",
        report_mode="full_diagnostics",
    )
    payload["split_scope"] = "train_only_regime_objective"
    payload["selection_policy"] = "train_only_regime_objective_dev_diagnostics_test_acceptance"
    payload["test_evaluated"] = False
    payload["promotion_allowed"] = False

    verdict = build_regime_objective_verdict(payload, candidate_variant=candidate_variant, split_spec=split_spec)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_json_path = output_dir / "comparison.json"
    comparison_md_path = output_dir / "comparison.md"
    verdict_json_path = output_dir / "regime_objective_check.json"
    verdict_md_path = output_dir / "regime_objective_check.md"
    comparison_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    comparison_md_path.write_text(build_markdown_summary(payload), encoding="utf-8")
    verdict_json_path.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    verdict_md_path.write_text(build_regime_objective_markdown(verdict), encoding="utf-8")

    print(f"Saved comparison JSON to {comparison_json_path}")
    print(f"Saved comparison Markdown to {comparison_md_path}")
    print(f"Saved verdict JSON to {verdict_json_path}")
    print(f"Saved verdict Markdown to {verdict_md_path}")


if __name__ == "__main__":
    main()
