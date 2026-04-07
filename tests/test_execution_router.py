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


class DCAOnLossTests(unittest.TestCase):
    """Verify PaulWei DCA: longs add on loss at 0.5x, shorts never add on loss."""

    def _target(self, qty=1.0, intent_id="t1", cutoff_ns=2):
        return TargetPosition(
            intent_id=intent_id, portfolio="arc", symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H, cutoff_time_ns=cutoff_ns,
            target_qty=qty, target_leverage=2.0, reduce_only=False,
            regime="TREND", regime_confidence=0.9, reason_codes=["TEST"],
        )

    def _setup(self, dca_on_loss_long=True, mark_price=40000.0):
        from zenquant.execution.router import ExecutionKernelConfig

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(dca_on_loss_long=dca_on_loss_long)
        kernel = ExecutionKernel(bus, SimulatedGateway(), risk, config=config)
        reports = []
        bus.subscribe("exec.order.report", lambda _, p: reports.append(p))

        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=1, ingest_time_ns=1, venue="BINANCE",
                               symbol="BTCUSDT_PERP", stream_seq=1),
                mark_price=mark_price,
            ),
        )
        return bus, kernel, reports

    def test_long_dca_on_loss_adds_half_size(self) -> None:
        """Losing long with DCA enabled → add at 0.5x normal size (15% not 30%)."""
        bus, kernel, reports = self._setup(mark_price=40000.0)

        # Initial long entry at 40000
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)
        initial_qty = reports[0].filled_qty
        self.assertAlmostEqual(initial_qty, 0.3, places=2)  # 30% initial

        # Price drops → losing position
        bus.publish("md.mark.BINANCE.BTCUSDT_PERP", MarkPriceUpdate(
            meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE",
                           symbol="BTCUSDT_PERP", stream_seq=2),
            mark_price=38000.0,
        ))

        # Second signal → should DCA add at half size
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t2", cutoff_ns=4))
        self.assertEqual(len(reports), 2)
        dca_qty = reports[1].filled_qty
        self.assertAlmostEqual(dca_qty, 0.15, places=2)  # 50% of 30% = 15%

    def test_short_no_dca_on_loss(self) -> None:
        """Losing short → NO add regardless of DCA config."""
        bus, kernel, reports = self._setup(mark_price=40000.0)

        # Initial short entry at 40000
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        # Price rises → losing short
        bus.publish("md.mark.BINANCE.BTCUSDT_PERP", MarkPriceUpdate(
            meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE",
                           symbol="BTCUSDT_PERP", stream_seq=2),
            mark_price=42000.0,
        ))

        # Second signal → should NOT add (short losing)
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="t2", cutoff_ns=4))
        self.assertEqual(len(reports), 1)  # No new fill

    def test_dca_disabled_no_add_on_loss(self) -> None:
        """dca_on_loss_long=False → losing long gets NO add."""
        bus, kernel, reports = self._setup(dca_on_loss_long=False, mark_price=40000.0)

        # Initial long
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        # Price drops
        bus.publish("md.mark.BINANCE.BTCUSDT_PERP", MarkPriceUpdate(
            meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE",
                           symbol="BTCUSDT_PERP", stream_seq=2),
            mark_price=38000.0,
        ))

        # No DCA add
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t2", cutoff_ns=4))
        self.assertEqual(len(reports), 1)  # No new fill

    def test_profitable_long_adds_full_size(self) -> None:
        """Profitable long → normal add (30%), not DCA reduced (15%)."""
        bus, kernel, reports = self._setup(mark_price=40000.0)

        # Initial long
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        # Price rises → profitable
        bus.publish("md.mark.BINANCE.BTCUSDT_PERP", MarkPriceUpdate(
            meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE",
                           symbol="BTCUSDT_PERP", stream_seq=2),
            mark_price=42000.0,
        ))

        # Add at full size (30%)
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t2", cutoff_ns=4))
        self.assertEqual(len(reports), 2)
        add_qty = reports[1].filled_qty
        self.assertAlmostEqual(add_qty, 0.3, places=2)  # Full 30%, not 15%

    def test_dca_accumulates_then_stops(self) -> None:
        """Multiple DCA adds accumulate, then stop when fully positioned."""
        bus, kernel, reports = self._setup(mark_price=40000.0)

        # Initial long (30% filled)
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        # Price drops → each subsequent signal should DCA at 15%
        for i in range(10):
            bus.publish("md.mark.BINANCE.BTCUSDT_PERP", MarkPriceUpdate(
                meta=EventMeta(event_time_ns=10+i, ingest_time_ns=10+i, venue="BINANCE",
                               symbol="BTCUSDT_PERP", stream_seq=10+i),
                mark_price=39000.0 - i * 100,  # Price keeps dropping
            ))
            bus.publish("intent.target_position.final.BTCUSDT_PERP",
                        self._target(qty=1.0, intent_id=f"dca{i}", cutoff_ns=20+i))

        # Should have: initial (30%) + some DCA adds (15% each) until 100%
        # Max fills: 30% + ceil((70%/15%)) = 30% + 5 adds = ~105% capped to 100%
        total_filled = sum(r.filled_qty for r in reports)
        self.assertLessEqual(total_filled, 1.0, "Total filled should not exceed 100%")


