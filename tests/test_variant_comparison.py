import math
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from zenquant.historical import (
    HistoricalBacktestConfig,
    HistoricalDatasetLayout,
    HistoricalFundingRow,
    HistoricalKlineRow,
    HistoricalOpenInterestRow,
    write_parquet_rows,
)
from zenquant.research.comparison import compare_variants, split_window_spec
from zenquant.research.splits import default_btc_split_spec


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


class VariantComparisonTests(unittest.TestCase):
    def test_compare_variants_returns_required_report_fields(self) -> None:
        @dataclass
        class FakeMetrics:
            sharpe: float = 1.2
            net_pnl: float = 100.0
            max_drawdown: float = 0.1
            profit_factor: float = 1.4
            trade_count: int = 10
            win_rate: float = 0.5

        @dataclass
        class FakeDiagnostics:
            playbook_counts: dict[str, int]
            risk_flag_counts: dict[str, int]
            actionable_exit_counts: dict[str, int]

        @dataclass
        class FakeCycle:
            cycle_position: float = 0.0
            cycle_peak_distance: float = 0.0
            cycle_trough_distance: float = 0.0
            cycle_momentum: float = 0.0
            cycle_confidence: float = 0.0

        @dataclass
        class FakeState:
            timeframe: str = "4h"
            directional_state: str = "bull"
            regime: str = "trend"

        @dataclass
        class FakePlaybook:
            timeframe: str = "4h"
            playbook_id: str = "BULL_CONTINUATION"

        class FakeResult:
            metrics = FakeMetrics()
            diagnostics = FakeDiagnostics({"BULL_CONTINUATION": 1}, {"OK": 1}, {"NONE": 1})
            cycle_context = FakeCycle()

        class FakeRunner:
            def __init__(self) -> None:
                self._market_states = [FakeState()]
                self._playbooks = [FakePlaybook()]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = HistoricalDatasetLayout(root_dir=root)
            write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), _daily_rows())
            write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), _four_hour_rows())
            write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), _funding_rows())
            write_parquet_rows(layout.open_interest_path("BTCUSDT", "4h", "2024-01"), _open_interest_rows())

            with patch("zenquant.research.comparison._run_variant_backtest", return_value=(FakeRunner(), FakeResult())):
                payload = compare_variants(
                    HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT"),
                    windows=[split_window_spec(default_btc_split_spec(), "dev")],
                    walk_forward_windows=[],
                    split_spec=default_btc_split_spec(),
                    comparison_split="dev",
                )

        variants = {item["name"] for item in payload["variants"]}
        self.assertEqual(variants, {"current", "paulwei_baseline"})
        self.assertEqual(len(payload["window_reports"]), 2)
        current_run = next(item for item in payload["window_reports"] if item["variant"] == "current")
        baseline_run = next(item for item in payload["window_reports"] if item["variant"] == "paulwei_baseline")
        self.assertIn("metrics", current_run)
        self.assertIn("diagnostics", current_run)
        self.assertIn("cycle_context", current_run)
        self.assertIn("directional_state_distribution", current_run)
        self.assertIn("regime_distribution", current_run)
        self.assertIn("playbook_usage", current_run)
        self.assertIsNotNone(current_run["comparison_to_baseline"])
        self.assertIsNone(baseline_run["comparison_to_baseline"])
        self.assertEqual(len(payload["validation_reports"]), 2)

    def test_compare_variants_summary_only_strips_heavy_diagnostics(self) -> None:
        @dataclass
        class FakeMetrics:
            sharpe: float = 1.2
            net_pnl: float = 100.0
            max_drawdown: float = 0.1
            profit_factor: float = 1.4
            trade_count: int = 10
            win_rate: float = 0.5

        @dataclass
        class FakeDiagnostics:
            playbook_counts: dict[str, int]
            risk_flag_counts: dict[str, int]
            actionable_exit_counts: dict[str, int]

        @dataclass
        class FakeCycle:
            cycle_position: float = 0.0
            cycle_peak_distance: float = 0.0
            cycle_trough_distance: float = 0.0
            cycle_momentum: float = 0.0
            cycle_confidence: float = 0.0

        @dataclass
        class FakeState:
            timeframe: str = "4h"
            directional_state: str = "bull"
            regime: str = "trend"

        @dataclass
        class FakePlaybook:
            timeframe: str = "4h"
            playbook_id: str = "BULL_CONTINUATION"

        class FakeResult:
            metrics = FakeMetrics()
            diagnostics = FakeDiagnostics({"BULL_CONTINUATION": 1}, {"OK": 1}, {"NONE": 1})
            cycle_context = FakeCycle()

        class FakeRunner:
            def __init__(self) -> None:
                self._market_states = [FakeState()]
                self._playbooks = [FakePlaybook()]

        with patch("zenquant.research.comparison._run_variant_backtest", return_value=(FakeRunner(), FakeResult())):
            payload = compare_variants(
                HistoricalBacktestConfig(root_dir=Path("data/raw/binance"), symbol="BTCUSDT"),
                windows=[split_window_spec(default_btc_split_spec(), "dev")],
                walk_forward_windows=[],
                split_spec=default_btc_split_spec(),
                comparison_split="dev",
                report_mode="summary_only",
            )

        self.assertEqual(payload["report_mode"], "summary_only")
        run = payload["window_reports"][0]
        self.assertIn("metrics", run)
        self.assertNotIn("diagnostics", run)
        self.assertNotIn("cycle_context", run)
        self.assertEqual(payload["validation_reports"][0]["windows"], [])

    def test_compare_variants_smoke_minimal_emits_heartbeat_and_skips_validation_windows(self) -> None:
        @dataclass
        class FakeMetrics:
            sharpe: float = 1.0
            net_pnl: float = 10.0
            max_drawdown: float = 0.1
            profit_factor: float = 1.1
            trade_count: int = 2
            win_rate: float = 0.5

        @dataclass
        class FakeDiagnostics:
            playbook_counts: dict[str, int]
            risk_flag_counts: dict[str, int]
            actionable_exit_counts: dict[str, int]

        @dataclass
        class FakeCycle:
            cycle_position: float = 0.0
            cycle_peak_distance: float = 0.0
            cycle_trough_distance: float = 0.0
            cycle_momentum: float = 0.0
            cycle_confidence: float = 0.0

        class FakeResult:
            metrics = FakeMetrics()
            diagnostics = FakeDiagnostics({"BULL_CONTINUATION": 1}, {"OK": 1}, {"NONE": 1})
            cycle_context = FakeCycle()

        class FakeRunner:
            def __init__(self) -> None:
                self._market_states = []
                self._playbooks = []

        heartbeats: list[dict[str, object]] = []

        def _status_callback(**payload: object) -> None:
            heartbeats.append(payload)

        with patch("zenquant.research.comparison._run_variant_backtest", return_value=(FakeRunner(), FakeResult())):
            payload = compare_variants(
                HistoricalBacktestConfig(root_dir=Path("data/raw/binance"), symbol="BTCUSDT"),
                windows=[split_window_spec(default_btc_split_spec(), "dev")],
                walk_forward_windows=[split_window_spec(default_btc_split_spec(), "dev")],
                split_spec=default_btc_split_spec(),
                comparison_split="dev",
                report_mode="summary_only",
                comparison_compute_mode="smoke_minimal",
                status_callback=_status_callback,
            )

        self.assertEqual(payload["comparison_compute_mode"], "smoke_minimal")
        self.assertEqual(len(payload["validation_reports"]), 2)
        self.assertTrue(all(item["windows"] == [] for item in payload["validation_reports"]))
        events = [str(item["event"]) for item in heartbeats]
        self.assertIn("comparison_window_completed", events)
        self.assertIn("comparison_validation_completed", events)
        self.assertIn("comparison_ranking_completed", events)


if __name__ == "__main__":
    unittest.main()
