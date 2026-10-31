import unittest
from dataclasses import asdict, replace

from zenquant.domain import (
    EventMeta,
    ExecutionReport,
    MarkPriceUpdate,
    OrderBookLevel,
    OrderBookSnapshot,
    RiskSnapshot,
    TargetPosition,
    Timeframe,
)
from zenquant.execution import ExecutionGateway, ExecutionKernel, ExecutionKernelConfig, SimulatedGateway
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay
from zenquant.strategy import ARCStrategyEngine
from zenquant.strategy.decision_snapshot import build_decision_snapshot


class ExecutionRouterTests(unittest.TestCase):
    def test_legacy_partial_fill_updates_router_and_risk_by_actual_quantity(self) -> None:
        class PartialFillGateway(ExecutionGateway):
            def submit_order(self, order, mark_price, best_bid=None, best_ask=None, now_ns=None):
                return ExecutionReport(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    status="PARTIALLY_FILLED",
                    filled_qty=0.4,
                    avg_price=mark_price,
                    fee=0.04,
                    transaction_time_ns=now_ns or 0,
                )

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(
            bus,
            PartialFillGateway(),
            risk,
            config=ExecutionKernelConfig(pyramid_enabled=False),
        )
        kernel._last_price["BTCUSDT_PERP"] = 100.0

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="legacy-partial-fill",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=1,
                target_qty=1.0,
                target_leverage=1.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["REGIME_TREND"],
            ),
        )

        self.assertEqual(kernel.positions, {"BTCUSDT_PERP": 0.4})
        self.assertEqual(dict(risk.positions), kernel.positions)
        self.assertAlmostEqual(risk.cash, 99_959.96)
        self.assertAlmostEqual(risk.equity, 99_999.96)

    def test_report_subscriber_failure_cannot_split_fill_and_fee_ledgers(self) -> None:
        class FixedFillGateway(ExecutionGateway):
            def submit_order(self, order, mark_price, best_bid=None, best_ask=None, now_ns=None):
                return ExecutionReport(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    status="FILLED",
                    filled_qty=0.4,
                    avg_price=mark_price,
                    fee=0.04,
                    transaction_time_ns=now_ns or 0,
                )

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(
            bus,
            FixedFillGateway(),
            risk,
            config=ExecutionKernelConfig(pyramid_enabled=False),
        )
        kernel._last_price["BTCUSDT_PERP"] = 100.0

        def fail_after_accounting(_subject, _payload):
            raise RuntimeError("subscriber failed")

        bus.subscribe("exec.order.report", fail_after_accounting)

        with self.assertRaisesRegex(RuntimeError, "subscriber failed"):
            bus.publish(
                "intent.target_position.final.BTCUSDT_PERP",
                TargetPosition(
                    intent_id="subscriber-failure",
                    portfolio="arc",
                    symbol="BTCUSDT_PERP",
                    timeframe=Timeframe.TF_4H,
                    cutoff_time_ns=1,
                    target_qty=1.0,
                    target_leverage=1.0,
                    reduce_only=False,
                    regime="TREND",
                    regime_confidence=0.9,
                    reason_codes=["REGIME_TREND"],
                ),
            )

        self.assertEqual(kernel.positions, {"BTCUSDT_PERP": 0.4})
        self.assertEqual(dict(risk.positions), kernel.positions)
        self.assertAlmostEqual(risk.cash, 99_959.96)
        self.assertAlmostEqual(risk.equity, 99_999.96)

    def test_legacy_flip_partial_close_updates_both_ledgers_without_opening_reverse(self) -> None:
        class PartialCloseGateway(ExecutionGateway):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            def submit_order(self, order, mark_price, best_bid=None, best_ask=None, now_ns=None):
                self.calls += 1
                return ExecutionReport(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    status="PARTIALLY_FILLED",
                    filled_qty=0.4,
                    avg_price=mark_price,
                    fee=0.04,
                    transaction_time_ns=now_ns or 0,
                )

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        gateway = PartialCloseGateway()
        kernel = ExecutionKernel(
            bus,
            gateway,
            risk,
            config=ExecutionKernelConfig(pyramid_enabled=False),
        )
        kernel._last_price["BTCUSDT_PERP"] = 100.0
        kernel._positions["BTCUSDT_PERP"] = 1.0
        bus.publish(
            "exec.order.report",
            ExecutionReport(
                client_order_id="seed-long",
                symbol="BTCUSDT_PERP",
                side=1,
                status="FILLED",
                filled_qty=1.0,
                avg_price=100.0,
                fee=0.0,
                transaction_time_ns=0,
            ),
        )

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="partial-flip",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=1,
                target_qty=-1.0,
                target_leverage=1.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["REGIME_TREND"],
            ),
        )

        self.assertEqual(gateway.calls, 1)
        self.assertEqual(kernel.positions, {"BTCUSDT_PERP": 0.6})
        self.assertEqual(dict(risk.positions), kernel.positions)
        self.assertAlmostEqual(risk.equity, 99_999.96)

    def test_non_finite_report_fee_is_rejected_before_publication_or_state_change(self) -> None:
        class NonFiniteFeeGateway(ExecutionGateway):
            def submit_order(self, order, mark_price, best_bid=None, best_ask=None, now_ns=None):
                return ExecutionReport(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    status="FILLED",
                    filled_qty=order.quantity,
                    avg_price=mark_price,
                    fee=float("nan"),
                    transaction_time_ns=now_ns or 0,
                )

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(bus, NonFiniteFeeGateway(), risk)
        reports = []
        bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))
        kernel._last_price["BTCUSDT_PERP"] = 100.0
        before = (dict(kernel.positions), risk._cash, risk.equity)

        with self.assertRaisesRegex(ValueError, "fee must be finite and non-negative"):
            bus.publish(
                "intent.target_position.final.BTCUSDT_PERP",
                TargetPosition(
                    intent_id="invalid-fee",
                    portfolio="arc",
                    symbol="BTCUSDT_PERP",
                    timeframe=Timeframe.TF_4H,
                    cutoff_time_ns=1,
                    target_qty=1.0,
                    target_leverage=1.0,
                    reduce_only=False,
                    regime="TREND",
                    regime_confidence=0.9,
                    reason_codes=["REGIME_TREND"],
                ),
            )

        self.assertEqual(reports, [])
        self.assertEqual((dict(kernel.positions), risk._cash, risk.equity), before)

    def test_report_identity_mismatch_is_rejected_before_publication_or_state_change(self) -> None:
        class MismatchedReportGateway(ExecutionGateway):
            def __init__(self, field, value) -> None:
                super().__init__()
                self.field = field
                self.value = value

            def submit_order(self, order, mark_price, best_bid=None, best_ask=None, now_ns=None):
                fields = {
                    "client_order_id": order.client_order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "status": "FILLED",
                    "filled_qty": order.quantity,
                    "avg_price": mark_price,
                    "fee": 0.04,
                    "transaction_time_ns": now_ns or 0,
                }
                fields[self.field] = self.value
                return ExecutionReport(**fields)

        mismatches = {
            "client_order_id": "wrong-order",
            "symbol": "ETHUSDT_PERP",
            "side": -1,
        }
        for field, value in mismatches.items():
            with self.subTest(field=field):
                bus = InMemoryJetStream()
                risk = RiskOverlay(bus)
                kernel = ExecutionKernel(
                    bus,
                    MismatchedReportGateway(field, value),
                    risk,
                    config=ExecutionKernelConfig(pyramid_enabled=False),
                )
                reports = []
                bus.subscribe(
                    "exec.order.report",
                    lambda _subject, payload, reports=reports: reports.append(payload),
                )
                kernel._last_price["BTCUSDT_PERP"] = 100.0

                with self.assertRaisesRegex(ValueError, "execution report does not match submitted order"):
                    bus.publish(
                        "intent.target_position.final.BTCUSDT_PERP",
                        TargetPosition(
                            intent_id=f"mismatch-{field}",
                            portfolio="arc",
                            symbol="BTCUSDT_PERP",
                            timeframe=Timeframe.TF_4H,
                            cutoff_time_ns=1,
                            target_qty=1.0,
                            target_leverage=1.0,
                            reduce_only=False,
                            regime="TREND",
                            regime_confidence=0.9,
                            reason_codes=["REGIME_TREND"],
                        ),
                    )

                self.assertEqual(reports, [])
                self.assertEqual(kernel.positions, {})
                self.assertEqual(dict(risk.positions), {})

    def test_invalid_report_fee_leaves_pyramid_fill_tracking_unchanged(self) -> None:
        class NonFiniteFeeGateway(ExecutionGateway):
            def submit_order(self, order, mark_price, best_bid=None, best_ask=None, now_ns=None):
                return ExecutionReport(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    status="FILLED",
                    filled_qty=order.quantity,
                    avg_price=mark_price,
                    fee=float("nan"),
                    transaction_time_ns=now_ns or 0,
                )

        def target(cutoff_time_ns: int) -> TargetPosition:
            return TargetPosition(
                intent_id=f"invalid-fee-{cutoff_time_ns}",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=cutoff_time_ns,
                target_qty=1.0,
                target_leverage=1.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["REGIME_TREND"],
                atr=10.0,
            )

        cases = (
            ("initial_entry", 100.0, {}),
            ("profitable_add", 110.0, {"BTCUSDT_PERP": 0.3}),
            ("loss_side_dca", 90.0, {"BTCUSDT_PERP": 0.3}),
        )
        for index, (name, mark_price, expected_pyramid_fill) in enumerate(cases, start=1):
            with self.subTest(name=name):
                bus = InMemoryJetStream()
                risk = RiskOverlay(bus)
                kernel = ExecutionKernel(
                    bus,
                    NonFiniteFeeGateway(),
                    risk,
                    config=ExecutionKernelConfig(adaptive_pyramid_enabled=False),
                )
                symbol = "BTCUSDT_PERP"
                kernel._last_price[symbol] = mark_price
                if expected_pyramid_fill:
                    kernel._positions[symbol] = 0.3
                    kernel._entry_prices[symbol] = 100.0
                    kernel._last_fill_prices[symbol] = 100.0
                    kernel._pyramid_filled_pct.update(expected_pyramid_fill)
                    kernel._inventory_tranches[symbol] = 1
                before = dict(kernel._pyramid_filled_pct)

                with self.assertRaisesRegex(ValueError, "fee must be finite and non-negative"):
                    bus.publish(f"intent.target_position.final.{symbol}", target(index))

                self.assertEqual(kernel._pyramid_filled_pct, before)

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


