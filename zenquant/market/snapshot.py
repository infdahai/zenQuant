"""Build point-in-time market snapshots from raw market-data updates.

Each snapshot is the feature source consumed by the indicator engine on
confirmed bar close. The builder therefore focuses on freshness, coverage,
and deterministic fallback behavior.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from zenquant.domain import (
    BarCloseEvent,
    FundingUpdate,
    MarketSnapshot,
    MarkPriceUpdate,
    OpenInterestUpdate,
    OrderBookSnapshot,
    Timeframe,
    TradeTick,
)
from zenquant.infra import EventBus


@dataclass(slots=True)
class SnapshotBuilderConfig:
    """Freshness and venue settings for market snapshot generation."""

    expected_venues: int = 2
    depth_levels: int = 5
    book_stale_ms: int = 15 * 60 * 1_000
    funding_oi_stale_ms: int = 4 * 60 * 60 * 1_000
    timeframes: tuple[Timeframe, ...] = (Timeframe.TF_5M, Timeframe.TF_1H, Timeframe.TF_4H, Timeframe.TF_1D)


@dataclass(slots=True)
class _BookState:
    ts_ns: int
    bid_depth: float
    ask_depth: float


@dataclass(slots=True)
class _FundingOIState:
    funding_rate: float | None = None
    funding_ts_ns: int = 0
    open_interest: float | None = None
    oi_ts_ns: int = 0


@dataclass(slots=True)
class _CVDState:
    bar_start_ns: int
    delta: float


class SnapshotBuilder:
    """Combine books, funding, OI, trades, and mark price into one snapshot.

    Example:
        builder = SnapshotBuilder(bus)
        # later, on ``clock.bar_close.4h`` the builder publishes
        # ``md.snapshot.4h.BTCUSDT_PERP``.
    """

    def __init__(self, bus: EventBus, config: SnapshotBuilderConfig | None = None) -> None:
        self._bus = bus
        self._config = config or SnapshotBuilderConfig()

        self._last_price: dict[str, float] = {}
        self._last_trade_price: dict[str, float] = {}
        self._books: dict[str, dict[str, _BookState]] = defaultdict(dict)
        self._funding_oi: dict[str, dict[str, _FundingOIState]] = defaultdict(dict)
        self._cvd_state: dict[tuple[str, Timeframe], _CVDState] = {}
        self._closed_cvd: dict[tuple[str, Timeframe, int], float] = {}

        self._bus.subscribe("md.trade.>", self._on_trade)
        self._bus.subscribe("md.mark.>", self._on_mark_price)
        self._bus.subscribe("md.book.>", self._on_book)
        self._bus.subscribe("md.funding.>", self._on_funding)
        self._bus.subscribe("md.oi.>", self._on_open_interest)
        self._bus.subscribe("clock.bar_close.>", self._on_bar_close)

    def _on_trade(self, _: str, payload: object) -> None:
        """Update last price and per-bar CVD state from trade ticks."""
        if not isinstance(payload, TradeTick):
            return

        symbol = payload.meta.symbol
        self._last_price[symbol] = payload.price

        aggressor = payload.is_buyer_aggressor
        if aggressor is None:
            prev = self._last_trade_price.get(symbol)
            if prev is None:
                aggressor = True
            else:
                aggressor = payload.price >= prev
        self._last_trade_price[symbol] = payload.price

        signed_size = payload.size if aggressor else -payload.size
        for timeframe in self._config.timeframes:
            interval_ns = timeframe.seconds * 1_000_000_000
            bar_start_ns = (payload.meta.event_time_ns // interval_ns) * interval_ns
            key = (symbol, timeframe)
            state = self._cvd_state.get(key)
            if state is None:
                self._cvd_state[key] = _CVDState(bar_start_ns=bar_start_ns, delta=signed_size)
                continue

            if bar_start_ns < state.bar_start_ns:
                continue

            if bar_start_ns > state.bar_start_ns:
                prior_bar_end_ns = state.bar_start_ns + interval_ns
                self._closed_cvd[(symbol, timeframe, prior_bar_end_ns)] = state.delta
                self._cvd_state[key] = _CVDState(bar_start_ns=bar_start_ns, delta=signed_size)
                continue

            state.delta += signed_size

    def _on_mark_price(self, _: str, payload: object) -> None:
        if not isinstance(payload, MarkPriceUpdate):
            return
        if payload.mark_price > 0:
            self._last_price[payload.meta.symbol] = payload.mark_price

    def _on_book(self, _: str, payload: object) -> None:
        if not isinstance(payload, OrderBookSnapshot):
            return

        levels = self._config.depth_levels
        bid_depth = sum(level.quantity for level in payload.bids[:levels])
        ask_depth = sum(level.quantity for level in payload.asks[:levels])
        self._books[payload.meta.symbol][payload.meta.venue] = _BookState(
            ts_ns=payload.meta.event_time_ns,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
        )

    def _on_funding(self, _: str, payload: object) -> None:
        if not isinstance(payload, FundingUpdate):
            return

        state = self._funding_oi[payload.meta.symbol].setdefault(payload.meta.venue, _FundingOIState())
        state.funding_rate = payload.funding_rate
        state.funding_ts_ns = payload.meta.event_time_ns

    def _on_open_interest(self, _: str, payload: object) -> None:
        if not isinstance(payload, OpenInterestUpdate):
            return

        state = self._funding_oi[payload.meta.symbol].setdefault(payload.meta.venue, _FundingOIState())
        state.open_interest = payload.open_interest
        state.oi_ts_ns = payload.meta.event_time_ns

    def _on_bar_close(self, _: str, payload: object) -> None:
        """Finalize bar-scoped state and publish a point-in-time snapshot."""
        if not isinstance(payload, BarCloseEvent):
            return

        # Live bar-close events can arrive before the first trade in the next bar.
        # Roll current CVD forward here so the just-closed bar remains point-in-time correct.
        cvd_key = (payload.symbol, payload.timeframe)
        current_cvd = self._cvd_state.get(cvd_key)
        closed_key = (payload.symbol, payload.timeframe, payload.bar_end_ns)
        if closed_key not in self._closed_cvd and current_cvd is not None:
            if current_cvd.bar_start_ns == payload.bar_start_ns:
                self._closed_cvd[closed_key] = current_cvd.delta
                self._cvd_state[cvd_key] = _CVDState(
                    bar_start_ns=payload.bar_end_ns,
                    delta=0.0,
                )

        snapshot = self._build_snapshot(payload)
        self._bus.publish(f"md.snapshot.{payload.timeframe.value}.{payload.symbol}", snapshot)

    def _build_snapshot(self, event: BarCloseEvent) -> MarketSnapshot:
        """Assemble the snapshot visible to the indicator engine at bar close."""
        symbol = event.symbol
        now_ns = event.bar_end_ns

        quality_flags: list[str] = []

        book_states = self._books.get(symbol, {})
        valid_book_states: list[_BookState] = []
        book_ages: list[int] = []
        future_book = False
        for state in book_states.values():
            if state.ts_ns > now_ns:
                future_book = True
                continue
            age_ms = int(max(0, (now_ns - state.ts_ns) / 1_000_000))
            if age_ms <= self._config.book_stale_ms:
                valid_book_states.append(state)
                book_ages.append(age_ms)

        if future_book:
            quality_flags.append("DATA_FUTURE")

        venue_count = len(valid_book_states)
        if venue_count == 0:
            quality_flags.append("BOOK_STALE")
        elif venue_count < self._config.expected_venues:
            quality_flags.append("VENUE_DOWN")

        bid_depth = sum(state.bid_depth for state in valid_book_states)
        ask_depth = sum(state.ask_depth for state in valid_book_states)
        book_age_ms = max(book_ages) if book_ages else self._config.book_stale_ms + 1

        funding_oi_weighted: float | None = None
        weighted_numerator = 0.0
        weighted_denominator = 0.0
        funding_only_values: list[float] = []
        venue_states = self._funding_oi.get(symbol, {})
        max_data_age_ms = 0
        oi_valid_count = 0

        for state in venue_states.values():
            if state.funding_ts_ns > now_ns or state.oi_ts_ns > now_ns:
                quality_flags.append("DATA_FUTURE")
                continue

            funding_age_ms = int(max(0, (now_ns - state.funding_ts_ns) / 1_000_000)) if state.funding_ts_ns else 0
            oi_age_ms = int(max(0, (now_ns - state.oi_ts_ns) / 1_000_000)) if state.oi_ts_ns else 0
            max_data_age_ms = max(max_data_age_ms, funding_age_ms, oi_age_ms)
            funding_fresh = state.funding_rate is not None and funding_age_ms <= self._config.funding_oi_stale_ms
            oi_fresh = state.open_interest is not None and oi_age_ms <= self._config.funding_oi_stale_ms
            if funding_fresh and oi_fresh and state.open_interest is not None:
                weighted_numerator += state.funding_rate * state.open_interest
                weighted_denominator += state.open_interest
                oi_valid_count += 1
            elif funding_fresh and state.funding_rate is not None:
                funding_only_values.append(state.funding_rate)

        total_venues_for_coverage = max(len(venue_states), self._config.expected_venues)
        oi_coverage = oi_valid_count / total_venues_for_coverage if total_venues_for_coverage > 0 else 0.0

        if weighted_denominator > 0:
            funding_oi_weighted = weighted_numerator / weighted_denominator
        elif funding_only_values:
            funding_oi_weighted = sum(funding_only_values) / len(funding_only_values)
            quality_flags.append("MISSING_OI")
        else:
            quality_flags.append("MISSING_OI")

        if oi_coverage < 0.5:
            quality_flags.append("LOW_OI_COVERAGE")

        cvd_delta = self._closed_cvd.pop((symbol, event.timeframe, event.bar_end_ns), 0.0)
        last_price = self._last_price.get(symbol, 0.0)

        staleness_ms = max(book_age_ms, max_data_age_ms)
        if staleness_ms > max(self._config.book_stale_ms, self._config.funding_oi_stale_ms):
            quality_flags.append("DATA_STALE")

        deduped_flags = sorted(set(quality_flags)) if quality_flags else ["OK"]
        return MarketSnapshot(
            symbol=symbol,
            timeframe=event.timeframe,
            cutoff_time_ns=event.bar_end_ns,
            last_price=last_price,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            book_age_ms=book_age_ms,
            venue_count=venue_count,
            cvd_delta=cvd_delta,
            funding_oi_weighted=funding_oi_weighted,
            oi_coverage=oi_coverage,
            staleness_ms=staleness_ms,
            quality_flags=deduped_flags,
        )