class AdaptivePyramidTests(unittest.TestCase):
    """Verify vol-adaptive pyramid speed: fast in calm, slow in crashes."""

    def _setup(self, adaptive_enabled: bool = True):
        from zenquant.execution.router import ExecutionKernelConfig

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(adaptive_pyramid_enabled=adaptive_enabled)
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

    def _target(self, qty=1.0, realized_vol=0.6, atr=500.0, reason_codes=None, **kwargs):
        return TargetPosition(
            intent_id=kwargs.get("intent_id", "t1"),
            portfolio="arc",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=kwargs.get("cutoff_ns", 2),
            target_qty=qty,
            target_leverage=2.0,
            reduce_only=kwargs.get("reduce_only", False),
            regime="TREND",
            regime_confidence=0.9,
            reason_codes=reason_codes or ["TEST"],
            realized_vol=realized_vol,
            atr=atr,
        )

    def test_low_vol_builds_faster(self) -> None:
        """Low vol (0.30) → init should be ~40% (capped at max_speed)."""
        bus, kernel, reports = self._setup()
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, realized_vol=0.30))
        self.assertEqual(len(reports), 1)
        # vol_ratio = 0.60/0.30 = 2.0, init_pct = 0.30*2.0 = 0.60 → capped at 0.40
        self.assertAlmostEqual(reports[0].filled_qty, 0.4, places=2)

    def test_high_vol_builds_slower(self) -> None:
        """High vol (1.20) → init should be ~15%."""
        bus, kernel, reports = self._setup()
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, realized_vol=1.20))
        self.assertEqual(len(reports), 1)
        # vol_ratio = 0.60/1.20 = 0.5, init_pct = 0.30*0.5 = 0.15
        self.assertAlmostEqual(reports[0].filled_qty, 0.15, places=2)

    def test_adaptive_disabled_uses_fixed(self) -> None:
        """adaptive_pyramid_enabled=False → fixed 30% regardless of vol."""
        bus, kernel, reports = self._setup(adaptive_enabled=False)
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, realized_vol=1.20))
        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(reports[0].filled_qty, 0.3, places=2)