class TargetTrackingV2Tests(unittest.TestCase):
    """V2 execution is a pure, audited tracker of one approved 4h target."""

    @staticmethod
    def _target(
        *,
        qty: float,
        cutoff_ns: int,
        reduce_only: bool = False,
        reason_codes: list[str] | None = None,
    ) -> TargetPosition:
        return TargetPosition(
            intent_id=f"target-{cutoff_ns}",
            portfolio="arc",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=cutoff_ns,
            target_qty=qty,
            target_leverage=abs(qty),
            reduce_only=reduce_only,
            regime="TREND",
            regime_confidence=0.9,
            reason_codes=reason_codes or ["APPROVED_4H_TARGET"],
            realized_vol=0.4,
            atr=500.0,
        )

    @staticmethod
    def _snapshot(target: TargetPosition):
        return build_decision_snapshot(
            target=target,
            formula_hash="formula-v2",
            config_hash="config-v2",
            indicator_version="pack.v1",
            data_identity=f"data-{target.cutoff_time_ns}",
            alpha_direction=1.0 if target.target_qty > 0 else -1.0 if target.target_qty < 0 else 0.0,
            alpha_raw_score=target.target_qty,
            alpha_confidence=0.8,
            directional_state="UPTREND",
            volatility_state="NORMAL",
            positioning_state="HEALTHY",
            quality_state="TRADABLE",
            regime_risk_multiplier=1.0,
            annualized_volatility=0.4,
            volatility_target=0.2,
            exposure_cap=1.5,
            quality_flags=["OK"],
            options_state_id=None,
        )

    def _setup(self, **config_overrides):
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(execution_authority_mode="target_tracking_v2", **config_overrides)
        kernel = ExecutionKernel(bus, SimulatedGateway(), risk, config=config)
        orders = []
        reports = []
        audits = []
        bus.subscribe("exec.order.submit", lambda _subject, payload: orders.append(payload))
        bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))
        bus.subscribe("exec.order.audit", lambda _subject, payload: audits.append(payload))
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(
                    event_time_ns=1,
                    ingest_time_ns=1,
                    venue="BINANCE",
                    symbol="BTCUSDT_PERP",
                    stream_seq=1,
                ),
                mark_price=40_000.0,
            ),
        )
        return bus, kernel, orders, reports, audits

    @staticmethod
    def _publish_approved(bus: InMemoryJetStream, target: TargetPosition) -> None:
        snapshot = TargetTrackingV2Tests._snapshot(target)
        bus.publish(f"strategy.decision_snapshot.4h.{target.symbol}", snapshot)
        bus.publish(f"intent.target_position.final.{target.symbol}", target)

    def test_legacy_remains_default_and_invalid_authority_mode_is_rejected(self) -> None:
        self.assertEqual(ExecutionKernelConfig().execution_authority_mode, "legacy")
        positional_legacy = ExecutionKernelConfig(0.001, 2.0, 5.0)
        self.assertEqual(positional_legacy.min_trade_qty, 0.001)
        self.assertEqual(positional_legacy.max_trade_qty_per_bar, 2.0)
        self.assertEqual(positional_legacy.max_slippage_bps, 5.0)
        with self.assertRaisesRegex(ValueError, "execution_authority_mode"):
            ExecutionKernelConfig(execution_authority_mode="autonomous")

    def test_v2_tracks_direct_add_reduce_and_requested_reversal_without_overshoot(self) -> None:
        bus, kernel, orders, reports, audits = self._setup(max_trade_qty_per_bar=1.0)

        expected_positions = [0.75, 1.25, 0.5, -0.5]
        targets = [
            self._target(qty=0.75, cutoff_ns=2),
            self._target(qty=1.25, cutoff_ns=3),
            self._target(qty=0.5, cutoff_ns=4),
            self._target(qty=-0.5, cutoff_ns=5),
        ]
        for target, expected in zip(targets, expected_positions, strict=True):
            self._publish_approved(bus, target)
            self.assertAlmostEqual(kernel.positions[target.symbol], expected)

        self.assertEqual([order.side for order in orders], [1, 1, -1, -1])
        self.assertEqual([order.quantity for order in orders], [0.75, 0.5, 0.75, 1.0])
        self.assertEqual(len(reports), 4)
        self.assertEqual(len(audits), 4)
        self.assertEqual(
            kernel.purity_counters,
            {
                "executor_created_exposure": 0,
                "unexpected_reversal": 0,
                "target_overshoot": 0,
            },
        )

    def test_v2_expected_small_quantity_reversal_is_not_a_purity_violation(self) -> None:
        bus, kernel, _orders, _reports, _audits = self._setup()
        self._publish_approved(bus, self._target(qty=-0.0002, cutoff_ns=2))
        self._publish_approved(bus, self._target(qty=0.0002, cutoff_ns=3))

        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0002)
        self.assertEqual(kernel.purity_counters["unexpected_reversal"], 0)

    def test_v2_obsolete_snapshot_and_final_target_cannot_reopen_exposure(self) -> None:
        bus, kernel, orders, _reports, _audits = self._setup()
        old_target = self._target(qty=1.0, cutoff_ns=2)
        new_target = self._target(qty=0.0, cutoff_ns=3)
        self._publish_approved(bus, old_target)
        self._publish_approved(bus, new_target)
        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0)
        order_count = len(orders)

        bus.publish(f"strategy.decision_snapshot.4h.{old_target.symbol}", self._snapshot(old_target))
        bus.publish(f"intent.target_position.final.{old_target.symbol}", old_target)

        self.assertEqual(len(orders), order_count)
        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0)
        self.assertEqual(kernel.latest_decision_cutoffs[("arc", "BTCUSDT_PERP")], 3)

    def test_v2_snapshot_rejects_mutated_final_target_metadata(self) -> None:
        mutations = {
            "regime": "RANGE",
            "regime_confidence": 0.1,
            "reason_codes": ["SPOOFED_FINAL"],
            "realized_vol": 0.9,
            "atr": 999.0,
        }
        for field_name, new_value in mutations.items():
            with self.subTest(field_name=field_name):
                bus, kernel, orders, _reports, _audits = self._setup()
                target = self._target(qty=1.0, cutoff_ns=2)
                bus.publish(f"strategy.decision_snapshot.4h.{target.symbol}", self._snapshot(target))

                bus.publish(
                    f"intent.target_position.final.{target.symbol}",
                    replace(target, **{field_name: new_value}),
                )

                self.assertEqual(orders, [])
                self.assertEqual(kernel.positions, {})

    def test_v2_completed_decision_is_consumed_and_cannot_reopen_exposure(self) -> None:
        bus, kernel, orders, _reports, _audits = self._setup()
        target = self._target(qty=1.0, cutoff_ns=2)
        self._publish_approved(bus, target)
        self.assertEqual(kernel.positions[target.symbol], 1.0)
        order_count = len(orders)
        kernel._positions[target.symbol] = 0.0  # external reconciliation after completion

        bus.publish(f"intent.target_position.final.{target.symbol}", target)

        self.assertEqual(len(orders), order_count)
        self.assertEqual(kernel.positions[target.symbol], 0.0)

    def test_v2_partial_fill_updates_all_positions_and_retry_uses_remaining_delta(self) -> None:
        class PartialThenFillGateway(ExecutionGateway):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            def submit_order(self, order, mark_price, best_bid=None, best_ask=None, now_ns=None):
                self.calls += 1
                filled_qty = 0.4 if self.calls == 1 else order.quantity
                return ExecutionReport(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    status="PARTIALLY_FILLED" if self.calls == 1 else "FILLED",
                    filled_qty=filled_qty,
                    avg_price=mark_price,
                    fee=filled_qty * mark_price * 0.0004,
                    transaction_time_ns=now_ns or 0,
                )

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        strategy = ARCStrategyEngine(bus)
        gateway = PartialThenFillGateway()
        kernel = ExecutionKernel(
            bus,
            gateway,
            risk,
            config=ExecutionKernelConfig(execution_authority_mode="target_tracking_v2"),
        )
        orders = []
        audits = []
        bus.subscribe("exec.order.submit", lambda _subject, payload: orders.append(payload))
        bus.subscribe("exec.order.audit", lambda _subject, payload: audits.append(payload))
        kernel._last_price["BTCUSDT_PERP"] = 100.0
        target = self._target(qty=1.0, cutoff_ns=2)
        snapshot = self._snapshot(target)
        bus.publish(f"strategy.decision_snapshot.4h.{target.symbol}", snapshot)

        bus.publish(f"intent.target_position.final.{target.symbol}", target)
        self.assertEqual(kernel.positions[target.symbol], 0.4)
        self.assertEqual(risk._actual_positions[target.symbol], 0.4)
        tracked = strategy._positions[(target.symbol, Timeframe.TF_4H)]
        self.assertEqual(tracked.current_qty, 0.4)
        self.assertEqual(audits[0].position_after_fill, 0.4)
        self.assertEqual(audits[0].order_delta_qty, 0.4)
        self.assertEqual(audits[0].remaining_target_delta_qty, 0.6)

        bus.publish(f"intent.target_position.final.{target.symbol}", target)
        self.assertEqual(orders[1].quantity, 0.6)
        self.assertEqual(kernel.positions[target.symbol], 1.0)
        self.assertEqual(risk._actual_positions[target.symbol], 1.0)
        self.assertEqual(strategy._positions[(target.symbol, Timeframe.TF_4H)].current_qty, 1.0)
        self.assertEqual(audits[1].remaining_target_delta_qty, 0.0)
        self.assertEqual(kernel.purity_counters["target_overshoot"], 0)

        bus.publish(f"intent.target_position.final.{target.symbol}", target)
        self.assertEqual(len(orders), 2)

    def test_v2_partial_reversal_overfill_and_zero_rejection_are_accounted(self) -> None:
        class FractionGateway(ExecutionGateway):
            def __init__(self, fills: list[tuple[str, float]]) -> None:
                super().__init__()
                self.fills = iter(fills)

            def submit_order(self, order, mark_price, best_bid=None, best_ask=None, now_ns=None):
                status, filled_qty = next(self.fills)
                return ExecutionReport(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    status=status,
                    filled_qty=filled_qty,
                    avg_price=mark_price if filled_qty > 0 else 0.0,
                    fee=filled_qty,
                    transaction_time_ns=now_ns or 0,
                    reject_reason="rejected" if filled_qty == 0 else "",
                )

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        gateway = FractionGateway(
            [
                ("FILLED", 0.6),
                ("PARTIALLY_FILLED", 0.7),
                ("FILLED", 0.3),
                ("PARTIALLY_FILLED", 0.6),
                ("REJECTED", 0.0),
            ]
        )
        kernel = ExecutionKernel(
            bus,
            gateway,
            risk,
            config=ExecutionKernelConfig(execution_authority_mode="target_tracking_v2"),
        )
        audits = []
        bus.subscribe("exec.order.audit", lambda _subject, payload: audits.append(payload))
        kernel._last_price["BTCUSDT_PERP"] = 100.0

        long_target = self._target(qty=0.6, cutoff_ns=2)
        self._publish_approved(bus, long_target)
        reverse_target = self._target(qty=-0.4, cutoff_ns=3)
        bus.publish(f"strategy.decision_snapshot.4h.{reverse_target.symbol}", self._snapshot(reverse_target))
        bus.publish(f"intent.target_position.final.{reverse_target.symbol}", reverse_target)
        self.assertAlmostEqual(kernel.positions[reverse_target.symbol], -0.1)
        self.assertEqual(kernel.purity_counters["unexpected_reversal"], 0)
        bus.publish(f"intent.target_position.final.{reverse_target.symbol}", reverse_target)
        self.assertAlmostEqual(kernel.positions[reverse_target.symbol], -0.4)

        overfill_target = self._target(qty=0.0, cutoff_ns=4)
        bus.publish(f"strategy.decision_snapshot.4h.{overfill_target.symbol}", self._snapshot(overfill_target))
        bus.publish(f"intent.target_position.final.{overfill_target.symbol}", overfill_target)
        self.assertAlmostEqual(kernel.positions[overfill_target.symbol], 0.2)
        self.assertEqual(kernel.purity_counters["target_overshoot"], 1)

        flat_target = self._target(qty=0.0, cutoff_ns=5)
        bus.publish(f"strategy.decision_snapshot.4h.{flat_target.symbol}", self._snapshot(flat_target))
        bus.publish(f"intent.target_position.final.{flat_target.symbol}", flat_target)
        self.assertEqual(audits[-1].status, "REJECTED")
        self.assertEqual(audits[-1].order_delta_qty, 0.0)
        self.assertAlmostEqual(kernel.positions[flat_target.symbol], 0.2)

    def test_v2_slices_and_floors_to_quantity_precision_without_overshoot(self) -> None:
        bus, kernel, orders, _reports, _audits = self._setup(
            max_trade_qty_per_bar=0.3333339,
            quantity_precision=6,
        )
        target = self._target(qty=1.0, cutoff_ns=2)

        self._publish_approved(bus, target)

        self.assertEqual(orders[0].quantity, 0.333333)
        self.assertLessEqual(kernel.positions[target.symbol], target.target_qty + kernel.quantity_epsilon)

    def test_legacy_market_judgment_options_cannot_change_v2_order_bytes(self) -> None:
        baseline_bus, _baseline_kernel, baseline_orders, _reports, _audits = self._setup()
        noisy_bus, _noisy_kernel, noisy_orders, _reports, _audits = self._setup(
            pyramid_enabled=True,
            dca_on_loss_long=True,
            profit_protect_reduce_enabled=True,
            target_delta_deadband_qty=10.0,
            target_delta_deadband_pct=10.0,
            entry_cooldown_bars=10,
            weak_signal_no_flip_enabled=True,
            flip_immediate=False,
            squeeze_flip_flatten_only_enabled=True,
            squeeze_flip_cooldown_enabled=True,
            range_entry_block_enabled=True,
            degraded_entry_block_enabled=True,
        )
        target = self._target(qty=0.75, cutoff_ns=2)

        self._publish_approved(baseline_bus, target)
        self._publish_approved(noisy_bus, target)

        self.assertEqual(asdict(baseline_orders[0]), asdict(noisy_orders[0]))

    def test_v2_hard_safety_can_reduce_but_cannot_increase_or_reverse_risk(self) -> None:
        bus, kernel, orders, reports, audits = self._setup()
        self._publish_approved(bus, self._target(qty=1.0, cutoff_ns=2))

        reduce_target = self._target(
            qty=0.5,
            cutoff_ns=3,
            reduce_only=True,
            reason_codes=["KILL_SWITCH"],
        )
        self._publish_approved(bus, reduce_target)
        self.assertAlmostEqual(kernel.positions[reduce_target.symbol], 0.5)
        self.assertTrue(orders[-1].reduce_only)
        self.assertEqual(audits[-1].hard_safety_reason, "KILL_SWITCH")

        report_count = len(reports)
        self._publish_approved(
            bus,
            self._target(qty=1.5, cutoff_ns=4, reduce_only=True, reason_codes=["KILL_SWITCH"]),
        )
        self._publish_approved(
            bus,
            self._target(qty=-1.0, cutoff_ns=5, reduce_only=True, reason_codes=["KILL_SWITCH"]),
        )
        self.assertEqual(len(reports), report_count)
        self.assertAlmostEqual(kernel.positions[reduce_target.symbol], 0.5)

    def test_v2_audit_is_complete_and_bound_to_approved_snapshot(self) -> None:
        bus, _kernel, _orders, _reports, audits = self._setup()
        target = self._target(qty=0.75, cutoff_ns=2)
        snapshot = self._snapshot(target)

        self._publish_approved(bus, target)

        audit = audits[0]
        self.assertEqual(audit.decision_id, snapshot.decision_id)
        self.assertEqual(audit.approved_target_qty, 0.75)
        self.assertEqual(audit.position_before_order, 0.0)
        self.assertEqual(audit.position_after_fill, 0.75)
        self.assertIsNone(audit.hard_safety_reason)
        self.assertEqual(audit.client_order_id, _orders[0].client_order_id)


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
                meta=EventMeta(event_time_ns=1, ingest_time_ns=1, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=1),
                mark_price=40000.0,
            ),
        )
        return bus, kernel, reports

    def test_long_initial_30pct(self) -> None:
        """Long pyramid entry = 30% of target."""
        bus, kernel, reports = self._setup()
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="t1",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                target_qty=1.0,
                target_leverage=2.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["TEST"],
            ),
        )
        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(reports[0].filled_qty, 0.3, places=2)

    def test_short_initial_20pct(self) -> None:
        """Short pyramid entry = 20% (more conservative)."""
        bus, kernel, reports = self._setup()
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="t1",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                target_qty=-1.0,
                target_leverage=2.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["TEST"],
            ),
        )
        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(reports[0].filled_qty, 0.2, places=2)

    def test_pyramid_disabled_fills_full(self) -> None:
        """Without pyramid, fill up to max_trade_qty_per_bar."""
        bus, kernel, reports = self._setup(pyramid_enabled=False)
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="t1",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                target_qty=1.0,
                target_leverage=2.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["TEST"],
            ),
        )
        self.assertEqual(len(reports), 1)
        self.assertGreater(reports[0].filled_qty, 0.3)

    def test_position_tracked(self) -> None:
        """Kernel tracks positions after fills."""
        bus, kernel, reports = self._setup()
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="t1",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                target_qty=1.0,
                target_leverage=2.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["TEST"],
            ),
        )
        self.assertIn("BTCUSDT_PERP", kernel.positions)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0)


