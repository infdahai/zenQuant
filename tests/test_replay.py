import unittest

from zenquant.execution import ExecutionGateway, ExecutionKernel
from zenquant.indicator import IndicatorEngine, IndicatorStore
from zenquant.ingestor import SyntheticIngestor, SyntheticIngestorConfig
from zenquant.infra import DeterministicReplayer, InMemoryJetStream, StreamConfig
from zenquant.market import BarClockService, SnapshotBuilder
from zenquant.risk import RiskOverlay
from zenquant.strategy import ARCStrategyEngine


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

    def test_replay_parity_for_indicators_intents_and_execution(self) -> None:
        source_bus = InMemoryJetStream()
        source_bus.add_stream(
            StreamConfig(
                name="STREAM_RAW_MARKET_DATA",
                subjects=[
                    "md.trade.>",
                    "md.book.>",
                    "md.funding.>",
                    "md.oi.>",
                ],
            )
        )

        source_store = IndicatorStore()
        SnapshotBuilder(source_bus)
        BarClockService(source_bus)
        IndicatorEngine(source_bus, source_store)
        source_risk = RiskOverlay(source_bus)
        ARCStrategyEngine(source_bus)
        ExecutionKernel(source_bus, ExecutionGateway(), source_risk)

        src_indicators = []
        src_intents = []
        src_reports = []
        source_bus.subscribe(
            "indicator.>",
            lambda _subject, payload: src_indicators.append(
                (
                    payload.symbol,
                    payload.timeframe.value,
                    payload.cutoff_time_ns,
                    round(float(payload.values.get("zen_pulse", 0.0)), 6),
                )
            ),
        )
        source_bus.subscribe(
            "intent.target_position.final.>",
            lambda _subject, payload: src_intents.append(
                (
                    payload.symbol,
                    payload.timeframe.value,
                    payload.cutoff_time_ns,
                    round(payload.target_qty, 6),
                    round(payload.target_leverage, 6),
                    payload.reduce_only,
                )
            ),
        )
        source_bus.subscribe(
            "exec.order.report",
            lambda _subject, payload: src_reports.append(
                (
                    payload.symbol,
                    payload.status,
                    payload.side,
                    round(payload.filled_qty, 6),
                    payload.transaction_time_ns,
                )
            ),
        )

        ingestor = SyntheticIngestor(source_bus, config=SyntheticIngestorConfig(seed=23))
        ingestor.run(steps=360)

        replayer = DeterministicReplayer.from_stream(source_bus, "STREAM_RAW_MARKET_DATA")

        target_bus = InMemoryJetStream()
        target_store = IndicatorStore()
        SnapshotBuilder(target_bus)
        BarClockService(target_bus)
        IndicatorEngine(target_bus, target_store)
        target_risk = RiskOverlay(target_bus)
        ARCStrategyEngine(target_bus)
        ExecutionKernel(target_bus, ExecutionGateway(), target_risk)

        dst_indicators = []
        dst_intents = []
        dst_reports = []
        target_bus.subscribe(
            "indicator.>",
            lambda _subject, payload: dst_indicators.append(
                (
                    payload.symbol,
                    payload.timeframe.value,
                    payload.cutoff_time_ns,
                    round(float(payload.values.get("zen_pulse", 0.0)), 6),
                )
            ),
        )
        target_bus.subscribe(
            "intent.target_position.final.>",
            lambda _subject, payload: dst_intents.append(
                (
                    payload.symbol,
                    payload.timeframe.value,
                    payload.cutoff_time_ns,
                    round(payload.target_qty, 6),
                    round(payload.target_leverage, 6),
                    payload.reduce_only,
                )
            ),
        )
        target_bus.subscribe(
            "exec.order.report",
            lambda _subject, payload: dst_reports.append(
                (
                    payload.symbol,
                    payload.status,
                    payload.side,
                    round(payload.filled_qty, 6),
                    payload.transaction_time_ns,
                )
            ),
        )

        replayer.replay(target_bus)

        self.assertEqual(src_indicators, dst_indicators)
        self.assertEqual(src_intents, dst_intents)
        self.assertEqual(src_reports, dst_reports)


if __name__ == "__main__":
    unittest.main()
