"""Execution kernel that turns final target positions into orders."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Protocol

from zenquant.domain import ExecutionOrder, MarkPriceUpdate, OrderBookSnapshot, RiskSnapshot, TargetPosition, TradeTick
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
    pyramid_spacing_atr: float = 0.50  # favorable move required between profitable adds
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
    dca_spacing_atr: float = 0.75  # adverse move required between DCA tranches
    dca_size_decay: float = 0.75  # later DCA tranches get smaller
    max_long_inventory_tranches: int = 6
    max_short_inventory_tranches: int = 4
    max_long_dca_tranches: int = 2
    # Mirror entry slices on non-urgent exits.
    gradual_exit_enabled: bool = True
    exit_unwind_pct: float = 0.30  # reduce 30% of remaining per bar
    exit_unwind_min_pct: float = 0.20
    exit_unwind_max_pct: float = 0.50
    # Stop adding when price moves too far from entry.
    dca_max_adverse_atr: float = 2.5
    # Direction flip: immediately flatten + open opposite in one bar
    flip_immediate: bool = True
    flip_min_confidence: float = 0.5  # min regime_confidence to allow flip
    # Research-only hostile router. Defaults off to preserve production parity.
    hostile_behavior_router_enabled: bool = False
    # Research-only squeeze guard. Defaults off to preserve production parity.
    squeeze_flip_flatten_only_enabled: bool = False
    squeeze_flip_cooldown_enabled: bool = False
    squeeze_flip_cooldown_bars: int = 2
    squeeze_conditional_cooldown_enabled: bool = False
    squeeze_conditional_cooldown_drawdown_threshold: float = 0.025
    squeeze_conditional_cooldown_toxic_only: bool = False
    squeeze_cooldown_cancel_on_non_squeeze_enabled: bool = False
    # Legacy gradual execution (kept for compatibility)
    entry_fraction: float = 1.0
    exit_fraction: float = 1.0

    def __post_init__(self) -> None:
        if self.pyramid_initial_pct < 0.30:
            raise ValueError("pyramid_initial_pct below 0.30")
        if self.pyramid_initial_pct > 0.30:
            raise ValueError("pyramid_initial_pct above 0.30")
        if self.pyramid_add_pct < 0.30:
            raise ValueError("pyramid_add_pct below 0.30")
        if self.pyramid_add_pct > 0.30:
            raise ValueError("pyramid_add_pct above 0.30")
        if self.pyramid_short_initial_pct < 0.15:
            raise ValueError("pyramid_short_initial_pct below 0.15")
        if self.pyramid_short_initial_pct > 0.20:
            raise ValueError("pyramid_short_initial_pct above 0.20")
        if self.pyramid_short_add_pct < 0.10:
            raise ValueError("pyramid_short_add_pct below 0.10")
        if self.pyramid_short_add_pct > 0.20:
            raise ValueError("pyramid_short_add_pct above 0.20")
        if not 0.0 < self.exit_unwind_pct <= 0.60:
            raise ValueError("exit_unwind_pct must be within (0.0, 0.60]")
        if self.pyramid_spacing_atr <= 0.0:
            raise ValueError("pyramid_spacing_atr must be positive")
        if self.dca_spacing_atr <= 0.0:
            raise ValueError("dca_spacing_atr must be positive")
        if not 0.0 < self.dca_size_decay <= 1.0:
            raise ValueError("dca_size_decay must be within (0.0, 1.0]")
        if self.max_long_inventory_tranches < 1:
            raise ValueError("max_long_inventory_tranches must be >= 1")
        if self.max_short_inventory_tranches < 1:
            raise ValueError("max_short_inventory_tranches must be >= 1")
        if self.max_long_dca_tranches < 1:
            raise ValueError("max_long_dca_tranches must be >= 1")
        if self.max_long_dca_tranches > self.max_long_inventory_tranches:
            raise ValueError("max_long_dca_tranches must be <= max_long_inventory_tranches")
        if self.squeeze_flip_cooldown_bars < 1:
            raise ValueError("squeeze_flip_cooldown_bars must be >= 1")
        if self.squeeze_conditional_cooldown_drawdown_threshold < 0.0:
            raise ValueError("squeeze_conditional_cooldown_drawdown_threshold must be >= 0.0")


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
        self._last_fill_prices: dict[str, float] = {}
        self._pyramid_filled_pct: dict[str, float] = {}  # How much of target is filled
        self._inventory_tranches: dict[str, int] = {}
        self._squeeze_flip_cooldowns: dict[str, int] = {}
        self._latest_drawdown_by_portfolio: dict[str, float] = {}

        self._bus.subscribe("md.trade.>", self._on_trade)
        self._bus.subscribe("md.mark.>", self._on_mark_price)
        self._bus.subscribe("md.book.>", self._on_book)
        self._bus.subscribe("risk.snapshot", self._on_risk_snapshot)
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

    def _on_risk_snapshot(self, _: str, payload: object) -> None:
        if not isinstance(payload, RiskSnapshot):
            return
        self._latest_drawdown_by_portfolio[payload.portfolio] = payload.drawdown

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
        router_micro_state = self._hostile_router_micro_state(payload, current_position)

        if self._should_squeeze_flip_flatten_only(payload, current_position, router_micro_state):
            self._execute_flatten_only(symbol, current_position, payload, mark_price)
            if self._should_arm_squeeze_cooldown(payload):
                self._squeeze_flip_cooldowns[symbol] = self._config.squeeze_flip_cooldown_bars
            return

        if self._should_block_squeeze_cooldown_reentry(payload, current_position, router_micro_state):
            self._advance_squeeze_cooldown(symbol)
            return
        if self._squeeze_cooldown_feature_enabled() and self._squeeze_flip_cooldowns.get(symbol, 0) > 0:
            self._advance_squeeze_cooldown(symbol)

        # Direction flip: current position and target are opposite directions
        # Requires minimum confidence to prevent oscillation-driven churn
        flip_min_confidence = self._effective_flip_min_confidence(payload, current_position, router_micro_state)
        is_flip = (
            not payload.reduce_only
            and abs(current_position) >= self._config.min_trade_qty
            and current_position * payload.target_qty < 0
            and payload.regime_confidence >= flip_min_confidence
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
                exit_unwind_pct = self._effective_exit_unwind_pct(
                    payload,
                    current_position,
                    router_micro_state,
                )
                unwind_pct = self._adaptive_speed(
                    payload.realized_vol,
                    exit_unwind_pct,
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
            last_fill_px = self._last_fill_prices.get(symbol, entry_px)
            tranche_count = self._inventory_tranches.get(symbol, 0)
            is_profitable = (
                entry_px > 0
                and abs(current_position) > self._config.min_trade_qty
                and (mark_price - entry_px) * (1 if current_position > 0 else -1) > 0
            )

            # Asymmetric pyramid: longs more aggressive, shorts more conservative
            is_short = payload.target_qty < 0
            init_pct = self._config.pyramid_short_initial_pct if is_short else self._config.pyramid_initial_pct
            add_pct = self._config.pyramid_short_add_pct if is_short else self._config.pyramid_add_pct
            max_tranches = (
                self._config.max_short_inventory_tranches if is_short else self._config.max_long_inventory_tranches
            )
            init_pct, add_pct, max_tranches = self._effective_pyramid_profile(
                payload,
                is_short=is_short,
                init_pct=init_pct,
                add_pct=add_pct,
                max_tranches=max_tranches,
                router_micro_state=router_micro_state,
            )

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
                favorable_move_atr = self._signed_move_atr(
                    reference_price=last_fill_px,
                    mark_price=mark_price,
                    direction=1 if current_position > 0 else -1,
                    atr=payload.atr,
                )
                spacing_ok = payload.atr <= 0 or favorable_move_atr >= self._config.pyramid_spacing_atr
                if filled < 1.0 and tranche_count < max_tranches and spacing_ok:
                    actual_add = min(add_pct, 1.0 - filled)
                    trade_qty = abs(payload.target_qty) * actual_add
                    self._pyramid_filled_pct[symbol] = filled + actual_add
                else:
                    trade_qty = 0  # Fully positioned
            elif not is_short and self._config.dca_on_loss_long:
                # PaulWei DCA: 做多亏损时也加仓 (conviction — 越跌越买)
                # But keep it slower and inventory-capped so loss-side adds do not
                # become the dominant source of fragility.
                filled = self._pyramid_filled_pct.get(symbol, 1.0)
                adverse_move_atr = -self._signed_move_atr(
                    reference_price=last_fill_px,
                    mark_price=mark_price,
                    direction=1,
                    atr=payload.atr,
                )
                spacing_ok = payload.atr <= 0 or adverse_move_atr >= self._config.dca_spacing_atr
                dca_tranche_cap = min(max_tranches, self._config.max_long_dca_tranches)
                if filled < 1.0 and tranche_count < dca_tranche_cap and spacing_ok:
                    dca_base = self._adaptive_speed(
                        payload.realized_vol,
                        add_pct * 0.5 * (self._config.dca_size_decay ** max(0, tranche_count - 1)),
                        self._config.dca_min_speed,
                        self._config.dca_max_speed,
                    )
                    # Distance gating: scale/block DCA when price too far from entry
                    if entry_px > 0 and payload.atr > 0:
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
                self._last_fill_prices[symbol] = report.avg_price
                init_pct = (
                    self._config.pyramid_short_initial_pct if new_position < 0 else self._config.pyramid_initial_pct
                )
                self._pyramid_filled_pct[symbol] = init_pct
                self._inventory_tranches[symbol] = 1
            elif current_position * new_position < 0:
                # Flipped direction: reset
                self._entry_prices[symbol] = report.avg_price
                self._last_fill_prices[symbol] = report.avg_price
                init_pct = (
                    self._config.pyramid_short_initial_pct if new_position < 0 else self._config.pyramid_initial_pct
                )
                self._pyramid_filled_pct[symbol] = init_pct
                self._inventory_tranches[symbol] = 1
            elif abs(new_position) < self._config.min_trade_qty:
                # Closed: reset
                self._entry_prices.pop(symbol, None)
                self._last_fill_prices.pop(symbol, None)
                self._pyramid_filled_pct.pop(symbol, None)
                self._inventory_tranches.pop(symbol, None)
            elif abs(new_position) > abs(current_position):
                self._last_fill_prices[symbol] = report.avg_price
                self._inventory_tranches[symbol] = self._inventory_tranches.get(symbol, 1) + 1
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
            self._last_fill_prices[symbol] = open_report.avg_price
            self._pyramid_filled_pct[symbol] = init_pct
            self._inventory_tranches[symbol] = 1
            self._risk_overlay.apply_fee(open_report.fee)

    def _execute_flatten_only(
        self,
        symbol: str,
        current_position: float,
        payload: TargetPosition,
        mark_price: float,
    ) -> None:
        """Close an existing position without opening the requested opposite side."""
        close_side = -1 if current_position > 0 else 1
        close_order = ExecutionOrder(
            client_order_id=f"{symbol}-{payload.cutoff_time_ns}-{next(self._order_seq)}-flatten",
            symbol=symbol,
            side=close_side,
            quantity=round(abs(current_position), 6),
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
        self._last_fill_prices.pop(symbol, None)
        self._pyramid_filled_pct.pop(symbol, None)
        self._inventory_tranches.pop(symbol, None)
        self._risk_overlay.apply_fee(close_report.fee)

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

    def _hostile_router_micro_state(self, payload: TargetPosition, current_position: float) -> str:
        """Classify execution-only microstate from already-audited target fields."""
        if not (
            self._config.hostile_behavior_router_enabled
            or self._config.squeeze_flip_flatten_only_enabled
            or self._config.squeeze_flip_cooldown_enabled
            or self._config.squeeze_conditional_cooldown_enabled
            or self._config.squeeze_cooldown_cancel_on_non_squeeze_enabled
        ):
            return "disabled"
        target_direction = 1 if payload.target_qty > 0 else -1 if payload.target_qty < 0 else 0
        if (
            current_position < -self._config.min_trade_qty
            and target_direction > 0
            and self._has_squeeze_degraded_signal(payload)
        ):
            return "squeeze_continuation"
        if (
            current_position < -self._config.min_trade_qty
            and target_direction >= 0
            and self._config.hostile_behavior_router_enabled
            and (payload.realized_vol >= 0.8 or payload.regime_confidence < 0.55)
        ):
            return "squeeze_continuation"
        if (
            target_direction < 0
            and payload.realized_vol >= 0.9
            and (payload.regime in {"STRESS", "RANGE"} or payload.regime_confidence < 0.60)
        ):
            return "panic_reversal"
        if (
            target_direction < 0
            and payload.regime == "TREND"
            and payload.regime_confidence >= 0.55
            and payload.realized_vol < 1.2
        ):
            return "bearish_grind"
        if payload.regime == "RANGE" or payload.regime_confidence < 0.50:
            return "chop_neutral"
        return "default"

    def _should_squeeze_flip_flatten_only(
        self,
        payload: TargetPosition,
        current_position: float,
        router_micro_state: str,
    ) -> bool:
        return (
            (
                self._config.squeeze_flip_flatten_only_enabled
                or self._config.squeeze_flip_cooldown_enabled
                or (
                    self._config.squeeze_conditional_cooldown_enabled
                    and self._conditional_squeeze_cooldown_allowed(payload)
                )
            )
            and current_position < -self._config.min_trade_qty
            and payload.target_qty > self._config.min_trade_qty
            and router_micro_state == "squeeze_continuation"
            and self._has_squeeze_degraded_signal(payload)
        )

    def _should_block_squeeze_cooldown_reentry(
        self,
        payload: TargetPosition,
        current_position: float,
        router_micro_state: str,
    ) -> bool:
        if not self._squeeze_cooldown_feature_enabled():
            return False
        remaining = self._squeeze_flip_cooldowns.get(payload.symbol, 0)
        if (
            remaining > 0
            and self._config.squeeze_cooldown_cancel_on_non_squeeze_enabled
            and router_micro_state != "squeeze_continuation"
        ):
            self._squeeze_flip_cooldowns.pop(payload.symbol, None)
            return False
        return (
            remaining > 0
            and abs(current_position) < self._config.min_trade_qty
            and payload.target_qty > self._config.min_trade_qty
            and not payload.reduce_only
        )

    def _advance_squeeze_cooldown(self, symbol: str) -> None:
        remaining = self._squeeze_flip_cooldowns.get(symbol, 0)
        if remaining <= 1:
            self._squeeze_flip_cooldowns.pop(symbol, None)
        else:
            self._squeeze_flip_cooldowns[symbol] = remaining - 1

    def _should_arm_squeeze_cooldown(self, payload: TargetPosition) -> bool:
        if self._config.squeeze_flip_cooldown_enabled:
            return True
        return self._config.squeeze_conditional_cooldown_enabled and self._conditional_squeeze_cooldown_allowed(payload)

    def _squeeze_cooldown_feature_enabled(self) -> bool:
        return self._config.squeeze_flip_cooldown_enabled or self._config.squeeze_conditional_cooldown_enabled

    def _conditional_squeeze_cooldown_allowed(self, payload: TargetPosition) -> bool:
        if not self._has_squeeze_degraded_signal(payload):
            return False
        if (
            not self._config.squeeze_conditional_cooldown_toxic_only
            and self._latest_drawdown_by_portfolio.get(payload.portfolio, 0.0)
            >= self._config.squeeze_conditional_cooldown_drawdown_threshold
        ):
            return True
        toxic_codes = {
            "DD_STEP_1",
            "DD_STEP_2",
            "DD_STEP_3",
            "STRATEGY_DEGRADED",
            "VPIN_TOXIC",
            "VOLATILITY_STRESS",
        }
        return bool(toxic_codes.intersection(payload.reason_codes))

    @staticmethod
    def _has_squeeze_degraded_signal(payload: TargetPosition) -> bool:
        degraded_codes = {
            "BIAS_QUALITY_DEGRADED",
            "DIRECTION_TRANSITION",
            "PLAYBOOK_RANGE_LIGHT",
        }
        return bool(degraded_codes.intersection(payload.reason_codes))

    def _effective_flip_min_confidence(
        self,
        payload: TargetPosition,
        current_position: float,
        router_micro_state: str,
    ) -> float:
        threshold = self._config.flip_min_confidence
        if (
            self._config.hostile_behavior_router_enabled
            and current_position > self._config.min_trade_qty
            and payload.target_qty < 0
            and router_micro_state in {"panic_reversal", "chop_neutral"}
        ):
            threshold = max(threshold, 0.70)
        return threshold

    def _effective_exit_unwind_pct(
        self,
        payload: TargetPosition,
        current_position: float,
        router_micro_state: str,
    ) -> float:
        unwind_pct = self._config.exit_unwind_pct
        if (
            self._config.hostile_behavior_router_enabled
            and current_position < -self._config.min_trade_qty
            and payload.target_qty >= 0
            and router_micro_state == "squeeze_continuation"
        ):
            unwind_pct = max(unwind_pct, 0.40)
        return unwind_pct

    def _effective_pyramid_profile(
        self,
        payload: TargetPosition,
        *,
        is_short: bool,
        init_pct: float,
        add_pct: float,
        max_tranches: int,
        router_micro_state: str,
    ) -> tuple[float, float, int]:
        if not self._config.hostile_behavior_router_enabled or not is_short:
            return init_pct, add_pct, max_tranches
        if router_micro_state in {"panic_reversal", "chop_neutral"}:
            return min(init_pct, 0.15), min(add_pct, 0.10), min(max_tranches, 2)
        if payload.regime == "STRESS":
            return min(init_pct, 0.15), min(add_pct, 0.10), min(max_tranches, 2)
        return init_pct, add_pct, max_tranches

    @staticmethod
    def _is_immediate_exit(target: TargetPosition) -> bool:
        """Check if this exit should bypass gradual unwinding."""
        immediate = {"EXIT_TRAILING_STOP", "EXIT_INVALIDATION", "KILL_SWITCH", "QUALITY_UNSAFE"}
        return bool(set(target.reason_codes) & immediate)

    @staticmethod
    def _signed_move_atr(*, reference_price: float, mark_price: float, direction: int, atr: float) -> float:
        if reference_price <= 0 or mark_price <= 0 or atr <= 0:
            return 0.0
        return ((mark_price - reference_price) * direction) / atr

    @property
    def positions(self) -> dict[str, float]:
        """Return a copy of filled positions tracked by the kernel."""
        return dict(self._positions)