class DCAOnLossTests(unittest.TestCase):
    """Verify PaulWei DCA: longs add on loss at 0.5x, shorts never add on loss."""

    def _target(
        self,
        qty=1.0,
        intent_id="t1",
        cutoff_ns=2,
        *,
        confidence=0.9,
        reason_codes=None,
        regime="TREND",
    ):
        return TargetPosition(
            intent_id=intent_id,
            portfolio="arc",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=cutoff_ns,
            target_qty=qty,
            target_leverage=2.0,
            reduce_only=False,
            regime=regime,
            regime_confidence=confidence,
            reason_codes=reason_codes or ["TEST"],
        )

    def _setup(self, dca_on_loss_long=True, mark_price=40000.0, **config_overrides):
        from zenquant.execution.router import ExecutionKernelConfig

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(dca_on_loss_long=dca_on_loss_long, **config_overrides)
        kernel = ExecutionKernel(bus, SimulatedGateway(), risk, config=config)
        reports = []
        bus.subscribe("exec.order.report", lambda _, p: reports.append(p))

        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=1, ingest_time_ns=1, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=1),
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
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=38000.0,
            ),
        )

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
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=42000.0,
            ),
        )

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
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=38000.0,
            ),
        )

        # No DCA add
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t2", cutoff_ns=4))
        self.assertEqual(len(reports), 1)  # No new fill

    def test_loss_dca_confidence_floor_blocks_weak_loss_add(self) -> None:
        bus, kernel, reports = self._setup(mark_price=40000.0, loss_dca_confidence_floor=0.85)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=38000.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, intent_id="weak-dca", cutoff_ns=4, confidence=0.80),
        )

        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], reports[0].filled_qty)

    def test_loss_dca_range_block_skips_range_loss_add(self) -> None:
        bus, kernel, reports = self._setup(mark_price=40000.0, loss_dca_range_block_enabled=True)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=38000.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="range-dca",
                cutoff_ns=4,
                reason_codes=["PLAYBOOK_RANGE_LIGHT", "TEST"],
                regime="RANGE",
            ),
        )

        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], reports[0].filled_qty)

    def test_loss_dca_degraded_block_skips_degraded_loss_add(self) -> None:
        bus, kernel, reports = self._setup(mark_price=40000.0, loss_dca_degraded_block_enabled=True)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=38000.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="degraded-dca",
                cutoff_ns=4,
                reason_codes=["QUALITY_DEGRADED", "TEST"],
            ),
        )

        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], reports[0].filled_qty)

    def test_profitable_long_adds_full_size(self) -> None:
        """Profitable long → normal add (30%), not DCA reduced (15%)."""
        bus, kernel, reports = self._setup(mark_price=40000.0)

        # Initial long
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        # Price rises → profitable
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=42000.0,
            ),
        )

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
            bus.publish(
                "md.mark.BINANCE.BTCUSDT_PERP",
                MarkPriceUpdate(
                    meta=EventMeta(
                        event_time_ns=10 + i,
                        ingest_time_ns=10 + i,
                        venue="BINANCE",
                        symbol="BTCUSDT_PERP",
                        stream_seq=10 + i,
                    ),
                    mark_price=39000.0 - i * 100,  # Price keeps dropping
                ),
            )
            bus.publish(
                "intent.target_position.final.BTCUSDT_PERP",
                self._target(qty=1.0, intent_id=f"dca{i}", cutoff_ns=20 + i),
            )

        # Should have: initial (30%) + some DCA adds (15% each) until 100%
        # Max fills: 30% + ceil((70%/15%)) = 30% + 5 adds = ~105% capped to 100%
        total_filled = sum(r.filled_qty for r in reports)
        self.assertLessEqual(total_filled, 1.0, "Total filled should not exceed 100%")

    def test_dca_requires_spacing_between_tranches(self) -> None:
        bus, kernel, reports = self._setup(mark_price=40000.0)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))
        self.assertEqual(len(reports), 1)

        # Only 0.4 ATR adverse move: should not trigger the next DCA tranche.
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=39800.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="t2",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=4,
                target_qty=1.0,
                target_leverage=2.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["TEST"],
                atr=500.0,
            ),
        )

        self.assertEqual(len(reports), 1)


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
                meta=EventMeta(event_time_ns=1, ingest_time_ns=1, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=1),
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
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, realized_vol=0.30))
        self.assertEqual(len(reports), 1)
        # vol_ratio = 0.60/0.30 = 2.0, init_pct = 0.30*2.0 = 0.60 → capped at 0.40
        self.assertAlmostEqual(reports[0].filled_qty, 0.4, places=2)

    def test_high_vol_builds_slower(self) -> None:
        """High vol (1.20) → init should be ~15%."""
        bus, kernel, reports = self._setup()
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, realized_vol=1.20))
        self.assertEqual(len(reports), 1)
        # vol_ratio = 0.60/1.20 = 0.5, init_pct = 0.30*0.5 = 0.15
        self.assertAlmostEqual(reports[0].filled_qty, 0.15, places=2)

    def test_adaptive_disabled_uses_fixed(self) -> None:
        """adaptive_pyramid_enabled=False → fixed 30% regardless of vol."""
        bus, kernel, reports = self._setup(adaptive_enabled=False)
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, realized_vol=1.20))
        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(reports[0].filled_qty, 0.3, places=2)

    def test_profitable_add_requires_spacing(self) -> None:
        bus, kernel, reports = self._setup()
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, realized_vol=0.60, atr=500.0))
        self.assertEqual(len(reports), 1)

        # Profitable but only 0.4 ATR away from the last fill: no new tranche.
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=40200.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, realized_vol=0.60, atr=500.0, intent_id="t2", cutoff_ns=4),
        )
        self.assertEqual(len(reports), 1)


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
                meta=EventMeta(event_time_ns=1, ingest_time_ns=1, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=1),
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
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="entry", cutoff_ns=2))
        self.assertEqual(len(reports), 1)
        entry_qty = reports[0].filled_qty

        # Gradual exit (TIME_DECAY)
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=0.0,
                reduce_only=True,
                reason_codes=["EXIT_TIME_DECAY", "UNPROFITABLE"],
                intent_id="exit1",
                cutoff_ns=3,
            ),
        )
        self.assertEqual(len(reports), 2)
        exit_qty = reports[1].filled_qty
        # Should reduce ~30% of position, not 100%
        self.assertLess(exit_qty, entry_qty * 0.5)
        self.assertGreater(exit_qty, 0)

    def test_immediate_exit_trailing_stop(self) -> None:
        """TRAILING_STOP → 100% immediate exit."""
        bus, kernel, reports = self._setup()

        # Build position
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="entry", cutoff_ns=2))
        entry_qty = reports[0].filled_qty

        # Immediate exit
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=0.0, reduce_only=True, reason_codes=["EXIT_TRAILING_STOP"], intent_id="exit1", cutoff_ns=3
            ),
        )
        self.assertEqual(len(reports), 2)
        # Should close entire position
        self.assertAlmostEqual(reports[1].filled_qty, entry_qty, places=4)

    def test_gradual_exit_full_unwind(self) -> None:
        """Multiple gradual exit bars → eventually fully closed."""
        bus, kernel, reports = self._setup()

        # Build position
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="entry", cutoff_ns=2))

        # Send gradual exit repeatedly
        for i in range(10):
            bus.publish(
                "intent.target_position.final.BTCUSDT_PERP",
                self._target(
                    qty=0.0, reduce_only=True, reason_codes=["EXIT_TIME_DECAY"], intent_id=f"exit{i}", cutoff_ns=10 + i
                ),
            )

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
            adaptive_pyramid_enabled=True,
            dca_max_adverse_atr=3.0,
            pyramid_target_vol=0.60,
        )
        kernel = ExecutionKernel(bus, SimulatedGateway(), risk, config=config)
        reports = []
        bus.subscribe("exec.order.report", lambda _, p: reports.append(p))

        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=1, ingest_time_ns=1, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=1),
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
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, atr=500.0, intent_id="t1", cutoff_ns=2)
        )
        self.assertEqual(len(reports), 1)

        # Price drops 2000 (4 ATR with atr=500)
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=38000.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, atr=500.0, intent_id="t2", cutoff_ns=4)
        )
        # Should NOT add — 4 ATR away exceeds 3 ATR limit
        self.assertEqual(len(reports), 1)

    def test_dca_scales_with_distance(self) -> None:
        """Price 1 ATR below entry → near-full DCA; 2 ATR → reduced."""
        bus, kernel, reports = self._setup()

        # Initial entry
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, atr=1000.0, intent_id="t1", cutoff_ns=2)
        )
        self.assertEqual(len(reports), 1)

        # Price drops 1000 (1 ATR)
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=39000.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, atr=1000.0, intent_id="t2", cutoff_ns=4)
        )
        self.assertEqual(len(reports), 2)
        close_dca = reports[1].filled_qty

        # Price drops 2000 more (now 3 ATR total from a new setup)
        bus2, kernel2, reports2 = self._setup()
        bus2.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, atr=1000.0, intent_id="t1", cutoff_ns=2)
        )
        bus2.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=37500.0,  # 2.5 ATR away
            ),
        )
        bus2.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, atr=1000.0, intent_id="t2", cutoff_ns=4)
        )
        if len(reports2) > 1:
            far_dca = reports2[1].filled_qty
            self.assertLess(far_dca, close_dca, "DCA should be smaller when farther from entry")


