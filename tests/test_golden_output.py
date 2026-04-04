"""Gate C: Golden output regression test (validates REPRODUCIBILITY not CORRECTNESS).

This test validates that backtest behavior is STABLE (unchanged from baseline).
It does NOT validate that behavior is CORRECT.

When this test fails:
  1. Check test_backtest_invariants.py first (validates correctness)
  2. If invariants pass: change is intentional → see tests/golden/POLICY.md
  3. If invariants fail: DO NOT regen golden, fix the bug in code

IMPORTANT: Regenerating golden files encodes current behavior as "correct".
Only regen after verifying correctness independently (see POLICY.md).

This is mechanical enforcement: documents rot, golden files don't.
But golden files can encode bugs if regenerated without discipline.
"""

from __future__ import annotations

import json
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

GOLDEN_PATH = Path(__file__).parent / "golden" / "backtest.json"


class TestGoldenOutput(unittest.TestCase):
    """Compare live backtest output against committed golden baseline."""

    def test_backtest_matches_golden_output(self) -> None:
        if not GOLDEN_PATH.exists():
            self.skipTest("Golden file not found — run zenquant-check --gate c to generate")

        golden = json.loads(GOLDEN_PATH.read_text())

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

            result = HistoricalBacktest(HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT")).run()

        gm = golden["metrics"]
        self.assertAlmostEqual(
            result.metrics.net_pnl,
            gm["net_pnl"],
            places=2,
            msg="net_pnl changed — update golden/backtest.json if intentional",
        )
        self.assertAlmostEqual(
            result.metrics.sharpe,
            gm["sharpe"],
            places=4,
            msg="sharpe changed — update golden/backtest.json if intentional",
        )
        self.assertAlmostEqual(
            result.metrics.max_drawdown,
            gm["max_drawdown"],
            places=6,
            msg="max_drawdown changed — update golden/backtest.json if intentional",
        )
        self.assertAlmostEqual(
            result.metrics.profit_factor,
            gm["profit_factor"],
            places=6,
            msg="profit_factor changed — update golden/backtest.json if intentional",
        )
        self.assertEqual(
            result.metrics.trade_count,
            gm["trade_count"],
            "trade_count changed — update golden/backtest.json if intentional",
        )

        ge = golden["equity"]
        self.assertAlmostEqual(
            result.final_equity,
            ge["final"],
            places=6,
            msg="final_equity changed — update golden/backtest.json if intentional",
        )

        gc = golden["counts"]
        self.assertEqual(result.total_indicator_packs, gc["indicator_packs"])
        self.assertEqual(result.total_targets, gc["targets"])
        self.assertEqual(result.total_reports, gc["reports"])

        gd = golden["diagnostics"]
        self.assertEqual(result.diagnostics.playbook_counts, gd["playbook_counts"])
        self.assertEqual(result.diagnostics.actionable_exit_counts, gd["actionable_exit_counts"])


# ── Synthetic data generators (locked — must not change) ──────


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
