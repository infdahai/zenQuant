#!/usr/bin/env python3
"""Stratified strategy evaluation — replaces single-number Sharpe.

Reports per-year, per-regime, and trade attribution metrics.
Designed to prevent overfitting by revealing hidden weaknesses.

Usage:
    uv run python scripts/stratified_eval.py
    uv run python scripts/stratified_eval.py --symbol ETHUSDT
    uv run python scripts/stratified_eval.py --train-only  # 2020-2023 only
"""

from __future__ import annotations

import argparse
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


def _year_sharpe(equities: list[float]) -> float:
    if len(equities) < 20:
        return 0.0
    rets = [(equities[i] - equities[i - 1]) / equities[i - 1] for i in range(1, len(equities)) if equities[i - 1] > 0]
    if len(rets) < 5:
        return 0.0
    mr = sum(rets) / len(rets)
    var = sum((r - mr) ** 2 for r in rets) / len(rets)
    return (mr / math.sqrt(var)) * math.sqrt(len(rets)) if var > 0 else 0.0


def evaluate(
    data_root: Path = Path("data/raw/binance"),
    symbol: str = "BTCUSDT",
    start_ns: int | None = None,
    end_ns: int | None = None,
) -> None:
    bt = HistoricalBacktest(
        HistoricalBacktestConfig(
            root_dir=data_root,
            symbol=symbol,
            portfolio_symbol=f"{symbol}_PERP",
            start_ns=start_ns,
            end_ns=end_ns,
        )
    )
    result = bt.run()
    m = result.metrics

    # === OVERALL ===
    print(f"{'=' * 65}")
    print(f"STRATIFIED EVALUATION: {symbol}")
    print(f"{'=' * 65}")
    print(f"Overall: Sharpe={m.sharpe:.2f}  PnL=${m.net_pnl:,.0f}  DD={m.max_drawdown:.1%}  Trades={m.trade_count}")
    print()

    # === PER-YEAR ===
    snapshots = bt._risk_snapshots
    records = sorted([(s.ts_ns, s.equity) for s in snapshots])
    years: dict[int, list[float]] = {}
    for ts, eq in records:
        y = pd.Timestamp(ts, unit="ns").year
        if y not in years:
            years[y] = []
        years[y].append(eq)

    print("PER-YEAR BREAKDOWN:")
    print(f"{'Year':<6} {'Return':>8} {'Sharpe':>8} {'MaxDD':>7} {'Verdict'}")
    print("-" * 40)
    worst_sharpe = 999.0
    losing_years = 0
    for year in sorted(years.keys()):
        eqs = years[year]
        if len(eqs) < 20:
            continue
        ret = (eqs[-1] - eqs[0]) / eqs[0]
        sharpe = _year_sharpe(eqs)
        hwm = eqs[0]
        mdd = 0.0
        for eq in eqs:
            if eq > hwm:
                hwm = eq
            dd = (hwm - eq) / hwm
            if dd > mdd:
                mdd = dd
        v = "PASS" if sharpe > 0 else "FAIL"
        if sharpe < worst_sharpe:
            worst_sharpe = sharpe
        if sharpe <= 0:
            losing_years += 1
        print(f"{year:<6} {ret:>7.1%} {sharpe:>7.1f} {mdd:>6.1%}  {v}")

    print()

    # === PER-REGIME ===
    targets = bt._targets
    reports = sorted(bt._reports, key=lambda r: r.transaction_time_ns)

    pos = 0.0
    entry_price = 0.0
    entry_ts = 0
    trades: list[dict] = []
    states_map = {s.cutoff_time_ns: s for s in bt._market_states if s.timeframe == Timeframe.TF_4H}

    for r in reports:
        if r.status != "FILLED" or r.filled_qty <= 0:
            continue
        new_pos = pos + r.side * r.filled_qty
        if pos != 0 and ((pos > 0 and new_pos <= 0) or (pos < 0 and new_pos >= 0)):
            pnl = (1 if pos > 0 else -1) * (r.avg_price - entry_price) * abs(pos)
            state = states_map.get(entry_ts)
            regime = state.regime if state else "UNK"
            trades.append({"pnl": pnl, "regime": regime, "year": pd.Timestamp(entry_ts, unit="ns").year})
        if new_pos != 0 and pos * new_pos <= 0:
            entry_price = r.avg_price
            entry_ts = r.transaction_time_ns
        pos = new_pos

    print("PER-REGIME ATTRIBUTION:")
    print(f"{'Regime':<8} {'Trades':>7} {'Win%':>6} {'Total PnL':>12} {'Avg PnL':>10}")
    print("-" * 48)
    regime_data: dict[str, dict] = {}
    for t in trades:
        r = t["regime"]
        if r not in regime_data:
            regime_data[r] = {"n": 0, "pnl": 0.0, "wins": 0}
        regime_data[r]["n"] += 1
        regime_data[r]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            regime_data[r]["wins"] += 1

    for r in sorted(regime_data):
        d = regime_data[r]
        wr = d["wins"] / d["n"] if d["n"] > 0 else 0
        avg = d["pnl"] / d["n"] if d["n"] > 0 else 0
        print(f"{r:<8} {d['n']:>7} {wr:>5.0%} {d['pnl']:>12,.0f} {avg:>10,.0f}")

    print()

    # === VERDICT ===
    print("VERDICT:")
    total_years = len([y for y in years if len(years[y]) >= 20])
    checks = [
        (f"Worst year Sharpe > -1.0", worst_sharpe > -1.0, f"{worst_sharpe:.1f}"),
        (f"Losing years < 50%", losing_years < total_years * 0.5, f"{losing_years}/{total_years}"),
        (f"TREND PnL > 0", regime_data.get("TREND", {}).get("pnl", 0) > 0, f"${regime_data.get('TREND', {}).get('pnl', 0):,.0f}"),
        (f"Max DD < 35%", m.max_drawdown < 0.35, f"{m.max_drawdown:.1%}"),
    ]
    for name, passed, value in checks:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name} ({value})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stratified strategy evaluation")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--train-only", action="store_true", help="Only evaluate train set (2020-2023)")
    parser.add_argument("--test-only", action="store_true", help="Only evaluate test set (2024-2026)")
    args = parser.parse_args()

    train_end = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1e9)

    if args.train_only:
        evaluate(Path(args.root), args.symbol, end_ns=train_end)
    elif args.test_only:
        evaluate(Path(args.root), args.symbol, start_ns=train_end)
    else:
        print("=== FULL PERIOD ===")
        evaluate(Path(args.root), args.symbol)
        print()
        print("=== TRAIN SET (2020-2023) ===")
        evaluate(Path(args.root), args.symbol, end_ns=train_end)
        print()
        print("=== TEST SET (2024-2026) ===")
        evaluate(Path(args.root), args.symbol, start_ns=train_end)


if __name__ == "__main__":
    main()
