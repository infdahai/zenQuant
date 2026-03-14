from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass

from zenquant.domain import (
    EventMeta,
    FundingUpdate,
    OpenInterestUpdate,
    OrderBookLevel,
    OrderBookSnapshot,
    TradeTick,
)
from zenquant.infra import InMemoryJetStream

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RealTimeIngestorConfig:
    symbols: tuple[str, ...] = ("BTCUSDT_PERP", "ETHUSDT_PERP")
    depth_levels: int = 10
    request_timeout_s: float = 6.0


class RealTimeIngestor:
    def __init__(self, bus: InMemoryJetStream, config: RealTimeIngestorConfig | None = None) -> None:
        self._bus = bus
        self._config = config or RealTimeIngestorConfig()
        self._seq: dict[tuple[str, str], int] = defaultdict(int)

    async def poll_once(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        for symbol in self._config.symbols:
            tasks.append(asyncio.create_task(self._poll_binance_symbol(symbol)))
            tasks.append(asyncio.create_task(self._poll_okx_symbol(symbol)))
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.warning("realtime ingestor polling failed: %s", result)

    async def _poll_binance_symbol(self, symbol: str) -> None:
        exch_symbol = _to_binance_symbol(symbol)
        if exch_symbol is None:
            return

        depth_url = f"https://fapi.binance.com/fapi/v1/depth?symbol={exch_symbol}&limit=20"
        trade_url = f"https://fapi.binance.com/fapi/v1/aggTrades?symbol={exch_symbol}&limit=1"
        funding_url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={exch_symbol}"
        oi_url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={exch_symbol}"

        depth = await _fetch_json(depth_url, timeout_s=self._config.request_timeout_s)
        trade = await _fetch_json(trade_url, timeout_s=self._config.request_timeout_s)
        funding = await _fetch_json(funding_url, timeout_s=self._config.request_timeout_s)
        open_interest = await _fetch_json(oi_url, timeout_s=self._config.request_timeout_s)

        now_ns = time.time_ns()
        if isinstance(depth, dict):
            ts_ms = int(depth.get("E", 0) or 0)
            event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else now_ns
            bids = _parse_depth_side(depth.get("bids"), self._config.depth_levels)
            asks = _parse_depth_side(depth.get("asks"), self._config.depth_levels)
            if bids and asks:
                meta = self._meta("BINANCE", symbol, event_ns)
                self._bus.publish(
                    f"md.book.BINANCE.{symbol}",
                    OrderBookSnapshot(meta=meta, bids=bids, asks=asks, is_snapshot=True),
                )

        if isinstance(trade, list) and trade:
            item = trade[0]
            price = _to_float(item.get("p"))
            size = _to_float(item.get("q"))
            ts_ms = int(item.get("T", 0) or 0)
            event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else now_ns
            if price > 0 and size > 0:
                # Binance agg trade field m=True means buyer was maker -> seller aggressor.
                is_buyer_aggressor = not bool(item.get("m", False))
                meta = self._meta("BINANCE", symbol, event_ns)
                self._bus.publish(
                    f"md.trade.BINANCE.{symbol}",
                    TradeTick(
                        meta=meta,
                        price=price,
                        size=size,
                        is_buyer_aggressor=is_buyer_aggressor,
                    ),
                )

        if isinstance(funding, dict):
            rate = _to_float(funding.get("lastFundingRate"))
            ts_ms = int(funding.get("time", 0) or 0)
            event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else now_ns
            meta = self._meta("BINANCE", symbol, event_ns)
            self._bus.publish(
                f"md.funding.BINANCE.{symbol}",
                FundingUpdate(meta=meta, funding_rate=rate),
            )

        if isinstance(open_interest, dict):
            oi = _to_float(open_interest.get("openInterest"))
            ts_ms = int(open_interest.get("time", 0) or 0)
            event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else now_ns
            meta = self._meta("BINANCE", symbol, event_ns)
            self._bus.publish(
                f"md.oi.BINANCE.{symbol}",
                OpenInterestUpdate(meta=meta, open_interest=oi),
            )

    async def _poll_okx_symbol(self, symbol: str) -> None:
        inst_id = _to_okx_inst_id(symbol)
        if inst_id is None:
            return

        books_url = f"https://www.okx.com/api/v5/market/books?instId={inst_id}&sz=20"
        trades_url = f"https://www.okx.com/api/v5/market/trades?instId={inst_id}&limit=1"
        funding_url = f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"
        oi_url = f"https://www.okx.com/api/v5/public/open-interest?instId={inst_id}"

        books = await _fetch_json(books_url, timeout_s=self._config.request_timeout_s)
        trades = await _fetch_json(trades_url, timeout_s=self._config.request_timeout_s)
        funding = await _fetch_json(funding_url, timeout_s=self._config.request_timeout_s)
        open_interest = await _fetch_json(oi_url, timeout_s=self._config.request_timeout_s)

        now_ns = time.time_ns()
        if isinstance(books, dict):
            rows = books.get("data") or []
            if rows:
                row = rows[0]
                ts_ms = int(row.get("ts", 0) or 0)
                event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else now_ns
                bids = _parse_depth_side(row.get("bids"), self._config.depth_levels)
                asks = _parse_depth_side(row.get("asks"), self._config.depth_levels)
                if bids and asks:
                    meta = self._meta("OKX", symbol, event_ns)
                    self._bus.publish(
                        f"md.book.OKX.{symbol}",
                        OrderBookSnapshot(meta=meta, bids=bids, asks=asks, is_snapshot=True),
                    )

        if isinstance(trades, dict):
            rows = trades.get("data") or []
            if rows:
                row = rows[0]
                price = _to_float(row.get("px"))
                size = _to_float(row.get("sz"))
                side = str(row.get("side", "")).lower()
                ts_ms = int(row.get("ts", 0) or 0)
                event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else now_ns
                if price > 0 and size > 0:
                    is_buyer_aggressor = side == "buy" if side in {"buy", "sell"} else None
                    meta = self._meta("OKX", symbol, event_ns)
                    self._bus.publish(
                        f"md.trade.OKX.{symbol}",
                        TradeTick(
                            meta=meta,
                            price=price,
                            size=size,
                            is_buyer_aggressor=is_buyer_aggressor,
                        ),
                    )

        if isinstance(funding, dict):
            rows = funding.get("data") or []
            if rows:
                row = rows[0]
                rate = _to_float(row.get("fundingRate"))
                ts_ms = int(row.get("fundingTime", 0) or 0)
                event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else now_ns
                meta = self._meta("OKX", symbol, event_ns)
                self._bus.publish(
                    f"md.funding.OKX.{symbol}",
                    FundingUpdate(meta=meta, funding_rate=rate),
                )

        if isinstance(open_interest, dict):
            rows = open_interest.get("data") or []
            if rows:
                row = rows[0]
                oi = _to_float(row.get("oi"))
                ts_ms = int(row.get("ts", 0) or 0)
                event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else now_ns
                meta = self._meta("OKX", symbol, event_ns)
                self._bus.publish(
                    f"md.oi.OKX.{symbol}",
                    OpenInterestUpdate(meta=meta, open_interest=oi),
                )

    def _meta(self, venue: str, symbol: str, event_time_ns: int) -> EventMeta:
        self._seq[(venue, symbol)] += 1
        return EventMeta(
            event_time_ns=event_time_ns,
            ingest_time_ns=time.time_ns(),
            venue=venue,
            symbol=symbol,
            stream_seq=self._seq[(venue, symbol)],
        )


def _to_binance_symbol(symbol: str) -> str | None:
    mapping = {
        "BTCUSDT_PERP": "BTCUSDT",
        "ETHUSDT_PERP": "ETHUSDT",
    }
    return mapping.get(symbol)


def _to_okx_inst_id(symbol: str) -> str | None:
    mapping = {
        "BTCUSDT_PERP": "BTC-USDT-SWAP",
        "ETHUSDT_PERP": "ETH-USDT-SWAP",
    }
    return mapping.get(symbol)


def _parse_depth_side(raw: object, levels: int) -> list[OrderBookLevel]:
    if not isinstance(raw, list):
        return []
    parsed: list[OrderBookLevel] = []
    for row in raw[:levels]:
        if not isinstance(row, list) or len(row) < 2:
            continue
        price = _to_float(row[0])
        qty = _to_float(row[1])
        if price <= 0 or qty <= 0:
            continue
        parsed.append(OrderBookLevel(price=price, quantity=qty))
    return parsed


def _to_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def _fetch_json(url: str, timeout_s: float) -> object | None:
    return await asyncio.to_thread(_fetch_json_sync, url, timeout_s)


def _fetch_json_sync(url: str, timeout_s: float) -> object | None:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "zenquant/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
