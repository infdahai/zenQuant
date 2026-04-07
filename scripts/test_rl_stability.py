#!/usr/bin/env python3
"""Monte Carlo stability test for RL tuner.

Run RL tuner N times with different random seeds, check parameter stability.

Usage:
    uv run python scripts/test_rl_stability.py --runs 5 --episodes 100
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))


def run_rl_once(seed: int, episodes: int) -> dict:
    """Run RL tuner once with specified seed."""
    import random

    import numpy as np

    from zenquant.research.rl_tuner import train_tuner

    # Set seeds
    random.seed(seed)
    np.random.seed(seed)

    print(f"\n{'=' * 60}")
    print(f"Run #{seed + 1} (seed={seed})")
    print(f"{'=' * 60}")

    result = train_tuner(total_timesteps=episodes, verbose=0)

    return {
        "seed": seed,
        "best_sharpe": result.get("best_sharpe", 0.0),
        "best_params": result.get("best_params", {}),
    }


def analyze_stability(results: list[dict]) -> None:
    """Analyze parameter stability across runs."""
    print("\n" + "=" * 70)
    print("MONTE CARLO STABILITY ANALYSIS")
    print("=" * 70)
    print()

    # Extract Sharpe values
    sharpes = [r["best_sharpe"] for r in results]

    sharpe_mean = np.mean(sharpes)
    sharpe_std = np.std(sharpes)
    sharpe_cv = sharpe_std / sharpe_mean if sharpe_mean > 0 else float("inf")

    print("Sharpe Ratio Stability:")
    print(f"  Mean:  {sharpe_mean:.2f}")
    print(f"  Std:   {sharpe_std:.2f}")
    print(f"  CV:    {sharpe_cv:.2f}")
    print()

    # CV interpretation
    if sharpe_cv < 0.20:
        sharpe_status = "✅ STABLE"
    elif sharpe_cv < 0.40:
        sharpe_status = "⚠️  MARGINAL"
    else:
        sharpe_status = "❌ UNSTABLE"

    print(f"  Status: {sharpe_status}")
    print()

    # Extract parameter values across runs
    all_params = {}
    for r in results:
        for param, value in r["best_params"].items():
            if param not in all_params:
                all_params[param] = []
            all_params[param].append(value)

    print("Parameter Stability:")
    print(f"  {'Parameter':<20} {'Mean':>8} {'Std':>8} {'CV':>8} Status")
    print(f"  {'-' * 60}")

    param_statuses = []
    for param, values in sorted(all_params.items()):
        mean = np.mean(values)
        std = np.std(values)
        cv = std / mean if mean > 0 else float("inf")

        if cv < 0.30:
            status = "✅"
        elif cv < 0.50:
            status = "⚠️ "
        else:
            status = "❌"

        param_statuses.append((param, cv, status))
        print(f"  {param:<20} {mean:>8.2f} {std:>8.3f} {cv:>8.2f} {status}")

    print()

    # Overall verdict
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    print()

    stable_params = sum(1 for _, cv, _ in param_statuses if cv < 0.30)
    total_params = len(param_statuses)

    print(f"Sharpe CV: {sharpe_cv:.2f} → {sharpe_status}")
    print(f"Stable params: {stable_params}/{total_params}")
    print()

    if sharpe_cv < 0.20 and stable_params >= total_params * 0.7:
        overall = "✅ PASS"
        verdict = "RL tuner is STABLE — parameters converge consistently"
    elif sharpe_cv < 0.40 and stable_params >= total_params * 0.5:
        overall = "⚠️  MARGINAL"
        verdict = "RL tuner is MARGINALLY STABLE — some parameter drift"
    else:
        overall = "❌ FAIL"
        verdict = "RL tuner is UNSTABLE — high variance, unreliable"

    print(f"Overall: {overall}")
    print(f"Verdict: {verdict}")
    print()

    # Recommendations
    print("Recommendations:")
    if overall == "✅ PASS":
        print("  - RL tuner is production-ready from stability perspective")
        print("  - Proceed to OOS validation (Dev set)")
    elif overall == "⚠️  MARGINAL":
        print("  - Increase training episodes (current: low for quick test)")
        print("  - Use ensemble averaging (average top-5 params)")
        print("  - Consider parameter constraints (limit search space)")
    else:
        print("  - RL tuner NOT ready for production")
        print("  - Debug: Check if RL is learning (convergence analysis)")
        print("  - Consider alternatives: Grid search, Bayesian optimization")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="RL tuner Monte Carlo stability test")
    parser.add_argument("--runs", type=int, default=5, help="Number of MC runs")
    parser.add_argument("--episodes", type=int, default=100, help="Episodes per run")
    parser.add_argument("--output", type=str, default="rl_stability.json", help="Output file")
    args = parser.parse_args()

    print("=" * 70)
    print("RL TUNER MONTE CARLO STABILITY TEST")
    print("=" * 70)
    print(f"Runs: {args.runs}")
    print(f"Episodes per run: {args.episodes}")
    print()
    print(f"Note: This will take ~{args.runs * args.episodes * 0.2} minutes")
    print("=" * 70)

    results = []
    for seed in range(args.runs):
        try:
            result = run_rl_once(seed, args.episodes)
            results.append(result)
        except Exception as e:
            print(f"\n❌ Run #{seed + 1} failed: {e}")
            results.append({"seed": seed, "best_sharpe": 0.0, "best_params": {}, "error": str(e)})

    # Save results
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n✅ Results saved to {args.output}")

    # Analyze
    analyze_stability(results)


if __name__ == "__main__":
    main()
