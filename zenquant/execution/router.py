from __future__ import annotations

import itertools
from dataclasses import dataclass

from zenquant.domain.models import (
    AlphaSignal,
    OrderType,
    PAPattern,
    SignalType,
    SubmitOrder,
    TradeTick,
)
from zenquant.execution.gateway import ExecutionGateway
from zenquant.infra.event_bus import InMemoryJetStream
from zenquant.infra.ring_buffer import RingBuffer


@dataclass(slots=True)
class ExecutionRouterConfig:
    base_quantity: float = 0.2
    continuous_trigger: float = 0.70
    max_slippage_tolerance: float = 0.0005
    order_queue_capacity: int = 2048


class ExecutionRouter:
    def __init__(
        self,
        bus: InMemoryJetStream,
        gateway: ExecutionGateway,
        config: ExecutionRouterConfig | None = None,
    ) -> None:
        self._bus = bus
        self._gateway = gateway
        self._config = config or ExecutionRouterConfig()
        self._last_price: dict[str, float] = {}
        self._client_seq = itertools.count(1)
        self._submit_queue: RingBuffer[SubmitOrder] = RingBuffer(self._config.order_queue_capacity)

        self._bus.subscribe("md.trade.>", self._on_trade)
        self._bus.subscribe("signal.alpha.>", self._on_signal)

    def _on_trade(self, _: str, payload: object) -> None:
        if not isinstance(payload, TradeTick):
            return
        self._last_price[payload.symbol] = payload.price
        self._drain_submit_queue()

    def _on_signal(self, _: str, payload: object) -> None:
        if not isinstance(payload, AlphaSignal):
            return
        order = self._signal_to_order(payload)
        if order is None:
            return
        if not self._submit_queue.push(order):
            return
        self._drain_submit_queue()

    def _drain_submit_queue(self) -> None:
        while True:
            order = self._submit_queue.pop()
            if order is None:
                return
            mark_price = self._last_price.get(order.symbol)
            if mark_price is None:
                continue
            self._bus.publish("exec.order.submit", order)
            report = self._gateway.submit_order(order, mark_price=mark_price)
            self._bus.publish("exec.order.report", report)

    def _signal_to_order(self, signal: AlphaSignal) -> SubmitOrder | None:
        side = 0
        quantity = 0.0

        if signal.type == SignalType.CONTINUOUS:
            if abs(signal.continuous_value) < self._config.continuous_trigger:
                return None
            side = 1 if signal.continuous_value > 0 else -1
            quantity = self._config.base_quantity * max(1.0, abs(signal.continuous_value))
        elif signal.type == SignalType.DISCRETE_BOOL:
            if signal.pattern_event == PAPattern.H2_RESUMPTION:
                side = 1
                quantity = self._config.base_quantity * max(1.0, signal.confidence_ic)
            elif signal.pattern_event == PAPattern.MAJOR_TREND_REVERSAL:
                side = -1
                quantity = self._config.base_quantity * max(1.0, signal.confidence_ic)
            else:
                return None
        else:
            return None

        client_order_id = f"{signal.symbol}-{signal.timestamp_ns}-{next(self._client_seq)}"
        return SubmitOrder(
            client_order_id=client_order_id,
            symbol=signal.symbol,
            type=OrderType.MARKET,
            limit_price=0.0,
            quantity=round(quantity, 6),
            side=side,
            reduce_only=False,
            max_slippage_tolerance=self._config.max_slippage_tolerance,
            enable_synthetic_arb=False,
        )
