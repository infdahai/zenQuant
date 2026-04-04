#!/usr/bin/env python3
"""Adversarial stress test — GAN-style evaluator for strategy robustness.

Anthropic harness principle: Generator should not self-evaluate.
This script constructs adversarial market conditions designed to break the strategy.

Two modes:

1. SYNTHETIC (default):
   - Regime Whipsaw — rapid trend reversals every 20 bars
   - Liquidity Vacuum — sudden price gaps (-5% to -10%)
   - Crowding Trap — extreme funding/OI but price continues trending

2. HISTORICAL (--historical):
   - Replay actual worst DD periods from full backtest
   - Requires: run `python scripts/extract_worst_periods.py` first
   - Reads: tests/adversarial/worst_periods.json

Sprint contracts (hard thresholds):
- Synthetic: Sharpe > 0.0 (must remain profitable)
- Historical: Sharpe > 0.0 (must survive actual worst periods)

Usage:
    uv run python scripts/adversarial_stress.py              # Synthetic scenarios
    uv run python scripts/adversarial_stress.py --historical # Historical replay
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import (
    HistoricalBacktest,
    HistoricalBacktestConfig,
    HistoricalDatasetLayout,
    HistoricalFundingRow,
    HistoricalKlineRow,
    HistoricalOpenInterestRow,
    write_parquet_rows,
)


@dataclass
class AdversarialResult:
    name: str
    sharpe: float
    max_dd: float
    pnl: float
    trades: int
    threshold_sharpe: float
    threshold_dd: float
    passed: bool
    detail: str = ""


def _base_ns() -> int:
    return int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)


def _4h_ns() -> int:
    return 4 * 60 * 60 * 1_000_000_000


def _1d_ns() -> int:
    return 24 * 60 * 60 * 1_000_000_000


# ── Scenario 1: Regime Whipsaw ──


def _whipsaw_klines(days: int = 180) -> tuple[list[HistoricalKlineRow], list[HistoricalKlineRow]]:
    """Generate sawtooth price pattern — trend reversal every 20 4H bars."""
    base = _base_ns()
    daily: list[HistoricalKlineRow] = []
    four_h: list[HistoricalKlineRow] = []

    price = 42000.0
    direction = 1.0
    cycle_len = 20  # bars per direction

    for idx in range(days * 6):
        # Reverse every cycle_len bars
        if idx % cycle_len == 0 and idx > 0:
            direction *= -1

        move = direction * 50 + math.sin(idx / 3.0) * 20
        open_px = price
        price += move
        close = price
        high = max(open_px, close) + 30
        low = min(open_px, close) - 30

        open_time = base + idx * _4h_ns()
        four_h.append(HistoricalKlineRow(
            symbol="BTCUSDT", timeframe="4h",
            open_time_ns=open_time, close_time_ns=open_time + _4h_ns(),
            open=round(open_px, 2), high=round(high, 2),
            low=round(low, 2), close=round(close, 2),
            volume=50.0, trade_count=500,
        ))

    # Daily from 4H
    for d in range(days):
        d_bars = four_h[d * 6:(d + 1) * 6]
        if not d_bars:
            continue
        daily.append(HistoricalKlineRow(
            symbol="BTCUSDT", timeframe="1d",
            open_time_ns=d_bars[0].open_time_ns,
            close_time_ns=d_bars[-1].close_time_ns,
            open=d_bars[0].open, high=max(b.high for b in d_bars),
            low=min(b.low for b in d_bars), close=d_bars[-1].close,
            volume=sum(b.volume for b in d_bars), trade_count=3000,
        ))

    return daily, four_h


# ── Scenario 2: Liquidity Vacuum ──


def _vacuum_klines(days: int = 180) -> tuple[list[HistoricalKlineRow], list[HistoricalKlineRow]]:
    """Normal uptrend with sudden -5% to -10% gaps at random intervals."""
    import random
    rng = random.Random(42)
    base = _base_ns()
    daily: list[HistoricalKlineRow] = []
    four_h: list[HistoricalKlineRow] = []

    price = 42000.0
    gap_intervals = sorted(rng.sample(range(50, days * 6 - 50), 8))  # 8 gaps

    for idx in range(days * 6):
        open_px = price

        if idx in gap_intervals:
            gap_pct = rng.uniform(-0.10, -0.05)
            price *= (1 + gap_pct)
        else:
            price += rng.gauss(5, 40)  # slight uptrend with noise

        close = price
        high = max(open_px, close) + rng.uniform(10, 50)
        low = min(open_px, close) - rng.uniform(10, 50)

        open_time = base + idx * _4h_ns()
        four_h.append(HistoricalKlineRow(
            symbol="BTCUSDT", timeframe="4h",
            open_time_ns=open_time, close_time_ns=open_time + _4h_ns(),
            open=round(open_px, 2), high=round(high, 2),
            low=round(low, 2), close=round(close, 2),
            volume=round(50 + rng.gauss(0, 10), 2), trade_count=500,
        ))

    for d in range(days):
        d_bars = four_h[d * 6:(d + 1) * 6]
        if not d_bars:
            continue
        daily.append(HistoricalKlineRow(
            symbol="BTCUSDT", timeframe="1d",
            open_time_ns=d_bars[0].open_time_ns,
            close_time_ns=d_bars[-1].close_time_ns,
            open=d_bars[0].open, high=max(b.high for b in d_bars),
            low=min(b.low for b in d_bars), close=d_bars[-1].close,
            volume=sum(b.volume for b in d_bars), trade_count=3000,
        ))

    return daily, four_h


# ── Scenario 3: Crowding Trap ──


def _crowding_trap_funding(days: int = 180) -> list[HistoricalFundingRow]:
    """Extreme funding rates that DON'T predict reversals — a trap."""
    base = _base_ns()
    interval = 8 * 60 * 60 * 1_000_000_000
    rows: list[HistoricalFundingRow] = []
    for i in range(days * 3):
        # Extreme positive funding (suggests crowded longs) but market keeps going up
        rate = 0.001 + math.sin(i / 5.0) * 0.0005  # 10x normal funding
        rows.append(HistoricalFundingRow(
            symbol="BTCUSDT",
            funding_time_ns=base + i * interval,
            funding_rate=round(rate, 8),
        ))
    return rows


