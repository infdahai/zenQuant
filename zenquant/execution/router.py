"""Execution kernel that turns final target positions into orders."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Protocol

from zenquant.domain import ExecutionOrder, MarkPriceUpdate, OrderBookSnapshot, TargetPosition, TradeTick
from zenquant.execution.gateway import ExecutionGateway
from zenquant.infra import EventBus


class FeeApplier(Protocol):
    """Minimal accounting hook required by execution after fills."""

    def apply_fee(self, fee: float) -> None: ...


@dataclass(slots=True)
class ExecutionKernelConfig:
    """Execution sizing and slippage limits for one bar-close cycle."""

    min_trade_qty: float = 0.0001
    max_trade_qty_per_bar: float = 1.0
    max_slippage_bps: float = 8.0
    # Pyramid execution (PaulWei pattern):
    # New position: only fill pyramid_initial_pct of target
    # Profitable position: add pyramid_add_pct more
    # Losing position: don't add (wait for time_decay exit)
    pyramid_initial_pct: float = 0.3  # 30% on first entry (long)
    pyramid_add_pct: float = 0.3  # 30% per profitable bar (long)
    pyramid_short_initial_pct: float = 0.2  # 20% on first short entry (more conservative)
    pyramid_short_add_pct: float = 0.2  # 20% per profitable bar (short)
    pyramid_enabled: bool = True
    # Legacy gradual execution (kept for compatibility)
    entry_fraction: float = 1.0
    exit_fraction: float = 1.0


class ExecutionKernel:
    """Convert final target intents into simulated fills.

    The kernel only listens to ``intent.target_position.final`` subjects so the
    risk overlay remains the last mutating step before execution.
    """

    def __init__(
        self,
        bus: EventBus,
        gateway: ExecutionGateway,
        risk_overlay: FeeApplier,
        config: ExecutionKernelConfig | None = None,
    ) -> None:
        self._bus = bus
        self._gateway = gateway
        self._risk_overlay = risk_overlay
        self._config = config or ExecutionKernelConfig()

        self._order_seq = itertools.count(1)
        self._last_price: dict[str, float] = {}
        self._best_bid: dict[str, float] = {}
        self._best_ask: dict[str, float] = {}
        self._positions: dict[str, float] = {}
        # Pyramid tracking: entry price for profitability check
        self._entry_prices: dict[str, float] = {}
        self._pyramid_filled_pct: dict[str, float] = {}  # How much of target is filled

        self._bus.subscribe("md.trade.>", self._on_trade)
        self._bus.subscribe("md.mark.>", self._on_mark_price)
        self._bus.subscribe("md.book.>", self._on_book)
        self._bus.subscribe("intent.target_position.final.>", self._on_target)

    def _on_trade(self, _: str, payload: object) -> None:
        if not isinstance(payload, TradeTick):
            return
        self._last_price[payload.meta.symbol] = payload.price

    def _on_mark_price(self, _: str, payload: object) -> None:
        if not isinstance(payload, MarkPriceUpdate):
            return
        if payload.mark_price > 0:
            self._last_price[payload.meta.symbol] = payload.mark_price

    def _on_book(self, _: str, payload: object) -> None:
        if not isinstance(payload, OrderBookSnapshot):
            return
        if payload.bids:
            self._best_bid[payload.meta.symbol] = payload.bids[0].price
        if payload.asks:
            self._best_ask[payload.meta.symbol] = payload.asks[0].price

    def _on_target(self, _: str, payload: object) -> None:
        """Diff current filled position against the new target and trade the delta."""
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
        is_reducing = current_position != 0 and current_position * delta_qty < 0

        if payload.reduce_only:
            if abs(current_position) < self._config.min_trade_qty:
                return
            if current_position * side > 0:
                return
            trade_qty = min(abs(delta_qty), abs(current_position))
        else:
            trade_qty = min(abs(delta_qty), self._config.max_trade_qty_per_bar)

        # Pyramid execution (PaulWei pattern)
        if self._config.pyramid_enabled and not is_reducing and not payload.reduce_only:
            is_new_direction = (
                abs(current_position) < self._config.min_trade_qty
                or current_position * delta_qty < 0
            )
            entry_px = self._entry_prices.get(symbol, 0)
            is_profitable = (
                entry_px > 0
                and abs(current_position) > self._config.min_trade_qty
                and (mark_price - entry_px) * (1 if current_position > 0 else -1) > 0
            )

            # Asymmetric pyramid: longs more aggressive, shorts more conservative
            is_short = payload.target_qty < 0
            init_pct = self._config.pyramid_short_initial_pct if is_short else self._config.pyramid_initial_pct
            add_pct = self._config.pyramid_short_add_pct if is_short else self._config.pyramid_add_pct

            if is_new_direction:
                trade_qty = abs(payload.target_qty) * init_pct
                self._pyramid_filled_pct[symbol] = init_pct
            elif is_profitable:
                # Profitable: add another slice (PaulWei: scale in on winners)
                filled = self._pyramid_filled_pct.get(symbol, 1.0)
                if filled < 1.0:
                    actual_add = min(add_pct, 1.0 - filled)
                    trade_qty = abs(payload.target_qty) * actual_add
                    self._pyramid_filled_pct[symbol] = filled + actual_add
                else:
                    trade_qty = 0  # Fully positioned
            else:
                # Losing: don't add (PaulWei: cut losers, don't average down)
                trade_qty = 0

            if trade_qty < self._config.min_trade_qty:
                return
        elif not is_reducing:
            # Legacy gradual execution
            fraction = self._config.entry_fraction
            if fraction < 1.0:
                trade_qty = trade_qty * fraction

        order = ExecutionOrder(
            client_order_id=f"{symbol}-{payload.cutoff_time_ns}-{next(self._order_seq)}",
            symbol=symbol,
            side=side,
            quantity=round(trade_qty, 6),
            reduce_only=payload.reduce_only,
            max_slippage_bps=self._config.max_slippage_bps,
        )
        self._bus.publish("exec.order.submit", order)

        report = self._gateway.submit_order(
            order,
            mark_price=mark_price,
            best_bid=self._best_bid.get(symbol),
            best_ask=self._best_ask.get(symbol),
            now_ns=payload.cutoff_time_ns,
        )
        self._bus.publish("exec.order.report", report)
        if report.status == "FILLED" and report.filled_qty > 0:
            new_position = current_position + (side * report.filled_qty)
            self._positions[symbol] = new_position
            # Track entry price for pyramid profitability check
            if abs(current_position) < self._config.min_trade_qty:
                # New position: set entry price
                self._entry_prices[symbol] = report.avg_price
                self._pyramid_filled_pct[symbol] = self._config.pyramid_initial_pct
            elif current_position * new_position < 0:
                # Flipped direction: reset
                self._entry_prices[symbol] = report.avg_price
                self._pyramid_filled_pct[symbol] = self._config.pyramid_initial_pct
            elif abs(new_position) < self._config.min_trade_qty:
                # Closed: reset
                self._entry_prices.pop(symbol, None)
                self._pyramid_filled_pct.pop(symbol, None)
            self._risk_overlay.apply_fee(report.fee)

    @property
    def positions(self) -> dict[str, float]:
        """Return a copy of filled positions tracked by the kernel."""
        return dict(self._positions)
