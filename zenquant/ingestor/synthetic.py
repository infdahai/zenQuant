"""Synthetic market-data source used for deterministic local development."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterator

from zenquant.domain import (
    EventMeta,
    FundingUpdate,
    OpenInterestUpdate,
    OrderBookLevel,
    OrderBookSnapshot,
    TradeTick,
)
from zenquant.infra import EventBus


@dataclass(slots=True)
class SyntheticIngestorConfig:
    """Synthetic feed controls for repeatable demo and test runs."""

    symbols: tuple[str, ...] = ("BTCUSDT_PERP", "ETHUSDT_PERP")
    venues: tuple[str, ...] = ("BINANCE", "OKX")
    seed: int = 7
    levels: int = 8
    step_seconds: int = 300
    funding_interval_steps: int = 12
    start_time_ns: int = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)


class SyntheticIngestor:
    """Generate a multi-venue synthetic market stream with stable randomness.

    Example:
        ingestor = SyntheticIngestor(bus, SyntheticIngestorConfig(seed=7))
        ingestor.run(steps=320)
    """

    def __init__(self, bus: EventBus, config: SyntheticIngestorConfig | None = None) -> None:
        self._bus = bus
        self._config = config or SyntheticIngestorConfig()
        self._rng = random.Random(self._config.seed)
        self._seq: dict[tuple[str, str], int] = defaultdict(int)
        self._base_prices: dict[str, float] = {
            "BTCUSDT_PERP": 42_000.0,
            "ETHUSDT_PERP": 2_400.0,
        }
        self._prices: dict[tuple[str, str], float] = {}
        for symbol in self._config.symbols:
            base = self._base_prices.get(symbol, 1_000.0)
            for venue in self._config.venues:
                venue_bias = 1.0 + (0.0007 if venue == "OKX" else 0.0)
                self._prices[(venue, symbol)] = base * venue_bias

    def run(self, steps: int) -> None:
        """Publish a fixed number of synthetic steps onto the event bus."""
        for subject, payload in self.iter_events(steps=steps, start_step=0):
            self._bus.publish(subject, payload)

    def iter_events(self, steps: int | None = None, start_step: int = 0) -> Iterator[tuple[str, object]]:
        """Yield the deterministic event sequence used by demo mode and tests."""
        step = start_step
        end_step = None if steps is None else start_step + steps
        while end_step is None or step < end_step:
            base_time_ns = self._config.start_time_ns + (step * self._config.step_seconds * 1_000_000_000)
            for symbol_index, symbol in enumerate(self._config.symbols):
                for venue_index, venue in enumerate(self._config.venues):
                    event_time_ns = base_time_ns + (venue_index * 300_000_000)
                    trade = self._build_trade(
                        step=step,
                        symbol=symbol,
                        venue=venue,
                        event_time_ns=event_time_ns,
                        symbol_index=symbol_index,
                    )
                    book = self._build_book(
                        symbol=symbol,
                        venue=venue,
                        event_time_ns=event_time_ns,
                    )
                    yield f"md.trade.{venue}.{symbol}", trade
                    yield f"md.book.{venue}.{symbol}", book

                    if step % self._config.funding_interval_steps == 0:
                        funding = self._build_funding(symbol=symbol, venue=venue, event_time_ns=event_time_ns, step=step)
                        yield f"md.funding.{venue}.{symbol}", funding

                    oi = self._build_open_interest(symbol=symbol, venue=venue, event_time_ns=event_time_ns, step=step)
                    yield f"md.oi.{venue}.{symbol}", oi
            step += 1

    def _next_seq(self, venue: str, symbol: str) -> int:
        key = (venue, symbol)
        self._seq[key] += 1
        return self._seq[key]

    def _build_trade(
        self,
        step: int,
        symbol: str,
        venue: str,
        event_time_ns: int,
        symbol_index: int,
    ) -> TradeTick:
        price_key = (venue, symbol)
        price = self._prices[price_key]
        cyclical = math.sin((step + (symbol_index * 5)) / 18.0) * (34.0 if "BTC" in symbol else 2.4)
        shock = self._rng.gauss(0.0, 16.0 if "BTC" in symbol else 1.2)
        mean_revert = (self._base_prices.get(symbol, price) - price) * 0.003
        next_price = max(10.0, price + cyclical + shock + mean_revert)
        self._prices[price_key] = next_price

        size_mean = 0.18 if "BTC" in symbol else 1.1
        size = max(0.001, abs(self._rng.gauss(size_mean, size_mean * 0.22)))
        is_buyer_aggressor = bool(self._rng.randint(0, 1))

        meta = EventMeta(
            event_time_ns=event_time_ns,
            ingest_time_ns=event_time_ns + 4_000_000,
            venue=venue,
            symbol=symbol,
            stream_seq=self._next_seq(venue=venue, symbol=symbol),
        )
        return TradeTick(
            meta=meta,
            price=round(next_price, 4),
            size=round(size, 4),
            is_buyer_aggressor=is_buyer_aggressor,
        )

    def _build_book(self, symbol: str, venue: str, event_time_ns: int) -> OrderBookSnapshot:
        mid = self._prices[(venue, symbol)]
        spread = max(0.1, mid * (0.0001 if "BTC" in symbol else 0.0002))
        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []
        for level in range(self._config.levels):
            step = spread * (1.0 + (level * 0.8))
            bid_price = mid - step
            ask_price = mid + step
            depth_scale = 1.0 / (1.0 + level * 0.35)
            base_qty = 1.6 if "BTC" in symbol else 8.0
            bid_qty = max(0.001, abs(self._rng.gauss(base_qty * depth_scale, base_qty * 0.08)))
            ask_qty = max(0.001, abs(self._rng.gauss(base_qty * depth_scale, base_qty * 0.08)))
            bids.append(OrderBookLevel(price=round(bid_price, 4), quantity=round(bid_qty, 4)))
            asks.append(OrderBookLevel(price=round(ask_price, 4), quantity=round(ask_qty, 4)))

        meta = EventMeta(
            event_time_ns=event_time_ns,
            ingest_time_ns=event_time_ns + 6_000_000,
            venue=venue,
            symbol=symbol,
            stream_seq=self._next_seq(venue=venue, symbol=symbol),
        )
        return OrderBookSnapshot(meta=meta, bids=bids, asks=asks, is_snapshot=True)

    def _build_funding(self, symbol: str, venue: str, event_time_ns: int, step: int) -> FundingUpdate:
        base = 0.00012 if "BTC" in symbol else 0.00016
        drift = math.sin(step / 36.0) * 0.00015
        venue_bias = 0.00003 if venue == "OKX" else 0.0
        funding = base + drift + venue_bias + self._rng.gauss(0.0, 0.00002)
        meta = EventMeta(
            event_time_ns=event_time_ns,
            ingest_time_ns=event_time_ns + 9_000_000,
            venue=venue,
            symbol=symbol,
            stream_seq=self._next_seq(venue=venue, symbol=symbol),
        )
        return FundingUpdate(meta=meta, funding_rate=round(funding, 8))

    def _build_open_interest(self, symbol: str, venue: str, event_time_ns: int, step: int) -> OpenInterestUpdate:
        base = 2_000_000_000.0 if "BTC" in symbol else 1_200_000_000.0
        venue_scale = 0.95 if venue == "OKX" else 1.0
        wave = 1.0 + (0.08 * math.sin(step / 42.0))
        oi = base * venue_scale * wave * (1.0 + self._rng.gauss(0.0, 0.01))
        meta = EventMeta(
            event_time_ns=event_time_ns,
            ingest_time_ns=event_time_ns + 10_000_000,
            venue=venue,
            symbol=symbol,
            stream_seq=self._next_seq(venue=venue, symbol=symbol),
        )
        return OpenInterestUpdate(meta=meta, open_interest=round(max(100_000.0, oi), 4))
