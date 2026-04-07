#!/usr/bin/env python3
"""BTC/ETH cointegration analysis — feasibility check for pairs trading.

Tests whether BTC and ETH prices are cointegrated (Engle-Granger method).
If cointegrated → pairs trading viable (mean-reversion on spread).
If not → don't invest 8-15 days in architecture changes.

Criteria for GO:
  - Engle-Granger p-value < 0.05
  - Half-life < 30 days (spread reverts fast enough for 4H trading)
  - Rolling stability > 70% (cointegration holds across time)

Usage:
    uv run python scripts/cointegration_analysis.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_close_prices(symbol: str) -> list[tuple[int, float]]:
    """Load 4H close prices for a symbol."""
    path = Path(f"data/raw/binance/klines/{symbol}/4h/data.parquet")
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)
    df = pq.read_table(path).to_pandas()
    df = df.sort_values("close_time_ns")
    return [(int(row["close_time_ns"]), float(row["close"])) for _, row in df.iterrows()]


def align_series(btc: list[tuple[int, float]], eth: list[tuple[int, float]]) -> tuple[list[float], list[float]]:
    """Align two time series by timestamp."""
    btc_dict = dict(btc)
    eth_dict = dict(eth)
    common = sorted(set(btc_dict.keys()) & set(eth_dict.keys()))
    return [btc_dict[t] for t in common], [eth_dict[t] for t in common]


def ols_regression(x: list[float], y: list[float]) -> tuple[float, float, list[float]]:
    """Simple OLS: y = alpha + beta * x. Returns alpha, beta, residuals."""
    n = len(x)
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    var_x = sum((x[i] - mx) ** 2 for i in range(n))
    beta = cov / var_x if var_x > 0 else 0
    alpha = my - beta * mx
    residuals = [y[i] - alpha - beta * x[i] for i in range(n)]
    return alpha, beta, residuals


def adf_test_manual(series: list[float]) -> tuple[float, bool]:
    """Simplified ADF test: regress Δy on y_{t-1}, check t-statistic.

    Returns (t_stat, is_stationary).
    Critical values (approximate): -2.86 (5%), -3.43 (1%)
    """
    n = len(series)
    if n < 30:
        return 0.0, False

    dy = [series[i] - series[i - 1] for i in range(1, n)]
    y_lag = series[:-1]

    # OLS: dy = a + b * y_lag
    _, beta, residuals = ols_regression(y_lag, dy)

    # t-statistic for beta
    n_r = len(residuals)
    sse = sum(r * r for r in residuals)
    mse = sse / max(1, n_r - 2)
    var_x = sum((y_lag[i] - sum(y_lag) / len(y_lag)) ** 2 for i in range(len(y_lag)))
    se_beta = math.sqrt(mse / var_x) if var_x > 0 and mse > 0 else 1.0
    t_stat = beta / se_beta if se_beta > 0 else 0.0

    # Critical value at 5% for ~1000+ obs ≈ -2.86
    return t_stat, t_stat < -2.86


def half_life(residuals: list[float]) -> float:
    """Estimate mean-reversion half-life from residuals (in bars)."""
    n = len(residuals)
    if n < 30:
        return float("inf")

    dy = [residuals[i] - residuals[i - 1] for i in range(1, n)]
    y_lag = residuals[:-1]

    _, beta, _ = ols_regression(y_lag, dy)

    if beta >= 0:
        return float("inf")  # Not mean-reverting

    hl = -math.log(2) / beta
    return hl


def rolling_cointegration(btc: list[float], eth: list[float], window: int = 1500) -> float:
    """Test cointegration stability: what % of rolling windows pass ADF test."""
    n = len(btc)
    if n < window + 100:
        return 0.0

    passes = 0
    total = 0
    step = window // 4  # 25% overlap

    for start in range(0, n - window, step):
        end = start + window
        _, _, residuals = ols_regression(btc[start:end], eth[start:end])
        _, is_stationary = adf_test_manual(residuals)
        if is_stationary:
            passes += 1
        total += 1

    return passes / total if total > 0 else 0.0


def main() -> None:
    print("BTC/ETH Cointegration Analysis")
    print("=" * 60)

    # Load data
    btc_raw = load_close_prices("BTCUSDT")
    eth_raw = load_close_prices("ETHUSDT")
    btc_prices, eth_prices = align_series(btc_raw, eth_raw)

    print(f"Aligned bars: {len(btc_prices)} (4H)")
    print(f"BTC range: ${min(btc_prices):,.0f} — ${max(btc_prices):,.0f}")
    print(f"ETH range: ${min(eth_prices):,.0f} — ${max(eth_prices):,.0f}")
    print()

    # Train set only (2020-2023)
    # Assume first 60% is train (roughly 2020-2023 out of 2020-2025)
    train_cutoff = int(len(btc_prices) * 0.6)
    btc_train = btc_prices[:train_cutoff]
    eth_train = eth_prices[:train_cutoff]

    print(f"Train set: {train_cutoff} bars (~{train_cutoff / 6 / 365:.1f} years)")

    # 1. Engle-Granger test
    alpha, beta, residuals = ols_regression(btc_train, eth_train)
    t_stat, is_stationary = adf_test_manual(residuals)

    print("\n--- Engle-Granger Cointegration Test ---")
    print(f"  Hedge ratio (beta): {beta:.4f}")
    print(f"  ADF t-statistic: {t_stat:.2f}")
    print(f"  Stationary (p<0.05): {'YES' if is_stationary else 'NO'}")

    # 2. Half-life
    hl = half_life(residuals)
    hl_days = hl / 6  # 4H bars → days
    print("\n--- Mean-Reversion Half-Life ---")
    print(f"  Half-life: {hl:.0f} bars ({hl_days:.1f} days)")
    print(f"  Fast enough for 4H: {'YES' if hl_days < 30 else 'NO'}")

    # 3. Rolling stability
    stability = rolling_cointegration(btc_train, eth_train)
    print("\n--- Rolling Stability ---")
    print(f"  Windows passing ADF: {stability:.0%}")
    print(f"  Stable (>70%): {'YES' if stability > 0.7 else 'NO'}")

    # Verdict
    print(f"\n{'=' * 60}")
    go = is_stationary and hl_days < 30 and stability > 0.7
    if go:
        print("VERDICT: GO — Cointegration confirmed.")
        print("  Pairs trading is viable. Proceed with architecture design.")
    else:
        print("VERDICT: NO-GO — Cointegration insufficient.")
        reasons = []
        if not is_stationary:
            reasons.append(f"ADF not significant (t={t_stat:.2f}, need <-2.86)")
        if hl_days >= 30:
            reasons.append(f"Half-life too slow ({hl_days:.0f} days, need <30)")
        if stability <= 0.7:
            reasons.append(f"Unstable ({stability:.0%}, need >70%)")
        for r in reasons:
            print(f"  - {r}")
        print("  Do NOT invest 8-15 days in pairs trading infrastructure.")


if __name__ == "__main__":
    main()
