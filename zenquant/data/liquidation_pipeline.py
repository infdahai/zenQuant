"""Liquidation ingestion helpers shared by collection, backfill, and research."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from zenquant.historical import HistoricalDatasetLayout, read_parquet_rows, write_parquet_rows

HOUR_NS = 3600 * 1_000_000_000
FOUR_HOUR_NS = 4 * HOUR_NS
BINANCE_FORCE_ORDERS_URL = "https://fapi.binance.com/fapi/v1/allForceOrders"


@dataclass(slots=True, frozen=True)
class LiquidationEvent:
    ts_ns: int
    symbol: str
    side: str
    price: float
    qty: float
    usd_value: float
    source: str
    trade_time_ms: int


@dataclass(slots=True, frozen=True)
class LiquidationBucket:
    bucket_start_ns: int
    bucket_end_ns: int
    symbol: str
    long_liq_count: int
    short_liq_count: int
    long_liq_usd: float
    short_liq_usd: float
    total_liq_usd: float
    max_single_liq_usd: float
    liq_imbalance: float
    source: str


@dataclass(slots=True, frozen=True)
class LiquidationProbe:
    available: bool
    blocked_by: str | None
    detail: str


@dataclass(slots=True, frozen=True)
class LiquidationReadiness:
    total_bars: int
    nonzero_bars: int
    nonstale_bars: int
    extreme_bars: int
    max_total_liq_usd: float
    coverage_ratio: float
    data_ready: bool
    promotion_ready: bool
    blocked_by: str | None
    detail: str


def aligned_bucket_bounds(ts_ns: int) -> tuple[int, int]:
    """Align an event timestamp to the enclosing UTC 4H bucket."""
    start_ns = (ts_ns // FOUR_HOUR_NS) * FOUR_HOUR_NS
    return start_ns, start_ns + FOUR_HOUR_NS


def aggregate_liquidation_events(events: Iterable[LiquidationEvent]) -> list[LiquidationBucket]:
    """Group raw liquidation events into deterministic 4H symbol buckets."""
    grouped: dict[tuple[str, int], dict[str, float | int | str]] = {}
    for event in events:
        if event.ts_ns <= 0 or not event.symbol:
            continue
        bucket_start_ns, bucket_end_ns = aligned_bucket_bounds(event.ts_ns)
        key = (event.symbol.upper(), bucket_start_ns)
        row = grouped.setdefault(
            key,
            {
                "symbol": event.symbol.upper(),
                "bucket_start_ns": bucket_start_ns,
                "bucket_end_ns": bucket_end_ns,
                "long_liq_count": 0,
                "short_liq_count": 0,
                "long_liq_usd": 0.0,
                "short_liq_usd": 0.0,
                "max_single_liq_usd": 0.0,
            },
        )
        usd_value = max(float(event.usd_value), 0.0)
        row["max_single_liq_usd"] = max(float(row["max_single_liq_usd"]), usd_value)
        if event.side.upper() == "SELL":
            row["long_liq_count"] = int(row["long_liq_count"]) + 1
            row["long_liq_usd"] = float(row["long_liq_usd"]) + usd_value
        elif event.side.upper() == "BUY":
            row["short_liq_count"] = int(row["short_liq_count"]) + 1
            row["short_liq_usd"] = float(row["short_liq_usd"]) + usd_value

    buckets: list[LiquidationBucket] = []
    for (_, _bucket_start_ns), row in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        long_liq_usd = round(float(row["long_liq_usd"]), 2)
        short_liq_usd = round(float(row["short_liq_usd"]), 2)
        total_liq_usd = round(long_liq_usd + short_liq_usd, 2)
        liq_imbalance = 0.0
        if total_liq_usd > 0:
            liq_imbalance = round((long_liq_usd - short_liq_usd) / total_liq_usd, 4)
        buckets.append(
            LiquidationBucket(
                bucket_start_ns=int(row["bucket_start_ns"]),
                bucket_end_ns=int(row["bucket_end_ns"]),
                symbol=str(row["symbol"]),
                long_liq_count=int(row["long_liq_count"]),
                short_liq_count=int(row["short_liq_count"]),
                long_liq_usd=long_liq_usd,
                short_liq_usd=short_liq_usd,
                total_liq_usd=total_liq_usd,
                max_single_liq_usd=round(float(row["max_single_liq_usd"]), 2),
                liq_imbalance=liq_imbalance,
                source=str(event.source),
            )
        )
    return buckets


def merge_liquidation_buckets(buckets: Iterable[LiquidationBucket]) -> list[LiquidationBucket]:
    """Merge multiple source buckets into one deterministic symbol/bucket series."""
    grouped: dict[tuple[str, int, int], dict[str, float | int | str | set[str]]] = {}
    for bucket in buckets:
        key = (bucket.symbol.upper(), bucket.bucket_start_ns, bucket.bucket_end_ns)
        row = grouped.setdefault(
            key,
            {
                "symbol": bucket.symbol.upper(),
                "bucket_start_ns": bucket.bucket_start_ns,
                "bucket_end_ns": bucket.bucket_end_ns,
                "long_liq_count": 0,
                "short_liq_count": 0,
                "long_liq_usd": 0.0,
                "short_liq_usd": 0.0,
                "max_single_liq_usd": 0.0,
                "sources": set(),
            },
        )
        row["long_liq_count"] = int(row["long_liq_count"]) + int(bucket.long_liq_count)
        row["short_liq_count"] = int(row["short_liq_count"]) + int(bucket.short_liq_count)
        row["long_liq_usd"] = float(row["long_liq_usd"]) + float(bucket.long_liq_usd)
        row["short_liq_usd"] = float(row["short_liq_usd"]) + float(bucket.short_liq_usd)
        row["max_single_liq_usd"] = max(float(row["max_single_liq_usd"]), float(bucket.max_single_liq_usd))
        sources = row["sources"]
        assert isinstance(sources, set)
        sources.add(bucket.source)

    merged: list[LiquidationBucket] = []
    for _, row in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        long_liq_usd = round(float(row["long_liq_usd"]), 2)
        short_liq_usd = round(float(row["short_liq_usd"]), 2)
        total_liq_usd = round(long_liq_usd + short_liq_usd, 2)
        liq_imbalance = 0.0
        if total_liq_usd > 0:
            liq_imbalance = round((long_liq_usd - short_liq_usd) / total_liq_usd, 4)
        merged.append(
            LiquidationBucket(
                bucket_start_ns=int(row["bucket_start_ns"]),
                bucket_end_ns=int(row["bucket_end_ns"]),
                symbol=str(row["symbol"]),
                long_liq_count=int(row["long_liq_count"]),
                short_liq_count=int(row["short_liq_count"]),
                long_liq_usd=long_liq_usd,
                short_liq_usd=short_liq_usd,
                total_liq_usd=total_liq_usd,
                max_single_liq_usd=round(float(row["max_single_liq_usd"]), 2),
                liq_imbalance=liq_imbalance,
                source="merged",
            )
        )
    return merged


def write_raw_liquidations(root_dir: Path, events: Iterable[LiquidationEvent]) -> list[Path]:
    """Write raw liquidation events partitioned by UTC month."""
    events_by_partition: dict[str, list[LiquidationEvent]] = {}
    for event in sorted(events, key=lambda item: (item.symbol, item.ts_ns, item.trade_time_ms)):
        partition = datetime.fromtimestamp(event.ts_ns / 1_000_000_000, tz=UTC).strftime("%Y-%m")
        events_by_partition.setdefault(partition, []).append(event)

    written: list[Path] = []
    for partition, rows in sorted(events_by_partition.items()):
        path = root_dir / "raw" / partition / "liquidations.parquet"
        write_parquet_rows(path, rows)
        written.append(path)
    return written


def write_aggregated_liquidations(root_dir: Path, buckets: Iterable[LiquidationBucket]) -> list[Path]:
    """Write aggregated liquidation buckets partitioned by symbol and UTC month."""
    grouped: dict[tuple[str, str], list[LiquidationBucket]] = {}
    for bucket in sorted(buckets, key=lambda item: (item.symbol, item.bucket_start_ns)):
        partition = datetime.fromtimestamp(bucket.bucket_start_ns / 1_000_000_000, tz=UTC).strftime("%Y-%m")
        grouped.setdefault((bucket.symbol, partition), []).append(bucket)

    written: list[Path] = []
    for (symbol, partition), rows in sorted(grouped.items()):
        path = root_dir / "aggregated" / symbol / partition / "liquidations.parquet"
        write_parquet_rows(path, rows)
        written.append(path)
    return written


def liquidation_readiness(
    root_dir: Path,
    symbol: str,
    *,
    liquidation_dir: Path | None = None,
) -> LiquidationReadiness:
    """Assess whether liquidation history is present enough to enable research."""
    layout = HistoricalDatasetLayout(root_dir=root_dir)
    kline_rows = read_parquet_rows(
        str(layout.kline_path(symbol, "4h", "*.parquet")),
        order_by="open_time_ns ASC",
    )
    total_bars = len(kline_rows)
    aggregated_root = liquidation_dir if liquidation_dir is not None else root_dir / "liquidations" / "aggregated"
    liquidations_glob = str(aggregated_root / symbol / "**" / "*.parquet")
    liq_rows = read_parquet_rows(liquidations_glob, order_by="bucket_start_ns ASC")
    if not liq_rows:
        detail = "no aggregated liquidation history found under liquidations/aggregated"
        return LiquidationReadiness(
            total_bars=total_bars,
            nonzero_bars=0,
            nonstale_bars=0,
            extreme_bars=0,
            max_total_liq_usd=0.0,
            coverage_ratio=0.0,
            data_ready=False,
            promotion_ready=False,
            blocked_by="missing_liquidation_history",
            detail=detail,
        )

    nonzero_rows = [row for row in liq_rows if float(row.get("total_liq_usd", 0.0)) > 0.0]
    nonzero_bars = len(nonzero_rows)
    max_total_liq_usd = max((float(row.get("total_liq_usd", 0.0)) for row in nonzero_rows), default=0.0)
    extreme_threshold = max(250_000.0, max_total_liq_usd * 0.5) if max_total_liq_usd > 0 else 250_000.0
    extreme_bars = sum(1 for row in nonzero_rows if float(row.get("total_liq_usd", 0.0)) >= extreme_threshold)
    nonstale_bars = nonzero_bars
    data_ready = nonzero_bars > 0
    coverage_ratio = round((nonzero_bars / total_bars), 4) if total_bars else 0.0
    promotion_ready = data_ready and coverage_ratio >= 0.10 and extreme_bars >= 3
    blocked_by = None
    if not data_ready:
        blocked_by = "missing_liquidation_history"
    elif not promotion_ready:
        blocked_by = "not_promotion_ready"
    detail = (
        f"aggregated liquidation history loaded: nonzero_bars={nonzero_bars} max_total_liq_usd={max_total_liq_usd:,.2f}"
    )
    return LiquidationReadiness(
        total_bars=total_bars,
        nonzero_bars=nonzero_bars,
        nonstale_bars=nonstale_bars,
        extreme_bars=extreme_bars,
        max_total_liq_usd=max_total_liq_usd,
        coverage_ratio=coverage_ratio,
        data_ready=data_ready,
        promotion_ready=promotion_ready,
        blocked_by=blocked_by,
        detail=detail,
    )


def probe_binance_force_orders_support(symbol: str) -> LiquidationProbe:
    """Check whether Binance force-order history is currently queryable."""
    query = urlencode({"symbol": symbol.upper(), "limit": 1})
    request = Request(f"{BINANCE_FORCE_ORDERS_URL}?{query}", headers={"User-Agent": "zenquant/1.0"})
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, list):
            detail = f"binance force-order history available; sample_count={len(payload)}"
            return LiquidationProbe(available=True, blocked_by=None, detail=detail)
        return LiquidationProbe(
            available=False,
            blocked_by="binance_force_orders_unexpected_response",
            detail=f"unexpected response type: {type(payload).__name__}",
        )
    except HTTPError as exc:
        message = _extract_http_error_message(exc)
        blocked_by = "binance_force_orders_http_error"
        if "maintenance" in message.lower():
            blocked_by = "binance_force_orders_maintenance"
        detail = f"http_{exc.code}: {message}"
        return LiquidationProbe(available=False, blocked_by=blocked_by, detail=detail)
    except URLError as exc:
        return LiquidationProbe(
            available=False,
            blocked_by="binance_force_orders_unreachable",
            detail=str(exc.reason),
        )


def iter_day_ranges(days: int) -> list[tuple[datetime, datetime]]:
    """Return newest-last UTC day ranges suitable for Binance backfills."""
    now = datetime.now(UTC)
    end_dt = datetime(now.year, now.month, now.day, tzinfo=UTC)
    ranges: list[tuple[datetime, datetime]] = []
    for offset in range(days, 0, -1):
        start = end_dt - timedelta(days=offset)
        ranges.append((start, start + timedelta(days=1)))
    return ranges


def fetch_force_orders_history(
    *,
    symbol: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int = 1000,
) -> list[LiquidationEvent]:
    """Fetch Binance force-order history for a bounded UTC window."""
    params = {
        "symbol": symbol.upper(),
        "startTime": start_time_ms,
        "endTime": end_time_ms,
        "limit": limit,
    }
    request = Request(
        f"{BINANCE_FORCE_ORDERS_URL}?{urlencode(params)}",
        headers={"User-Agent": "zenquant/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    events: list[LiquidationEvent] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        side = str(item.get("side") or item.get("S") or "").upper()
        symbol_value = str(item.get("symbol") or item.get("s") or symbol).upper()
        price = float(item.get("avgPrice") or item.get("ap") or item.get("price") or item.get("p") or 0.0)
        qty = float(item.get("executedQty") or item.get("z") or item.get("origQty") or item.get("q") or 0.0)
        trade_time_ms = int(item.get("time") or item.get("T") or item.get("updateTime") or 0)
        if side not in {"BUY", "SELL"} or price <= 0 or qty <= 0 or trade_time_ms <= 0:
            continue
        events.append(
            LiquidationEvent(
                ts_ns=trade_time_ms * 1_000_000,
                symbol=symbol_value,
                side=side,
                price=price,
                qty=qty,
                usd_value=round(price * qty, 2),
                source="binance_force_orders_history",
                trade_time_ms=trade_time_ms,
            )
        )
    return events


def _extract_http_error_message(exc: HTTPError) -> str:
    try:
        payload = exc.read().decode("utf-8")
        data = json.loads(payload)
        if isinstance(data, dict) and data.get("msg"):
            return str(data["msg"])
        return payload
    except Exception:
        return exc.reason if isinstance(exc.reason, str) else str(exc.reason)
