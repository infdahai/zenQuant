"""CLI entry point for the RL parameter tuner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research import train_tuner


def main() -> None:
    """Run the RL research tuner from the command line."""
    parser = argparse.ArgumentParser(
        description="Train the ZenQuant RL tuner for parameter-only research optimization."
    )
    parser.add_argument("--episodes", type=int, default=200, help="Number of RL episodes/backtests to run.")
    parser.add_argument("--output", default="", help="Optional JSON path for best params and summary metrics.")
    parser.add_argument("--quiet", action="store_true", help="Reduce PPO training log noise.")
    args = parser.parse_args()

    result = train_tuner(total_timesteps=args.episodes, verbose=0 if args.quiet else 1)
    if not result:
        raise SystemExit("RL tuner produced no results")

    print("ZenQuant RL parameter tuning complete")
    print(f"episodes={args.episodes}")
    print(f"best_sharpe={result['best_sharpe']:.4f}")
    print(f"best_params={json.dumps(result['best_params'], sort_keys=True)}")
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
