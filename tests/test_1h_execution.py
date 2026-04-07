"""Tests for 1H execution layer and dual-layer coordination."""

import math
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from zenquant.domain import Timeframe
from zenquant.historical import (
    HistoricalBacktest,
    HistoricalBacktestConfig,
    HistoricalDatasetLayout,
    HistoricalFundingRow,
    HistoricalKlineRow,
    HistoricalOpenInterestRow,
    write_parquet_rows,
)
from zenquant.strategy import ARCStrategyConfig


class TimeframeEnumTests(unittest.TestCase):
    def test_1h_value(self) -> None:
        self.assertEqual(Timeframe.TF_1H.value, "1h")

    def test_1h_seconds(self) -> None:
        self.assertEqual(Timeframe.TF_1H.seconds, 3600)

    def test_4h_seconds_unchanged(self) -> None:
        self.assertEqual(Timeframe.TF_4H.seconds, 14400)

    def test_1d_seconds_unchanged(self) -> None:
        self.assertEqual(Timeframe.TF_1D.seconds, 86400)


class DualLayerBacktestTests(unittest.TestCase):
    def test_dual_layer_runs_with_three_timeframes(self) -> None:
        """Verify backtest runs with 1D bias, 4H context, 1H execution."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = HistoricalDatasetLayout(root_dir=root)
            write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), _daily_rows(30))
            write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), _four_hour_rows(30))
            write_parquet_rows(layout.kline_path("BTCUSDT", "1h", "2024-01"), _one_hour_rows(30))
            write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), _funding_rows(30))
            write_parquet_rows(
                layout.open_interest_path("BTCUSDT", "4h", "2024-01"),
                _open_interest_rows(30),
            )

            config = HistoricalBacktestConfig(
                root_dir=root,
                symbol="BTCUSDT",
                execution_timeframe=Timeframe.TF_1H,
                context_timeframe=Timeframe.TF_4H,
                strategy_config=ARCStrategyConfig(execution_timeframe=Timeframe.TF_1H),
            )
            result = HistoricalBacktest(config).run()

            self.assertGreater(result.total_indicator_packs, 0)
            self.assertGreater(result.total_targets, 0)
            # 1H should produce more bars than 4H
            self.assertGreater(result.metrics.total_bars, 100)

    def test_1h_produces_more_bars_than_4h(self) -> None:
        """1H execution should see 6x more bars than 4H."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = HistoricalDatasetLayout(root_dir=root)
            write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), _daily_rows(30))
            write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), _four_hour_rows(30))
            write_parquet_rows(layout.kline_path("BTCUSDT", "1h", "2024-01"), _one_hour_rows(30))
            write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), _funding_rows(30))
            write_parquet_rows(
                layout.open_interest_path("BTCUSDT", "4h", "2024-01"),
                _open_interest_rows(30),
            )

            # 4H baseline
            r4h = HistoricalBacktest(HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT")).run()
            # 1H dual-layer
            r1h = HistoricalBacktest(
                HistoricalBacktestConfig(
                    root_dir=root,
                    symbol="BTCUSDT",
                    execution_timeframe=Timeframe.TF_1H,
                    context_timeframe=Timeframe.TF_4H,
                    strategy_config=ARCStrategyConfig(execution_timeframe=Timeframe.TF_1H),
                )
            ).run()

            # 1H should have ~4-6x the bars of 4H
            self.assertGreater(r1h.metrics.total_bars, r4h.metrics.total_bars * 3)


# ── Synthetic data generators ──


def _daily_rows(days: int = 30) -> list[HistoricalKlineRow]:
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


def _four_hour_rows(days: int = 30) -> list[HistoricalKlineRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 4 * 60 * 60 * 1_000_000_000
    rows: list[HistoricalKlineRow] = []
    for idx in range(days * 6):
        trend = 42_000 + (idx * 15)
        wave = math.sin(idx / 5.0) * 90
        close = trend + wave + 18
        open_px = trend + math.sin((idx - 1) / 5.0) * 90
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


def _one_hour_rows(days: int = 30) -> list[HistoricalKlineRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 1 * 60 * 60 * 1_000_000_000
    rows: list[HistoricalKlineRow] = []
    for idx in range(days * 24):
        trend = 42_000 + (idx * 3.75)  # Same daily drift as 4H
        wave = math.sin(idx / 20.0) * 45 + math.sin(idx / 7.0) * 15
        close = trend + wave + 5
        open_px = trend + math.sin((idx - 1) / 20.0) * 45 + math.sin((idx - 1) / 7.0) * 15
        high = max(open_px, close) + 12
        low = min(open_px, close) - 12
        open_time_ns = base_ns + (idx * interval_ns)
        close_time_ns = open_time_ns + interval_ns
        rows.append(
            HistoricalKlineRow(
                symbol="BTCUSDT",
                timeframe="1h",
                open_time_ns=open_time_ns,
                close_time_ns=close_time_ns,
                open=round(open_px, 4),
                high=round(high, 4),
                low=round(low, 4),
                close=round(close, 4),
                volume=round(8 + (idx % 5) * 0.5, 4),
                trade_count=30 + (idx % 20),
            )
        )
    return rows


def _funding_rows(days: int = 30) -> list[HistoricalFundingRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 8 * 60 * 60 * 1_000_000_000
    return [
        HistoricalFundingRow(
            symbol="BTCUSDT",
            funding_time_ns=base_ns + (idx * interval_ns),
            funding_rate=round(0.00008 + math.sin(idx / 8.0) * 0.00002, 8),
        )
        for idx in range(days * 3)
    ]


def _open_interest_rows(days: int = 30) -> list[HistoricalOpenInterestRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 4 * 60 * 60 * 1_000_000_000
    return [
        HistoricalOpenInterestRow(
            symbol="BTCUSDT",
            timeframe="4h",
            ts_ns=base_ns + (idx * interval_ns),
            open_interest=round(1_500_000_000 + idx * 2_500_000, 4),
        )
        for idx in range(days * 6)
    ]


if __name__ == "__main__":
    unittest.main()
