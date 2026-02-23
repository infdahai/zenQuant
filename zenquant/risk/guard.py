from __future__ import annotations

from dataclasses import dataclass

from zenquant.domain.models import ExecutionReport, OrderStatus, SubmitOrder


@dataclass(slots=True)
class RiskGuardConfig:
    max_drawdown: float = 0.05
    max_symbol_position: float = 5.0
    max_order_quantity: float = 2.0
    max_notional_per_order: float = 200_000.0


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    reason: str


class RiskGuard:
    def __init__(self, config: RiskGuardConfig | None = None, starting_equity: float = 100_000.0) -> None:
        self.config = config or RiskGuardConfig()
        self.equity = starting_equity
        self.high_water_mark = starting_equity
        self.kill_switch_active = False
        self.positions: dict[str, float] = {}

    def update_equity(self, new_equity: float) -> None:
        self.equity = new_equity
        if new_equity > self.high_water_mark:
            self.high_water_mark = new_equity
        if self.high_water_mark <= 0:
            self.kill_switch_active = False
            return
        drawdown = (self.high_water_mark - new_equity) / self.high_water_mark
        self.kill_switch_active = drawdown > self.config.max_drawdown

    def allow_order(self, order: SubmitOrder, mark_price: float) -> RiskDecision:
        if order.quantity <= 0:
            return RiskDecision(False, "quantity_must_be_positive")
        if order.side not in (-1, 1):
            return RiskDecision(False, "invalid_side")
        if order.quantity > self.config.max_order_quantity:
            return RiskDecision(False, "order_quantity_limit")
        notional = abs(order.quantity * mark_price)
        if notional > self.config.max_notional_per_order:
            return RiskDecision(False, "order_notional_limit")

        current = self.positions.get(order.symbol, 0.0)
        projected = current + (order.side * order.quantity)
        exposure_increase = abs(projected) > abs(current)

        if order.reduce_only and exposure_increase:
            return RiskDecision(False, "reduce_only_violation")
        if self.kill_switch_active and exposure_increase:
            return RiskDecision(False, "kill_switch_active")
        if not order.reduce_only and abs(projected) > self.config.max_symbol_position:
            return RiskDecision(False, "symbol_position_limit")

        return RiskDecision(True, "allowed")

    def on_fill(self, report: ExecutionReport, order: SubmitOrder) -> None:
        if report.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            return
        if report.cumulative_filled_qty <= 0:
            return
        current = self.positions.get(order.symbol, 0.0)
        self.positions[order.symbol] = current + (order.side * report.cumulative_filled_qty)

