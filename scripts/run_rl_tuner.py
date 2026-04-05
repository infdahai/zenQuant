#!/usr/bin/env python3
"""Run RL parameter tuner and compare with current parameters.

Usage:
    uv run python scripts/run_rl_tuner.py
    uv run python scripts/run_rl_tuner.py --episodes 500  # More thorough
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="RL parameter tuner")
    parser.add_argument("--episodes", type=int, default=100, help="Training episodes")
    args = parser.parse_args()

    from zenquant.research.rl_tuner import train_tuner

    result = train_tuner(total_timesteps=args.episodes)

    if not result:
        print("No results produced")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("RL TUNER RESULTS")
    print("=" * 60)
    print(f"\nBest Sharpe: {result['best_sharpe']:.2f}")
    print(f"Best Params: {result['best_params']}")
    print(f"\nTop 10 configurations:")
    for i, (sharpe, params) in enumerate(result["all_results"][:10]):
        print(f"  {i + 1}. Sharpe={sharpe:.2f} — {params}")


if __name__ == "__main__":
    main()