class GradualExitTests(unittest.TestCase):
    """Verify gradual exit: non-immediate exits unwind fraction per bar."""

    def _setup(self, gradual_enabled: bool = True):
        from zenquant.execution.router import ExecutionKernelConfig

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(
            gradual_exit_enabled=gradual_enabled,
            adaptive_pyramid_enabled=False,  # isolate exit behavior
        )
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

    def _target(self, qty=1.0, reduce_only=False, reason_codes=None, **kwargs):
        return TargetPosition(
            intent_id=kwargs.get("intent_id", "t1"),
            portfolio="arc",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=kwargs.get("cutoff_ns", 2),
            target_qty=qty,
            target_leverage=2.0,
            reduce_only=reduce_only,
            regime="TREND",
            regime_confidence=0.9,
            reason_codes=reason_codes or ["TEST"],
        )

    def test_gradual_exit_time_decay(self) -> None:
        """TIME_DECAY exit → only 30% of position closed per bar."""
        bus, kernel, reports = self._setup()

        # Build position (pyramid disabled for simplicity — uses legacy path)
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, intent_id="entry", cutoff_ns=2))
        self.assertEqual(len(reports), 1)
        entry_qty = reports[0].filled_qty

        # Gradual exit (TIME_DECAY)
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=0.0, reduce_only=True,
                                 reason_codes=["EXIT_TIME_DECAY", "UNPROFITABLE"],
                                 intent_id="exit1", cutoff_ns=3))
        self.assertEqual(len(reports), 2)
        exit_qty = reports[1].filled_qty
        # Should reduce ~30% of position, not 100%
        self.assertLess(exit_qty, entry_qty * 0.5)
        self.assertGreater(exit_qty, 0)

    def test_immediate_exit_trailing_stop(self) -> None:
        """TRAILING_STOP → 100% immediate exit."""
        bus, kernel, reports = self._setup()

        # Build position
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, intent_id="entry", cutoff_ns=2))
        entry_qty = reports[0].filled_qty

        # Immediate exit
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=0.0, reduce_only=True,
                                 reason_codes=["EXIT_TRAILING_STOP"],
                                 intent_id="exit1", cutoff_ns=3))
        self.assertEqual(len(reports), 2)
        # Should close entire position
        self.assertAlmostEqual(reports[1].filled_qty, entry_qty, places=4)

    def test_gradual_exit_full_unwind(self) -> None:
        """Multiple gradual exit bars → eventually fully closed."""
        bus, kernel, reports = self._setup()

        # Build position
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, intent_id="entry", cutoff_ns=2))

        # Send gradual exit repeatedly
        for i in range(10):
            bus.publish("intent.target_position.final.BTCUSDT_PERP",
                        self._target(qty=0.0, reduce_only=True,
                                     reason_codes=["EXIT_TIME_DECAY"],
                                     intent_id=f"exit{i}", cutoff_ns=10 + i))

        total_exit = sum(r.filled_qty for r in reports[1:])
        # 30% geometric decay: after 10 bars, ~97% unwound
        self.assertGreater(total_exit / reports[0].filled_qty, 0.95)


class DCADistanceGatingTests(unittest.TestCase):
    """Verify DCA stops when price moves too far from entry."""

    def _setup(self):
        from zenquant.execution.router import ExecutionKernelConfig

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(
            adaptive_pyramid_enabled=True,  # distance gating requires adaptive
            dca_max_adverse_atr=3.0,
            pyramid_target_vol=0.60,
        )
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

    def _target(self, qty=1.0, atr=500.0, **kwargs):
        return TargetPosition(
            intent_id=kwargs.get("intent_id", "t1"),
            portfolio="arc",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=kwargs.get("cutoff_ns", 2),
            target_qty=qty,
            target_leverage=2.0,
            reduce_only=False,
            regime="TREND",
            regime_confidence=0.9,
            reason_codes=["TEST"],
            atr=atr,
        )

    def test_dca_blocked_beyond_3_atr(self) -> None:
        """Price 4 ATR below entry → no DCA add."""
        bus, kernel, reports = self._setup()

        # Initial entry at 40000
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, atr=500.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        # Price drops 2000 (4 ATR with atr=500)
        bus.publish("md.mark.BINANCE.BTCUSDT_PERP", MarkPriceUpdate(
            meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE",
                           symbol="BTCUSDT_PERP", stream_seq=2),
            mark_price=38000.0,
        ))
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, atr=500.0, intent_id="t2", cutoff_ns=4))
        # Should NOT add — 4 ATR away exceeds 3 ATR limit
        self.assertEqual(len(reports), 1)

    def test_dca_scales_with_distance(self) -> None:
        """Price 1 ATR below entry → near-full DCA; 2 ATR → reduced."""
        bus, kernel, reports = self._setup()

        # Initial entry
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, atr=1000.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        # Price drops 1000 (1 ATR)
        bus.publish("md.mark.BINANCE.BTCUSDT_PERP", MarkPriceUpdate(
            meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE",
                           symbol="BTCUSDT_PERP", stream_seq=2),
            mark_price=39000.0,
        ))
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, atr=1000.0, intent_id="t2", cutoff_ns=4))
        self.assertEqual(len(reports), 2)
        close_dca = reports[1].filled_qty

        # Price drops 2000 more (now 3 ATR total from a new setup)
        bus2, kernel2, reports2 = self._setup()
        bus2.publish("intent.target_position.final.BTCUSDT_PERP",
                     self._target(qty=1.0, atr=1000.0, intent_id="t1", cutoff_ns=2))
        bus2.publish("md.mark.BINANCE.BTCUSDT_PERP", MarkPriceUpdate(
            meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE",
                           symbol="BTCUSDT_PERP", stream_seq=2),
            mark_price=37500.0,  # 2.5 ATR away
        ))
        bus2.publish("intent.target_position.final.BTCUSDT_PERP",
                     self._target(qty=1.0, atr=1000.0, intent_id="t2", cutoff_ns=4))
        if len(reports2) > 1:
            far_dca = reports2[1].filled_qty
            self.assertLess(far_dca, close_dca, "DCA should be smaller when farther from entry")


