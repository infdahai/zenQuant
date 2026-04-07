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
    """Execution defaults for the bar-close kernel.

    The supported default philosophy is the PaulWei execution pattern:
    long-only DCA on adverse moves, profitable pyramiding in both directions,
    and gradual exits unless urgency requires an immediate unwind.
    """

    min_trade_qty: float = 0.0001
    max_trade_qty_per_bar: float = 1.0
    max_slippage_bps: float = 8.0
    # PaulWei execution defaults:
    # - Longs can add on profitable bars and on controlled adverse moves
    # - Shorts can pyramid only when profitable
    # - Exits unwind gradually unless the exit is urgent
    pyramid_initial_pct: float = 0.30  # 30% on first entry
    pyramid_add_pct: float = 0.30  # 30% per bar
    pyramid_short_initial_pct: float = 0.20  # 20% on first short entry
    pyramid_short_add_pct: float = 0.20  # 20% per profitable bar (short)
    pyramid_enabled: bool = True
    # Keep long-only DCA fixed in the default philosophy.
    dca_on_loss_long: bool = True
    # Scale entry/add speed by realized vol while preserving the same rules.
    adaptive_pyramid_enabled: bool = True
    pyramid_target_vol: float = 0.60  # reference vol level
    pyramid_min_speed: float = 0.10  # floor: min % per bar (extreme vol)
    pyramid_max_speed: float = 0.40  # ceiling: max % per bar (calm)
    dca_min_speed: float = 0.05  # DCA floor in extreme vol
    dca_max_speed: float = 0.20  # DCA ceiling
    # Mirror entry slices on non-urgent exits.
    gradual_exit_enabled: bool = True
    exit_unwind_pct: float = 0.30  # reduce 30% of remaining per bar
    exit_unwind_min_pct: float = 0.20
    exit_unwind_max_pct: float = 0.50
    # Stop adding when price moves too far from entry.
    dca_max_adverse_atr: float = 3.0
    # Direction flip: immediately flatten + open opposite in one bar
    flip_immediate: bool = True
    flip_min_confidence: float = 0.5  # min regime_confidence to allow flip
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

        # Direction flip: current position and target are opposite directions
        # Requires minimum confidence to prevent oscillation-driven churn
        is_flip = (
            not payload.reduce_only
            and abs(current_position) >= self._config.min_trade_qty
            and current_position * payload.target_qty < 0
            and payload.regime_confidence >= self._config.flip_min_confidence
        )
        if is_flip and self._config.flip_immediate:
            self._execute_flip(symbol, current_position, payload, mark_price)
            return

        if payload.reduce_only:
            if abs(current_position) < self._config.min_trade_qty:
                return
            if current_position * side > 0:
                return
            trade_qty = min(abs(delta_qty), abs(current_position))
            # Gradual exit: unwind fraction per bar for non-immediate exits
            if self._config.gradual_exit_enabled and not self._is_immediate_exit(payload):
                unwind_pct = self._adaptive_speed(
                    payload.realized_vol,
                    self._config.exit_unwind_pct,
                    self._config.exit_unwind_min_pct,
                    self._config.exit_unwind_max_pct,
                )
                max_exit = abs(current_position) * unwind_pct
                trade_qty = min(trade_qty, max_exit)
                if trade_qty < self._config.min_trade_qty:
                    return
        else:
            trade_qty = min(abs(delta_qty), self._config.max_trade_qty_per_bar)

        # Pyramid execution (PaulWei pattern)
        if self._config.pyramid_enabled and not is_reducing and not payload.reduce_only:
            is_new_direction = abs(current_position) < self._config.min_trade_qty or current_position * delta_qty < 0
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

            # Volatility-adaptive speed: fast in calm trends, slow in crashes
            init_pct = self._adaptive_speed(
                payload.realized_vol,
                init_pct,
                self._config.pyramid_min_speed,
                self._config.pyramid_max_speed,
            )
            add_pct = self._adaptive_speed(
                payload.realized_vol,
                add_pct,
                self._config.pyramid_min_speed,
                self._config.pyramid_max_speed,
            )

            if is_new_direction:
                trade_qty = abs(payload.target_qty) * init_pct
                self._pyramid_filled_pct[symbol] = init_pct
            elif is_profitable:
                # Profitable: add another slice
                filled = self._pyramid_filled_pct.get(symbol, 1.0)
                if filled < 1.0:
                    actual_add = min(add_pct, 1.0 - filled)
                    trade_qty = abs(payload.target_qty) * actual_add
                    self._pyramid_filled_pct[symbol] = filled + actual_add
                else:
                    trade_qty = 0  # Fully positioned
            elif not is_short and self._config.dca_on_loss_long:
                # PaulWei DCA: 做多亏损时也加仓 (conviction — 越跌越买)
                # But at reduced size (half of normal add)
                filled = self._pyramid_filled_pct.get(symbol, 1.0)
                if filled < 1.0:
                    dca_base = self._adaptive_speed(
                        payload.realized_vol,
                        add_pct * 0.5,
                        self._config.dca_min_speed,
                        self._config.dca_max_speed,
                    )
                    # Distance gating: scale/block DCA when price too far from entry
                    if self._config.adaptive_pyramid_enabled and entry_px > 0 and payload.atr > 0:
                        adverse_distance = abs(mark_price - entry_px) / payload.atr
                        if adverse_distance > self._config.dca_max_adverse_atr:
                            trade_qty = 0
                        else:
                            distance_scalar = max(0.3, 1.0 - adverse_distance / self._config.dca_max_adverse_atr)
                            dca_add = min(dca_base * distance_scalar, 1.0 - filled)
                            trade_qty = abs(payload.target_qty) * dca_add
                            self._pyramid_filled_pct[symbol] = filled + dca_add
                    else:
                        dca_add = min(dca_base, 1.0 - filled)
                        trade_qty = abs(payload.target_qty) * dca_add
                        self._pyramid_filled_pct[symbol] = filled + dca_add
                else:
                    trade_qty = 0
            else:
                # Short losing: don't add (shorts are strict)
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

    def _execute_flip(
        self,
        symbol: str,
        current_position: float,
        payload: TargetPosition,
        mark_price: float,
    ) -> None:
        """Flatten current position and open opposite direction in one bar."""
        # Step 1: Close current position
        close_side = -1 if current_position > 0 else 1
        close_qty = abs(current_position)
        close_order = ExecutionOrder(
            client_order_id=f"{symbol}-{payload.cutoff_time_ns}-{next(self._order_seq)}-close",
            symbol=symbol,
            side=close_side,
            quantity=round(close_qty, 6),
            reduce_only=True,
            max_slippage_bps=self._config.max_slippage_bps,
        )
        self._bus.publish("exec.order.submit", close_order)
        close_report = self._gateway.submit_order(
            close_order,
            mark_price=mark_price,
            best_bid=self._best_bid.get(symbol),
            best_ask=self._best_ask.get(symbol),
            now_ns=payload.cutoff_time_ns,
        )
        self._bus.publish("exec.order.report", close_report)
        if close_report.status != "FILLED":
            return
        self._positions[symbol] = 0.0
        self._entry_prices.pop(symbol, None)
        self._pyramid_filled_pct.pop(symbol, None)
        self._risk_overlay.apply_fee(close_report.fee)

        # Step 2: Open opposite direction with pyramid initial sizing
        is_short = payload.target_qty < 0
        init_pct = self._config.pyramid_short_initial_pct if is_short else self._config.pyramid_initial_pct
        if self._config.pyramid_enabled:
            init_pct = self._adaptive_speed(
                payload.realized_vol,
                init_pct,
                self._config.pyramid_min_speed,
                self._config.pyramid_max_speed,
            )
        open_qty = abs(payload.target_qty) * init_pct
        open_side = 1 if payload.target_qty > 0 else -1
        if open_qty < self._config.min_trade_qty:
            return
        open_order = ExecutionOrder(
            client_order_id=f"{symbol}-{payload.cutoff_time_ns}-{next(self._order_seq)}-open",
            symbol=symbol,
            side=open_side,
            quantity=round(open_qty, 6),
            reduce_only=False,
            max_slippage_bps=self._config.max_slippage_bps,
        )
        self._bus.publish("exec.order.submit", open_order)
        open_report = self._gateway.submit_order(
            open_order,
            mark_price=mark_price,
            best_bid=self._best_bid.get(symbol),
            best_ask=self._best_ask.get(symbol),
            now_ns=payload.cutoff_time_ns,
        )
        self._bus.publish("exec.order.report", open_report)
        if open_report.status == "FILLED" and open_report.filled_qty > 0:
            self._positions[symbol] = open_side * open_report.filled_qty
            self._entry_prices[symbol] = open_report.avg_price
            self._pyramid_filled_pct[symbol] = init_pct
            self._risk_overlay.apply_fee(open_report.fee)

    def _adaptive_speed(
        self,
        realized_vol: float,
        base_pct: float,
        min_speed: float,
        max_speed: float,
    ) -> float:
        """Scale a pyramid percentage by vol ratio. High vol = slower, low vol = faster."""
        if not self._config.adaptive_pyramid_enabled or realized_vol <= 0:
            return base_pct
        vol_ratio = self._config.pyramid_target_vol / realized_vol
        return max(min_speed, min(max_speed, base_pct * vol_ratio))

    @staticmethod
    def _is_immediate_exit(target: TargetPosition) -> bool:
        """Check if this exit should bypass gradual unwinding."""
        immediate = {"EXIT_TRAILING_STOP", "EXIT_INVALIDATION", "KILL_SWITCH", "QUALITY_UNSAFE"}
        return bool(set(target.reason_codes) & immediate)

    @property
    def positions(self) -> dict[str, float]:
        """Return a copy of filled positions tracked by the kernel."""
        return dict(self._positions)
