"""CLI entry point for historical backtests."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


def main() -> None:
    """Run a local historical replay from the command line.

    Example:
        zenquant-backtest --root data/raw/binance --symbol BTCUSDT
    """
    parser = argparse.ArgumentParser(description="Run a historical ZenQuant backtest from Parquet data.")
    parser.add_argument("--root", default="data/raw/binance", help="Historical data root directory.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Exchange symbol, for example BTCUSDT.")
    parser.add_argument("--output", default="", help="Optional JSON file for full metrics and diagnostics.")
    args = parser.parse_args()

    runner = HistoricalBacktest(
        HistoricalBacktestConfig(
            root_dir=Path(args.root),
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
        )
    )
    result = runner.run()
    metrics = result.metrics
    print("ZenQuant historical backtest complete")
    print(f"symbol={metrics.symbol}")
    print(f"bars={metrics.total_bars}")
    print(f"net_pnl={metrics.net_pnl:.2f}")
    print(f"max_drawdown={metrics.max_drawdown:.4f}")
    print(f"trade_count={metrics.trade_count}")
    print(f"sharpe={metrics.sharpe:.4f}")
    print(f"profit_factor={metrics.profit_factor:.4f}")
    print(f"turnover_per_day={metrics.turnover_per_day:.2f}")
    print(f"playbooks={result.diagnostics.playbook_counts}")
    print(f"actionable_exits={result.diagnostics.actionable_exit_counts}")
    if args.output:
        payload = {
            "metrics": asdict(metrics),
            "diagnostics": asdict(result.diagnostics),
            "total_indicator_packs": result.total_indicator_packs,
            "total_targets": result.total_targets,
            "total_reports": result.total_reports,
            "final_equity": result.final_equity,
        }
        Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