class FlipExecutionTests(unittest.TestCase):
    """Verify direction flip: flatten + open opposite in one bar."""

    def _setup(
        self,
        flip_immediate: bool = True,
        squeeze_flip_flatten_only_enabled: bool = False,
        squeeze_flip_cooldown_enabled: bool = False,
        squeeze_conditional_cooldown_enabled: bool = False,
        squeeze_conditional_cooldown_toxic_only: bool = False,
        squeeze_cooldown_cancel_on_non_squeeze_enabled: bool = False,
    ):
        from zenquant.execution.router import ExecutionKernelConfig

        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(
            flip_immediate=flip_immediate,
            adaptive_pyramid_enabled=False,
            gradual_exit_enabled=True,
            squeeze_flip_flatten_only_enabled=squeeze_flip_flatten_only_enabled,
            squeeze_flip_cooldown_enabled=squeeze_flip_cooldown_enabled,
            squeeze_flip_cooldown_bars=2,
            squeeze_conditional_cooldown_enabled=squeeze_conditional_cooldown_enabled,
            squeeze_conditional_cooldown_drawdown_threshold=0.025,
            squeeze_conditional_cooldown_toxic_only=squeeze_conditional_cooldown_toxic_only,
            squeeze_cooldown_cancel_on_non_squeeze_enabled=squeeze_cooldown_cancel_on_non_squeeze_enabled,
        )
        kernel = ExecutionKernel(bus, SimulatedGateway(), risk, config=config)
        reports = []
        bus.subscribe("exec.order.report", lambda _, p: reports.append(p))

        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=1, ingest_time_ns=1, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=1),
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
            regime=kwargs.get("regime", "TREND"),
            regime_confidence=kwargs.get("regime_confidence", 0.9),
            reason_codes=reason_codes or ["TEST"],
            realized_vol=kwargs.get("realized_vol", 0.0),
        )

    def test_hostile_router_restrains_panic_short_without_changing_default(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(
            bus,
            ExecutionGateway(),
            risk,
            config=ExecutionKernelConfig(hostile_behavior_router_enabled=True),
        )
        reports = []
        bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))
        kernel._last_price["BTCUSDT_PERP"] = 40000.0

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=-1.0,
                intent_id="panic-short",
                cutoff_ns=10,
                regime="STRESS",
                regime_confidence=0.55,
                realized_vol=1.1,
            ),
        )

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].side, -1)
        self.assertAlmostEqual(reports[0].filled_qty, 0.10, places=4)

    def test_hostile_router_keeps_bearish_grind_short_speed(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(
            bus,
            ExecutionGateway(),
            risk,
            config=ExecutionKernelConfig(hostile_behavior_router_enabled=True),
        )
        reports = []
        bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))
        kernel._last_price["BTCUSDT_PERP"] = 40000.0

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=-1.0,
                intent_id="grind-short",
                cutoff_ns=11,
                regime="TREND",
                regime_confidence=0.8,
                realized_vol=0.6,
            ),
        )

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].side, -1)
        self.assertAlmostEqual(reports[0].filled_qty, 0.2, places=4)

    def test_squeeze_flip_flatten_only_closes_short_without_opening_long(self) -> None:
        bus, kernel, reports = self._setup(squeeze_flip_flatten_only_enabled=True)
        orders = []
        bus.subscribe("exec.order.submit", lambda _, payload: orders.append(payload))

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="squeeze-flip",
                cutoff_ns=3,
                reason_codes=["BIAS_QUALITY_DEGRADED", "PLAYBOOK_RANGE_LIGHT", "TREND_LONG"],
            ),
        )

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[1].side, 1)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertTrue(orders[-1].reduce_only)
        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_squeeze_flip_flatten_only_leaves_non_degraded_flip_unchanged(self) -> None:
        bus, kernel, reports = self._setup(squeeze_flip_flatten_only_enabled=True)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, intent_id="clean-flip", cutoff_ns=3, reason_codes=["TREND_LONG"]),
        )

        self.assertEqual(len(reports), 3)
        self.assertEqual(reports[1].side, 1)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertEqual(reports[2].side, 1)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_squeeze_flip_flatten_only_does_not_change_flat_exit(self) -> None:
        bus, kernel, reports = self._setup(squeeze_flip_flatten_only_enabled=True)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=0.0,
                intent_id="flat-exit",
                cutoff_ns=3,
                reduce_only=True,
                reason_codes=["EXIT_TRAILING_STOP", "BIAS_QUALITY_DEGRADED"],
            ),
        )

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[1].side, 1)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_squeeze_flip_cooldown_blocks_two_long_reentries_after_flatten(self) -> None:
        bus, kernel, reports = self._setup(squeeze_flip_cooldown_enabled=True)
        orders = []
        bus.subscribe("exec.order.submit", lambda _, payload: orders.append(payload))

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="squeeze-flip",
                cutoff_ns=3,
                reason_codes=["BIAS_QUALITY_DEGRADED", "PLAYBOOK_RANGE_LIGHT", "TREND_LONG"],
            ),
        )

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[1].side, 1)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertTrue(orders[-1].reduce_only)
        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, intent_id="cooldown-1", cutoff_ns=4, reason_codes=["TREND_LONG"]),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, intent_id="cooldown-2", cutoff_ns=5, reason_codes=["TREND_LONG"]),
        )

        self.assertEqual(len(reports), 2)
        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, intent_id="post-cooldown", cutoff_ns=6, reason_codes=["TREND_LONG"]),
        )

        self.assertEqual(len(reports), 3)
        self.assertEqual(reports[2].side, 1)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_squeeze_flip_cooldown_leaves_non_degraded_flip_unchanged(self) -> None:
        bus, kernel, reports = self._setup(squeeze_flip_cooldown_enabled=True)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, intent_id="clean-flip", cutoff_ns=3, reason_codes=["TREND_LONG"]),
        )

        self.assertEqual(len(reports), 3)
        self.assertEqual(reports[1].side, 1)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertEqual(reports[2].side, 1)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_conditional_squeeze_cooldown_requires_drawdown_or_toxic_reason(self) -> None:
        bus, kernel, reports = self._setup(squeeze_conditional_cooldown_enabled=True)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty
        bus.publish(
            "risk.snapshot",
            RiskSnapshot(
                portfolio="arc",
                ts_ns=3,
                equity=100000.0,
                high_water_mark=100000.0,
                drawdown=0.01,
                lev_cap=1.0,
                reduce_only_mode=False,
                flags=["OK"],
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="clean-low-dd",
                cutoff_ns=3,
                reason_codes=["BIAS_QUALITY_DEGRADED", "PLAYBOOK_RANGE_LIGHT", "TREND_LONG"],
            ),
        )

        self.assertEqual(len(reports), 3)
        self.assertEqual(reports[1].side, 1)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_conditional_squeeze_cooldown_blocks_reentry_when_drawdown_is_elevated(self) -> None:
        bus, kernel, reports = self._setup(squeeze_conditional_cooldown_enabled=True)
        orders = []
        bus.subscribe("exec.order.submit", lambda _, payload: orders.append(payload))

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty
        bus.publish(
            "risk.snapshot",
            RiskSnapshot(
                portfolio="arc",
                ts_ns=3,
                equity=97000.0,
                high_water_mark=100000.0,
                drawdown=0.03,
                lev_cap=1.0,
                reduce_only_mode=False,
                flags=["OK"],
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="squeeze-high-dd",
                cutoff_ns=3,
                reason_codes=["BIAS_QUALITY_DEGRADED", "PLAYBOOK_RANGE_LIGHT", "TREND_LONG"],
            ),
        )

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[1].side, 1)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertTrue(orders[-1].reduce_only)
        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, intent_id="cooldown-1", cutoff_ns=4, reason_codes=["TREND_LONG"]),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, intent_id="cooldown-2", cutoff_ns=5, reason_codes=["TREND_LONG"]),
        )
        self.assertEqual(len(reports), 2)

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(qty=1.0, intent_id="post-cooldown", cutoff_ns=6, reason_codes=["TREND_LONG"]),
        )
        self.assertEqual(len(reports), 3)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_squeeze_cooldown_can_cancel_when_state_leaves_squeeze(self) -> None:
        bus, kernel, reports = self._setup(
            squeeze_conditional_cooldown_enabled=True,
            squeeze_cooldown_cancel_on_non_squeeze_enabled=True,
        )

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty
        bus.publish(
            "risk.snapshot",
            RiskSnapshot(
                portfolio="arc",
                ts_ns=3,
                equity=93000.0,
                high_water_mark=100000.0,
                drawdown=0.07,
                lev_cap=1.0,
                reduce_only_mode=False,
                flags=["OK"],
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="squeeze",
                cutoff_ns=3,
                reason_codes=["BIAS_QUALITY_DEGRADED", "PLAYBOOK_RANGE_LIGHT", "TREND_LONG"],
            ),
        )

        self.assertEqual(len(reports), 2)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="chop-long",
                cutoff_ns=4,
                regime="RANGE",
                regime_confidence=0.45,
                reason_codes=["TREND_LONG"],
            ),
        )

        self.assertEqual(len(reports), 3)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_toxic_only_squeeze_cooldown_ignores_plain_drawdown(self) -> None:
        bus, kernel, reports = self._setup(
            squeeze_conditional_cooldown_enabled=True,
            squeeze_conditional_cooldown_toxic_only=True,
        )

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty
        bus.publish(
            "risk.snapshot",
            RiskSnapshot(
                portfolio="arc",
                ts_ns=3,
                equity=95000.0,
                high_water_mark=100000.0,
                drawdown=0.05,
                lev_cap=1.0,
                reduce_only_mode=False,
                flags=["OK"],
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="plain-dd",
                cutoff_ns=3,
                reason_codes=["BIAS_QUALITY_DEGRADED", "PLAYBOOK_RANGE_LIGHT", "TREND_LONG"],
            ),
        )

        self.assertEqual(len(reports), 3)
        self.assertEqual(reports[1].side, 1)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_toxic_only_squeeze_cooldown_triggers_on_explicit_toxic_reason(self) -> None:
        bus, kernel, reports = self._setup(
            squeeze_conditional_cooldown_enabled=True,
            squeeze_conditional_cooldown_toxic_only=True,
        )

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        short_qty = reports[0].filled_qty
        bus.publish(
            "risk.snapshot",
            RiskSnapshot(
                portfolio="arc",
                ts_ns=3,
                equity=99000.0,
                high_water_mark=100000.0,
                drawdown=0.01,
                lev_cap=1.0,
                reduce_only_mode=False,
                flags=["OK"],
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(
                qty=1.0,
                intent_id="toxic",
                cutoff_ns=3,
                reason_codes=["BIAS_QUALITY_DEGRADED", "PLAYBOOK_RANGE_LIGHT", "VPIN_TOXIC", "TREND_LONG"],
            ),
        )

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[1].side, 1)
        self.assertAlmostEqual(reports[1].filled_qty, short_qty, places=4)
        self.assertEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_flip_long_to_short_one_bar(self) -> None:
        """Long→Short completes in 1 bar: close long + open short."""
        bus, kernel, reports = self._setup()

        # Build long position
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="entry", cutoff_ns=2))
        self.assertEqual(len(reports), 1)
        long_qty = reports[0].filled_qty
        self.assertGreater(long_qty, 0)

        # Flip to short
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="flip", cutoff_ns=3))

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
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="entry", cutoff_ns=2))
        self.assertEqual(len(reports), 1)
        short_qty = reports[0].filled_qty

        # Flip to long
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="flip", cutoff_ns=3))

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
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="entry", cutoff_ns=2))

        # Flip without immediate mode — single order crosses zero
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="flip", cutoff_ns=3))

        # Old behavior: 1 entry + 1 cross-zero order (no separate close+open)
        self.assertEqual(len(reports), 2)
        self.assertLess(kernel.positions["BTCUSDT_PERP"], 0)

    def test_flip_resets_pyramid_state(self) -> None:
        """After flip, pyramid filled_pct resets for new direction."""
        bus, kernel, reports = self._setup()

        # Build long, add more
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=1.0, intent_id="t1", cutoff_ns=2))

        # Flip to short
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(qty=-1.0, intent_id="flip", cutoff_ns=3))

        # Pyramid state should be reset to initial short pct (0.20)
        self.assertAlmostEqual(kernel._pyramid_filled_pct.get("BTCUSDT_PERP", 0), 0.20, places=2)


