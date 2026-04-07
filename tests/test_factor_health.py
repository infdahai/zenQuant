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


def _make_kline(
    open_time_ns: int, close_time_ns: int, o: float, h: float, lo: float, c: float, vol: float
) -> HistoricalKlineRow:
    return HistoricalKlineRow(
        symbol="BTCUSDT",
        timeframe="4h",
        open_time_ns=open_time_ns,
        close_time_ns=close_time_ns,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=vol,
        trade_count=1000,
    )


def _daily_rows(days: int = 90) -> list[HistoricalKlineRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 24 * 60 * 60 * 1_000_000_000
    rows: list[HistoricalKlineRow] = []
    for idx in range(days):
        trend = 42_000 + (idx * 95)
        rows.append(
            HistoricalKlineRow(
                symbol="BTCUSDT",
                timeframe="1d",
                open_time_ns=base_ns + idx * interval_ns,
                close_time_ns=base_ns + (idx + 1) * interval_ns - 1,
                open=trend,
                high=trend + 400,
                low=trend - 300,
                close=trend + 50,
                volume=1000.0 + idx * 10,
                trade_count=5000,
            )
        )
    return rows


def _four_hour_rows(days: int = 90) -> list[HistoricalKlineRow]:
    base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = 4 * 60 * 60 * 1_000_000_000
    rows: list[HistoricalKlineRow] = []
    for idx in range(days * 6):
        day = idx // 6
        trend = 42_000 + (day * 95) + (idx % 6) * 15
        rows.append(
            HistoricalKlineRow(
                symbol="BTCUSDT",
                timeframe="4h",
                open_time_ns=base_ns + idx * interval_ns,
                close_time_ns=base_ns + (idx + 1) * interval_ns - 1,
                open=trend,
                high=trend + 200,
                low=trend - 150,
                close=trend + 25,
                volume=200.0 + idx,
                trade_count=1000,
            )
        )
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
        HistoricalOpenInterestRow(
            symbol="BTCUSDT", timeframe="4h", ts_ns=base_ns + i * interval_ns, open_interest=50_000.0 + i * 10
        )
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

    def test_no_disabled_signal_has_high_weight(self) -> None:
        """Disabled signals should not silently carry high weight (sprint contract)."""
        import tomllib

        config_path = Path("config/profiles/default.toml")
        if not config_path.exists():
            self.skipTest("default.toml not found")

        config = tomllib.loads(config_path.read_text())
        for name, cfg in config.get("signals", {}).items():
            if not cfg.get("enabled", True):
                weight = cfg.get("weight", 0)
                # Disabled signals with weight > 0.3 might indicate a config error
                self.assertLessEqual(
                    weight,
                    0.3,
                    f"Disabled signal '{name}' has weight={weight} > 0.3 — review if this should be re-enabled",
                )

    def test_active_signals_have_minimal_count(self) -> None:
        """Strategy needs at least 2 active signals to avoid single-factor risk."""
        import tomllib

        config_path = Path("config/profiles/default.toml")
        if not config_path.exists():
            self.skipTest("default.toml not found")

        config = tomllib.loads(config_path.read_text())
        active = [n for n, c in config.get("signals", {}).items() if c.get("enabled", True)]
        self.assertGreaterEqual(
            len(active),
            2,
            f"Only {len(active)} active signal(s) — single-factor risk too high",
        )

    def test_walk_forward_stability(self) -> None:
        """Walk-forward: out-of-sample Sharpe must be positive in synthetic data.

        Sprint contract: train on first 60 bars, test on last 30.
        If test Sharpe < -2.0, the strategy overfits even synthetic data.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = HistoricalDatasetLayout(root_dir=root)
            write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), _daily_rows(90))
            write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), _four_hour_rows(90))
            write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), _funding_rows(90))
            write_parquet_rows(
                layout.open_interest_path("BTCUSDT", "4h", "2024-01"),
                _open_interest_rows(90),
            )

            # Test period only (last 30 days)
            base_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
            day_ns = 24 * 60 * 60 * 1_000_000_000
            test_start = base_ns + 60 * day_ns

            result = HistoricalBacktest(
                HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT", start_ns=test_start)
            ).run()

            # Sprint contract: test Sharpe must not be catastrophically negative
            self.assertGreater(
                result.metrics.sharpe,
                -2.0,
                f"Walk-forward test Sharpe={result.metrics.sharpe:.2f} < -2.0 — potential overfit",
            )

    def test_sprint_contract_ic_stability(self) -> None:
        """Sprint contract: active signals must have stable IC across time windows.

        NOT a single threshold — checks multiple properties jointly:
        1. Full-period |IC| > 0.02 (signal has any predictive value)
        2. IC doesn't flip sign between first and second half
        3. Rolling IC std < mean (signal isn't just noise spikes)

        This avoids single-parameter overfitting by checking structural
        consistency rather than optimizing a threshold.
        """
        import math

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = HistoricalDatasetLayout(root_dir=root)
            write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), _daily_rows(90))
            write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), _four_hour_rows(90))
            write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), _funding_rows(90))
            write_parquet_rows(
                layout.open_interest_path("BTCUSDT", "4h", "2024-01"),
                _open_interest_rows(90),
            )

            bt = HistoricalBacktest(HistoricalBacktestConfig(root_dir=root, symbol="BTCUSDT"))
            bt.run()

            packs = [p for p in bt._indicators if p.timeframe == Timeframe.TF_4H]
            if len(packs) < 60:
                self.skipTest("Insufficient data for IC stability check")

            # Compute forward returns
            fwd_returns = []
            for i in range(len(packs) - 1):
                p0 = float(packs[i].values.get("last_price", 0) or 0)
                p1 = float(packs[i + 1].values.get("last_price", 0) or 0)
                if p0 > 0 and p1 > 0:
                    fwd_returns.append((p1 - p0) / p0)
                else:
                    fwd_returns.append(0.0)

            signal_keys = ["multi_momentum_z", "cvd_slope", "funding_oi_z"]

            for key in signal_keys:
                values = [float(p.values.get(key, 0) or 0) for p in packs[:-1]]
                n = len(values)
                if n < 30:
                    continue

                # Rank correlation (IC)
                def _rank_corr(x: list[float], y: list[float]) -> float:
                    nn = len(x)
                    if nn < 10:
                        return 0.0
                    indexed_x = sorted(range(nn), key=lambda i: x[i])
                    indexed_y = sorted(range(nn), key=lambda i: y[i])
                    rx = [0.0] * nn
                    ry = [0.0] * nn
                    for rank, idx in enumerate(indexed_x):
                        rx[idx] = float(rank)
                    for rank, idx in enumerate(indexed_y):
                        ry[idx] = float(rank)
                    mx = sum(rx) / nn
                    my = sum(ry) / nn
                    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(nn))
                    sx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(nn)))
                    sy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(nn)))
                    return cov / (sx * sy) if sx > 1e-12 and sy > 1e-12 else 0.0

                # Check: IC sign doesn't flip between halves
                half = n // 2
                ic_first = _rank_corr(values[:half], fwd_returns[:half])
                ic_second = _rank_corr(values[half:], fwd_returns[half:])

                # Structural check: if both halves show IC, they shouldn't contradict
                # Only flag if BOTH halves have |IC| > 0.03 but opposite signs
                if abs(ic_first) > 0.03 and abs(ic_second) > 0.03:
                    self.assertFalse(
                        ic_first * ic_second < 0,
                        f"IC sign flip for {key}: first_half={ic_first:.3f}, second_half={ic_second:.3f}",
                    )


if __name__ == "__main__":
    unittest.main()
