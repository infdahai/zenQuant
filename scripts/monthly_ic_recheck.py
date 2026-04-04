#!/usr/bin/env python3
"""Monthly IC recheck — detect signal decay automatically.

Run monthly to check if any active signal's IC has degraded.
If degradation detected → flag for review, optionally trigger AutoResearch.

This implements Level 4 AI-native: the system detects its own decay
and proposes corrections without human intervention.

Usage:
    uv run python scripts/monthly_ic_recheck.py
    uv run python scripts/monthly_ic_recheck.py --window 90  # last 90 days
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


def _rank_corr(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 30:
        return 0.0

    def _ranks(vals: list[float]) -> list[float]:
        indexed = sorted(enumerate(vals), key=lambda t: t[1])
        ranks = [0.0] * n
        for rank, (idx, _) in enumerate(indexed):
            ranks[idx] = float(rank)
        return ranks

    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    sx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    sy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    return cov / (sx * sy) if sx > 1e-12 and sy > 1e-12 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Monthly IC recheck")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--window", type=int, default=180, help="Rolling window in 4H bars (~30 days)")
    args = parser.parse_args()

    print(f"Monthly IC Recheck — {datetime.now(UTC).strftime('%Y-%m-%d')}")
    print(f"Symbol: {args.symbol}, Window: {args.window} bars")
    print("=" * 60)

    bt = HistoricalBacktest(
        HistoricalBacktestConfig(
            root_dir=Path(args.root),
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
        )
    )
    bt.run()

    packs = [p for p in bt._indicators if p.timeframe == Timeframe.TF_4H]
    if len(packs) < args.window + 10:
        print("Insufficient data for IC analysis")
        return

    signal_keys = [
        "multi_momentum_z", "cvd_slope", "funding_oi_z",
        "cycle_position", "vol_ratio_rv", "directional_consistency",
    ]

    # Compute IC for last N bars vs full history
    full_values: dict[str, list[float]] = {k: [] for k in signal_keys}
    recent_values: dict[str, list[float]] = {k: [] for k in signal_keys}
    full_fwd: list[float] = []
    recent_fwd: list[float] = []

    for i in range(len(packs) - 1):
        p = packs[i]
        close = float(p.values.get("close", p.values.get("last_price", 0)) or 0)
        next_close = float(packs[i + 1].values.get("close", packs[i + 1].values.get("last_price", 0)) or 0)
        if close <= 0 or next_close <= 0:
            continue
        fwd = (next_close - close) / close
        full_fwd.append(fwd)
        is_recent = i >= len(packs) - args.window - 1

        for k in signal_keys:
            v = float(p.values.get(k, 0) or 0)
            full_values[k].append(v)
            if is_recent:
                recent_values[k].append(v)
                if k == signal_keys[0]:
                    recent_fwd.append(fwd)

    print(f"\n{'Signal':<25} {'Full IC':>10} {'Recent IC':>10} {'Decay?':>10}")
    print("-" * 60)

    alerts = []
    for k in signal_keys:
        ic_full = _rank_corr(full_values[k], full_fwd[: len(full_values[k])])
        ic_recent = _rank_corr(recent_values[k], recent_fwd[: len(recent_values[k])])

        decay = ""
        if abs(ic_full) > 0.03 and abs(ic_recent) < 0.01:
            decay = "⚠️ DECAYED"
            alerts.append(k)
        elif abs(ic_full) > 0.03 and ic_full * ic_recent < 0:
            decay = "🔴 FLIPPED"
            alerts.append(k)
        elif abs(ic_recent) > abs(ic_full) * 1.5:
            decay = "📈 STRONGER"

        print(f"{k:<25} {ic_full:>+10.4f} {ic_recent:>+10.4f} {decay:>10}")

    print()
    if alerts:
        print(f"⚠️  ALERTS: {len(alerts)} signal(s) show degradation:")
        for a in alerts:
            print(f"    → {a}: consider reducing weight or disabling")
        print()
        print("Action: Run AutoResearch to find optimal weights")
        print("  uv run python scripts/run_auto_research.py --bayesian")
    else:
        print("✅ All signals stable. No action needed.")


if __name__ == "__main__":
    main()