class ChurnControlExecutionTests(unittest.TestCase):
    def _setup(self, **config_overrides):
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        config = ExecutionKernelConfig(
            pyramid_enabled=False,
            adaptive_pyramid_enabled=False,
            gradual_exit_enabled=False,
            **config_overrides,
        )
        kernel = ExecutionKernel(bus, SimulatedGateway(), risk, config=config)
        orders = []
        reports = []
        bus.subscribe("exec.order.submit", lambda _, p: orders.append(p))
        bus.subscribe("exec.order.report", lambda _, p: reports.append(p))
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=1, ingest_time_ns=1, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=1),
                mark_price=40000.0,
            ),
        )
        return bus, kernel, orders, reports

    def _target(
        self,
        qty: float,
        *,
        intent_id: str,
        cutoff_ns: int,
        confidence: float = 0.9,
        atr: float = 0.0,
    ) -> TargetPosition:
        return TargetPosition(
            intent_id=intent_id,
            portfolio="arc",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=cutoff_ns,
            target_qty=qty,
            target_leverage=2.0,
            reduce_only=False,
            regime="TREND",
            regime_confidence=confidence,
            reason_codes=["TEST"],
            atr=atr,
        )

    def _target_with_reasons(
        self,
        qty: float,
        *,
        intent_id: str,
        cutoff_ns: int,
        confidence: float = 0.9,
        reason_codes: list[str] | None = None,
    ) -> TargetPosition:
        target = self._target(qty, intent_id=intent_id, cutoff_ns=cutoff_ns, confidence=confidence)
        target.reason_codes[:] = reason_codes or ["TEST"]
        return target

    def test_target_delta_deadband_skips_small_add_but_allows_reduction(self) -> None:
        bus, kernel, orders, reports = self._setup(target_delta_deadband_pct=0.05)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.04, intent_id="small-add", cutoff_ns=3))
        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 1.0)

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(0.96, intent_id="small-reduce", cutoff_ns=4)
        )
        self.assertEqual(len(reports), 2)
        self.assertEqual(orders[-1].side, -1)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.96)

    def test_target_reduce_deadband_skips_strategy_trim_but_allows_reduce_only(self) -> None:
        bus, kernel, orders, reports = self._setup(target_reduce_deadband_pct=0.05)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(0.96, intent_id="small-trim", cutoff_ns=3)
        )
        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 1.0)

        reduce_only = self._target(0.96, intent_id="risk-trim", cutoff_ns=4)
        reduce_only.reduce_only = True
        bus.publish("intent.target_position.final.BTCUSDT_PERP", reduce_only)
        self.assertEqual(len(reports), 2)
        self.assertTrue(orders[-1].reduce_only)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.96)

    def test_weak_signal_reverse_flattens_without_opening_opposite(self) -> None:
        bus, kernel, orders, reports = self._setup(
            weak_signal_no_flip_enabled=True,
            weak_signal_no_flip_confidence=0.70,
        )

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(-1.0, intent_id="weak-flip", cutoff_ns=3, confidence=0.55),
        )

        self.assertEqual(len(reports), 2)
        self.assertTrue(orders[-1].reduce_only)
        self.assertEqual(orders[-1].side, -1)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_flip_flatten_only_blocks_opposite_open_even_for_strong_signal(self) -> None:
        bus, kernel, orders, reports = self._setup(flip_flatten_only_enabled=True)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(-1.0, intent_id="strong-flip", cutoff_ns=3, confidence=0.95),
        )

        self.assertEqual(len(reports), 2)
        self.assertTrue(orders[-1].reduce_only)
        self.assertEqual(orders[-1].side, -1)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_flip_reverse_open_scale_reduces_opposite_open_size(self) -> None:
        bus, kernel, orders, reports = self._setup(flip_reverse_open_scale=0.5)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(-1.0, intent_id="scaled-flip", cutoff_ns=3, confidence=0.95),
        )

        self.assertEqual(len(reports), 3)
        self.assertTrue(orders[-2].reduce_only)
        self.assertFalse(orders[-1].reduce_only)
        self.assertEqual(orders[-1].side, -1)
        self.assertAlmostEqual(orders[-1].quantity, 0.1)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], -0.1)

    def test_loss_flip_confidence_floor_flattens_losing_position(self) -> None:
        bus, kernel, orders, reports = self._setup(loss_flip_reverse_open_confidence_floor=0.95)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=39000.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(-1.0, intent_id="loss-flip", cutoff_ns=4, confidence=0.9),
        )

        self.assertEqual(len(reports), 2)
        self.assertTrue(orders[-1].reduce_only)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_loss_flip_confidence_floor_allows_profitable_position_flip(self) -> None:
        bus, kernel, orders, reports = self._setup(loss_flip_reverse_open_confidence_floor=0.95)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=41000.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(-1.0, intent_id="profit-flip", cutoff_ns=4, confidence=0.9),
        )

        self.assertEqual(len(reports), 3)
        self.assertTrue(orders[-2].reduce_only)
        self.assertFalse(orders[-1].reduce_only)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], -0.2)

    def test_min_hold_blocks_reverse_open_until_position_ages(self) -> None:
        bus, kernel, orders, reports = self._setup(min_hold_bars_before_flip=2)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(-1.0, intent_id="early-flip", cutoff_ns=3)
        )

        self.assertEqual(len(reports), 2)
        self.assertTrue(orders[-1].reduce_only)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_tiny_reverse_target_reduces_only(self) -> None:
        bus, kernel, orders, reports = self._setup(
            tiny_reverse_reduce_only_enabled=True,
            tiny_reverse_deadband_pct=0.10,
        )

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(-0.05, intent_id="tiny-reverse", cutoff_ns=3)
        )

        self.assertEqual(len(reports), 2)
        self.assertTrue(orders[-1].reduce_only)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_entry_cooldown_blocks_reentry_after_flatten_only(self) -> None:
        bus, kernel, orders, reports = self._setup(
            entry_cooldown_bars=2,
            weak_signal_no_flip_enabled=True,
            weak_signal_no_flip_confidence=0.70,
        )

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(-1.0, intent_id="weak-flip", cutoff_ns=3, confidence=0.55),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(-1.0, intent_id="cooldown-1", cutoff_ns=4)
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(-1.0, intent_id="cooldown-2", cutoff_ns=5)
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP", self._target(-1.0, intent_id="after-cooldown", cutoff_ns=6)
        )

        self.assertEqual(len(reports), 3)
        self.assertLess(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_entry_confidence_floor_blocks_new_low_confidence_risk(self) -> None:
        bus, kernel, orders, reports = self._setup(entry_confidence_floor=0.75)

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(1.0, intent_id="weak-entry", cutoff_ns=2, confidence=0.70),
        )

        self.assertEqual(orders, [])
        self.assertEqual(reports, [])
        self.assertEqual(kernel.positions, {})

    def test_range_entry_block_skips_range_light_new_risk(self) -> None:
        bus, kernel, orders, reports = self._setup(range_entry_block_enabled=True)

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target_with_reasons(
                1.0,
                intent_id="range-entry",
                cutoff_ns=2,
                reason_codes=["PLAYBOOK_RANGE_LIGHT", "TREND_LONG"],
            ),
        )

        self.assertEqual(orders, [])
        self.assertEqual(reports, [])
        self.assertEqual(kernel.positions, {})

    def test_degraded_entry_block_allows_only_strong_degraded_signal(self) -> None:
        bus, kernel, orders, reports = self._setup(
            degraded_entry_block_enabled=True,
            degraded_entry_confidence_floor=0.80,
        )

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target_with_reasons(
                1.0,
                intent_id="weak-degraded",
                cutoff_ns=2,
                confidence=0.75,
                reason_codes=["QUALITY_DEGRADED", "TREND_LONG"],
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target_with_reasons(
                1.0,
                intent_id="strong-degraded",
                cutoff_ns=3,
                confidence=0.85,
                reason_codes=["QUALITY_DEGRADED", "TREND_LONG"],
            ),
        )

        self.assertEqual(len(reports), 1)
        self.assertGreater(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_reverse_open_confidence_floor_flattens_without_opposite_open(self) -> None:
        bus, kernel, orders, reports = self._setup(reverse_open_confidence_floor=0.85)

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(-1.0, intent_id="weak-reverse", cutoff_ns=3, confidence=0.80),
        )

        self.assertEqual(len(reports), 2)
        self.assertTrue(orders[-1].reduce_only)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.0)

    def test_profit_protect_reduces_profitable_position_on_weak_add(self) -> None:
        bus, kernel, orders, reports = self._setup(
            profit_protect_reduce_enabled=True,
            profit_protect_min_move_atr=1.0,
            profit_protect_unwind_pct=0.25,
            profit_protect_confidence_ceiling=0.75,
        )

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=42000.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(1.2, intent_id="weak-profitable-add", cutoff_ns=4, confidence=0.70, atr=1000.0),
        )

        self.assertEqual(len(reports), 2)
        self.assertTrue(orders[-1].reduce_only)
        self.assertEqual(orders[-1].side, -1)
        self.assertAlmostEqual(orders[-1].quantity, 0.25)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.75)

    def test_profit_protect_cooldown_suppresses_repeated_weak_add_reductions(self) -> None:
        bus, kernel, orders, reports = self._setup(
            profit_protect_reduce_enabled=True,
            profit_protect_min_move_atr=1.0,
            profit_protect_unwind_pct=0.25,
            profit_protect_confidence_ceiling=0.75,
            profit_protect_cooldown_bars=2,
        )

        bus.publish("intent.target_position.final.BTCUSDT_PERP", self._target(1.0, intent_id="entry", cutoff_ns=2))
        bus.publish(
            "md.mark.BINANCE.BTCUSDT_PERP",
            MarkPriceUpdate(
                meta=EventMeta(event_time_ns=3, ingest_time_ns=3, venue="BINANCE", symbol="BTCUSDT_PERP", stream_seq=2),
                mark_price=42000.0,
            ),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(1.2, intent_id="weak-profitable-add-1", cutoff_ns=4, confidence=0.70, atr=1000.0),
        )
        position_after_first_reduce = kernel.positions["BTCUSDT_PERP"]

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(1.2, intent_id="weak-profitable-add-2", cutoff_ns=5, confidence=0.70, atr=1000.0),
        )
        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(1.2, intent_id="weak-profitable-add-3", cutoff_ns=6, confidence=0.70, atr=1000.0),
        )

        self.assertEqual(len(reports), 2)
        self.assertEqual(len(orders), 2)
        self.assertAlmostEqual(position_after_first_reduce, 0.75)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.75)

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            self._target(1.2, intent_id="weak-profitable-add-4", cutoff_ns=7, confidence=0.70, atr=1000.0),
        )

        self.assertEqual(len(reports), 3)
        self.assertTrue(orders[-1].reduce_only)
        self.assertEqual(orders[-1].side, -1)
        self.assertAlmostEqual(orders[-1].quantity, 0.1875)
        self.assertAlmostEqual(kernel.positions["BTCUSDT_PERP"], 0.5625)


