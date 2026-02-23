import unittest

from zenquant.infra import DeterministicReplayer, InMemoryJetStream, StreamConfig


class ReplayTests(unittest.TestCase):
    def test_replay_preserves_message_order(self) -> None:
        source_bus = InMemoryJetStream()
        source_bus.add_stream(StreamConfig(name="SRC", subjects=["md.>"]))
        payloads = ["a", "b", "c", "d"]
        for payload in payloads:
            source_bus.publish("md.trade.BINANCE.BTC_USDT", payload)

        replayer = DeterministicReplayer.from_stream(source_bus, "SRC")
        target_bus = InMemoryJetStream()
        replayed: list[str] = []
        target_bus.subscribe("md.>", lambda _subject, data: replayed.append(data))
        replayer.replay(target_bus)

        self.assertEqual(replayed, payloads)


if __name__ == "__main__":
    unittest.main()

