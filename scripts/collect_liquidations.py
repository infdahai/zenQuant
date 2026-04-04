#!/usr/bin/env python3
"""Binance forced liquidation collector via WebSocket.

Connects to wss://fstream.binance.com/ws/!forceOrder@arr and stores
liquidation events aggregated into 4H buckets as Parquet files.

Free, no API key needed. Covers ~50-60% of crypto futures volume.

Usage:
    uv run python scripts/collect_liquidations.py
    uv run python scripts/collect_liquidations.py --symbol BTCUSDT --flush-interval 3600
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

BINANCE_WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
DEFAULT_OUTPUT = Path("data/raw/binance/liquidations")
DEFAULT_FLUSH_INTERVAL = 4 * 3600  # 4 hours


@dataclass(slots=True)
class LiquidationEvent:
    """Single forced liquidation event from Binance."""

    ts_ns: int
    symbol: str
    side: str  # "BUY" or "SELL" (liquidated side)
    price: float
    qty: float
    trade_time_ms: int


@dataclass(slots=True)
class LiquidationBucket:
    """Aggregated liquidation stats for one 4H period."""

    bucket_start_ns: int
    bucket_end_ns: int
    symbol: str
    long_liq_count: int
    short_liq_count: int
    long_liq_usd: float
    short_liq_usd: float
    total_liq_usd: float
    max_single_liq_usd: float
    liq_imbalance: float  # (long - short) / total, [-1, 1]


class LiquidationCollector:
    """Collect liquidation events and flush aggregated buckets to Parquet."""

    def __init__(
        self,
        output_dir: Path,
        symbol_filter: str | None = None,
        flush_interval: int = DEFAULT_FLUSH_INTERVAL,
    ) -> None:
        self._output_dir = output_dir
        self._symbol_filter = symbol_filter.upper() if symbol_filter else None
        self._flush_interval = flush_interval
        self._events: list[LiquidationEvent] = []
        self._total_events = 0
        self._total_flushes = 0
        self._shutdown = False

    async def run(self) -> None:
        """Main loop: connect, collect, flush."""
        try:
            import websockets
        except ImportError:
            print("ERROR: websockets package required. Install with: uv add websockets")
            sys.exit(1)

        print(f"Connecting to {BINANCE_WS_URL}")

        while not self._shutdown:
            try:
                async with websockets.connect(BINANCE_WS_URL, ping_interval=30) as ws:
                    print(f"  [{_ts()}] Connected. Listening for liquidations...")
                    flush_task = asyncio.create_task(self._flush_loop())
                    try:
                        async for message in ws:
                            if self._shutdown:
                                break
                            self._handle_message(message)
                    finally:
                        flush_task.cancel()
                        try:
                            await flush_task
                        except asyncio.CancelledError:
                            pass
            except Exception as e:
                if self._shutdown:
                    break
                print(f"  [{_ts()}] WS error: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)

        # Final flush
        if self._events:
            self._flush()

    def _handle_message(self, raw: str) -> None:
        """Parse one forceOrder message."""
        try:
            data = json.loads(raw)
            order = data.get("o", {})
            symbol = order.get("s", "")

            if self._symbol_filter and symbol != self._symbol_filter:
                return

            price = float(order.get("p", 0))
            qty = float(order.get("q", 0))
            trade_time_ms = int(order.get("T", 0))

            event = LiquidationEvent(
                ts_ns=time.time_ns(),
                symbol=symbol,
                side=order.get("S", ""),
                price=price,
                qty=qty,
                trade_time_ms=trade_time_ms,
            )
            self._events.append(event)
            self._total_events += 1

            usd_value = price * qty
            if usd_value > 100_000:
                print(
                    f"  [{_ts()}] LARGE LIQ: {symbol} {event.side} "
                    f"${usd_value:,.0f} @ {price:,.2f}"
                )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"  [{_ts()}] Parse error: {e}")

    async def _flush_loop(self) -> None:
        """Periodically flush accumulated events to disk."""
        while not self._shutdown:
            await asyncio.sleep(self._flush_interval)
            if self._events:
                self._flush()

    def _flush(self) -> None:
        """Aggregate events into buckets and write Parquet."""
        if not self._events:
            return

        events = self._events
        self._events = []

        # Group by symbol
        by_symbol: dict[str, list[LiquidationEvent]] = {}
        for e in events:
            by_symbol.setdefault(e.symbol, []).append(e)

        # Write raw events
        self._write_raw_events(events)

        # Write aggregated buckets
        buckets = self._aggregate(by_symbol)
        self._write_buckets(buckets)

        self._total_flushes += 1
        total_usd = sum(b.total_liq_usd for b in buckets)
        print(
            f"  [{_ts()}] FLUSH #{self._total_flushes}: "
            f"{len(events)} events, {len(buckets)} buckets, "
            f"${total_usd:,.0f} total liquidated"
        )

    def _aggregate(
        self, by_symbol: dict[str, list[LiquidationEvent]]
    ) -> list[LiquidationBucket]:
        """Aggregate events into one bucket per symbol."""
        buckets: list[LiquidationBucket] = []

        for symbol, events in by_symbol.items():
            if not events:
                continue

            long_count = 0
            short_count = 0
            long_usd = 0.0
            short_usd = 0.0
            max_single = 0.0
            min_ts = events[0].ts_ns
            max_ts = events[0].ts_ns

            for e in events:
                usd = e.price * e.qty
                max_single = max(max_single, usd)
                min_ts = min(min_ts, e.ts_ns)
                max_ts = max(max_ts, e.ts_ns)

                if e.side == "SELL":
                    # SELL liquidation = long position liquidated
                    long_count += 1
                    long_usd += usd
                else:
                    short_count += 1
                    short_usd += usd

            total_usd = long_usd + short_usd
            imbalance = (long_usd - short_usd) / total_usd if total_usd > 0 else 0.0

            buckets.append(
                LiquidationBucket(
                    bucket_start_ns=min_ts,
                    bucket_end_ns=max_ts,
                    symbol=symbol,
                    long_liq_count=long_count,
                    short_liq_count=short_count,
                    long_liq_usd=round(long_usd, 2),
                    short_liq_usd=round(short_usd, 2),
                    total_liq_usd=round(total_usd, 2),
                    max_single_liq_usd=round(max_single, 2),
                    liq_imbalance=round(imbalance, 4),
                )
            )

        return buckets

    def _write_raw_events(self, events: list[LiquidationEvent]) -> None:
        """Write raw events to Parquet for future research."""
        if not events:
            return

        stamp = datetime.now(UTC)
        partition = stamp.strftime("%Y-%m")
        raw_dir = self._output_dir / "raw" / partition
        raw_dir.mkdir(parents=True, exist_ok=True)

        filename = f"liquidations_{stamp.strftime('%Y%m%dT%H%M%SZ')}.parquet"
        records = [asdict(e) for e in events]
        table = pa.Table.from_pylist(records)
        pq.write_table(table, raw_dir / filename)

    def _write_buckets(self, buckets: list[LiquidationBucket]) -> None:
        """Write aggregated buckets to Parquet."""
        if not buckets:
            return

        stamp = datetime.now(UTC)
        partition = stamp.strftime("%Y-%m")
        agg_dir = self._output_dir / "aggregated" / partition
        agg_dir.mkdir(parents=True, exist_ok=True)

        filename = f"liq_agg_{stamp.strftime('%Y%m%dT%H%M%SZ')}.parquet"
        records = [asdict(b) for b in buckets]
        table = pa.Table.from_pylist(records)
        pq.write_table(table, agg_dir / filename)

    def request_shutdown(self) -> None:
        self._shutdown = True


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Binance forced liquidations")
    parser.add_argument("--symbol", default=None, help="Filter to one symbol (e.g. BTCUSDT)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--flush-interval", type=int, default=DEFAULT_FLUSH_INTERVAL)
    args = parser.parse_args()

    print("Binance Liquidation Collector")
    print(f"Symbol filter: {args.symbol or 'ALL'}")
    print(f"Output: {args.output}")
    print(f"Flush interval: {args.flush_interval}s ({args.flush_interval / 3600:.1f}h)")
    print("=" * 50)

    collector = LiquidationCollector(
        output_dir=args.output,
        symbol_filter=args.symbol,
        flush_interval=args.flush_interval,
    )

    loop = asyncio.new_event_loop()

    def _shutdown(signum: int, _: object) -> None:
        print(f"\nSignal {signum} received, shutting down...")
        collector.request_shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(collector.run())
    finally:
        loop.close()

    print(f"Total events: {collector._total_events}, Flushes: {collector._total_flushes}")


if __name__ == "__main__":
    main()