class ExecutionConfigBoundsTests(unittest.TestCase):
    def test_rejects_initial_pct_above_supported_range(self) -> None:
        from zenquant.execution.router import ExecutionKernelConfig

        with self.assertRaisesRegex(ValueError, "pyramid_initial_pct above 0.30"):
            ExecutionKernelConfig(pyramid_initial_pct=0.35)

    def test_rejects_initial_pct_below_supported_range(self) -> None:
        from zenquant.execution.router import ExecutionKernelConfig

        with self.assertRaisesRegex(ValueError, "pyramid_initial_pct below 0.30"):
            ExecutionKernelConfig(pyramid_initial_pct=0.25)

    def test_rejects_add_pct_above_supported_range(self) -> None:
        from zenquant.execution.router import ExecutionKernelConfig

        with self.assertRaisesRegex(ValueError, "pyramid_add_pct above 0.30"):
            ExecutionKernelConfig(pyramid_add_pct=0.35)

    def test_rejects_add_pct_below_supported_range(self) -> None:
        from zenquant.execution.router import ExecutionKernelConfig

        with self.assertRaisesRegex(ValueError, "pyramid_add_pct below 0.30"):
            ExecutionKernelConfig(pyramid_add_pct=0.20)

    def test_rejects_invalid_dca_decay(self) -> None:
        from zenquant.execution.router import ExecutionKernelConfig

        with self.assertRaisesRegex(ValueError, "dca_size_decay must be within"):
            ExecutionKernelConfig(dca_size_decay=1.2)

    def test_rejects_negative_loss_dca_confidence_floor(self) -> None:
        from zenquant.execution.router import ExecutionKernelConfig

        with self.assertRaisesRegex(ValueError, "loss_dca_confidence_floor must be >= 0.0"):
            ExecutionKernelConfig(loss_dca_confidence_floor=-0.1)

    def test_rejects_invalid_profit_protect_unwind_pct(self) -> None:
        from zenquant.execution.router import ExecutionKernelConfig

        with self.assertRaisesRegex(ValueError, "profit_protect_unwind_pct must be within"):
            ExecutionKernelConfig(profit_protect_unwind_pct=0.0)

    def test_rejects_negative_profit_protect_cooldown(self) -> None:
        from zenquant.execution.router import ExecutionKernelConfig

        with self.assertRaisesRegex(ValueError, "profit_protect_cooldown_bars must be >= 0"):
            ExecutionKernelConfig(profit_protect_cooldown_bars=-1)
