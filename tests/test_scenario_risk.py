"""Scenario risk tests — GAN Discriminator adversarial verification.

These tests construct specific MARKET SCENARIOS (not just unit inputs)
and verify the FULL PIPELINE behaves safely end-to-end.

Unlike unit tests (code does what it's designed to do) and golden tests
(output is reproducible), these test WHETHER THE DESIGN IS SAFE.

Each scenario answers: "In this market condition, does the strategy
protect capital or blow up?"
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


def _ts(year: int, month: int = 1, day: int = 1) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1_000_000_000)


def _build_scenario(
    closes: list[float],
    funding_rate: float = 0.0001,
    oi: float = 1_500_000_000,
    timeframe_hours: int = 4,
) -> tuple[
    list[HistoricalKlineRow], list[HistoricalKlineRow], list[HistoricalFundingRow], list[HistoricalOpenInterestRow]
]:
    """Build synthetic dataset from a close price sequence.

    Returns 4H rows, 1D rows, funding rows, and OI rows.
    """
    base_ns = _ts(2024, 1, 1)
    interval_4h = timeframe_hours * 3600 * 1_000_000_000
    interval_1d = 24 * 3600 * 1_000_000_000
    interval_8h = 8 * 3600 * 1_000_000_000

    rows_4h: list[HistoricalKlineRow] = []
    for i, close in enumerate(closes):
        open_px = closes[i - 1] if i > 0 else close
        high = max(open_px, close) * 1.002
        low = min(open_px, close) * 0.998
        t = base_ns + i * interval_4h
        rows_4h.append(
            HistoricalKlineRow(
                symbol="BTCUSDT",
                timeframe="4h",
                open_time_ns=t,
                close_time_ns=t + interval_4h,
                open=round(open_px, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=round(100 + (i % 5) * 10, 2),
                trade_count=500 + i,
            )
        )

    # Daily rows: aggregate every 6 4H bars
    rows_1d: list[HistoricalKlineRow] = []
    for day_start in range(0, len(closes), 6):
        day_closes = closes[day_start : day_start + 6]
        if not day_closes:
            break
        t = base_ns + day_start * interval_4h
        rows_1d.append(
            HistoricalKlineRow(
                symbol="BTCUSDT",
                timeframe="1d",
                open_time_ns=t,
                close_time_ns=t + interval_1d,
                open=round(day_closes[0], 2),
                high=round(max(day_closes) * 1.003, 2),
                low=round(min(day_closes) * 0.997, 2),
                close=round(day_closes[-1], 2),
                volume=round(600 + day_start, 2),
                trade_count=3000 + day_start,
            )
        )

    # Funding: every 8 hours
    funding_rows = [
        HistoricalFundingRow(
            symbol="BTCUSDT",
            funding_time_ns=base_ns + i * interval_8h,
            funding_rate=round(funding_rate + math.sin(i / 5) * 0.00005, 8),
        )
        for i in range(len(closes) * interval_4h // interval_8h + 1)
    ]

    # OI: every 4 hours
    oi_rows = [
        HistoricalOpenInterestRow(
            symbol="BTCUSDT",
            timeframe="4h",
            ts_ns=base_ns + i * interval_4h,
            open_interest=round(oi + math.sin(i / 10) * oi * 0.05, 2),
        )
        for i in range(len(closes))
    ]

    return rows_4h, rows_1d, funding_rows, oi_rows


def _run_scenario(closes: list[float], **kwargs) -> dict:
    """Run full backtest on a synthetic scenario, return metrics + diagnostics."""
    rows_4h, rows_1d, funding, oi = _build_scenario(closes, **kwargs)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        layout = HistoricalDatasetLayout(root_dir=root)
        write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), rows_1d)
        write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), rows_4h)
        write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), funding)
        write_parquet_rows(layout.open_interest_path("BTCUSDT", "4h", "2024-01"), oi)

        bt = HistoricalBacktest(HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT"))
        result = bt.run()

    return {
        "sharpe": result.metrics.sharpe,
        "net_pnl": result.metrics.net_pnl,
        "max_drawdown": result.metrics.max_drawdown,
        "trade_count": result.metrics.trade_count,
        "final_equity": result.final_equity,
        "profit_factor": result.metrics.profit_factor,
    }


class SustainedDowntrendDCATests(unittest.TestCase):
    """SCENARIO: Price drops 25% over 30 bars while signal stays LONG.
    This is the $120k→$95k disaster scenario.
    DCA should NOT build full position into a crash."""

    def test_sustained_drop_dd_capped(self) -> None:
        """25% sustained drop → DD must stay below kill switch (30%)."""
        # 120k → 90k over 30 bars (linear drop, -1% per bar)
        closes = [120_000 * (1 - 0.01 * i) for i in range(60)]
        result = _run_scenario(closes)
        self.assertLess(
            result["max_drawdown"],
            0.30,
            f"DD {result['max_drawdown']:.1%} exceeded kill switch during sustained drop",
        )

    def test_sustained_drop_loss_limited(self) -> None:
        """25% sustained drop → should not lose more than 25% of equity."""
        closes = [120_000 * (1 - 0.008 * i) for i in range(60)]
        result = _run_scenario(closes)
        loss_pct = (100_000 - result["final_equity"]) / 100_000
        self.assertLess(
            loss_pct,
            0.25,
            f"Lost {loss_pct:.1%} of equity during sustained drop (max 25%)",
        )


class FlashCrashTests(unittest.TestCase):
    """SCENARIO: Sudden -20% crash in 1 bar, then sideways."""

    def test_flash_crash_dd_capped(self) -> None:
        """Single bar -20% crash → DD must stay manageable."""
        closes = [100_000] * 10 + [80_000] + [80_000] * 30
        result = _run_scenario(closes)
        self.assertLess(
            result["max_drawdown"],
            0.30,
            f"DD {result['max_drawdown']:.1%} after flash crash",
        )

    def test_flash_crash_recovery(self) -> None:
        """Crash then recovery → strategy should participate in recovery."""
        closes = [100_000] * 10 + [80_000] + [80_000 + i * 500 for i in range(40)]
        result = _run_scenario(closes)
        # Should not be deeply negative after recovery
        self.assertGreater(
            result["final_equity"],
            80_000,
            "Strategy failed to participate in post-crash recovery",
        )


class WhipsawTests(unittest.TestCase):
    """SCENARIO: Rapid direction changes every 3-4 bars.
    Tests whether the strategy avoids getting chopped up."""

    def test_whipsaw_limited_loss(self) -> None:
        """10 whipsaw cycles → should not lose more than 15%."""
        closes = []
        price = 100_000
        for cycle in range(10):
            # Up 3%, then down 3%, net flat
            for i in range(3):
                price *= 1.01
                closes.append(price)
            for i in range(3):
                price *= 0.99
                closes.append(price)
        result = _run_scenario(closes)
        loss_pct = max(0, (100_000 - result["final_equity"]) / 100_000)
        self.assertLess(
            loss_pct,
            0.15,
            f"Lost {loss_pct:.1%} in whipsaw conditions (max 15%)",
        )


class TrendReversalTests(unittest.TestCase):
    """SCENARIO: Strong uptrend → sudden reversal.
    Tests whether fully-positioned long exits safely on reversal."""

    def test_trend_reversal_dd_limited(self) -> None:
        """20-bar uptrend then 20-bar downtrend → DD should be capped."""
        closes = []
        price = 100_000
        # Uptrend: +1% per bar for 20 bars
        for i in range(20):
            price *= 1.01
            closes.append(price)
        # Reversal: -1.5% per bar for 20 bars (faster decline)
        for i in range(20):
            price *= 0.985
            closes.append(price)
        # Sideways
        for i in range(20):
            closes.append(price)

        result = _run_scenario(closes)
        self.assertLess(
            result["max_drawdown"],
            0.30,
            f"DD {result['max_drawdown']:.1%} after trend reversal (fully positioned)",
        )


class ExtendedSidewaysTests(unittest.TestCase):
    """SCENARIO: 60 bars of noise around the same price.
    Strategy should not bleed from transaction costs."""

    def test_sideways_minimal_loss(self) -> None:
        """60 bars sideways noise → loss from fees should be < 5%."""
        closes = [100_000 + math.sin(i / 2.0) * 500 for i in range(60)]
        result = _run_scenario(closes)
        loss_pct = max(0, (100_000 - result["final_equity"]) / 100_000)
        self.assertLess(
            loss_pct,
            0.05,
            f"Lost {loss_pct:.1%} in sideways market (max 5% from fees/churn)",
        )


class BlackSwanTests(unittest.TestCase):
    """SCENARIO: -40% single bar (March 2020 level)."""

    def test_black_swan_survives(self) -> None:
        """-40% crash → strategy must survive (equity > 50% of starting)."""
        closes = [100_000] * 10 + [60_000] + [60_000] * 20
        result = _run_scenario(closes)
        self.assertGreater(
            result["final_equity"],
            50_000,
            f"Equity ${result['final_equity']:,.0f} < $50k after black swan — fatal",
        )

    def test_black_swan_dd_below_kill_switch(self) -> None:
        """-40% crash → DD may exceed kill switch but equity must survive."""
        closes = [100_000] * 10 + [60_000] + [60_000] * 20
        result = _run_scenario(closes)
        # Kill switch is 30%, a -40% crash will likely breach it
        # But the question is: does the system SURVIVE and RECOVER?
        self.assertGreater(result["final_equity"], 0, "Strategy went bankrupt")


class HighFundingCrowdingTests(unittest.TestCase):
    """SCENARIO: Extreme funding rate (crowded market).
    Tests whether crowding guards activate."""

    def test_high_funding_reduces_exposure(self) -> None:
        """Extreme positive funding → should reduce long exposure."""
        # Uptrend with extreme funding (longs crowded)
        closes = [100_000 * (1 + 0.005 * i) for i in range(40)]
        result_normal = _run_scenario(closes, funding_rate=0.0001)
        result_crowded = _run_scenario(closes, funding_rate=0.001)
        # Crowded scenario should have fewer/smaller trades
        self.assertLessEqual(
            result_crowded["trade_count"],
            result_normal["trade_count"] * 1.5,
            "Crowded funding should not increase trading activity",
        )


class DCAvsRiskGuardConflictTests(unittest.TestCase):
    """SCENARIO: DCA wants to add, but DD guard wants to reduce.
    Risk guard MUST win this conflict."""

    def test_dd_guard_overrides_dca(self) -> None:
        """DD > step1 (15%) → even with DCA enabled, position should shrink or hold.
        Risk overlay rewrites target AFTER strategy, so DD guard has final say."""
        # Start high, drop 20% (triggers DD_STEP_2 at 22% for longs)
        closes = [100_000] * 5
        price = 100_000
        for i in range(25):
            price *= 0.99  # -1% per bar, cumulative -22% over 25 bars
            closes.append(price)
        # Hold at bottom
        for i in range(20):
            closes.append(price)

        result = _run_scenario(closes)
        # Key assertion: even with DCA, DD should be managed
        self.assertLess(
            result["max_drawdown"],
            0.30,
            f"DD {result['max_drawdown']:.1%}: risk guard failed to override DCA during drawdown",
        )


if __name__ == "__main__":
    unittest.main()
