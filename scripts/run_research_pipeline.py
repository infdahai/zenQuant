#!/usr/bin/env python3
"""Run one deterministic replay-to-dataset research export."""

from __future__ import annotations

import argparse
from pathlib import Path

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research import ResearchRunConfig, run_research_pipeline
from zenquant.strategy import ARCStrategyConfig, ExitEngineConfig, PlaybookEngineConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run replay and export research artifacts.")
    parser.add_argument("--root", default="data/raw/binance", help="Historical data root directory.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Exchange symbol, for example BTCUSDT.")
    parser.add_argument("--output-root", default="results/research_runs", help="Artifact root directory.")
    parser.add_argument("--label", default="", help="Optional short label for the run.")
    parser.add_argument(
        "--horizons",
        default="1,3,6",
        help="Comma-separated forward-return horizons in execution bars.",
    )
    parser.add_argument("--start", type=int, default=None, help="Optional start timestamp in ns.")
    parser.add_argument("--end", type=int, default=None, help="Optional end timestamp in ns.")
    parser.add_argument("--preserve-capital-risk", type=float, default=None, help="Override playbook threshold.")
    parser.add_argument("--exit-transition-risk", type=float, default=None, help="Override exit threshold.")
    args = parser.parse_args()
    root = Path(args.root)

    if not root.exists():
        raise SystemExit(
            "Historical data root does not exist: "
            f"{root}. Provide Binance Parquet data first, or use "
            "`uv run python scripts/export_live_training_data.py --dsn ...` "
            "to build a training dataset from PostgreSQL live audit logs."
        )

    strategy_config = None
    if args.preserve_capital_risk is not None or args.exit_transition_risk is not None:
        strategy_config = ARCStrategyConfig(
            playbook_config=PlaybookEngineConfig(
                preserve_capital_transition_risk=args.preserve_capital_risk or 0.65,
            ),
            exit_config=ExitEngineConfig(
                transition_risk_exit_threshold=args.exit_transition_risk or 0.85,
            ),
        )

    artifact = run_research_pipeline(
        ResearchRunConfig(
            backtest=HistoricalBacktestConfig(
                root_dir=root,
                symbol=args.symbol,
                portfolio_symbol=f"{args.symbol}_PERP",
                start_ns=args.start,
                end_ns=args.end,
                strategy_config=strategy_config,
            ),
            output_root=Path(args.output_root),
            horizons=tuple(int(token.strip()) for token in args.horizons.split(",") if token.strip()),
            run_label=args.label,
        )
    )

    print("Research pipeline complete")
    print(f"run_id={artifact.run_id}")
    print(f"run_dir={artifact.run_dir}")
    print(f"rows={artifact.row_count}")
    print(f"manifest={artifact.manifest_path}")
    print(f"dataset_jsonl={artifact.dataset_jsonl_path}")
    print(f"dataset_csv={artifact.dataset_csv_path}")
    print(f"report={artifact.report_path}")


if __name__ == "__main__":
    main()
