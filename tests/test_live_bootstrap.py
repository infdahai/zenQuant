import unittest

from zenquant.app.server import ZenQuantWebRuntime
from zenquant.domain import Candle, EventMeta, MarkPriceUpdate, MarketSnapshot, Timeframe


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


class LiveBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_runtime_bootstrap_primes_current_signal(self) -> None:
        runtime = ZenQuantWebRuntime(runtime_mode="live")
        fake_ingestor = _FakeLiveIngestor(runtime.bus)
        runtime.ingestor = fake_ingestor

        await runtime.start()
        snapshot = runtime.snapshot(symbol="BTCUSDT_PERP", tf="4h")

        self.assertTrue(fake_ingestor.started)
        self.assertIsNotNone(runtime.indicator_store.latest("BTCUSDT_PERP", Timeframe.TF_1D))
        self.assertIsNotNone(runtime.indicator_store.latest("BTCUSDT_PERP", Timeframe.TF_4H))
        self.assertIsNotNone(snapshot["latest_indicator"])
        self.assertIsNotNone(snapshot["latest_target"])
        self.assertTrue(snapshot["runtime_status"]["market_connected"])

        await runtime.stop()
        self.assertTrue(fake_ingestor.stopped)


if __name__ == "__main__":
    unittest.main()
