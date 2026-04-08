#!/usr/bin/env python3
"""Compare current strategy behavior against the frozen PaulWei baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.comparison import (
    build_markdown_summary,
    compare_variants,
    default_comparison_windows,
    filtered_worst_period_windows,
)
from zenquant.research.splits import default_btc_split_spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare current strategy against PaulWei baseline")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", default="results/variant_comparison")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--include-worst-periods", action="store_true")
    parser.add_argument("--skip-walk-forward", action="store_true")
    args = parser.parse_args()

    split_spec = default_btc_split_spec()
    windows = default_comparison_windows(split_spec, args.split)
    if args.include_worst_periods:
        seen = {window.label for window in windows}
        for window in filtered_worst_period_windows(split_spec, args.split):
            if window.label not in seen:
                windows.append(window)
                seen.add(window.label)

    payload = compare_variants(
        HistoricalBacktestConfig(
            root_dir=Path(args.root),
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
        ),
        windows=windows,
        walk_forward_windows=[] if args.skip_walk_forward else None,
        split_spec=split_spec,
        comparison_split=args.split,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "comparison.json"
    md_path = output_dir / "comparison.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(build_markdown_summary(payload), encoding="utf-8")

    print(f"Saved JSON report to {json_path}")
    print(f"Saved Markdown summary to {md_path}")
    for run in payload["window_reports"]:
        metrics = run["metrics"]
        print(
            f"{run['variant']:>16} | {run['window_label']:<12} | "
            f"Sharpe {metrics['sharpe']:>6.2f} | PnL {metrics['net_pnl']:>10.0f} | "
            f"DD {metrics['max_drawdown']:>6.2%}"
        )


if __name__ == "__main__":
    main()
