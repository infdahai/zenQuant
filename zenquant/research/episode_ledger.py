"""Deterministic position-episode accounting built from chronological fills."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256

from zenquant.domain import ExecutionReport, PositionEpisode


@dataclass(frozen=True, slots=True)
class FundingAllocation:
    """Signed funding PnL allocated to the position open at ``timestamp_ns``."""

    symbol: str
    timestamp_ns: int
    pnl: float


@dataclass(frozen=True, slots=True)
class PriceObservation:
    """Point-in-time price range used for open-episode excursion diagnostics."""

    symbol: str
    timestamp_ns: int
    high: float
    low: float
    close: float


@dataclass(slots=True)
class _EpisodeState:
    episode_id: str
    symbol: str
    side: int
    opened_at_ns: int
    entry_price: float
    remaining_qty: float
    max_abs_qty: float
    gross_realized_pnl: float = 0.0
    fees: float = 0.0
    funding_pnl: float = 0.0
    mfe_pnl: float = 0.0
    mae_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    fill_count: int = 1
    add_count: int = 0
    reduce_count: int = 0
    last_event_ns: int = 0
    exit_qty: float = 0.0
    exit_notional: float = 0.0

    def snapshot(self, *, closed_at_ns: int | None = None) -> PositionEpisode:
        closed = closed_at_ns is not None
        exit_price = self.exit_notional / self.exit_qty if self.exit_qty > 0.0 else None
        return PositionEpisode(
            episode_id=self.episode_id,
            symbol=self.symbol,
            side=self.side,
            opened_at_ns=self.opened_at_ns,
            closed_at_ns=closed_at_ns,
            entry_price=self.entry_price,
            exit_price=exit_price,
            max_abs_qty=self.max_abs_qty,
            remaining_qty=0.0 if closed else self.remaining_qty,
            gross_realized_pnl=self.gross_realized_pnl,
            fees=self.fees,
            funding_pnl=self.funding_pnl,
            net_pnl=self.gross_realized_pnl + self.funding_pnl - self.fees,
            mfe_pnl=self.mfe_pnl,
            mae_pnl=self.mae_pnl,
            unrealized_pnl=0.0 if closed else self.unrealized_pnl,
            fill_count=self.fill_count,
            closed=closed,
            add_count=self.add_count,
            reduce_count=self.reduce_count,
            holding_duration_ns=max(0, (closed_at_ns or self.last_event_ns) - self.opened_at_ns),
        )


class PositionEpisodeLedger:
    """Build independent per-symbol episodes without changing execution state."""

    def __init__(self) -> None:
        self._open: dict[str, _EpisodeState] = {}
        self._closed: list[PositionEpisode] = []
        self._last_timestamp_ns: dict[str, int] = {}
        self._episode_sequences: dict[str, int] = {}
        self._previous_episode_side: dict[str, int] = {}
        self._position_switch_count = 0

    @property
    def closed_episodes(self) -> tuple[PositionEpisode, ...]:
        return tuple(self._closed)

    @property
    def open_episodes(self) -> tuple[PositionEpisode, ...]:
        return tuple(self._open[symbol].snapshot() for symbol in sorted(self._open))

    @property
    def episodes(self) -> tuple[PositionEpisode, ...]:
        return tuple(sorted((*self._closed, *self.open_episodes), key=lambda item: (item.opened_at_ns, item.symbol)))

    @property
    def position_switch_count(self) -> int:
        """Count side changes, including opposite-side reopens after flat gaps."""
        return self._position_switch_count

    def position_qty(self, symbol: str) -> float:
        state = self._open.get(symbol)
        return 0.0 if state is None else state.side * state.remaining_qty

    def on_fill(self, report: ExecutionReport) -> None:
        """Apply one filled execution report, splitting flips exactly at zero."""
        if report.filled_qty <= 0.0 or report.avg_price <= 0.0:
            return
        if report.side not in {-1, 1}:
            raise ValueError("filled execution report side must be -1 or 1")
        self._check_chronological(report.symbol, report.transaction_time_ns)
        state = self._open.get(report.symbol)
        if state is None:
            self._open[report.symbol] = self._new_episode(
                symbol=report.symbol,
                side=report.side,
                qty=report.filled_qty,
                price=report.avg_price,
                fee=report.fee,
                timestamp_ns=report.transaction_time_ns,
            )
            return

        self._observe_state(state, high=report.avg_price, low=report.avg_price, close=report.avg_price)
        state.last_event_ns = report.transaction_time_ns
        if state.side == report.side:
            previous_qty = state.remaining_qty
            next_qty = previous_qty + report.filled_qty
            state.entry_price = (state.entry_price * previous_qty + report.avg_price * report.filled_qty) / next_qty
            state.remaining_qty = next_qty
            state.max_abs_qty = max(state.max_abs_qty, next_qty)
            state.fees += report.fee
            state.fill_count += 1
            state.add_count += 1
            return

        close_qty = min(state.remaining_qty, report.filled_qty)
        remainder = report.filled_qty - close_qty
        if remainder <= 1e-12:
            remainder = 0.0
            close_fee = report.fee
        else:
            close_fee = report.fee * (close_qty / report.filled_qty)
        self._reduce_state(state, qty=close_qty, price=report.avg_price, fee=close_fee)
        state.fill_count += 1
        state.reduce_count += 1
        if state.remaining_qty > 0.0:
            return

        self._closed.append(state.snapshot(closed_at_ns=report.transaction_time_ns))
        del self._open[report.symbol]
        if remainder > 0.0:
            self._open[report.symbol] = self._new_episode(
                symbol=report.symbol,
                side=report.side,
                qty=remainder,
                price=report.avg_price,
                fee=report.fee - close_fee,
                timestamp_ns=report.transaction_time_ns,
            )

    def allocate_funding(self, allocation: FundingAllocation) -> None:
        """Assign signed funding PnL to the episode open for its symbol."""
        self._check_chronological(allocation.symbol, allocation.timestamp_ns)
        state = self._open.get(allocation.symbol)
        if state is not None:
            state.funding_pnl += allocation.pnl
            state.last_event_ns = allocation.timestamp_ns

    on_funding = allocate_funding

    def observe_price(self, observation: PriceObservation) -> None:
        """Update MFE, MAE, and current unrealized PnL for an open episode."""
        if observation.high < observation.low:
            raise ValueError("price observation high must be at least low")
        if observation.close <= 0.0 or observation.low <= 0.0:
            raise ValueError("price observations must be positive")
        self._check_chronological(observation.symbol, observation.timestamp_ns)
        state = self._open.get(observation.symbol)
        if state is not None:
            self._observe_state(
                state,
                high=observation.high,
                low=observation.low,
                close=observation.close,
            )
            state.last_event_ns = observation.timestamp_ns

    def _new_episode(
        self,
        *,
        symbol: str,
        side: int,
        qty: float,
        price: float,
        fee: float,
        timestamp_ns: int,
    ) -> _EpisodeState:
        sequence = self._episode_sequences.get(symbol, 0) + 1
        self._episode_sequences[symbol] = sequence
        previous_side = self._previous_episode_side.get(symbol)
        if previous_side is not None and previous_side != side:
            self._position_switch_count += 1
        self._previous_episode_side[symbol] = side
        identity = f"{symbol}|{timestamp_ns}|{side}|{sequence}"
        return _EpisodeState(
            episode_id=sha256(identity.encode("utf-8")).hexdigest(),
            symbol=symbol,
            side=side,
            opened_at_ns=timestamp_ns,
            entry_price=price,
            remaining_qty=qty,
            max_abs_qty=qty,
            fees=fee,
            last_event_ns=timestamp_ns,
        )

    @staticmethod
    def _reduce_state(state: _EpisodeState, *, qty: float, price: float, fee: float) -> None:
        state.gross_realized_pnl += (price - state.entry_price) * qty * state.side
        state.fees += fee
        state.exit_qty += qty
        state.exit_notional += qty * price
        state.remaining_qty -= qty
        if state.remaining_qty <= 1e-12:
            state.remaining_qty = 0.0
            state.unrealized_pnl = 0.0
        else:
            state.unrealized_pnl = (price - state.entry_price) * state.remaining_qty * state.side

    @staticmethod
    def _observe_state(state: _EpisodeState, *, high: float, low: float, close: float) -> None:
        if state.side > 0:
            favorable = state.gross_realized_pnl + (high - state.entry_price) * state.remaining_qty
            adverse = state.gross_realized_pnl + (low - state.entry_price) * state.remaining_qty
        else:
            favorable = state.gross_realized_pnl + (state.entry_price - low) * state.remaining_qty
            adverse = state.gross_realized_pnl + (state.entry_price - high) * state.remaining_qty
        state.mfe_pnl = max(state.mfe_pnl, favorable)
        state.mae_pnl = min(state.mae_pnl, adverse)
        state.unrealized_pnl = (close - state.entry_price) * state.remaining_qty * state.side

    def _check_chronological(self, symbol: str, timestamp_ns: int) -> None:
        last_timestamp = self._last_timestamp_ns.get(symbol)
        if last_timestamp is not None and timestamp_ns < last_timestamp:
            raise ValueError(
                f"events for {symbol} must be chronological: timestamp_ns={timestamp_ns} "
                f"last_timestamp_ns={last_timestamp}"
            )
        self._last_timestamp_ns[symbol] = timestamp_ns


def build_position_episodes(
    fills: Iterable[ExecutionReport],
    *,
    funding_allocations: Iterable[FundingAllocation] = (),
    price_observations: Iterable[PriceObservation] = (),
) -> tuple[PositionEpisode, ...]:
    """Build episodes from separately supplied chronological event streams."""
    events: list[tuple[int, int, object]] = []
    events.extend((item.timestamp_ns, 0, item) for item in price_observations)
    events.extend((item.timestamp_ns, 1, item) for item in funding_allocations)
    events.extend((item.transaction_time_ns, 2, item) for item in fills)
    ledger = PositionEpisodeLedger()
    for _, _, event in sorted(events, key=lambda item: (item[0], item[1])):
        if isinstance(event, PriceObservation):
            ledger.observe_price(event)
        elif isinstance(event, FundingAllocation):
            ledger.allocate_funding(event)
        else:
            ledger.on_fill(event)
    return ledger.episodes
