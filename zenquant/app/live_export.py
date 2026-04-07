"""CLI entrypoint for exporting live/paper audit data into research artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from zenquant.research.live_export import LiveExportConfig, export_live_training_data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export live/paper audit logs into a research dataset")
    parser.add_argument("--dsn", required=True, help="PostgreSQL DSN for the live/paper audit database")
    parser.add_argument("--symbol", default="BTCUSDT_PERP")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--output-root", type=Path, default=Path("results/live_training_runs"))
    parser.add_argument(
        "--horizons",
        default="1,3,6",
        help="Comma-separated forward return horizons, e.g. 1,3,6",
    )
    parser.add_argument("--label", default="", help="Run label such as paper-live")
    parser.add_argument("--start-ns", type=int, default=None)
    parser.add_argument("--end-ns", type=int, default=None)
    return parser


def _parse_horizons(raw: str) -> tuple[int, ...]:
    parts = [part.strip() for part in raw.split(",")]
    horizons = tuple(int(part) for part in parts if part)
    if not horizons:
        raise ValueError("at least one horizon is required")
    return horizons


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    artifact = export_live_training_data(
        LiveExportConfig(
            dsn=args.dsn,
            symbol=args.symbol,
            timeframe=args.timeframe,
            output_root=args.output_root,
            horizons=_parse_horizons(args.horizons),
            run_label=args.label,
            start_ns=args.start_ns,
            end_ns=args.end_ns,
        )
    )
    print(artifact.run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
