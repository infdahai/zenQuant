"""Execution gateway abstraction used by the execution kernel."""

from __future__ import annotations

import time
from dataclasses import dataclass

from zenquant.domain import ExecutionOrder, ExecutionReport


@dataclass(slots=True)
class ExecutionGatewayConfig:
    """Shared execution-cost assumptions for gateway implementations."""

    taker_fee_rate: float = 0.0004


class ExecutionGateway:
    """Default gateway that fills immediately at the provided mark price."""

    def __init__(self, config: ExecutionGatewayConfig | None = None) -> None:
        self._config = config or ExecutionGatewayConfig()

    def submit_order(
        self,
        order: ExecutionOrder,
        mark_price: float,
        best_bid: float | None = None,
        best_ask: float | None = None,
        now_ns: int | None = None,
    ) -> ExecutionReport:
        """Submit an order and return a deterministic simulated report.

        Example:
            report = gateway.submit_order(order, mark_price=42000.0)
        """
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

        fee = order.quantity * mark_price * self._config.taker_fee_rate
        return ExecutionReport(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            status="FILLED",
            filled_qty=order.quantity,
            avg_price=mark_price,
            fee=fee,
            transaction_time_ns=now_ns or time.time_ns(),
        )
