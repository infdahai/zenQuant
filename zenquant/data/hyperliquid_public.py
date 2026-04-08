"""Minimal Hyperliquid public-data helpers for proxy liquidation research."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from zenquant.data.liquidation_pipeline import LiquidationBucket, LiquidationEvent, aggregate_liquidation_events
from zenquant.historical import write_parquet_rows

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
FOUR_HOUR_MS = 4 * 3600 * 1000


@dataclass(slots=True, frozen=True)
class HyperliquidTrade:
    symbol: str
    side: str
    price: float
    size: float
    ts_ms: int
    trade_id: int
    notional_usd: float
    hash: str | None


@dataclass(slots=True, frozen=True)
class HyperliquidCandle:
    symbol: str
    interval: str
    start_ms: int
    end_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int


@dataclass(slots=True, frozen=True)
class HyperliquidFunding:
    symbol: str
    ts_ms: int
    funding_rate: float
    premium: float


@dataclass(slots=True, frozen=True)
class HyperliquidBackfillStatus:
    blocked_by: str | None
    detail: str


def _post_info(payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        HYPERLIQUID_INFO_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "zenquant/1.0"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_recent_trades(coin: str) -> list[HyperliquidTrade]:
    payload = _post_info({"type": "recentTrades", "coin": coin.upper()})
    rows: list[HyperliquidTrade] = []
    for row in payload or []:
        price = float(row["px"])
        size = float(row["sz"])
        rows.append(
            HyperliquidTrade(
                symbol=f"{coin.upper()}USDT",
                side="BUY" if str(row["side"]).upper() == "B" else "SELL",
                price=price,
                size=size,
                ts_ms=int(row["time"]),
                trade_id=int(row["tid"]),
                notional_usd=round(price * size, 2),
                hash=row.get("hash"),
            )
        )
    return rows


def fetch_candle_snapshot(coin: str, interval: str, start_ms: int, end_ms: int) -> list[HyperliquidCandle]:
    payload = _post_info(
        {
            "type": "candleSnapshot",
            "req": {"coin": coin.upper(), "interval": interval, "startTime": start_ms, "endTime": end_ms},
        }
    )
    candles: list[HyperliquidCandle] = []
    for row in payload or []:
        candles.append(
            HyperliquidCandle(
                symbol=f"{coin.upper()}USDT",
                interval=str(row["i"]),
                start_ms=int(row["t"]),
                end_ms=int(row["T"]) + 1,
                open=float(row["o"]),
                high=float(row["h"]),
                low=float(row["l"]),
                close=float(row["c"]),
                volume=float(row["v"]),
                trade_count=int(row["n"]),
            )
        )
    return candles


def fetch_funding_history(coin: str, start_ms: int, end_ms: int) -> list[HyperliquidFunding]:
    payload = _post_info(
        {
            "type": "fundingHistory",
            "coin": coin.upper(),
            "startTime": start_ms,
            "endTime": end_ms,
        }
    )
    funding_rows: list[HyperliquidFunding] = []
    for row in payload or []:
        funding_rows.append(
            HyperliquidFunding(
                symbol=f"{coin.upper()}USDT",
                ts_ms=int(row["time"]),
                funding_rate=float(row["fundingRate"]),
                premium=float(row.get("premium", 0.0)),
            )
        )
    return funding_rows


def recent_trades_to_buckets(
    trades: list[HyperliquidTrade],
    *,
    min_trade_notional_usd: float = 50_000.0,
) -> list[LiquidationBucket]:
    events = [
        LiquidationEvent(
            ts_ns=trade.ts_ms * 1_000_000,
            symbol=trade.symbol,
            side=trade.side,
            price=trade.price,
            qty=trade.size,
            usd_value=trade.notional_usd,
            source="hyperliquid_public",
            trade_time_ms=trade.ts_ms,
        )
        for trade in trades
        if trade.notional_usd >= min_trade_notional_usd
    ]
    return aggregate_liquidation_events(events)


def build_hyperliquid_proxy_history(
    *,
    coin: str,
    start_ms: int,
    end_ms: int,
) -> tuple[list[HyperliquidCandle], list[HyperliquidFunding], list[LiquidationBucket], HyperliquidBackfillStatus]:
    candles = fetch_candle_snapshot(coin, "4h", start_ms, end_ms)
    funding = fetch_funding_history(coin, start_ms, end_ms)

    events: list[LiquidationEvent] = []
    for candle in candles:
        direction = "SELL" if candle.close <= candle.open else "BUY"
        move_ratio = abs(candle.close - candle.open) / max(abs(candle.open), 1.0)
        funding_bias = 1.0
        matching_funding = [row for row in funding if candle.start_ms <= row.ts_ms < candle.end_ms]
        if matching_funding:
            funding_bias += min(2.0, sum(abs(row.funding_rate) + abs(row.premium) for row in matching_funding) * 500.0)
        proxy_notional = max(0.0, candle.volume * candle.close * move_ratio * funding_bias * 0.01)
        events.append(
            LiquidationEvent(
                ts_ns=candle.start_ms * 1_000_000,
                symbol=candle.symbol,
                side=direction,
                price=candle.close,
                qty=max(candle.volume * move_ratio * 0.01, 0.0),
                usd_value=round(proxy_notional, 2),
                source="hyperliquid_public",
                trade_time_ms=candle.start_ms,
            )
        )
    buckets = aggregate_liquidation_events(events)
    blocked_by = None
    detail = "hyperliquid public proxy history available"
    if (end_ms - start_ms) < 30 * 24 * 3600 * 1000:
        blocked_by = "hyperliquid_public_history_limit"
        detail = "hyperliquid public API only provides bounded recent history; proxy sample is short"
    return candles, funding, buckets, HyperliquidBackfillStatus(blocked_by=blocked_by, detail=detail)


def write_hyperliquid_raw(
    root: Path,
    *,
    trades: list[HyperliquidTrade] | None = None,
    candles: list[HyperliquidCandle] | None = None,
    funding: list[HyperliquidFunding] | None = None,
) -> list[Path]:
    paths: list[Path] = []
    if trades:
        trade_dir = root / "raw" / "recent_trades"
        trade_dir.mkdir(parents=True, exist_ok=True)
        path = trade_dir / "recent_trades.parquet"
        write_parquet_rows(path, [asdict(row) for row in trades])
        paths.append(path)
    if candles:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for candle in candles:
            partition = datetime.fromtimestamp(candle.start_ms / 1000, tz=UTC).strftime("%Y-%m")
            grouped.setdefault(partition, []).append(asdict(candle))
        for partition, rows in sorted(grouped.items()):
            path = root / "raw" / "candles" / partition / "candles.parquet"
            write_parquet_rows(path, rows)
            paths.append(path)
    if funding:
        grouped_funding: dict[str, list[dict[str, Any]]] = {}
        for row in funding:
            partition = datetime.fromtimestamp(row.ts_ms / 1000, tz=UTC).strftime("%Y-%m")
            grouped_funding.setdefault(partition, []).append(asdict(row))
        for partition, rows in sorted(grouped_funding.items()):
            path = root / "raw" / "funding" / partition / "funding.parquet"
            write_parquet_rows(path, rows)
            paths.append(path)
    return paths
