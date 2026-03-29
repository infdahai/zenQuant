import asyncio
import unittest

from zenquant.app.server import ZenQuantWebRuntime
from zenquant.domain import Candle, EventMeta, MarketSnapshot, MarkPriceUpdate, Timeframe


class _FakeLiveIngestor:
    def __init__(self, bus) -> None:
        self._bus = bus
        self.started = False
        self.stopped = False

    async def bootstrap_history(self) -> None:
        symbol = "BTCUSDT_PERP"
        bootstrap_points = [
            (
                Timeframe.TF_1D,
                86_400_000_000_000,
                Candle(
                    symbol=symbol,
                    timeframe=Timeframe.TF_1D,
                    bar_start_ns=0,
                    bar_end_ns=86_400_000_000_000,
                    open=40_000.0,
                    high=40_800.0,
                    low=39_800.0,
                    close=40_500.0,
                    volume=220.0,
                    trade_count=1_200,
                ),
                MarketSnapshot(
                    symbol=symbol,
                    timeframe=Timeframe.TF_1D,
                    cutoff_time_ns=86_400_000_000_000,
                    last_price=40_500.0,
                    bid_depth=220.0,
                    ask_depth=220.0,
                    book_age_ms=0,
                    venue_count=1,
                    cvd_delta=220.0,
                    funding_oi_weighted=0.0001,
                    oi_coverage=1.0,
                    staleness_ms=0,
                    quality_flags=["OK"],
                ),
            ),
            (
                Timeframe.TF_4H,
                100_800_000_000_000,
                Candle(
                    symbol=symbol,
                    timeframe=Timeframe.TF_4H,
                    bar_start_ns=86_400_000_000_000,
                    bar_end_ns=100_800_000_000_000,
                    open=40_500.0,
                    high=40_900.0,
                    low=40_200.0,
                    close=40_700.0,
                    volume=90.0,
                    trade_count=480,
                ),
                MarketSnapshot(
                    symbol=symbol,
                    timeframe=Timeframe.TF_4H,
                    cutoff_time_ns=100_800_000_000_000,
                    last_price=40_700.0,
                    bid_depth=90.0,
                    ask_depth=90.0,
                    book_age_ms=0,
                    venue_count=1,
                    cvd_delta=90.0,
                    funding_oi_weighted=0.00012,
                    oi_coverage=1.0,
                    staleness_ms=0,
                    quality_flags=["OK"],
                ),
            ),
        ]

        for timeframe, event_time_ns, candle, snapshot in bootstrap_points:
            self._bus.publish(
                f"md.mark.BINANCE.{symbol}",
                MarkPriceUpdate(
                    meta=EventMeta(
                        event_time_ns=event_time_ns,
                        ingest_time_ns=event_time_ns,
                        venue="BINANCE",
                        symbol=symbol,
                        stream_seq=event_time_ns,
                    ),
                    mark_price=snapshot.last_price,
                    index_price=snapshot.last_price,
                ),
            )
            self._bus.publish(f"md.candle.{timeframe.value}.{symbol}", candle)
            self._bus.publish(f"md.snapshot.{timeframe.value}.{symbol}", snapshot)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FailingBootstrapIngestor(_FakeLiveIngestor):
    async def bootstrap_history(self) -> None:
        raise RuntimeError("bootstrap offline")


class _PartialBootstrapIngestor(_FakeLiveIngestor):
    async def bootstrap_history(self) -> None:
        symbol = "BTCUSDT_PERP"
        self._bus.publish(
            f"md.candle.4h.{symbol}",
            Candle(
                symbol=symbol,
                timeframe=Timeframe.TF_4H,
                bar_start_ns=86_400_000_000_000,
                bar_end_ns=100_800_000_000_000,
                open=40_500.0,
                high=40_900.0,
                low=40_200.0,
                close=40_700.0,
                volume=90.0,
                trade_count=480,
            ),
        )
        self._bus.publish(
            f"md.snapshot.4h.{symbol}",
            MarketSnapshot(
                symbol=symbol,
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=100_800_000_000_000,
                last_price=40_700.0,
                bid_depth=90.0,
                ask_depth=90.0,
                book_age_ms=0,
                venue_count=1,
                cvd_delta=90.0,
                funding_oi_weighted=0.00012,
                oi_coverage=1.0,
                staleness_ms=0,
                quality_flags=["OK"],
            ),
        )


