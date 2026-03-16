#!/usr/bin/env python3
"""Export training rows from PostgreSQL audit logs produced by live/paper runs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from zenquant.research.live_export import LiveExportConfig, export_live_training_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Export training data from PostgreSQL live audit logs.")
    parser.add_argument("--dsn", default=os.getenv("ZENQUANT_PG_DSN", ""), help="PostgreSQL DSN.")
    parser.add_argument("--symbol", default="BTCUSDT_PERP", help="Portfolio symbol, for example BTCUSDT_PERP.")
    parser.add_argument("--timeframe", default="4h", help="Execution timeframe, default 4h.")
    parser.add_argument("--output-root", default="results/live_training_runs", help="Artifact root directory.")
    parser.add_argument("--label", default="", help="Optional short label for the export.")
    parser.add_argument("--start", type=int, default=None, help="Optional start timestamp in ns.")
    parser.add_argument("--end", type=int, default=None, help="Optional end timestamp in ns.")
    parser.add_argument(
        "--horizons",
        default="1,3,6",
        help="Comma-separated forward-return horizons in stored execution bars.",
    )
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("PostgreSQL DSN is required. Pass --dsn or set ZENQUANT_PG_DSN.")

    artifact = export_live_training_data(
        LiveExportConfig(
            dsn=args.dsn,
            symbol=args.symbol,
            timeframe=args.timeframe,
            output_root=Path(args.output_root),
            horizons=tuple(int(token.strip()) for token in args.horizons.split(",") if token.strip()),
            run_label=args.label,
            start_ns=args.start,
            end_ns=args.end,
        )
    )

    print("Live audit export complete")
    print(f"run_id={artifact.run_id}")
    print(f"run_dir={artifact.run_dir}")
    print(f"rows={artifact.row_count}")
    print(f"manifest={artifact.manifest_path}")
    print(f"dataset_jsonl={artifact.dataset_jsonl_path}")
    print(f"dataset_csv={artifact.dataset_csv_path}")
    print(f"report={artifact.report_path}")


if __name__ == "__main__":
    main()
