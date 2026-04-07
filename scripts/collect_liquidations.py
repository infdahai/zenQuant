#!/usr/bin/env python3
"""Collect Binance force-order events and write aligned 4H liquidation buckets."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.data.liquidation_pipeline import (
    LiquidationEvent,
    aggregate_liquidation_events,
    aligned_bucket_bounds,
    write_aggregated_liquidations,
    write_raw_liquidations,
)

BINANCE_WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
DEFAULT_OUTPUT = Path("data/raw/binance/liquidations")


class LiquidationCollector:
    """Collect websocket liquidation events and flush closed 4H buckets."""

    def __init__(self, output_dir: Path, symbol_filter: str | None = None, flush_interval: int = 3600) -> None:
        self._output_dir = output_dir
        self._symbol_filter = symbol_filter.upper() if symbol_filter else None
        self._flush_interval = flush_interval
        self._pending_events: list[LiquidationEvent] = []
        self._shutdown = False
        self.total_events = 0
        self.total_flushes = 0
        self.total_buckets = 0

    async def run(self) -> None:
        try:
            import websockets
        except ImportError:
            print("ERROR: websockets package required. Install with: uv add websockets")
            sys.exit(1)

        while not self._shutdown:
            try:
                async with websockets.connect(BINANCE_WS_URL, ping_interval=30) as ws:
                    print(f"[{_ts()}] Connected to {BINANCE_WS_URL}")
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
            except Exception as exc:
                if self._shutdown:
                    break
                print(f"[{_ts()}] WS error: {exc}; reconnecting in 5s")
                await asyncio.sleep(5)

        self._flush(force_all=True)

    def request_shutdown(self) -> None:
        self._shutdown = True

    def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
            order = data.get("o", {})
            symbol = str(order.get("s", "")).upper()
            if not symbol:
                return
            if self._symbol_filter and symbol != self._symbol_filter:
                return

            price = float(order.get("ap") or order.get("p") or 0.0)
            qty = float(order.get("z") or order.get("q") or 0.0)
            trade_time_ms = int(order.get("T") or data.get("E") or 0)
            side = str(order.get("S", "")).upper()
            if price <= 0 or qty <= 0 or trade_time_ms <= 0 or side not in {"BUY", "SELL"}:
                return

            event = LiquidationEvent(
                ts_ns=trade_time_ms * 1_000_000,
                symbol=symbol,
                side=side,
                price=price,
                qty=qty,
                usd_value=round(price * qty, 2),
                source="binance_ws_forceOrder",
                trade_time_ms=trade_time_ms,
            )
            self._pending_events.append(event)
            self.total_events += 1
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            print(f"[{_ts()}] Parse error: {exc}")

    async def _flush_loop(self) -> None:
        while not self._shutdown:
            await asyncio.sleep(self._flush_interval)
            self._flush(force_all=False)

    def _flush(self, *, force_all: bool) -> None:
        if not self._pending_events:
            return

        now_ns = datetime.now(UTC).timestamp() * 1_000_000_000
        cutoff_ns = int(now_ns) if force_all else aligned_bucket_bounds(int(now_ns))[0]
        ready_events = [event for event in self._pending_events if force_all or event.ts_ns < cutoff_ns]
        if not ready_events:
            return

        self._pending_events = [event for event in self._pending_events if event not in ready_events]
        raw_paths = write_raw_liquidations(self._output_dir, ready_events)
        buckets = aggregate_liquidation_events(ready_events)
        bucket_paths = write_aggregated_liquidations(self._output_dir, buckets)

        self.total_flushes += 1
        self.total_buckets += len(buckets)
        total_liq = sum(bucket.total_liq_usd for bucket in buckets)
        print(
            f"[{_ts()}] FLUSH #{self.total_flushes}: events={len(ready_events)} "
            f"buckets={len(buckets)} total_liq=${total_liq:,.0f} "
            f"raw_files={len(raw_paths)} agg_files={len(bucket_paths)}"
        )


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Binance forced liquidations")
    parser.add_argument("--symbol", default=None, help="Optional symbol filter, e.g. BTCUSDT")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--flush-interval", type=int, default=3600, help="Seconds between flush attempts")
    args = parser.parse_args()

    collector = LiquidationCollector(
        output_dir=args.output,
        symbol_filter=args.symbol,
        flush_interval=args.flush_interval,
    )

    loop = asyncio.new_event_loop()

    def _shutdown(signum: int, _: object) -> None:
        print(f"\n[{_ts()}] Signal {signum} received; shutting down")
        collector.request_shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(collector.run())
    finally:
        loop.close()

    print(
        f"[{_ts()}] Done: events={collector.total_events} "
        f"flushes={collector.total_flushes} buckets={collector.total_buckets}"
    )


if __name__ == "__main__":
    main()
