from __future__ import annotations

import time
from dataclasses import dataclass

from zenquant.domain import ExecutionOrder, ExecutionReport
from zenquant.execution.gateway import ExecutionGateway, ExecutionGatewayConfig


@dataclass(slots=True)
class SimulatedGatewayConfig(ExecutionGatewayConfig):
    fallback_slippage_bps: float = 1.5


class SimulatedGateway(ExecutionGateway):
    def __init__(self, config: SimulatedGatewayConfig | None = None) -> None:
        self._config = config or SimulatedGatewayConfig()
        super().__init__(config=self._config)

    def submit_order(
        self,
        order: ExecutionOrder,
        mark_price: float,
        best_bid: float | None = None,
        best_ask: float | None = None,
        now_ns: int | None = None,
    ) -> ExecutionReport:
        if order.quantity <= 0 or mark_price <= 0:
            return ExecutionReport(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                status="REJECTED",
                filled_qty=0.0,
                avg_price=0.0,
                fee=0.0,
                transaction_time_ns=now_ns or time.time_ns(),
                reject_reason="invalid_order",
            )

        exec_price = self._execution_price(
            side=order.side,
            mark_price=mark_price,
            best_bid=best_bid,
            best_ask=best_ask,
        )
        fee = order.quantity * exec_price * self._config.taker_fee_rate
        return ExecutionReport(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            status="FILLED",
            filled_qty=order.quantity,
            avg_price=round(exec_price, 6),
            fee=round(fee, 6),
            transaction_time_ns=now_ns or time.time_ns(),
        )

    def _execution_price(
        self,
        side: int,
        mark_price: float,
        best_bid: float | None,
        best_ask: float | None,
    ) -> float:
        if side > 0 and best_ask is not None and best_ask > 0:
            return best_ask
        if side < 0 and best_bid is not None and best_bid > 0:
            return best_bid

        slippage = self._config.fallback_slippage_bps / 10_000.0
        if side > 0:
            return mark_price * (1.0 + slippage)
        return mark_price * (1.0 - slippage)
