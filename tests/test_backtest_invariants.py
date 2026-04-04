"""Property-based backtest invariants — validate correctness, not just reproducibility.

Golden output test (test_golden_output.py) validates REPRODUCIBILITY:
  - "Does current code produce the same output as last commit?"
  - Useful for catching unintended regressions
  - But can encode bugs as "correct" if golden file is regenerated

This test validates CORRECTNESS:
  - "Does the backtest output satisfy basic invariants?"
  - Independent of frozen golden values
  - Cannot be "defined away" by regenerating a file

When both tests pass:
  - Golden test → behavior is stable (no accidental changes)
  - Invariant test → behavior is sane (meets minimum quality bars)

When golden test fails but invariant test passes:
  - Strategy changed intentionally → OK to regen golden (with approval)

When invariant test fails:
  - Something is fundamentally broken → DO NOT regen golden, fix the bug
"""

from __future__ import annotations

import math
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from zenquant.historical import (
    HistoricalBacktest,
    HistoricalBacktestConfig,
    HistoricalDatasetLayout,
    HistoricalFundingRow,
    HistoricalKlineRow,
    HistoricalOpenInterestRow,
    write_parquet_rows,
)


class TestBacktestInvariants(unittest.TestCase):
    """Validate backtest satisfies fundamental correctness properties."""

    def setUp(self) -> None:
        """Run backtest once, verify invariants on the result."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = HistoricalDatasetLayout(root_dir=root)
            write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), _daily_rows())
            write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), _four_hour_rows())
            write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), _funding_rows())
            write_parquet_rows(
                layout.open_interest_path("BTCUSDT", "4h", "2024-01"),
                _open_interest_rows(),
            )

            self.result = HistoricalBacktest(HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT")).run()

    def test_sharpe_is_positive(self) -> None:
        """Sharpe must be positive (strategy should be profitable on synthetic uptrend)."""
        self.assertGreater(
            self.result.metrics.sharpe,
            0.0,
            "Sharpe negative — strategy loses money on synthetic uptrend (fundamental failure)",
        )

    def test_sharpe_not_absurdly_high(self) -> None:
        """Sharpe should be realistic (<10) even on synthetic data."""
        self.assertLess(
            self.result.metrics.sharpe,
            10.0,
            "Sharpe >10 suggests overfitting to synthetic data or unrealistic behavior",
        )

    def test_net_pnl_is_positive(self) -> None:
        """Net PnL must be positive (strategy should profit on synthetic uptrend)."""
        self.assertGreater(
            self.result.metrics.net_pnl,
            0.0,
            "Net PnL negative — strategy loses money on synthetic uptrend",
        )

    def test_max_drawdown_under_50pct(self) -> None:
        """Max drawdown must be <50% (even on synthetic data, strategy should not blow up)."""
        self.assertLess(
            self.result.metrics.max_drawdown,
            0.50,
            "Max DD >50% — strategy is too aggressive or has risk management failure",
        )

    def test_final_equity_exceeds_starting(self) -> None:
        """Final equity must exceed starting equity (100k)."""
        starting_equity = 100_000.0
        self.assertGreater(
            self.result.final_equity,
            starting_equity,
            "Final equity below starting — strategy lost money",
        )

    def test_trade_count_is_reasonable(self) -> None:
        """Trade count should be >0 (strategy actually trades) and <1000 (not excessive)."""
        self.assertGreater(
            self.result.metrics.trade_count,
            0,
            "Zero trades — strategy is inactive",
        )
        self.assertLess(
            self.result.metrics.trade_count,
            1000,
            "Excessive trades (>1000 over 90 days) — possible overtrading",
        )

    def test_profit_factor_exceeds_one(self) -> None:
        """Profit factor must be >1.0 (gross profit exceeds gross loss)."""
        self.assertGreater(
            self.result.metrics.profit_factor,
            1.0,
            "Profit factor <1 — strategy loses more on losses than gains on wins",
        )

    def test_profit_factor_not_infinite(self) -> None:
        """Profit factor should not be >1000 (unrealistic on any data)."""
        self.assertLess(
            self.result.metrics.profit_factor,
            1000.0,
            "Profit factor >1000 suggests division by near-zero losses (unrealistic)",
        )

    def test_indicator_packs_processed(self) -> None:
        """Backtest must process >0 indicator packs."""
        self.assertGreater(
            self.result.total_indicator_packs,
            0,
            "No indicator packs processed — data pipeline failure",
        )

    def test_targets_generated(self) -> None:
        """Backtest must generate >0 target positions."""
        self.assertGreater(
            self.result.total_targets,
            0,
            "No targets generated — strategy logic failure",
        )

    def test_execution_reports_generated(self) -> None:
        """Backtest must generate >0 execution reports (actual fills)."""
        self.assertGreater(
            self.result.total_reports,
            0,
            "No execution reports — execution engine failure",
        )

    def test_playbook_counts_nonempty(self) -> None:
        """Diagnostics must record at least one playbook."""
        self.assertTrue(
            self.result.diagnostics.playbook_counts,
            "No playbook counts — diagnostics not recording decisions",
        )

    def test_no_nan_in_metrics(self) -> None:
        """All metrics must be finite (not NaN or Inf)."""
        self.assertFalse(math.isnan(self.result.metrics.sharpe), "Sharpe is NaN")
        self.assertFalse(math.isinf(self.result.metrics.sharpe), "Sharpe is Inf")
        self.assertFalse(math.isnan(self.result.metrics.net_pnl), "Net PnL is NaN")
        self.assertFalse(math.isinf(self.result.metrics.net_pnl), "Net PnL is Inf")
        self.assertFalse(math.isnan(self.result.metrics.max_drawdown), "Max DD is NaN")
        self.assertFalse(math.isinf(self.result.metrics.max_drawdown), "Max DD is Inf")


# ── Synthetic data generators (shared with test_golden_output.py) ──


def _daily_rows(days: int = 90) -> list[HistoricalKlineRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 24 * 60 * 60 * 1_000_000_000
    rows: list[HistoricalKlineRow] = []
    for idx in range(days):
        trend = 42_000 + (idx * 95)
        wave = math.sin(idx / 3.2) * 280
        close = trend + wave + 70
        open_px = trend + math.sin((idx - 1) / 3.2) * 280
        high = max(open_px, close) + 120
        low = min(open_px, close) - 120
        open_time_ns = base_ns + (idx * interval_ns)
        close_time_ns = open_time_ns + interval_ns
        rows.append(
            HistoricalKlineRow(
                symbol="BTCUSDT",
                timeframe="1d",
                open_time_ns=open_time_ns,
                close_time_ns=close_time_ns,
                open=round(open_px, 4),
                high=round(high, 4),
                low=round(low, 4),
                close=round(close, 4),
                volume=round(180 + (idx % 7) * 4, 4),
                trade_count=800 + idx,
            )
        )
    return rows


def _four_hour_rows(days: int = 90) -> list[HistoricalKlineRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 4 * 60 * 60 * 1_000_000_000
    rows: list[HistoricalKlineRow] = []
    total_bars = days * 6
    for idx in range(total_bars):
        trend = 42_000 + (idx * 15)
        wave = math.sin(idx / 5.0) * 90 + math.sin(idx / 17.0) * 35
        close = trend + wave + 18
        open_px = trend + math.sin((idx - 1) / 5.0) * 90 + math.sin((idx - 1) / 17.0) * 35
        high = max(open_px, close) + 30
        low = min(open_px, close) - 30
        open_time_ns = base_ns + (idx * interval_ns)
        close_time_ns = open_time_ns + interval_ns
        rows.append(
            HistoricalKlineRow(
                symbol="BTCUSDT",
                timeframe="4h",
                open_time_ns=open_time_ns,
                close_time_ns=close_time_ns,
                open=round(open_px, 4),
                high=round(high, 4),
                low=round(low, 4),
                close=round(close, 4),
                volume=round(38 + (idx % 9) * 1.5, 4),
                trade_count=120 + idx,
            )
        )
    return rows


def _funding_rows(days: int = 90) -> list[HistoricalFundingRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 8 * 60 * 60 * 1_000_000_000
    total_points = days * 3
    return [
        HistoricalFundingRow(
            symbol="BTCUSDT",
            funding_time_ns=base_ns + (idx * interval_ns),
            funding_rate=round(0.00008 + math.sin(idx / 8.0) * 0.00002, 8),
        )
        for idx in range(total_points)
    ]


def _open_interest_rows(days: int = 90) -> list[HistoricalOpenInterestRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 4 * 60 * 60 * 1_000_000_000
    total_points = days * 6
    return [
        HistoricalOpenInterestRow(
            symbol="BTCUSDT",
            timeframe="4h",
            ts_ns=base_ns + (idx * interval_ns),
            open_interest=round(1_500_000_000 + idx * 2_500_000 + math.sin(idx / 9.0) * 50_000_000, 4),
        )
        for idx in range(total_points)
    ]


if __name__ == "__main__":
    unittest.main()
