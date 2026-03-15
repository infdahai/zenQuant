"""CLI entry point for historical backtests."""

from __future__ import annotations

import argparse
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


if __name__ == "__main__":
    main()
