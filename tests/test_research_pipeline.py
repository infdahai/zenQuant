import math
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from zenquant.historical import (
    HistoricalBacktestConfig,
    HistoricalDatasetLayout,
    HistoricalFundingRow,
    HistoricalKlineRow,
    HistoricalOpenInterestRow,
    write_parquet_rows,
)
from zenquant.research import ResearchRunConfig, run_research_pipeline


class ResearchPipelineTests(unittest.TestCase):
    def test_research_pipeline_exports_artifacts_from_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            layout = HistoricalDatasetLayout(root_dir=root)
            write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), _daily_rows())
            write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), _four_hour_rows())
            write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), _funding_rows())
            write_parquet_rows(
                layout.open_interest_path("BTCUSDT", "4h", "2024-01"),
                _open_interest_rows(),
            )

            output_root = Path(tmp) / "results"
            artifact = run_research_pipeline(
                ResearchRunConfig(
                    backtest=HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT"),
                    output_root=output_root,
                    run_label="integration",
                )
            )

            self.assertTrue(artifact.manifest_path.exists())
            self.assertTrue(artifact.summary_path.exists())
            self.assertTrue(artifact.dataset_jsonl_path.exists())
            self.assertTrue(artifact.dataset_csv_path.exists())
            self.assertTrue(artifact.report_path.exists())
            self.assertGreater(artifact.row_count, 0)

            lines = artifact.dataset_jsonl_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), artifact.row_count)
            first_row = lines[0]
            self.assertIn('"future_return_1":', first_row)
            self.assertIn('"playbook_id":', first_row)
            self.assertIn('"regime":', first_row)

            report = artifact.report_path.read_text(encoding="utf-8")
            self.assertIn("Research Run", report)
            self.assertIn("Label Coverage", report)

            index_path = output_root / "index.json"
            self.assertTrue(index_path.exists())
            self.assertIn(artifact.run_id, index_path.read_text(encoding="utf-8"))


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
