import tempfile
import unittest
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from zenquant.data.liquidation_pipeline import (
    LiquidationEvent,
    aggregate_liquidation_events,
    aligned_bucket_bounds,
    liquidation_readiness,
    merge_liquidation_buckets,
    probe_binance_force_orders_support,
    write_aggregated_liquidations,
)
from zenquant.historical import HistoricalDatasetLayout, HistoricalKlineRow, write_parquet_rows

HOUR_NS = 3600 * 1_000_000_000


def _ts(year: int, month: int, day: int, hour: int) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp() * 1_000_000_000)


class LiquidationPipelineTests(unittest.TestCase):
    def test_aligned_bucket_bounds_use_utc_four_hour_buckets(self) -> None:
        ts_ns = _ts(2024, 1, 1, 5)
        start_ns, end_ns = aligned_bucket_bounds(ts_ns)
        self.assertEqual(start_ns, _ts(2024, 1, 1, 4))
        self.assertEqual(end_ns, _ts(2024, 1, 1, 8))

    def test_aggregate_events_aligns_and_splits_by_symbol(self) -> None:
        events = [
            LiquidationEvent(
                ts_ns=_ts(2024, 1, 1, 4),
                symbol="BTCUSDT",
                side="SELL",
                price=40_000.0,
                qty=1.0,
                usd_value=40_000.0,
                source="test",
                trade_time_ms=_ts(2024, 1, 1, 4) // 1_000_000,
            ),
            LiquidationEvent(
                ts_ns=_ts(2024, 1, 1, 7),
                symbol="BTCUSDT",
                side="BUY",
                price=39_000.0,
                qty=2.0,
                usd_value=78_000.0,
                source="test",
                trade_time_ms=_ts(2024, 1, 1, 7) // 1_000_000,
            ),
            LiquidationEvent(
                ts_ns=_ts(2024, 1, 1, 8),
                symbol="ETHUSDT",
                side="SELL",
                price=2_000.0,
                qty=5.0,
                usd_value=10_000.0,
                source="test",
                trade_time_ms=_ts(2024, 1, 1, 8) // 1_000_000,
            ),
        ]

        buckets = aggregate_liquidation_events(events)
        self.assertEqual(len(buckets), 2)
        btc = next(item for item in buckets if item.symbol == "BTCUSDT")
        eth = next(item for item in buckets if item.symbol == "ETHUSDT")
        self.assertEqual(btc.bucket_start_ns, _ts(2024, 1, 1, 4))
        self.assertEqual(btc.bucket_end_ns, _ts(2024, 1, 1, 8))
        self.assertEqual(btc.long_liq_count, 1)
        self.assertEqual(btc.short_liq_count, 1)
        self.assertAlmostEqual(btc.total_liq_usd, 118_000.0)
        self.assertEqual(btc.source, "test")
        self.assertEqual(eth.bucket_start_ns, _ts(2024, 1, 1, 8))

    def test_merge_liquidation_buckets_combines_sources(self) -> None:
        buckets = merge_liquidation_buckets(
            [
                aggregate_liquidation_events(
                    [
                        LiquidationEvent(
                            ts_ns=_ts(2024, 1, 1, 4),
                            symbol="BTCUSDT",
                            side="SELL",
                            price=40_000.0,
                            qty=1.0,
                            usd_value=40_000.0,
                            source="binance_official",
                            trade_time_ms=_ts(2024, 1, 1, 4) // 1_000_000,
                        )
                    ]
                )[0],
                aggregate_liquidation_events(
                    [
                        LiquidationEvent(
                            ts_ns=_ts(2024, 1, 1, 5),
                            symbol="BTCUSDT",
                            side="BUY",
                            price=39_000.0,
                            qty=1.0,
                            usd_value=39_000.0,
                            source="hyperliquid_public",
                            trade_time_ms=_ts(2024, 1, 1, 5) // 1_000_000,
                        )
                    ]
                )[0],
            ]
        )

        self.assertEqual(len(buckets), 1)
        merged = buckets[0]
        self.assertEqual(merged.source, "merged")
        self.assertEqual(merged.long_liq_count, 1)
        self.assertEqual(merged.short_liq_count, 1)
        self.assertAlmostEqual(merged.total_liq_usd, 79_000.0)

    def test_liquidation_readiness_reports_missing_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = HistoricalDatasetLayout(root_dir=root)
            rows = [
                HistoricalKlineRow(
                    symbol="BTCUSDT",
                    timeframe="4h",
                    open_time_ns=_ts(2024, 1, 1, 0),
                    close_time_ns=_ts(2024, 1, 1, 4),
                    open=40_000.0,
                    high=40_100.0,
                    low=39_900.0,
                    close=40_050.0,
                    volume=100.0,
                    trade_count=50,
                )
            ]
            write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), rows)

            readiness = liquidation_readiness(root_dir=root, symbol="BTCUSDT")

        self.assertFalse(readiness.data_ready)
        self.assertEqual(readiness.blocked_by, "missing_liquidation_history")
        self.assertEqual(readiness.nonzero_bars, 0)

    def test_liquidation_readiness_reports_nonzero_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = HistoricalDatasetLayout(root_dir=root)
            write_parquet_rows(
                layout.kline_path("BTCUSDT", "4h", "2024-01"),
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="4h",
                        open_time_ns=_ts(2024, 1, 1, 0),
                        close_time_ns=_ts(2024, 1, 1, 4),
                        open=40_000.0,
                        high=40_100.0,
                        low=39_900.0,
                        close=40_050.0,
                        volume=100.0,
                        trade_count=50,
                    )
                ],
            )

            buckets = aggregate_liquidation_events(
                [
                    LiquidationEvent(
                        ts_ns=_ts(2024, 1, 1, 1),
                        symbol="BTCUSDT",
                        side="SELL",
                        price=40_000.0,
                        qty=1.5,
                        usd_value=60_000.0,
                        source="test",
                        trade_time_ms=_ts(2024, 1, 1, 1) // 1_000_000,
                    )
                ]
            )
            liq_root = root / "merged_liquidations"
            write_aggregated_liquidations(liq_root, buckets)
            readiness = liquidation_readiness(root_dir=root, symbol="BTCUSDT", liquidation_dir=liq_root / "aggregated")

        self.assertTrue(readiness.data_ready)
        self.assertFalse(readiness.promotion_ready)
        self.assertEqual(readiness.nonzero_bars, 1)
        self.assertEqual(readiness.nonstale_bars, 1)

    def test_binance_force_orders_probe_exposes_current_blocker(self) -> None:
        from urllib.error import HTTPError

        error = HTTPError(
            url="https://fapi.binance.com/fapi/v1/allForceOrders?symbol=BTCUSDT&limit=1",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(b'{"code":400,"msg":"The endpoint has been out of maintenance"}'),
        )
        with patch("zenquant.data.liquidation_pipeline.urlopen", side_effect=error):
            probe = probe_binance_force_orders_support("BTCUSDT")

        self.assertFalse(probe.available)
        self.assertEqual(probe.blocked_by, "binance_force_orders_maintenance")


if __name__ == "__main__":
    unittest.main()