def _trending_klines(days: int = 180) -> tuple[list[HistoricalKlineRow], list[HistoricalKlineRow]]:
    """Steady uptrend (crowding trap: funding extreme but price keeps rising)."""
    base = _base_ns()
    daily: list[HistoricalKlineRow] = []
    four_h: list[HistoricalKlineRow] = []
    price = 42000.0

    for idx in range(days * 6):
        open_px = price
        price += 15 + math.sin(idx / 10.0) * 5  # steady uptrend
        close = price
        high = max(open_px, close) + 25
        low = min(open_px, close) - 20
        open_time = base + idx * _4h_ns()
        four_h.append(HistoricalKlineRow(
            symbol="BTCUSDT", timeframe="4h",
            open_time_ns=open_time, close_time_ns=open_time + _4h_ns(),
            open=round(open_px, 2), high=round(high, 2),
            low=round(low, 2), close=round(close, 2),
            volume=50.0, trade_count=500,
        ))

    for d in range(days):
        d_bars = four_h[d * 6:(d + 1) * 6]
        if not d_bars:
            continue
        daily.append(HistoricalKlineRow(
            symbol="BTCUSDT", timeframe="1d",
            open_time_ns=d_bars[0].open_time_ns,
            close_time_ns=d_bars[-1].close_time_ns,
            open=d_bars[0].open, high=max(b.high for b in d_bars),
            low=min(b.low for b in d_bars), close=d_bars[-1].close,
            volume=sum(b.volume for b in d_bars), trade_count=3000,
        ))

    return daily, four_h


