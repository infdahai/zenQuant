"""Realtime Binance futures market-data adapter for live paper trading.

The client normalizes Binance websocket and REST payloads into the repository's
internal event contracts so the downstream pipeline does not depend on
exchange-specific message shapes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from zenquant.domain import (
    BarCloseEvent,
    Candle,
    EventMeta,
    FundingUpdate,
    MarketSnapshot,
    MarkPriceUpdate,
    OpenInterestUpdate,
    OrderBookLevel,
    OrderBookSnapshot,
    Timeframe,
    TradeTick,
)
from zenquant.infra import EventBus

logger = logging.getLogger(__name__)

BINANCE_FSTREAM_WS = "wss://fstream.binance.com/stream"
BINANCE_FSTREAM_REST = "https://fapi.binance.com"


@dataclass(slots=True)
class BinancePerpStreamConfig:
    """Subscription and polling settings for Binance USD-M perpetual feeds.

    The config mirrors the higher-level realtime ingestor settings, but lives
    at the exchange adapter boundary where websocket, polling, and bootstrap
    behavior are implemented.
    """

    symbols: tuple[str, ...] = ("BTCUSDT_PERP",)
    depth_levels: int = 10
    depth_update_ms: int = 1000
    mark_price_update_s: int = 1
    open_interest_poll_interval_s: float = 30.0
    request_timeout_s: float = 6.0
    reconnect_backoff_s: float = 3.0
    bootstrap_enabled: bool = True
    bootstrap_4h_bars: int = 90
    bootstrap_1d_bars: int = 90
    bootstrap_funding_points: int = 500
    bootstrap_open_interest_points: int = 500
    bootstrap_required: bool = False


class BinancePerpStreamClient:
    """Publish Binance futures market data onto the internal event bus."""

    def __init__(
        self,
        bus: EventBus,
        config: BinancePerpStreamConfig | None = None,
    ) -> None:
        self._bus = bus
        self._config = config or BinancePerpStreamConfig()
        self._seq: dict[str, int] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._ws: ClientConnection | None = None

    async def start(self) -> None:
        """Start websocket streaming and open-interest polling tasks."""
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run_market_stream()),
            asyncio.create_task(self._run_open_interest_poll()),
        ]

    async def stop(self) -> None:
        """Stop background tasks and close the websocket cleanly."""
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

    async def bootstrap_history(self) -> None:
        """Warm recent 4h/1d indicator context before live streaming starts."""
        if not self._config.bootstrap_enabled:
            return
        for symbol in self._config.symbols:
            try:
                await asyncio.to_thread(self._bootstrap_symbol, symbol)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._config.bootstrap_required:
                    raise
                # Fail open by default: the live stream is still useful even if
                # historical warmup is temporarily unavailable.
                logger.exception("bootstrap warmup failed for %s; continuing with live stream", symbol)

    async def _run_market_stream(self) -> None:
        """Maintain the combined websocket stream and reconnect on failure."""
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
        """Poll open interest because Binance does not stream it on the main socket."""
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

    def _bootstrap_symbol(self, symbol: str) -> None:
        """Fetch recent closed bars and auxiliary data for live warmup."""
        exch_symbol = _to_binance_symbol(symbol)
        if exch_symbol is None:
            return

        logger.info("bootstrapping live history for %s", symbol)
        daily_rows = _fetch_recent_klines(
            exch_symbol,
            timeframe="1d",
            limit=self._config.bootstrap_1d_bars,
            timeout_s=self._config.request_timeout_s,
        )
        four_hour_rows = _fetch_recent_klines(
            exch_symbol,
            timeframe="4h",
            limit=self._config.bootstrap_4h_bars,
            timeout_s=self._config.request_timeout_s,
        )
        funding_rows = _fetch_recent_funding(
            exch_symbol,
            limit=self._config.bootstrap_funding_points,
            timeout_s=self._config.request_timeout_s,
        )
        open_interest_rows = _fetch_recent_open_interest(
            exch_symbol,
            timeframe="4h",
            limit=self._config.bootstrap_open_interest_points,
            timeout_s=self._config.request_timeout_s,
        )

        candles: list[dict[str, Any]] = []
        for timeframe, rows in (("1d", daily_rows), ("4h", four_hour_rows)):
            for row in rows:
                row["event_timeframe"] = timeframe
                candles.append(row)
        candles.sort(
            key=lambda item: (int(item["close_time_ns"]), _bootstrap_timeframe_priority(str(item["event_timeframe"])))
        )

        funding_index = -1
        open_interest_index = -1
        for row in candles:
            cutoff_ns = int(row["close_time_ns"])
            while (
                funding_index + 1 < len(funding_rows)
                and int(funding_rows[funding_index + 1]["funding_time_ns"]) <= cutoff_ns
            ):
                funding_index += 1
            while (
                open_interest_index + 1 < len(open_interest_rows)
                and int(open_interest_rows[open_interest_index + 1]["ts_ns"]) <= cutoff_ns
            ):
                open_interest_index += 1

            funding_row = funding_rows[funding_index] if funding_index >= 0 else None
            oi_row = open_interest_rows[open_interest_index] if open_interest_index >= 0 else None
            self._publish_bootstrap_bar(symbol=symbol, row=row, funding_row=funding_row, oi_row=oi_row)

        logger.info(
            "bootstrapped %s with %d historical bars (%d 1d, %d 4h)",
            symbol,
            len(candles),
            len(daily_rows),
            len(four_hour_rows),
        )

    def _publish_bootstrap_bar(
        self,
        *,
        symbol: str,
        row: dict[str, Any],
        funding_row: dict[str, Any] | None,
        oi_row: dict[str, Any] | None,
    ) -> None:
        """Publish a warmup candle/snapshot pair without replaying old orders."""
        timeframe = _timeframe_from_stream(str(row["event_timeframe"]))
        if timeframe is None:
            return

        cutoff_ns = int(row["close_time_ns"])
        last_price = float(row["close"])
        meta = self._meta(symbol=symbol, event_time_ns=cutoff_ns)
        self._bus.publish(
            f"md.mark.BINANCE.{symbol}",
            MarkPriceUpdate(meta=meta, mark_price=last_price, index_price=last_price),
        )
        self._bus.publish(
            f"md.candle.{timeframe.value}.{symbol}",
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                bar_start_ns=int(row["open_time_ns"]),
                bar_end_ns=cutoff_ns,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=last_price,
                volume=float(row["volume"]),
                trade_count=int(row["trade_count"]),
            ),
        )
        self._bus.publish(
            f"md.snapshot.{timeframe.value}.{symbol}",
            MarketSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                cutoff_time_ns=cutoff_ns,
                last_price=last_price,
                bid_depth=max(float(row["volume"]), 1.0),
                ask_depth=max(float(row["volume"]), 1.0),
                book_age_ms=0,
                venue_count=1,
                cvd_delta=_proxy_cvd_delta(row),
                funding_oi_weighted=float(funding_row["funding_rate"]) if funding_row else None,
                oi_coverage=1.0 if oi_row else 0.0,
                staleness_ms=_bootstrap_staleness_ms(cutoff_ns, funding_row, oi_row),
                quality_flags=["OK"] if oi_row else ["MISSING_OI"],
            ),
        )

    def _handle_market_message(self, message: str) -> None:
        """Route a normalized Binance payload to the correct event publisher."""
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
        """Publish only exchange-confirmed bar closes into the decision pipeline."""
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
        """Build the combined-stream URL for the configured symbols."""
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
        """Attach deterministic per-symbol stream sequence numbers to events."""
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
    """Map Binance kline interval tokens into the domain timeframe enum."""
    token = value.lower()
    if token == "4h":
        return Timeframe.TF_4H
    if token == "1d":
        return Timeframe.TF_1D
    return None


def _symbol_from_stream(stream: str) -> str | None:
    """Recover the internal symbol from a combined-stream name."""
    token = stream.split("@", 1)[0].upper()
    return _from_binance_symbol(token)


def _from_binance_symbol(symbol: str) -> str | None:
    """Convert exchange symbols into the repo's perpetual-symbol naming."""
    mapping = {
        "BTCUSDT": "BTCUSDT_PERP",
        "ETHUSDT": "ETHUSDT_PERP",
    }
    return mapping.get(symbol)


