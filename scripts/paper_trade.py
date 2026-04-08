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
    from zenResearch.domain import IndicatorPack, MarketState, TargetPosition, Timeframe

    print(f"{'=' * 60}")
    print("zenQuant Paper Trading")
    print(f"{'=' * 60}")
    print(f"Symbol: {symbol}")
    print(f"Duration: {duration_hours}h")
    print(f"Start: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    # Initialize pipeline components directly (not ZenQuantPipeline which includes SyntheticIngestor)
    from pathlib import Path as _Path

    from zenResearch.data import CotDataStore, CotStoreConfig, MacroDataStore, MacroDataStoreConfig
    from zenResearch.execution import ExecutionKernel, SimulatedGateway
    from zenResearch.indicator import IndicatorEngine, IndicatorEngineConfig, IndicatorStore
    from zenResearch.indicator.hf_features import HFFeatureStore, HFFeatureStoreConfig
    from zenResearch.infra import InMemoryJetStream, StreamConfig
    from zenResearch.market import BarClockService, SnapshotBuilder
    from zenResearch.plugins.profile import build_from_profile
    from zenResearch.risk import RiskOverlay
    from zenResearch.state import StateEngine
    from zenResearch.strategy import ARCStrategyEngine

    bus = InMemoryJetStream()
    bus.add_stream(StreamConfig(name="STREAM_MARKET_DATA", subjects=["md.>"]))
    bus.add_stream(StreamConfig(name="STREAM_CLOCK", subjects=["clock.>"]))
    bus.add_stream(StreamConfig(name="STREAM_INDICATORS", subjects=["indicator.>"]))
    bus.add_stream(StreamConfig(name="STREAM_INTENTS", subjects=["intent.>"]))
    bus.add_stream(StreamConfig(name="STREAM_EXEC", subjects=["exec.>"]))

    # Load data stores
    macro_store = MacroDataStore(MacroDataStoreConfig())
    macro_store.load()
    cot_store = CotDataStore(CotStoreConfig())
    cot_store.load()
    hf_dir = _Path("data/raw/binance/klines") / symbol / "5m"
    hf_store = HFFeatureStore(HFFeatureStoreConfig(data_dir=hf_dir))
    hf_store.load()

    # Wire components (ordering matters: snapshot → clock → indicator → risk → strategy → state → execution)
    SnapshotBuilder(bus=bus)
    BarClockService(bus=bus)

    indicator_store = IndicatorStore()
    IndicatorEngine(
        bus=bus,
        store=indicator_store,
        config=IndicatorEngineConfig(expected_symbols=(f"{symbol}_PERP",)),
        macro_store=macro_store if macro_store.loaded else None,
        hf_store=hf_store if hf_store.loaded else None,
        cot_store=cot_store if cot_store.loaded else None,
    )

    risk_overlay = RiskOverlay(bus=bus)

    profile_path = _Path("config/profiles/default.toml")
    plugin_registry = None
    if profile_path.exists():
        _, plugin_registry = build_from_profile(profile_path, plugin_root=_Path("zenResearch/plugins"))
    ARCStrategyEngine(bus=bus, plugin_registry=plugin_registry)
    StateEngine(bus=bus)
    ExecutionKernel(bus=bus, gateway=SimulatedGateway(), risk_overlay=risk_overlay)

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
        print(
            f"  [INDICATOR] {payload.symbol} 4H close={payload.values.get('close', '?')} "
            f"momentum_z={payload.values.get('multi_momentum_z', 0):.2f} "
            f"cvd_slope={payload.values.get('cvd_slope', 0):.4f}"
        )

    def on_state(subject: str, payload: object) -> None:
        if not isinstance(payload, MarketState):
            return
        if payload.timeframe != Timeframe.TF_4H:
            return
        print(
            f"  [STATE] regime={payload.regime} dir={payload.directional_state} "
            f"vol={payload.volatility_state} confidence={payload.overall_confidence():.2f}"
        )

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
        print(
            f"  [TARGET] {direction} qty={payload.target_qty:.4f} "
            f"lev={payload.target_leverage:.2f} reduce_only={payload.reduce_only}"
        )
        print(f"           reasons: {', '.join(payload.reason_codes[:5])}")

    bus.subscribe("indicator.>", on_indicator)
    bus.subscribe("state.market.>", on_state)
    bus.subscribe("intent.target_position.final.>", on_target)

    # Connect to Binance live data
    from zenResearch.ingestor.binance_ws import BinancePerpStreamClient, BinancePerpStreamConfig

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

    # Start embedded dashboard server (shares bus with paper trading)
    dashboard_task = None
    try:
        import uvicorn

        from zenResearch.app.server import create_app

        app = create_app(bus=bus)

        async def _run_dashboard():
            config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
            server = uvicorn.Server(config)
            await server.serve()

        dashboard_task = asyncio.ensure_future(_run_dashboard())
        print("Dashboard: http://localhost:8000/static/ (embedded)")
    except Exception as e:
        print(f"Dashboard: disabled ({e})")

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
                print(
                    f"  [STATUS] Elapsed: {elapsed / 3600:.1f}h, "
                    f"Decisions: {len(decisions)}, "
                    f"Remaining: {remaining / 3600:.1f}h"
                )

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
    print(f"Duration: {(time.time() - start_time) / 3600:.1f}h")
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