def _normal_funding(days: int = 180) -> list[HistoricalFundingRow]:
    base = _base_ns()
    interval = 8 * 60 * 60 * 1_000_000_000
    return [
        HistoricalFundingRow(
            symbol="BTCUSDT",
            funding_time_ns=base + i * interval,
            funding_rate=round(0.0001 + math.sin(i / 8.0) * 0.00003, 8),
        )
        for i in range(days * 3)
    ]


def _normal_oi(days: int = 180) -> list[HistoricalOpenInterestRow]:
    base = _base_ns()
    interval = _4h_ns()
    return [
        HistoricalOpenInterestRow(
            symbol="BTCUSDT", timeframe="4h",
            ts_ns=base + i * interval,
            open_interest=round(1_500_000_000 + i * 1_000_000, 2),
        )
        for i in range(days * 6)
    ]


def run_scenario(
    name: str,
    daily: list[HistoricalKlineRow],
    four_h: list[HistoricalKlineRow],
    funding: list[HistoricalFundingRow],
    oi: list[HistoricalOpenInterestRow],
    threshold_sharpe: float,
    threshold_dd: float,
) -> AdversarialResult:
    """Run one adversarial scenario and return results."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        layout = HistoricalDatasetLayout(root_dir=root)
        write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), daily)
        write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), four_h)
        write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), funding)
        write_parquet_rows(layout.open_interest_path("BTCUSDT", "4h", "2024-01"), oi)

        result = HistoricalBacktest(
            HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT")
        ).run()

    m = result.metrics
    passed = m.sharpe >= threshold_sharpe and m.max_drawdown <= threshold_dd

    return AdversarialResult(
        name=name,
        sharpe=m.sharpe,
        max_dd=m.max_drawdown,
        pnl=m.net_pnl,
        trades=m.trade_count,
        threshold_sharpe=threshold_sharpe,
        threshold_dd=threshold_dd,
        passed=passed,
        detail=f"Risk flags: {result.diagnostics.risk_flag_counts}",
    )


def run_historical_replay(periods_file: Path = Path("tests/adversarial/worst_periods.json")) -> list[AdversarialResult]:
    """Replay actual worst DD periods from backtest history.

    Uses real market data from periods where strategy performed worst.
    More realistic than synthetic scenarios.
    """
    if not periods_file.exists():
        print(f"ERROR: {periods_file} not found")
        print("Run: python scripts/extract_worst_periods.py first")
        sys.exit(1)

    data = json.loads(periods_file.read_text())
    worst_periods = data['worst_periods']

    print(f"Loaded {len(worst_periods)} worst periods from {periods_file}")
    print(f"Full backtest: Sharpe {data['full_backtest']['sharpe']:.2f}, "
          f"Max DD {data['full_backtest']['max_dd']:.1%}")

    results: list[AdversarialResult] = []
    for period in worst_periods:
        name = f"HISTORICAL_{period['description'].replace(' ', '_')}"
        start_ns = period['start_ns']
        end_ns = period['end_ns']

        print(f"\n[{period['rank']}/{len(worst_periods)}] {period['description']} "
              f"(Original DD: {period['max_dd']:.1%})...")

        # Run backtest on this specific time window
        config = HistoricalBacktestConfig(
            root_dir=Path("data/raw/binance"),
            symbol="BTCUSDT",
            start_ns=start_ns,
            end_ns=end_ns,
        )
        result = HistoricalBacktest(config).run()
        m = result.metrics

        # Historical replay pass criteria: must remain profitable even in worst period
        passed = m.sharpe >= 0.0 and m.max_drawdown <= 0.25

        adv_result = AdversarialResult(
            name=name,
            sharpe=m.sharpe,
            max_dd=m.max_drawdown,
            pnl=m.net_pnl,
            trades=m.trade_count,
            threshold_sharpe=0.0,
            threshold_dd=0.25,
            passed=passed,
            detail=f"Original DD: {period['max_dd']:.1%}, Bars: {period['bars']}",
        )
        results.append(adv_result)

        status = "PASS" if passed else "FAIL"
        print(f"  Sharpe={m.sharpe:.2f}, DD={m.max_drawdown:.1%}, PnL=${m.net_pnl:,.0f} → {status}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Adversarial stress testing")
    parser.add_argument("--historical", action="store_true",
                       help="Run historical replay instead of synthetic scenarios")
    parser.add_argument("--periods-file", type=Path, default=Path("tests/adversarial/worst_periods.json"),
                       help="Path to worst periods JSON (for --historical)")
    args = parser.parse_args()

    print("Adversarial Stress Test — GAN-Style Evaluator")
    print("=" * 65)

    if args.historical:
        print("Mode: HISTORICAL REPLAY (actual worst periods)")
        scenarios = run_historical_replay(args.periods_file)
    else:
        print("Mode: SYNTHETIC (invented adversarial scenarios)")
        scenarios = []

        # Scenario 1: Regime Whipsaw
        print("\n[1/3] Regime Whipsaw (rapid trend reversals)...")
        daily, four_h = _whipsaw_klines()
        r1 = run_scenario(
            "REGIME_WHIPSAW", daily, four_h,
            _normal_funding(), _normal_oi(),
            threshold_sharpe=0.0, threshold_dd=0.25,
        )
        scenarios.append(r1)
        print(f"  Sharpe={r1.sharpe:.2f}, DD={r1.max_dd:.1%} → {'PASS' if r1.passed else 'FAIL'}")

        # Scenario 2: Liquidity Vacuum
        print("\n[2/3] Liquidity Vacuum (sudden price gaps)...")
        daily, four_h = _vacuum_klines()
        r2 = run_scenario(
            "LIQUIDITY_VACUUM", daily, four_h,
            _normal_funding(), _normal_oi(),
            threshold_sharpe=0.0, threshold_dd=0.20,
        )
        scenarios.append(r2)
        print(f"  Sharpe={r2.sharpe:.2f}, DD={r2.max_dd:.1%} → {'PASS' if r2.passed else 'FAIL'}")

        # Scenario 3: Crowding Trap
        print("\n[3/3] Crowding Trap (extreme funding, price keeps trending)...")
        daily, four_h = _trending_klines()
        r3 = run_scenario(
            "CROWDING_TRAP", daily, four_h,
            _crowding_trap_funding(), _normal_oi(),
            threshold_sharpe=0.0, threshold_dd=0.15,
        )
        scenarios.append(r3)
        print(f"  Sharpe={r3.sharpe:.2f}, DD={r3.max_dd:.1%} → {'PASS' if r3.passed else 'FAIL'}")

    # Summary
    print("\n" + "=" * 65)
    print(f"{'Scenario':<30} {'Sharpe':>8} {'Max DD':>8} {'Trades':>8} {'Result':>8}")
    print("-" * 65)
    for s in scenarios:
        status = "PASS" if s.passed else "FAIL"
        print(f"{s.name:<30} {s.sharpe:>8.2f} {s.max_dd:>7.1%} {s.trades:>8} {status:>8}")

    passed = sum(1 for s in scenarios if s.passed)
    total = len(scenarios)
    print(f"\n{passed}/{total} scenarios passed.")

    if passed < total:
        print("\nVulnerabilities detected:")
        for s in scenarios:
            if not s.passed:
                print(f"  {s.name}: Sharpe={s.sharpe:.2f} (need>{s.threshold_sharpe}), "
                      f"DD={s.max_dd:.1%} (need<{s.threshold_dd:.0%})")
                print(f"    {s.detail}")
        sys.exit(1)
    else:
        mode = "HISTORICAL" if args.historical else "SYNTHETIC"
        print(f"\nAll {mode} adversarial scenarios passed. Strategy is robust.")


if __name__ == "__main__":
    main()
