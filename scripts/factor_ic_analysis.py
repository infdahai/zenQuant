#!/usr/bin/env python3
"""Factor IC (Information Coefficient) analysis tool.

Computes the predictive power of each signal by correlating signal values
at time t with forward returns at t+1. Prevents factor mirages by
identifying which signals actually carry alpha vs statistical noise.

Usage:
    uv run python scripts/factor_ic_analysis.py
    uv run python scripts/factor_ic_analysis.py --output results/ic_report.json

Reference: López de Prado (2018) - "Advances in Financial Machine Learning"
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


@dataclass(slots=True)
class FactorIC:
    """IC analysis result for one factor."""

    name: str
    ic_mean: float  # Average rank IC
    ic_std: float  # IC standard deviation
    ic_ir: float  # IC information ratio (ic_mean / ic_std)
    hit_rate: float  # Fraction of periods where IC has correct sign
    n_observations: int
    verdict: str  # STRONG / WEAK / NOISE


def _rank_corr(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation (non-parametric, robust to outliers)."""
    n = len(x)
    if n < 5:
        return 0.0

    def _ranks(vals: list[float]) -> list[float]:
        indexed = sorted(enumerate(vals), key=lambda t: t[1])
        ranks = [0.0] * n
        for rank, (idx, _) in enumerate(indexed):
            ranks[idx] = float(rank)
        return ranks

    rx = _ranks(x)
    ry = _ranks(y)

    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    sx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    sy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))

    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return cov / (sx * sy)


def _rolling_ic(
    signal_values: list[float],
    forward_returns: list[float],
    window: int = 60,
) -> list[float]:
    """Compute rolling rank IC over sliding windows."""
    ics = []
    for i in range(window, len(signal_values)):
        x = signal_values[i - window : i]
        y = forward_returns[i - window : i]
        ics.append(_rank_corr(x, y))
    return ics


def analyze_factors(
    data_root: Path = Path("data/raw/binance"),
    symbol: str = "BTCUSDT",
) -> list[FactorIC]:
    """Run IC analysis on all indicator pack values."""

    # Run backtest to collect indicator packs
    config = HistoricalBacktestConfig(
        root_dir=data_root,
        symbol=symbol,
        portfolio_symbol=f"{symbol}_PERP",
    )
    bt = HistoricalBacktest(config)
    result = bt.run()

    packs = bt._indicators
    if len(packs) < 100:
        print(f"Warning: only {len(packs)} indicator packs. Need 100+ for reliable IC.")
        return []

    # Extract 4H packs only (execution timeframe)
    from zenquant.domain import Timeframe

    packs_4h = [p for p in packs if p.timeframe == Timeframe.TF_4H]
    if len(packs_4h) < 100:
        print(f"Warning: only {len(packs_4h)} 4H packs.")
        return []

    # Compute forward returns: ret_t+1 = (close_t+1 - close_t) / close_t
    closes = []
    for p in packs_4h:
        close = p.values.get("close")
        if close is None:
            # Fallback: use last price from any available field
            close = p.values.get("last_price", 0)
        closes.append(float(close) if close else 0.0)

    forward_returns: list[float] = []
    for i in range(len(closes) - 1):
        if closes[i] > 0:
            forward_returns.append((closes[i + 1] - closes[i]) / closes[i])
        else:
            forward_returns.append(0.0)

    # Identify signal-related values in indicator packs
    signal_keys = [
        "ob_liq_z",
        "cvd_slope",
        "funding_oi_z",
        "momentum_roc_z",
        "multi_momentum_z",
        "realized_vol",
        "zen_pulse",
        # Macro
        "vix_z",
        "dxy_z",
        "yield_10y_z",
        # Options
        "atm_iv_z",
        "skew_z",
        # New indicators
        "cycle_position",
        "ou_kappa",
        "fragility_ratio",
        "funding_oi_raw",
        # P1: IV Skew velocity
        "skew_velocity",
        # P3: ETH→BTC Transfer Entropy
        "te_eth_to_btc",
        "eth_momentum",
    ]

    results: list[FactorIC] = []

    for key in signal_keys:
        # Extract signal values
        values = []
        for p in packs_4h:
            v = p.values.get(key)
            values.append(float(v) if v is not None else 0.0)

        # Skip if all zeros (no data for this indicator)
        if all(abs(v) < 1e-12 for v in values):
            continue

        # Align: signal at t, return at t+1 (drop last signal, drop first return)
        signal_aligned = values[:-1]
        return_aligned = forward_returns

        n = min(len(signal_aligned), len(return_aligned))
        signal_aligned = signal_aligned[:n]
        return_aligned = return_aligned[:n]

        if n < 30:
            continue

        # Overall IC
        ic = _rank_corr(signal_aligned, return_aligned)

        # Rolling IC for stability analysis
        rolling_ics = _rolling_ic(signal_aligned, return_aligned, window=60)

        if rolling_ics:
            ic_mean = sum(rolling_ics) / len(rolling_ics)
            ic_std = math.sqrt(sum((x - ic_mean) ** 2 for x in rolling_ics) / len(rolling_ics))
            ic_ir = ic_mean / ic_std if ic_std > 1e-9 else 0.0
            hit_rate = sum(1 for x in rolling_ics if x > 0) / len(rolling_ics)
        else:
            ic_mean = ic
            ic_std = 0.0
            ic_ir = 0.0
            hit_rate = 0.5

        # Verdict
        if abs(ic_mean) > 0.05 and abs(ic_ir) > 0.3:
            verdict = "STRONG"
        elif abs(ic_mean) > 0.02:
            verdict = "WEAK"
        else:
            verdict = "NOISE"

        results.append(
            FactorIC(
                name=key,
                ic_mean=round(ic_mean, 4),
                ic_std=round(ic_std, 4),
                ic_ir=round(ic_ir, 4),
                hit_rate=round(hit_rate, 4),
                n_observations=n,
                verdict=verdict,
            )
        )

    # Sort by |IC| descending
    results.sort(key=lambda r: abs(r.ic_mean), reverse=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Factor IC analysis")
    parser.add_argument("--root", default="data/raw/binance", help="Data root directory")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--output", default="", help="Output JSON path")
    args = parser.parse_args()

    print("Running factor IC analysis...")
    print(f"Data: {args.root}/{args.symbol}")
    print()

    results = analyze_factors(Path(args.root), args.symbol)

    if not results:
        print("No results. Check data availability.")
        return

    print(f"{'Factor':<25} {'IC_mean':>8} {'IC_std':>8} {'IC_IR':>8} {'Hit%':>6} {'N':>6} {'Verdict'}")
    print("=" * 80)
    for r in results:
        print(
            f"{r.name:<25} {r.ic_mean:>8.4f} {r.ic_std:>8.4f} {r.ic_ir:>8.4f} "
            f"{r.hit_rate * 100:>5.1f}% {r.n_observations:>6} {r.verdict}"
        )

    strong = [r for r in results if r.verdict == "STRONG"]
    weak = [r for r in results if r.verdict == "WEAK"]
    noise = [r for r in results if r.verdict == "NOISE"]

    print()
    print(f"STRONG: {len(strong)} factors (|IC|>0.05, IR>0.3)")
    print(f"WEAK:   {len(weak)} factors (|IC|>0.02)")
    print(f"NOISE:  {len(noise)} factors (|IC|<0.02 — consider removing)")

    if args.output:
        payload = {
            "factors": [asdict(r) for r in results],
            "summary": {
                "strong": len(strong),
                "weak": len(weak),
                "noise": len(noise),
                "total": len(results),
            },
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(payload, indent=2))
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