def _to_binance_symbol(symbol: str) -> str | None:
    """Convert internal perpetual symbols into Binance market symbols."""
    mapping = {
        "BTCUSDT_PERP": "BTCUSDT",
        "ETHUSDT_PERP": "ETHUSDT",
    }
    return mapping.get(symbol)


def _parse_depth_side(raw: object, levels: int) -> list[OrderBookLevel]:
    """Parse one side of a Binance depth payload into typed levels."""
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
    """Parse float-like payload values with zero-on-failure semantics."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fetch_json_sync(url: str, timeout_s: float) -> object | None:
    """Small sync helper used by the background open-interest poller."""
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


def _fetch_recent_klines(symbol: str, *, timeframe: str, limit: int, timeout_s: float) -> list[dict[str, Any]]:
    """Fetch recent closed REST klines for live warmup."""
    if limit <= 0:
        return []
    query = urllib.parse.urlencode({"symbol": symbol, "interval": timeframe, "limit": str(limit)})
    payload = _fetch_json_sync(f"{BINANCE_FSTREAM_REST}/fapi/v1/klines?{query}", timeout_s)
    if not isinstance(payload, list):
        return []

    now_ms = int(time.time() * 1_000)
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, list) or len(item) < 9:
            continue
        open_ms = int(item[0] or 0)
        close_ms = int(item[6] or 0)
        if open_ms <= 0 or close_ms <= 0:
            continue
        if close_ms >= now_ms:
            # Skip the still-forming bar. Live decisions remain bar-close only.
            continue
        rows.append(
            {
                "open_time_ns": open_ms * 1_000_000,
                "close_time_ns": (close_ms + 1) * 1_000_000,
                "open": _to_float(item[1]),
                "high": _to_float(item[2]),
                "low": _to_float(item[3]),
                "close": _to_float(item[4]),
                "volume": _to_float(item[5]),
                "trade_count": int(item[8] or 0),
            }
        )
    rows.sort(key=lambda item: int(item["close_time_ns"]))
    return rows


def _fetch_recent_funding(symbol: str, *, limit: int, timeout_s: float) -> list[dict[str, Any]]:
    """Fetch recent funding history for snapshot warmup."""
    if limit <= 0:
        return []
    query = urllib.parse.urlencode({"symbol": symbol, "limit": str(limit)})
    payload = _fetch_json_sync(f"{BINANCE_FSTREAM_REST}/fapi/v1/fundingRate?{query}", timeout_s)
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        funding_time_ms = int(item.get("fundingTime", 0) or 0)
        if funding_time_ms <= 0:
            continue
        rows.append(
            {
                "funding_time_ns": funding_time_ms * 1_000_000,
                "funding_rate": _to_float(item.get("fundingRate")),
            }
        )
    rows.sort(key=lambda item: int(item["funding_time_ns"]))
    return rows


def _fetch_recent_open_interest(
    symbol: str,
    *,
    timeframe: str,
    limit: int,
    timeout_s: float,
) -> list[dict[str, Any]]:
    """Fetch recent open-interest history for snapshot warmup."""
    if limit <= 0:
        return []
    query = urllib.parse.urlencode({"symbol": symbol, "period": timeframe, "limit": str(limit)})
    payload = _fetch_json_sync(f"{BINANCE_FSTREAM_REST}/futures/data/openInterestHist?{query}", timeout_s)
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        ts_ms = int(item.get("timestamp", 0) or 0)
        if ts_ms <= 0:
            continue
        rows.append(
            {
                "ts_ns": ts_ms * 1_000_000,
                "open_interest": _to_float(item.get("sumOpenInterest")),
            }
        )
    rows.sort(key=lambda item: int(item["ts_ns"]))
    return rows


def _proxy_cvd_delta(row: dict[str, Any]) -> float:
    """Approximate bar CVD when warmup only has candle history."""
    volume = float(row["volume"])
    open_px = float(row["open"])
    close_px = float(row["close"])
    if close_px > open_px:
        return volume
    if close_px < open_px:
        return -volume
    return 0.0


def _bootstrap_staleness_ms(
    cutoff_ns: int,
    funding_row: dict[str, Any] | None,
    oi_row: dict[str, Any] | None,
) -> int:
    """Measure auxiliary-data age for warmup snapshots."""
    ages_ms: list[int] = []
    if funding_row is not None:
        ages_ms.append(max(0, int((cutoff_ns - int(funding_row["funding_time_ns"])) / 1_000_000)))
    if oi_row is not None:
        ages_ms.append(max(0, int((cutoff_ns - int(oi_row["ts_ns"])) / 1_000_000)))
    return max(ages_ms) if ages_ms else 0


def _bootstrap_timeframe_priority(value: str) -> int:
    """Keep 1d bootstrap bars ahead of 4h bars sharing the same cutoff."""
    return 0 if value == Timeframe.TF_1D.value else 1
