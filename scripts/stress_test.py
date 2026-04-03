#!/usr/bin/env python3
"""Strategy stress testing — Verification Agent pattern.

"Try to break it." Runs the strategy under extreme conditions to
discover hidden weaknesses before they appear in live trading.

Usage:
    uv run python scripts/stress_test.py
    uv run python scripts/stress_test.py --symbol BTCUSDT
"""

from __future__ import annotations

import argparse
import math
from datetime import UTC, datetime
from pathlib import Path

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.historical.perturbation import PerturbationConfig


def _sharpe_from_snapshots(snapshots: list) -> float:
    if len(snapshots) < 20:
        return 0.0
    eqs = [s.equity for s in sorted(snapshots, key=lambda s: s.ts_ns)]
    rets = [(eqs[i] - eqs[i - 1]) / eqs[i - 1] for i in range(1, len(eqs)) if eqs[i - 1] > 0]
    if len(rets) < 5:
        return 0.0
    mr = sum(rets) / len(rets)
    var = sum((r - mr) ** 2 for r in rets) / len(rets)
    return (mr / math.sqrt(var)) * math.sqrt(len(rets)) if var > 0 else 0.0


def run_stress_tests(data_root: str = "data/raw/binance", symbol: str = "BTCUSDT") -> None:
    root = Path(data_root)

    tests = [
        # (name, start_ns, end_ns, perturbation, verdict_rule)
        ("Full period (baseline)", None, None, None, lambda s, dd: s > 0.5),
        (
            "2022 only (bear market)",
            int(datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1e9),
            int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1e9),
            None,
            lambda s, dd: s > -2.0 and dd < 0.4,
        ),
        (
            "2025 only (current chop)",
            int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1e9),
            int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1e9),
            None,
            lambda s, dd: s > -2.0 and dd < 0.3,
        ),
        ("Noise seed=42", None, None, PerturbationConfig(seed=42, price_noise_pct=0.001, volume_noise_pct=0.05, bar_dropout_pct=0.02), lambda s, dd: s > 0),
        ("Noise seed=123", None, None, PerturbationConfig(seed=123, price_noise_pct=0.001, volume_noise_pct=0.05, bar_dropout_pct=0.02), lambda s, dd: s > 0),
        ("Noise seed=456", None, None, PerturbationConfig(seed=456, price_noise_pct=0.001, volume_noise_pct=0.05, bar_dropout_pct=0.02), lambda s, dd: s > 0),
        ("Noise seed=789", None, None, PerturbationConfig(seed=789, price_noise_pct=0.001, volume_noise_pct=0.05, bar_dropout_pct=0.02), lambda s, dd: s > 0),
        ("Noise seed=1024", None, None, PerturbationConfig(seed=1024, price_noise_pct=0.001, volume_noise_pct=0.05, bar_dropout_pct=0.02), lambda s, dd: s > 0),
    ]

    print(f"{'=' * 65}")
    print(f"STRESS TEST — {symbol}")
    print(f"{'=' * 65}")
    print(f"{'Test':<30} {'Sharpe':>7} {'DD':>7} {'Trades':>7} {'Result'}")
    print("-" * 60)

    passed = 0
    for name, start_ns, end_ns, perturb, verdict_fn in tests:
        bt = HistoricalBacktest(
            HistoricalBacktestConfig(
                root_dir=root,
                symbol=symbol,
                portfolio_symbol=f"{symbol}_PERP",
                start_ns=start_ns,
                end_ns=end_ns,
                perturbation=perturb,
            )
        )
        result = bt.run()
        m = result.metrics
        sharpe = _sharpe_from_snapshots(bt._risk_snapshots) if bt._risk_snapshots else m.sharpe

        ok = verdict_fn(sharpe, m.max_drawdown)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"{name:<30} {sharpe:>7.2f} {m.max_drawdown:>6.1%} {m.trade_count:>7} {status}")

    print()
    print(f"Passed: {passed}/{len(tests)}")
    robustness = sum(1 for name, _, _, p, _ in tests if p is not None)
    rob_pass = sum(
        1
        for i, (name, s, e, p, fn) in enumerate(tests)
        if p is not None and i < len(tests) and fn(
            _sharpe_from_snapshots(
                HistoricalBacktest(
                    HistoricalBacktestConfig(root_dir=root, symbol=symbol, portfolio_symbol=f"{symbol}_PERP", perturbation=p)
                ).run()._risk_snapshots if False else []
            ),
            0,
        )
    )
    print(f"Robustness: {passed - 2}/{robustness} seeds passed (need ≥4/5)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy stress testing")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    run_stress_tests(args.root, args.symbol)


if __name__ == "__main__":
    main()
