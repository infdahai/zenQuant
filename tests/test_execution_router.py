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


class PyramidExecutionTests(unittest.TestCase):
    """Verify pyramid sizing: partial initial entry, asymmetric long/short."""

    def _setup(self, pyramid_enabled: bool = True):
        from zenquant.execution.router import ExecutionKernelConfig

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(pyramid_enabled=pyramid_enabled)
        kernel = ExecutionKernel(bus, SimulatedGateway(), risk, config=config)
        reports = []
        bus.subscribe("exec.order.report", lambda _, p: reports.append(p))

        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=1, ingest_time_ns=1, venue="BINANCE",
                               symbol="BTCUSDT_PERP", stream_seq=1),
                mark_price=40000.0,
            ),
        )
        return bus, kernel, reports

    def test_long_initial_30pct(self) -> None:
        """Long pyramid entry = 30% of target."""
        bus, kernel, reports = self._setup()
        bus.publish("intent.target_position.final.BTCUSDT_PERP", TargetPosition(
            intent_id="t1", portfolio="arc", symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H, cutoff_time_ns=2,
            target_qty=1.0, target_leverage=2.0, reduce_only=False,
            regime="TREND", regime_confidence=0.9, reason_codes=["TEST"],
        ))
        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(reports[0].filled_qty, 0.3, places=2)

    def test_short_initial_20pct(self) -> None:
        """Short pyramid entry = 20% (more conservative)."""
        bus, kernel, reports = self._setup()
        bus.publish("intent.target_position.final.BTCUSDT_PERP", TargetPosition(
            intent_id="t1", portfolio="arc", symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H, cutoff_time_ns=2,
            target_qty=-1.0, target_leverage=2.0, reduce_only=False,
            regime="TREND", regime_confidence=0.9, reason_codes=["TEST"],
        ))
        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(reports[0].filled_qty, 0.2, places=2)

    def test_pyramid_disabled_fills_full(self) -> None:
        """Without pyramid, fill up to max_trade_qty_per_bar."""
        bus, kernel, reports = self._setup(pyramid_enabled=False)
        bus.publish("intent.target_position.final.BTCUSDT_PERP", TargetPosition(
            intent_id="t1", portfolio="arc", symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H, cutoff_time_ns=2,
            target_qty=1.0, target_leverage=2.0, reduce_only=False,
            regime="TREND", regime_confidence=0.9, reason_codes=["TEST"],
        ))
        self.assertEqual(len(reports), 1)
        self.assertGreater(reports[0].filled_qty, 0.3)

    def test_position_tracked(self) -> None:
        """Kernel tracks positions after fills."""
        bus, kernel, reports = self._setup()
        bus.publish("intent.target_position.final.BTCUSDT_PERP", TargetPosition(
            intent_id="t1", portfolio="arc", symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H, cutoff_time_ns=2,
            target_qty=1.0, target_leverage=2.0, reduce_only=False,
            regime="TREND", regime_confidence=0.9, reason_codes=["TEST"],
        ))
        self.assertIn("BTCUSDT_PERP", kernel.positions)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0)
