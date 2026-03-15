from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from websockets.asyncio.client import ClientConnection, connect

from zenquant.domain import (
    BarCloseEvent,
    Candle,
    EventMeta,
    FundingUpdate,
    MarkPriceUpdate,
    OpenInterestUpdate,
    OrderBookLevel,
    OrderBookSnapshot,
    Timeframe,
    TradeTick,
)
from zenquant.infra import InMemoryJetStream


logger = logging.getLogger(__name__)

BINANCE_FSTREAM_WS = "wss://fstream.binance.com/stream"
BINANCE_FSTREAM_REST = "https://fapi.binance.com"


@dataclass(slots=True)
class BinancePerpStreamConfig:
    symbols: tuple[str, ...] = ("BTCUSDT_PERP",)
    depth_levels: int = 10
    depth_update_ms: int = 1000
    mark_price_update_s: int = 1
    open_interest_poll_interval_s: float = 30.0
    request_timeout_s: float = 6.0
    reconnect_backoff_s: float = 3.0


class BinancePerpStreamClient:
    def __init__(
        self,
        bus: InMemoryJetStream,
        config: BinancePerpStreamConfig | None = None,
    ) -> None:
        self._bus = bus
        self._config = config or BinancePerpStreamConfig()
        self._seq: dict[str, int] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._ws: ClientConnection | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run_market_stream()),
            asyncio.create_task(self._run_open_interest_poll()),
        ]

    async def stop(self) -> None:
        self._running = False
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_market_stream(self) -> None:
        while self._running:
            try:
                async with connect(self._stream_url(), ping_interval=20, ping_timeout=20) as websocket:
                    self._ws = websocket
                    logger.info("connected to Binance futures market stream")
                    async for message in websocket:
                        if not self._running:
                            break
                        self._handle_market_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("binance market stream disconnected: %s", exc)
            finally:
                self._ws = None
            if self._running:
                await asyncio.sleep(self._config.reconnect_backoff_s)

    async def _run_open_interest_poll(self) -> None:
        while self._running:
            try:
                await self._poll_open_interest_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("open interest poll failed: %s", exc)
            await asyncio.sleep(self._config.open_interest_poll_interval_s)

    async def _poll_open_interest_once(self) -> None:
        for symbol in self._config.symbols:
            exch_symbol = _to_binance_symbol(symbol)
            if exch_symbol is None:
                continue
            url = f"{BINANCE_FSTREAM_REST}/fapi/v1/openInterest?symbol={exch_symbol}"
            payload = await asyncio.to_thread(_fetch_json_sync, url, self._config.request_timeout_s)
            if not isinstance(payload, dict):
                continue
            oi = _to_float(payload.get("openInterest"))
            ts_ms = int(payload.get("time", 0) or 0)
            event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else time.time_ns()
            if oi <= 0:
                continue
            meta = self._meta(symbol=symbol, event_time_ns=event_ns)
            self._bus.publish(
                f"md.oi.BINANCE.{symbol}",
                OpenInterestUpdate(meta=meta, open_interest=oi),
            )

    def _handle_market_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return

        data = payload.get("data")
        if not isinstance(data, dict):
            return

        event_type = str(data.get("e", ""))
        stream = str(payload.get("stream", ""))
        symbol = _from_binance_symbol(str(data.get("s", "")))
        if symbol is None:
            symbol = _symbol_from_stream(stream)
        if symbol is None:
            return

        if event_type == "aggTrade":
            self._publish_trade(symbol, data)
            return
        if event_type == "markPriceUpdate":
            self._publish_mark_price(symbol, data)
            return
        if event_type == "depthUpdate":
            self._publish_depth(symbol, data)
            return
        if event_type == "kline":
            self._publish_kline(symbol, data)

    def _publish_trade(self, symbol: str, payload: dict[str, object]) -> None:
        price = _to_float(payload.get("p"))
        size = _to_float(payload.get("q"))
        ts_ms = int(payload.get("T", 0) or 0)
        if price <= 0 or size <= 0:
            return
        event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else time.time_ns()
        meta = self._meta(symbol=symbol, event_time_ns=event_ns)
        self._bus.publish(
            f"md.trade.BINANCE.{symbol}",
            TradeTick(
                meta=meta,
                price=price,
                size=size,
                is_buyer_aggressor=not bool(payload.get("m", False)),
            ),
        )

    def _publish_mark_price(self, symbol: str, payload: dict[str, object]) -> None:
        mark_price = _to_float(payload.get("p"))
        index_price = _to_float(payload.get("i"))
        funding_rate = _to_float(payload.get("r"))
        ts_ms = int(payload.get("E", 0) or 0)
        next_funding_ms = int(payload.get("T", 0) or 0)
        if mark_price <= 0:
            return
        event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else time.time_ns()
        meta = self._meta(symbol=symbol, event_time_ns=event_ns)
        self._bus.publish(
            f"md.mark.BINANCE.{symbol}",
            MarkPriceUpdate(
                meta=meta,
                mark_price=mark_price,
                index_price=index_price if index_price > 0 else None,
                next_funding_time_ns=(next_funding_ms * 1_000_000) if next_funding_ms > 0 else None,
            ),
        )
        self._bus.publish(
            f"md.funding.BINANCE.{symbol}",
            FundingUpdate(meta=meta, funding_rate=funding_rate),
        )

    def _publish_depth(self, symbol: str, payload: dict[str, object]) -> None:
        bids = _parse_depth_side(payload.get("b"), self._config.depth_levels)
        asks = _parse_depth_side(payload.get("a"), self._config.depth_levels)
        if not bids or not asks:
            return
        ts_ms = int(payload.get("E", 0) or 0)
        event_ns = (ts_ms * 1_000_000) if ts_ms > 0 else time.time_ns()
        meta = self._meta(symbol=symbol, event_time_ns=event_ns)
        self._bus.publish(
            f"md.book.BINANCE.{symbol}",
            OrderBookSnapshot(meta=meta, bids=bids, asks=asks, is_snapshot=False),
        )

    def _publish_kline(self, symbol: str, payload: dict[str, object]) -> None:
        raw_kline = payload.get("k")
        if not isinstance(raw_kline, dict):
            return
        if not bool(raw_kline.get("x", False)):
            return

        timeframe = _timeframe_from_stream(str(raw_kline.get("i", "")))
        if timeframe is None:
            return

        bar_start_ms = int(raw_kline.get("t", 0) or 0)
        bar_close_ms = int(raw_kline.get("T", 0) or 0)
        if bar_start_ms <= 0 or bar_close_ms <= 0:
            return

        bar_start_ns = bar_start_ms * 1_000_000
        bar_end_ns = (bar_close_ms + 1) * 1_000_000
        self._bus.publish(
            f"clock.bar_close.{timeframe.value}",
            BarCloseEvent(
                timeframe=timeframe,
                symbol=symbol,
                bar_start_ns=bar_start_ns,
                bar_end_ns=bar_end_ns,
            ),
        )
        self._bus.publish(
            f"md.candle.{timeframe.value}.{symbol}",
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                bar_start_ns=bar_start_ns,
                bar_end_ns=bar_end_ns,
                open=_to_float(raw_kline.get("o")),
                high=_to_float(raw_kline.get("h")),
                low=_to_float(raw_kline.get("l")),
                close=_to_float(raw_kline.get("c")),
                volume=_to_float(raw_kline.get("v")),
                trade_count=int(raw_kline.get("n", 0) or 0),
            ),
        )

    def _stream_url(self) -> str:
        streams: list[str] = []
        for symbol in self._config.symbols:
            exch_symbol = _to_binance_symbol(symbol)
            if exch_symbol is None:
                continue
            token = exch_symbol.lower()
            streams.extend(
                [
                    f"{token}@aggTrade",
                    f"{token}@markPrice@{self._config.mark_price_update_s}s",
                    f"{token}@depth{self._config.depth_levels}@{self._config.depth_update_ms}ms",
                    f"{token}@kline_4h",
                    f"{token}@kline_1d",
                ]
            )
        joined = "/".join(streams)
        return f"{BINANCE_FSTREAM_WS}?streams={joined}"

    def _meta(self, symbol: str, event_time_ns: int) -> EventMeta:
        current = self._seq.get(symbol, 0) + 1
        self._seq[symbol] = current
        return EventMeta(
            event_time_ns=event_time_ns,
            ingest_time_ns=time.time_ns(),
            venue="BINANCE",
            symbol=symbol,
            stream_seq=current,
        )


def _timeframe_from_stream(value: str) -> Timeframe | None:
    token = value.lower()
    if token == "4h":
        return Timeframe.TF_4H
    if token == "1d":
        return Timeframe.TF_1D
    return None


def _symbol_from_stream(stream: str) -> str | None:
    token = stream.split("@", 1)[0].upper()
    return _from_binance_symbol(token)


def _from_binance_symbol(symbol: str) -> str | None:
    mapping = {
        "BTCUSDT": "BTCUSDT_PERP",
        "ETHUSDT": "ETHUSDT_PERP",
    }
    return mapping.get(symbol)


def _to_binance_symbol(symbol: str) -> str | None:
    mapping = {
        "BTCUSDT_PERP": "BTCUSDT",
        "ETHUSDT_PERP": "ETHUSDT",
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
        return json.loads(payload)
    except json.JSONDecodeError:
        return None
