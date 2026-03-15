import unittest

from zenquant.domain import EventMeta, MarkPriceUpdate, OrderBookLevel, OrderBookSnapshot, TargetPosition, Timeframe
from zenquant.execution import ExecutionGateway, ExecutionKernel, SimulatedGateway
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay


class ExecutionRouterTests(unittest.TestCase):
    def test_reduce_only_ignores_zero_position(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(bus, ExecutionGateway(), risk)

        reports = []
        bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))
        kernel._last_price["BTCUSDT_PERP"] = 100.0

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="intent-ro",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=1,
                target_qty=0.0,
                target_leverage=0.0,
                reduce_only=True,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["EXIT_TRAILING_STOP"],
            ),
        )

        self.assertEqual(reports, [])

    def test_mark_price_updates_enable_execution_without_trade_ticks(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(bus, ExecutionGateway(), risk)

        reports = []
        bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(
                    event_time_ns=10,
                    ingest_time_ns=11,
                    venue="BINANCE",
                    symbol="BTCUSDT_PERP",
                    stream_seq=1,
                ),
                mark_price=100.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="intent-mark",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=12,
                target_qty=1.0,
                target_leverage=1.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["REGIME_TREND"],
            ),
        )

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].status, "FILLED")
        self.assertEqual(reports[0].avg_price, 100.0)

    def test_simulated_gateway_uses_top_of_book(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(bus, SimulatedGateway(), risk)

        reports = []
        bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(
                    event_time_ns=20,
                    ingest_time_ns=21,
                    venue="BINANCE",
                    symbol="BTCUSDT_PERP",
                    stream_seq=1,
                ),
                mark_price=100.0,
            ),
        )
        bus.publish(
            "md.book.BINANCE.BTCUSDT_PERP",
            OrderBookSnapshot(
                meta=EventMeta(
                    event_time_ns=21,
                    ingest_time_ns=22,
                    venue="BINANCE",
                    symbol="BTCUSDT_PERP",
                    stream_seq=2,
                ),
                bids=[OrderBookLevel(price=99.5, quantity=4.0)],
                asks=[OrderBookLevel(price=100.5, quantity=4.0)],
                is_snapshot=True,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="intent-book",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=22,
                target_qty=1.0,
                target_leverage=1.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["REGIME_TREND"],
            ),
        )

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].avg_price, 100.5)
