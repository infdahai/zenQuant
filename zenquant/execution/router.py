from __future__ import annotations

import itertools
from dataclasses import dataclass

from zenquant.domain import ExecutionOrder, TargetPosition, TradeTick
from zenquant.execution.gateway import ExecutionGateway
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay


@dataclass(slots=True)
class ExecutionKernelConfig:
    min_trade_qty: float = 0.0001
    max_trade_qty_per_bar: float = 1.0
    max_slippage_bps: float = 8.0


class ExecutionKernel:
    def __init__(
        self,
        bus: InMemoryJetStream,
        gateway: ExecutionGateway,
        risk_overlay: RiskOverlay,
        config: ExecutionKernelConfig | None = None,
    ) -> None:
        self._bus = bus
        self._gateway = gateway
        self._risk_overlay = risk_overlay
        self._config = config or ExecutionKernelConfig()

        self._order_seq = itertools.count(1)
        self._last_price: dict[str, float] = {}
        self._positions: dict[str, float] = {}

        self._bus.subscribe("md.trade.>", self._on_trade)
        self._bus.subscribe("intent.target_position.final.>", self._on_target)

    def _on_trade(self, _: str, payload: object) -> None:
        if not isinstance(payload, TradeTick):
            return
        self._last_price[payload.meta.symbol] = payload.price

    def _on_target(self, _: str, payload: object) -> None:
        if not isinstance(payload, TargetPosition):
            return

        symbol = payload.symbol
        mark_price = self._last_price.get(symbol)
        if mark_price is None or mark_price <= 0:
            return

        current_position = self._positions.get(symbol, 0.0)
        delta_qty = payload.target_qty - current_position
        if abs(delta_qty) < self._config.min_trade_qty:
            return

        side = 1 if delta_qty > 0 else -1
        trade_qty = min(abs(delta_qty), self._config.max_trade_qty_per_bar)
        if payload.reduce_only and abs(current_position + (side * trade_qty)) > abs(current_position):
            return

        order = ExecutionOrder(
            client_order_id=f"{symbol}-{payload.cutoff_time_ns}-{next(self._order_seq)}",
            symbol=symbol,
            side=side,
            quantity=round(trade_qty, 6),
            reduce_only=payload.reduce_only,
            max_slippage_bps=self._config.max_slippage_bps,
        )
        self._bus.publish("exec.order.submit", order)

        report = self._gateway.submit_order(order, mark_price=mark_price, now_ns=payload.cutoff_time_ns)
        self._bus.publish("exec.order.report", report)
        if report.status == "FILLED" and report.filled_qty > 0:
            self._positions[symbol] = current_position + (side * report.filled_qty)
            self._risk_overlay.apply_fee(report.fee)

    @property
    def positions(self) -> dict[str, float]:
        return dict(self._positions)
