#!/usr/bin/env python3
"""OOS (Out-of-Sample) validation for RL tuner.

Test RL-optimized parameters on Dev set (2024-2025) to verify generalization.

Usage:
    uv run python scripts/test_rl_oos.py --rl-params rl_best.json
    uv run python scripts/test_rl_oos.py --rl-params rl_best.json --quick
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.risk import RiskOverlayConfig


def run_backtest(params: dict, period_name: str, start_ns: int, end_ns: int) -> dict:
    """Run backtest with given parameters on specified period."""
    print(f"\n[{period_name}] Running backtest...")

    # Extract RL params
    risk_config = RiskOverlayConfig(
        dd_step_1_long=params.get("dd_step_1_long", 0.15),
        vpin_toxic_threshold=params.get("vpin_threshold", 0.10),
    )

    weight_overrides = {
        "multi_momentum_signal": params.get("w_momentum", 0.45),
        "crash_reversal_signal": params.get("w_crash", 0.25),
        "cvd_signal": params.get("w_cvd", 0.25),
        "funding_alpha_signal": params.get("w_funding", 0.10),
        "crowding_signal": params.get("w_crowding", 0.10),
        "liquidity_signal": params.get("w_liquidity", 0.20),
    }

    config = HistoricalBacktestConfig(
        start_ns=start_ns,
        end_ns=end_ns,
        risk_config=risk_config,
        signal_weight_overrides=weight_overrides,
    )

    result = HistoricalBacktest(config).run()

    return {
        "period": period_name,
        "sharpe": result.metrics.sharpe,
        "pnl": result.metrics.net_pnl,
        "max_dd": result.metrics.max_drawdown,
        "trades": result.metrics.trade_count,
        "win_rate": result.metrics.win_rate if hasattr(result.metrics, "win_rate") else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RL tuner OOS validation")
    parser.add_argument(
        "--rl-params",
        type=str,
        required=True,
        help="Path to RL best params JSON (e.g., rl_best.json)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick test (Dev set only, no full period)",
    )
    args = parser.parse_args()

    # Load RL params
    rl_params_path = Path(args.rl_params)
    if not rl_params_path.exists():
        print(f"Error: {args.rl_params} not found")
        sys.exit(1)

    with open(rl_params_path) as f:
        rl_params = json.load(f)

    print("=" * 70)
    print("RL TUNER OOS VALIDATION")
    print("=" * 70)
    print()
    print("RL Parameters:")
    for k, v in sorted(rl_params.items()):
        print(f"  {k:<20} = {v}")
    print()

    # Define periods
    train_start = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1e9)
    train_end = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1e9)
    dev_start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1e9)
    dev_end = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1e9)
    full_start = train_start
    full_end = dev_end

    # Manual params (current baseline from CLAUDE.md)
    manual_params = {
        "w_momentum": 0.45,
        "w_crash": 0.25,
        "w_cvd": 0.25,
        "w_funding": 0.10,
        "w_crowding": 0.10,
        "w_liquidity": 0.20,
        "dd_step_1_long": 0.15,
        "vpin_threshold": 0.10,
    }

    results = {}

    # 1. Dev set (OOS) — PRIMARY TEST
    print("=" * 70)
    print("TEST 1: Dev Set (2024-2025) — OOS Validation")
    print("=" * 70)

    results["dev_rl"] = run_backtest(rl_params, "Dev (RL)", dev_start, dev_end)
    results["dev_manual"] = run_backtest(manual_params, "Dev (Manual)", dev_start, dev_end)

    print()
    print("Dev Set Results:")
    print(
        f"  RL params:     Sharpe={results['dev_rl']['sharpe']:.2f}, "
        f"PnL=${results['dev_rl']['pnl']:,.0f}, DD={results['dev_rl']['max_dd']:.1%}"
    )
    print(
        f"  Manual params: Sharpe={results['dev_manual']['sharpe']:.2f}, "
        f"PnL=${results['dev_manual']['pnl']:,.0f}, DD={results['dev_manual']['max_dd']:.1%}"
    )

    # 2. Train set (in-sample) — for OOS ratio
    if not args.quick:
        print()
        print("=" * 70)
        print("TEST 2: Train Set (2020-2023) — In-Sample Check")
        print("=" * 70)

        results["train_rl"] = run_backtest(rl_params, "Train (RL)", train_start, train_end)

        print()
        print("Train Set Results:")
        print(
            f"  RL params: Sharpe={results['train_rl']['sharpe']:.2f}, "
            f"PnL=${results['train_rl']['pnl']:,.0f}, DD={results['train_rl']['max_dd']:.1%}"
        )

        # Compute OOS ratio
        oos_ratio = (
            results["dev_rl"]["sharpe"] / results["train_rl"]["sharpe"] if results["train_rl"]["sharpe"] > 0 else 0.0
        )

        print()
        print(f"OOS Ratio (Dev/Train): {oos_ratio:.2f}")

    # 3. Full period — overall comparison
    if not args.quick:
        print()
        print("=" * 70)
        print("TEST 3: Full Period (2020-2026) — Overall Comparison")
        print("=" * 70)

        results["full_rl"] = run_backtest(rl_params, "Full (RL)", full_start, full_end)
        results["full_manual"] = run_backtest(manual_params, "Full (Manual)", full_start, full_end)

        print()
        print("Full Period Results:")
        print(
            f"  RL params:     Sharpe={results['full_rl']['sharpe']:.2f}, "
            f"PnL=${results['full_rl']['pnl']:,.0f}, DD={results['full_rl']['max_dd']:.1%}"
        )
        print(
            f"  Manual params: Sharpe={results['full_manual']['sharpe']:.2f}, "
            f"PnL=${results['full_manual']['pnl']:,.0f}, DD={results['full_manual']['max_dd']:.1%}"
        )

    # Analysis
    print()
    print("=" * 70)
    print("ANALYSIS")
    print("=" * 70)
    print()

    # Dev set improvement
    dev_improvement = results["dev_rl"]["sharpe"] - results["dev_manual"]["sharpe"]
    dev_improvement_pct = (
        (dev_improvement / results["dev_manual"]["sharpe"]) * 100 if results["dev_manual"]["sharpe"] > 0 else 0
    )

    print("Dev Set Improvement (RL vs Manual):")
    print(f"  Sharpe delta: {dev_improvement:+.2f} ({dev_improvement_pct:+.1f}%)")
    print()

    # OOS ratio (if available)
    if not args.quick:
        oos_ratio = (
            results["dev_rl"]["sharpe"] / results["train_rl"]["sharpe"] if results["train_rl"]["sharpe"] > 0 else 0.0
        )
        print(f"OOS Ratio (Dev/Train): {oos_ratio:.2f}")
        print()

        if oos_ratio > 0.80:
            oos_status = "✅ EXCELLENT (good generalization)"
        elif oos_ratio > 0.60:
            oos_status = "⚠️  MARGINAL (some overfitting)"
        else:
            oos_status = "❌ POOR (severe overfitting)"

        print(f"  Status: {oos_status}")
        print()

    # Verdict
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    print()

    # Criteria:
    # 1. Dev Sharpe improvement > +0.2
    # 2. OOS ratio > 0.80 (if available)
    # 3. No reward hacking (DD < 40%, trades > 20)

    criteria_passed = 0
    criteria_total = 3

    # Criterion 1: Dev improvement
    if dev_improvement > 0.2:
        print(f"✅ Dev Sharpe improvement: +{dev_improvement:.2f} (target: >+0.20)")
        criteria_passed += 1
    elif dev_improvement > 0.0:
        print(f"⚠️  Dev Sharpe improvement: +{dev_improvement:.2f} (marginal, target: >+0.20)")
    else:
        print(f"❌ Dev Sharpe improvement: {dev_improvement:.2f} (worse than manual)")

    # Criterion 2: OOS ratio (if available)
    if not args.quick:
        if oos_ratio > 0.80:
            print(f"✅ OOS ratio: {oos_ratio:.2f} (target: >0.80)")
            criteria_passed += 1
        elif oos_ratio > 0.60:
            print(f"⚠️  OOS ratio: {oos_ratio:.2f} (marginal, target: >0.80)")
        else:
            print(f"❌ OOS ratio: {oos_ratio:.2f} (overfitting, target: >0.80)")
    else:
        print("⏸️  OOS ratio: Not computed (use --full for this)")
        criteria_total = 2  # Adjust total if OOS not computed

    # Criterion 3: Reward hacking
    dev_dd = results["dev_rl"]["max_dd"]
    dev_trades = results["dev_rl"]["trades"]

    if dev_dd < 0.40 and dev_trades > 20:
        print(f"✅ No reward hacking: DD={dev_dd:.1%}, trades={dev_trades}")
        criteria_passed += 1
    elif dev_dd < 0.50 or dev_trades > 10:
        print(f"⚠️  Marginal metrics: DD={dev_dd:.1%}, trades={dev_trades}")
    else:
        print(f"❌ Reward hacking detected: DD={dev_dd:.1%}, trades={dev_trades}")

    print()
    print(f"Criteria passed: {criteria_passed}/{criteria_total}")
    print()

    # Final recommendation
    if criteria_passed == criteria_total:
        verdict = "✅ DEPLOY RL PARAMS"
        recommendation = "RL parameters generalize well and improve over manual baseline."
    elif criteria_passed >= criteria_total * 0.6:
        verdict = "⚠️  MARGINAL — Consider A/B test"
        recommendation = "Some improvement but not decisive. A/B test recommended (10% capital on RL, 90% on manual)."
    else:
        verdict = "❌ REJECT RL PARAMS"
        recommendation = (
            "RL parameters do not improve over manual baseline or show overfitting. Stick with manual params."
        )

    print(f"Verdict: {verdict}")
    print(f"Recommendation: {recommendation}")
    print()

    # Save results
    output_path = Path("rl_oos_results.json")
    with open(output_path, "w") as f:
        json.dump(
            {
                "rl_params": rl_params,
                "manual_params": manual_params,
                "results": results,
                "verdict": verdict,
                "recommendation": recommendation,
            },
            f,
            indent=2,
        )

    print(f"Results saved to {output_path}")
    print()

    # Update SNOOPING_LOG.md reminder
    print("=" * 70)
    print("IMPORTANT: Update SNOOPING_LOG.md")
    print("=" * 70)
    print()
    print("This test used Dev set (2024-2025). Please update SNOOPING_LOG.md with:")
    print(f"  - Outcome: {verdict}")
    print(f"  - Dev Sharpe (RL): {results['dev_rl']['sharpe']:.2f}")
    print(f"  - Dev Sharpe (Manual): {results['dev_manual']['sharpe']:.2f}")
    if not args.quick:
        print(f"  - OOS ratio: {oos_ratio:.2f}")
    print()


if __name__ == "__main__":
    main()
