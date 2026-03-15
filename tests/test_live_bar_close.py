import unittest

from zenquant.domain import BarCloseEvent, EventMeta, Timeframe, TradeTick
from zenquant.infra import InMemoryJetStream
from zenquant.market import SnapshotBuilder


class LiveBarCloseTests(unittest.TestCase):
    def test_snapshot_builder_finalizes_cvd_on_explicit_bar_close(self) -> None:
        bus = InMemoryJetStream()
        builder = SnapshotBuilder(bus)
        snapshots = []
        bus.subscribe("md.snapshot.>", lambda _subject, payload: snapshots.append(payload))

        interval_ns = Timeframe.TF_4H.seconds * 1_000_000_000
        bar_start_ns = 0
        bar_end_ns = interval_ns
        bus.publish(
            "md.trade.BINANCE.BTCUSDT_PERP",
            TradeTick(
                meta=EventMeta(
                    event_time_ns=bar_start_ns + 1,
                    ingest_time_ns=bar_start_ns + 2,
                    venue="BINANCE",
                    symbol="BTCUSDT_PERP",
                    stream_seq=1,
                ),
                price=100.0,
                size=2.0,
                is_buyer_aggressor=True,
            ),
        )
        bus.publish(
            "clock.bar_close.4h",
            BarCloseEvent(
                timeframe=Timeframe.TF_4H,
                symbol="BTCUSDT_PERP",
                bar_start_ns=bar_start_ns,
                bar_end_ns=bar_end_ns,
            ),
        )

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].cvd_delta, 2.0)


if __name__ == "__main__":
    unittest.main()
