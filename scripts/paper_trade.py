#!/usr/bin/env python3
"""Lightweight paper trading — connect to Binance, run strategy, log decisions.

No PostgreSQL required. Logs decisions to JSON lines file for comparison
with historical backtest decisions.

Usage:
    uv run python scripts/paper_trade.py
    uv run python scripts/paper_trade.py --symbol BTCUSDT --duration 24h
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def run_paper_trading(symbol: str = "BTCUSDT", duration_hours: float = 24.0) -> None:
    from zenquant.app.pipeline import ZenQuantPipeline
    from zenquant.domain import IndicatorPack, MarketState, TargetPosition, Timeframe

    print(f"{'=' * 60}")
    print("zenQuant Paper Trading")
    print(f"{'=' * 60}")
    print(f"Symbol: {symbol}")
    print(f"Duration: {duration_hours}h")
    print(f"Start: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    # Initialize pipeline
    pipeline = ZenQuantPipeline()
    bus = pipeline.bus

    # Decision log
    log_dir = Path("logs/paper_trading")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"decisions_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.jsonl"

    decisions: list[dict] = []

    # Subscribe to key events
    def on_indicator(subject: str, payload: object) -> None:
        if not isinstance(payload, IndicatorPack):
            return
        if payload.timeframe != Timeframe.TF_4H:
            return
        print(f"  [INDICATOR] {payload.symbol} 4H close={payload.values.get('close', '?')} "
              f"momentum_z={payload.values.get('multi_momentum_z', 0):.2f} "
              f"cvd_slope={payload.values.get('cvd_slope', 0):.4f}")

    def on_state(subject: str, payload: object) -> None:
        if not isinstance(payload, MarketState):
            return
        if payload.timeframe != Timeframe.TF_4H:
            return
        print(f"  [STATE] regime={payload.regime} dir={payload.directional_state} "
              f"vol={payload.volatility_state} confidence={payload.overall_confidence():.2f}")

    def on_target(subject: str, payload: object) -> None:
        if not isinstance(payload, TargetPosition):
            return
        ts = datetime.fromtimestamp(payload.cutoff_time_ns / 1e9, tz=UTC)
        decision = {
            "ts": ts.isoformat(),
            "symbol": payload.symbol,
            "target_qty": payload.target_qty,
            "target_leverage": payload.target_leverage,
            "reduce_only": payload.reduce_only,
            "reason_codes": payload.reason_codes[:10],
        }
        decisions.append(decision)

        # Write to log file
        with open(log_file, "a") as f:
            f.write(json.dumps(decision) + "\n")

        direction = "LONG" if payload.target_qty > 0 else "SHORT" if payload.target_qty < 0 else "FLAT"
        print(f"  [TARGET] {direction} qty={payload.target_qty:.4f} "
              f"lev={payload.target_leverage:.2f} reduce_only={payload.reduce_only}")
        print(f"           reasons: {', '.join(payload.reason_codes[:5])}")

    bus.subscribe("indicator.>", on_indicator)
    bus.subscribe("state.market.>", on_state)
    bus.subscribe("intent.target_position.final.>", on_target)

    # Connect to Binance live data
    from zenquant.ingestor.binance_ws import BinancePerpStreamClient, BinancePerpStreamConfig

    ws_config = BinancePerpStreamConfig(
        symbols=(f"{symbol}_PERP",),
        bootstrap_4h_bars=90,
    )
    stream = BinancePerpStreamClient(bus=bus, config=ws_config)

    # Start Deribit options collector (background, every 4H)
    options_task = None
    try:
        from pathlib import Path as _P

        from scripts.collect_deribit_options import collect_once

        async def _options_loop():
            while True:
                try:
                    collect_once("BTC", _P("data/raw/deribit_options"))
                except Exception as e:
                    print(f"  [OPTIONS] Error: {e}")
                await asyncio.sleep(4 * 3600)  # Every 4H

        print("Deribit options collector: enabled (every 4H)")
    except ImportError:
        print("Deribit options collector: disabled (import error)")


    print("Connecting to Binance WebSocket...")
    print(f"Logging decisions to: {log_file}")
    print("Press Ctrl+C to stop")
    print()

    # Handle graceful shutdown
    stop_event = asyncio.Event()

    def shutdown(*_):
        print("\nShutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start streaming
    start_time = time.time()
    end_time = start_time + duration_hours * 3600

    try:
        await stream.start()
        # Start background options collector
        if options_task is None:
            options_task = asyncio.ensure_future(_options_loop())

        while not stop_event.is_set():
            await asyncio.sleep(10)

            elapsed = time.time() - start_time
            remaining = end_time - time.time()

            if remaining <= 0:
                print(f"\nDuration reached ({duration_hours}h). Stopping.")
                break

            # Periodic status
            if int(elapsed) % 300 < 10:  # Every 5 minutes
                print(f"  [STATUS] Elapsed: {elapsed/3600:.1f}h, "
                      f"Decisions: {len(decisions)}, "
                      f"Remaining: {remaining/3600:.1f}h")

    except asyncio.CancelledError:
        pass
    finally:
        if options_task:
            options_task.cancel()
        await stream.stop()

    # Summary
    print(f"\n{'=' * 60}")
    print("Paper Trading Summary")
    print(f"{'=' * 60}")
    print(f"Duration: {(time.time() - start_time)/3600:.1f}h")
    print(f"Decisions logged: {len(decisions)}")
    print(f"Log file: {log_file}")

    if decisions:
        longs = sum(1 for d in decisions if d["target_qty"] > 0)
        shorts = sum(1 for d in decisions if d["target_qty"] < 0)
        flats = sum(1 for d in decisions if d["target_qty"] == 0)
        print(f"Long: {longs}, Short: {shorts}, Flat: {flats}")


def main() -> None:
    parser = argparse.ArgumentParser(description="zenQuant paper trading")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--duration", default="24h", help="Duration (e.g., 4h, 24h, 7d)")
    args = parser.parse_args()

    # Parse duration
    dur = args.duration.lower()
    if dur.endswith("d"):
        hours = float(dur[:-1]) * 24
    elif dur.endswith("h"):
        hours = float(dur[:-1])
    else:
        hours = float(dur)

    asyncio.run(run_paper_trading(symbol=args.symbol, duration_hours=hours))


if __name__ == "__main__":
    main()
