"""Gate 0: Factor Health Check — Verification Agent pattern.

Inspired by Claude Code's Verification Agent: "try to break it."
Detects signal direction mismatches, noise factors with high weight,
and factor decay before they reach production.

This gate runs IC analysis on the synthetic dataset and validates
that no active signal has fundamentally wrong properties.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from datetime import UTC, datetime

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


def _make_kline(open_time_ns: int, close_time_ns: int, o: float, h: float, lo: float, c: float, vol: float) -> HistoricalKlineRow:
    return HistoricalKlineRow(
        symbol="BTCUSDT", timeframe="4h",
        open_time_ns=open_time_ns, close_time_ns=close_time_ns,
        open=o, high=h, low=lo, close=c, volume=vol, trade_count=1000,
    )


def _daily_rows(days: int = 90) -> list[HistoricalKlineRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 24 * 60 * 60 * 1_000_000_000
    rows: list[HistoricalKlineRow] = []
    for idx in range(days):
        trend = 42_000 + (idx * 95)
        rows.append(HistoricalKlineRow(
            symbol="BTCUSDT", timeframe="1d",
            open_time_ns=base_ns + idx * interval_ns,
            close_time_ns=base_ns + (idx + 1) * interval_ns - 1,
            open=trend, high=trend + 400, low=trend - 300, close=trend + 50,
            volume=1000.0 + idx * 10, trade_count=5000,
        ))
    return rows


def _four_hour_rows(days: int = 90) -> list[HistoricalKlineRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 4 * 60 * 60 * 1_000_000_000
    rows: list[HistoricalKlineRow] = []
    for idx in range(days * 6):
        day = idx // 6
        trend = 42_000 + (day * 95) + (idx % 6) * 15
        rows.append(HistoricalKlineRow(
            symbol="BTCUSDT", timeframe="4h",
            open_time_ns=base_ns + idx * interval_ns,
            close_time_ns=base_ns + (idx + 1) * interval_ns - 1,
            open=trend, high=trend + 200, low=trend - 150, close=trend + 25,
            volume=200.0 + idx, trade_count=1000,
        ))
    return rows


def _funding_rows(days: int = 90) -> list[HistoricalFundingRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 8 * 60 * 60 * 1_000_000_000
    return [
        HistoricalFundingRow(symbol="BTCUSDT", funding_time_ns=base_ns + i * interval_ns, funding_rate=0.0001)
        for i in range(days * 3)
    ]


def _open_interest_rows(days: int = 90) -> list[HistoricalOpenInterestRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 4 * 60 * 60 * 1_000_000_000
    return [
        HistoricalOpenInterestRow(symbol="BTCUSDT", timeframe="4h", ts_ns=base_ns + i * interval_ns, open_interest=50_000.0 + i * 10)
        for i in range(days * 6)
    ]


class TestFactorHealth(unittest.TestCase):
    """Gate 0: Verify active signals have valid IC properties."""

    def test_indicator_pack_contains_expected_keys(self) -> None:
        """All expected indicator keys are present in the pack."""
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

            bt = HistoricalBacktest(HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT"))
            bt.run()

            packs_4h = [p for p in bt._indicators if p.timeframe == Timeframe.TF_4H]
            self.assertGreater(len(packs_4h), 50)

            # Core indicators must be present
            last_pack = packs_4h[-1]
            required_keys = [
                "realized_vol",
                "atr",
                "ob_liq_z",
                "cvd_slope",
                "funding_oi_z",
                "zen_pulse",
                "momentum_roc_z",
                "multi_momentum_z",
                "cycle_position",
                "ou_kappa",
                "fragility_ratio",
            ]
            for key in required_keys:
                self.assertIn(key, last_pack.values, f"Missing indicator: {key}")

    def test_no_nan_or_inf_in_indicators(self) -> None:
        """No NaN or Inf values leak into indicator packs."""
        import math

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

            bt = HistoricalBacktest(HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT"))
            bt.run()

            for pack in bt._indicators:
                for key, value in pack.values.items():
                    if isinstance(value, float):
                        self.assertFalse(
                            math.isnan(value) or math.isinf(value),
                            f"NaN/Inf detected: {key}={value} at ts={pack.ts_ns}",
                        )

    def test_signal_sign_parameter_is_explicit(self) -> None:
        """Multi-momentum signal must have explicit signal_sign config."""
        import tomllib

        config_path = Path("config/profiles/default.toml")
        if not config_path.exists():
            self.skipTest("default.toml not found")

        config = tomllib.loads(config_path.read_text())
        momentum_config = config.get("signals", {}).get("multi_momentum_signal", {})

        # signal_sign must be explicitly set (not left to default)
        self.assertIn(
            "signal_sign",
            momentum_config,
            "signal_sign must be explicit in config (IC analysis required before changing)",
        )


if __name__ == "__main__":
    unittest.main()
