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
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from zenquant.historical import (
    HistoricalBacktest,
    HistoricalBacktestConfig,
    HistoricalBacktestResult,
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
        self.result = _run_synthetic_backtest()

    def test_repeated_backtest_is_deterministic(self) -> None:
        """The same Gate C stream must reproduce every semantic result field."""
        repeated = _run_synthetic_backtest()

        semantic_fields = (
            "metrics",
            "diagnostics",
            "total_indicator_packs",
            "total_targets",
            "total_reports",
            "final_equity",
            "cycle_context",
            "terminated_early",
            "termination_reason",
            "processed_event_ratio",
            "position_episodes",
            "decision_snapshots",
            "options_state_sidecar",
            "options_state_links",
        )
        result_fields = set(HistoricalBacktestResult.__dataclass_fields__)
        self.assertEqual(result_fields - {"profiling"}, set(semantic_fields))
        for field_name in semantic_fields:
            with self.subTest(field=field_name):
                self.assertEqual(getattr(self.result, field_name), getattr(repeated, field_name))

        # Profiling contains wall-clock durations and is deliberately excluded
        # from the deterministic replay contract.

    def test_net_pnl_reconciles_with_final_equity(self) -> None:
        """Net PnL must reconcile with the signed change in portfolio equity."""
        starting_equity = 100_000.0
        self.assertAlmostEqual(
            self.result.metrics.net_pnl,
            self.result.final_equity - starting_equity,
            places=2,
            msg="Net PnL does not reconcile with final minus starting equity",
        )

    def test_max_drawdown_under_50pct(self) -> None:
        """Max drawdown must be <50% (even on synthetic data, strategy should not blow up)."""
        self.assertLess(
            self.result.metrics.max_drawdown,
            0.50,
            "Max DD >50% — strategy is too aggressive or has risk management failure",
        )

    def test_final_equity_remains_finite_and_positive(self) -> None:
        """The bounded smoke replay must remain solvent with finite equity."""
        self.assertTrue(math.isfinite(self.result.final_equity), "Final equity is not finite")
        self.assertGreater(
            self.result.final_equity,
            0.0,
            "Final equity is non-positive — the bounded smoke replay became insolvent",
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

    def test_profit_factor_is_non_negative(self) -> None:
        """Profit factor is an unsigned finite-risk diagnostic, not a profit promise."""
        self.assertGreaterEqual(
            self.result.metrics.profit_factor,
            0.0,
            "Profit factor cannot be negative",
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

    def test_core_metrics_are_finite(self) -> None:
        """Core return and risk diagnostics must be finite."""
        finite_fields = (
            "sharpe",
            "sortino",
            "net_pnl",
            "gross_pnl",
            "max_drawdown",
            "profit_factor",
        )
        for field_name in finite_fields:
            with self.subTest(field=field_name):
                self.assertTrue(
                    math.isfinite(getattr(self.result.metrics, field_name)),
                    f"{field_name} is not finite",
                )


def _run_synthetic_backtest() -> HistoricalBacktestResult:
    original_cwd = Path.cwd()
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

        try:
            os.chdir(root)
            return HistoricalBacktest(
                HistoricalBacktestConfig(
                    root_dir=root,
                    symbol="BTCUSDT",
                    macro_dir=root / "macro",
                    options_dir=root / "deribit_options",
                    cot_path=root / "cftc" / "cot_btc.parquet",
                    liquidation_dir=root / "liquidations" / "aggregated",
                    profile_path=None,
                )
            ).run()
        finally:
            os.chdir(original_cwd)


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