class _ReconnectLiveIngestor(_FakeLiveIngestor):
    async def start(self) -> None:
        self.started = True
        symbol = "BTCUSDT_PERP"
        event_time_ns = 115_200_000_000_000
        await asyncio.sleep(0)
        self._bus.publish(
            f"md.mark.BINANCE.{symbol}",
            MarkPriceUpdate(
                meta=EventMeta(
                    event_time_ns=event_time_ns,
                    ingest_time_ns=event_time_ns,
                    venue="BINANCE",
                    symbol=symbol,
                    stream_seq=event_time_ns,
                ),
                mark_price=41_200.0,
                index_price=41_200.0,
            ),
        )
        self._bus.publish(
            f"md.candle.4h.{symbol}",
            Candle(
                symbol=symbol,
                timeframe=Timeframe.TF_4H,
                bar_start_ns=100_800_000_000_000,
                bar_end_ns=event_time_ns,
                open=40_700.0,
                high=41_500.0,
                low=40_600.0,
                close=41_200.0,
                volume=125.0,
                trade_count=620,
            ),
        )
        self._bus.publish(
            f"md.snapshot.4h.{symbol}",
            MarketSnapshot(
                symbol=symbol,
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=event_time_ns,
                last_price=41_200.0,
                bid_depth=110.0,
                ask_depth=108.0,
                book_age_ms=0,
                venue_count=1,
                cvd_delta=130.0,
                funding_oi_weighted=0.00015,
                oi_coverage=1.0,
                staleness_ms=0,
                quality_flags=["OK"],
            ),
        )


class LiveBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_runtime_bootstrap_primes_current_signal(self) -> None:
        runtime = ZenQuantWebRuntime(runtime_mode="live")
        fake_ingestor = _FakeLiveIngestor(runtime.bus)
        runtime.ingestor = fake_ingestor

        await runtime.start()
        await runtime._live_start_task
        snapshot = runtime.snapshot(symbol="BTCUSDT_PERP", tf="4h")

        self.assertTrue(fake_ingestor.started)
        self.assertIsNotNone(runtime.indicator_store.latest("BTCUSDT_PERP", Timeframe.TF_1D))
        self.assertIsNotNone(runtime.indicator_store.latest("BTCUSDT_PERP", Timeframe.TF_4H))
        self.assertIsNotNone(snapshot["latest_indicator"])
        self.assertIsNotNone(snapshot["latest_target"])
        self.assertIsNotNone(snapshot["directional_bias"])
        self.assertIsNotNone(snapshot["market_state"])
        self.assertIsNotNone(snapshot["active_playbook"])
        self.assertIsNotNone(snapshot["latest_sizing"])
        self.assertTrue(snapshot["runtime_status"]["market_connected"])
        self.assertEqual(snapshot["runtime_status"]["bootstrap_phase"], "ready")
        self.assertTrue(snapshot["runtime_status"]["bootstrap_live_started"])
        self.assertEqual(snapshot["reports"], [])

        await runtime.stop()
        self.assertTrue(fake_ingestor.stopped)

    async def test_live_runtime_continues_when_bootstrap_fails(self) -> None:
        runtime = ZenQuantWebRuntime(runtime_mode="live")
        fake_ingestor = _FailingBootstrapIngestor(runtime.bus)
        runtime.ingestor = fake_ingestor

        await runtime.start()
        await runtime._live_start_task
        snapshot = runtime.snapshot(symbol="BTCUSDT_PERP", tf="4h")

        self.assertTrue(fake_ingestor.started)
        self.assertEqual(snapshot["runtime_status"]["bootstrap_phase"], "failed")
        self.assertIn("cold live feed", snapshot["runtime_status"]["bootstrap_detail"])

        await runtime.stop()
        self.assertTrue(fake_ingestor.stopped)

    async def test_live_runtime_refreshes_state_after_post_start_feed_event(self) -> None:
        runtime = ZenQuantWebRuntime(runtime_mode="live")
        fake_ingestor = _ReconnectLiveIngestor(runtime.bus)
        runtime.ingestor = fake_ingestor

        await runtime.start()
        await runtime._live_start_task
        snapshot = runtime.snapshot(symbol="BTCUSDT_PERP", tf="4h")

        self.assertTrue(fake_ingestor.started)
        self.assertEqual(snapshot["runtime_status"]["bootstrap_phase"], "ready")
        self.assertEqual(snapshot["runtime_status"]["last_market_event_ns"], 115_200_000_000_000)
        self.assertAlmostEqual(snapshot["last_price"], 41_200.0)
        self.assertEqual(snapshot["latest_indicator"]["cutoff_time_ns"], 115_200_000_000_000)
        self.assertEqual(snapshot["market_state"]["cutoff_time_ns"], 115_200_000_000_000)
        self.assertGreater(len(snapshot["price_series"]), 0)

        await runtime.stop()
        self.assertTrue(fake_ingestor.stopped)

    async def test_live_runtime_marks_partial_bootstrap_as_failed(self) -> None:
        runtime = ZenQuantWebRuntime(runtime_mode="live")
        fake_ingestor = _PartialBootstrapIngestor(runtime.bus)
        runtime.ingestor = fake_ingestor

        await runtime.start()
        await runtime._live_start_task
        snapshot = runtime.snapshot(symbol="BTCUSDT_PERP", tf="4h")

        self.assertTrue(fake_ingestor.started)
        self.assertEqual(snapshot["runtime_status"]["bootstrap_phase"], "failed")
        self.assertIn("BTCUSDT_PERP:1d", snapshot["runtime_status"]["bootstrap_error"])

        await runtime.stop()
        self.assertTrue(fake_ingestor.stopped)


if __name__ == "__main__":
    unittest.main()
