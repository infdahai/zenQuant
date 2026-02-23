from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Iterator

from zenquant.domain.models import OrderBookLevel, OrderBookSnapshot, TradeTick
from zenquant.infra.event_bus import InMemoryJetStream


@dataclass(slots=True)
class SyntheticIngestorConfig:
    symbol: str = "BTC_USDT"
    exchange: str = "BINANCE"
    seed: int = 7
    levels: int = 5
    base_spread: float = 0.8


class SyntheticIngestor:
    def __init__(self, bus: InMemoryJetStream, config: SyntheticIngestorConfig | None = None) -> None:
        self._bus = bus
        self._config = config or SyntheticIngestorConfig()
        self._rng = random.Random(self._config.seed)

    def run(self, steps: int = 240, start_price: float = 40_000.0) -> None:
        for trade_tick, snapshot in self.iter_events(steps=steps, start_price=start_price):
            self._bus.publish(f"md.trade.{self._config.exchange}.{self._config.symbol}", trade_tick)
            self._bus.publish(f"md.book.{self._config.exchange}.{self._config.symbol}", snapshot)

    def iter_events(
        self, steps: int | None = 240, start_price: float = 40_000.0
    ) -> Iterator[tuple[TradeTick, OrderBookSnapshot]]:
        price = start_price
        start_ns = time.time_ns()
        i = 0
        while steps is None or i < steps:
            drift = math.sin(i / 16.0) * 1.2
            noise = self._rng.gauss(0.0, 2.2)
            price = max(10.0, price + drift + noise)
            exchange_timestamp_ns = start_ns + i * 50_000_000
            jitter_ns = int(self._rng.random() * 3_000_000)
            local_timestamp_ns = exchange_timestamp_ns + jitter_ns
            size = max(0.001, abs(self._rng.gauss(0.2, 0.08)))

            trade_tick = TradeTick(
                symbol=self._config.symbol,
                exchange=self._config.exchange,
                price=round(price, 4),
                size=round(size, 4),
                trade_id=i + 1,
                is_buyer_maker=bool(self._rng.randint(0, 1)),
                exchange_timestamp_ns=exchange_timestamp_ns,
                local_timestamp_ns=local_timestamp_ns,
            )
            snapshot = self._build_orderbook_snapshot(price, exchange_timestamp_ns, local_timestamp_ns)
            yield trade_tick, snapshot
            i += 1

    @property
    def symbol(self) -> str:
        return self._config.symbol

    @property
    def exchange(self) -> str:
        return self._config.exchange

    def _build_orderbook_snapshot(
        self, mid_price: float, exchange_timestamp_ns: int, local_timestamp_ns: int
    ) -> OrderBookSnapshot:
        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []
        for level in range(self._config.levels):
            step = self._config.base_spread + (level * 0.3)
            bid_price = mid_price - step
            ask_price = mid_price + step
            bid_qty = max(0.001, abs(self._rng.gauss(0.8, 0.3)))
            ask_qty = max(0.001, abs(self._rng.gauss(0.8, 0.3)))
            bids.append(OrderBookLevel(price=round(bid_price, 4), quantity=round(bid_qty, 4)))
            asks.append(OrderBookLevel(price=round(ask_price, 4), quantity=round(ask_qty, 4)))

        return OrderBookSnapshot(
            symbol=self._config.symbol,
            exchange=self._config.exchange,
            exchange_timestamp_ns=exchange_timestamp_ns,
            local_timestamp_ns=local_timestamp_ns,
            is_snapshot=True,
            bids=bids,
            asks=asks,
        )