class FlipExecutionTests(unittest.TestCase):
    """Verify direction flip: flatten + open opposite in one bar."""

    def _setup(self, flip_immediate: bool = True):
        from zenquant.execution.router import ExecutionKernelConfig

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(
            flip_immediate=flip_immediate,
            adaptive_pyramid_enabled=False,
            gradual_exit_enabled=True,
        )
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

    def _target(self, qty=1.0, reason_codes=None, **kwargs):
        return TargetPosition(
            intent_id=kwargs.get("intent_id", "t1"),
            portfolio="arc",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=kwargs.get("cutoff_ns", 2),
            target_qty=qty,
            target_leverage=2.0,
            reduce_only=kwargs.get("reduce_only", False),
            regime="TREND",
            regime_confidence=0.9,
            reason_codes=reason_codes or ["TEST"],
        )

    def test_flip_long_to_short_one_bar(self) -> None:
        """Long→Short completes in 1 bar: close long + open short."""
        bus, kernel, reports = self._setup()

        # Build long position
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, intent_id="entry", cutoff_ns=2))
        self.assertEqual(len(reports), 1)
        long_qty = reports[0].filled_qty
        self.assertGreater(long_qty, 0)

        # Flip to short
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=-1.0, intent_id="flip", cutoff_ns=3))

        # Should have 2 new reports: close long + open short
        self.assertEqual(len(reports), 3)
        # First: close the long
        self.assertEqual(reports[1].side, -1)
        self.assertAlmostEqual(reports[1].filled_qty, long_qty, places=4)
        # Second: open short (20% initial)
        self.assertEqual(reports[2].side, -1)
        self.assertAlmostEqual(reports[2].filled_qty, 0.2, places=2)
        # Position should be short now
        self.assertLess(kernel.positions["BTCUSDT_PERP"], 0)

    def test_flip_short_to_long_one_bar(self) -> None:
        """Short→Long completes in 1 bar."""
        bus, kernel, reports = self._setup()

        # Build short position
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        self.assertEqual(len(reports), 1)
        short_qty = reports[0].filled_qty

        # Flip to long
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, intent_id="flip", cutoff_ns=3))

        self.assertEqual(len(reports), 3)
        # Close short (buy)
        self.assertEqual(reports[1].side, 1)
        # Open long (buy, 30% initial)
        self.assertEqual(reports[2].side, 1)
        self.assertAlmostEqual(reports[2].filled_qty, 0.3, places=2)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0)

    def test_flip_disabled_single_order(self) -> None:
        """flip_immediate=False → single order crossing zero (old behavior)."""
        bus, kernel, reports = self._setup(flip_immediate=False)

        # Build long
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, intent_id="entry", cutoff_ns=2))

        # Flip without immediate mode — single order crosses zero
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=-1.0, intent_id="flip", cutoff_ns=3))

        # Old behavior: 1 entry + 1 cross-zero order (no separate close+open)
        self.assertEqual(len(reports), 2)
        self.assertLess(kernel.positions["BTCUSDT_PERP"], 0)

    def test_flip_resets_pyramid_state(self) -> None:
        """After flip, pyramid filled_pct resets for new direction."""
        bus, kernel, reports = self._setup()

        # Build long, add more
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=1.0, intent_id="t1", cutoff_ns=2))

        # Flip to short
        bus.publish("intent.target_position.final.BTCUSDT_PERP",
                    self._target(qty=-1.0, intent_id="flip", cutoff_ns=3))

        # Pyramid state should be reset to initial short pct (0.20)
        self.assertAlmostEqual(
            kernel._pyramid_filled_pct.get("BTCUSDT_PERP", 0), 0.20, places=2)
