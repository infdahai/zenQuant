from __future__ import annotations

import time
from dataclasses import dataclass

from zenquant.domain.models import ExecutionReport, OrderStatus, OrderType, SubmitOrder
from zenquant.risk.guard import RiskGuard


@dataclass(slots=True)
class ExecutionGatewayConfig:
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0004


class ExecutionGateway:
    def __init__(self, risk_guard: RiskGuard, config: ExecutionGatewayConfig | None = None) -> None:
        self._risk_guard = risk_guard
        self._config = config or ExecutionGatewayConfig()
        self._idempotency_reports: dict[str, ExecutionReport] = {}
        self._exchange_order_seq = 0

    def submit_order(self, order: SubmitOrder, mark_price: float, now_ns: int | None = None) -> ExecutionReport:
        if order.client_order_id in self._idempotency_reports:
            return self._idempotency_reports[order.client_order_id]

        decision = self._risk_guard.allow_order(order, mark_price)
        if not decision.allowed:
            report = self._build_report(
                order=order,
                status=OrderStatus.REJECTED,
                filled_qty=0.0,
                fill_price=0.0,
                fee=0.0,
                now_ns=now_ns,
                reject_reason=decision.reason,
            )
            self._idempotency_reports[order.client_order_id] = report
            return report

        report = self._simulate_execution(order, mark_price, now_ns)
        self._idempotency_reports[order.client_order_id] = report
        self._risk_guard.on_fill(report, order)
        return report

    def _simulate_execution(self, order: SubmitOrder, mark_price: float, now_ns: int | None) -> ExecutionReport:
        can_cross = self._can_cross(order, mark_price)
        filled_qty = 0.0
        fill_price = 0.0
        status = OrderStatus.NEW
        reject_reason = ""

        if order.type == OrderType.MARKET:
            filled_qty = order.quantity
            fill_price = mark_price
            status = OrderStatus.FILLED
        elif order.type == OrderType.POST_ONLY:
            if can_cross:
                status = OrderStatus.REJECTED
                reject_reason = "post_only_would_cross"
            else:
                status = OrderStatus.NEW
        elif order.type == OrderType.LIMIT:
            if can_cross:
                filled_qty = order.quantity
                fill_price = self._limit_fill_price(order, mark_price)
                status = OrderStatus.FILLED
            else:
                status = OrderStatus.NEW
        elif order.type == OrderType.FOK:
            if can_cross:
                filled_qty = order.quantity
                fill_price = self._limit_fill_price(order, mark_price)
                status = OrderStatus.FILLED
            else:
                status = OrderStatus.REJECTED
                reject_reason = "fok_not_fillable"
        elif order.type == OrderType.IOC:
            if can_cross:
                filled_qty = order.quantity * 0.5
                fill_price = self._limit_fill_price(order, mark_price)
                status = OrderStatus.PARTIALLY_FILLED
            else:
                status = OrderStatus.REJECTED
                reject_reason = "ioc_not_fillable"

        fee_rate = self._config.taker_fee_rate
        if order.type == OrderType.POST_ONLY and status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            fee_rate = self._config.maker_fee_rate
        fee = filled_qty * fill_price * fee_rate

        return self._build_report(
            order=order,
            status=status,
            filled_qty=filled_qty,
            fill_price=fill_price,
            fee=fee,
            now_ns=now_ns,
            reject_reason=reject_reason,
        )

    def _build_report(
        self,
        order: SubmitOrder,
        status: OrderStatus,
        filled_qty: float,
        fill_price: float,
        fee: float,
        now_ns: int | None,
        reject_reason: str,
    ) -> ExecutionReport:
        self._exchange_order_seq += 1
        return ExecutionReport(
            client_order_id=order.client_order_id,
            exchange_order_id=f"SIM-{self._exchange_order_seq}",
            status=status,
            cumulative_filled_qty=filled_qty,
            volume_weighted_avg_price=fill_price,
            commission=fee,
            commission_asset="USDT",
            transaction_time_ns=now_ns or time.time_ns(),
            reject_reason=reject_reason,
        )

    @staticmethod
    def _can_cross(order: SubmitOrder, mark_price: float) -> bool:
        if order.type == OrderType.MARKET:
            return True
        if order.limit_price <= 0:
            return False
        if order.side > 0:
            return order.limit_price >= mark_price
        return order.limit_price <= mark_price

    @staticmethod
    def _limit_fill_price(order: SubmitOrder, mark_price: float) -> float:
        if order.side > 0:
            return min(order.limit_price, mark_price)
        return max(order.limit_price, mark_price)

