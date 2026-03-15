"""FastAPI server and runtime wiring for demo and live paper-trading modes."""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    _FASTAPI_AVAILABLE = True
except ModuleNotFoundError as exc:
    FastAPI = Any  # type: ignore[misc,assignment]
    HTTPException = Exception  # type: ignore[misc,assignment]
    Query = Any  # type: ignore[misc,assignment]
    WebSocket = Any  # type: ignore[misc,assignment]
    WebSocketDisconnect = Exception  # type: ignore[misc,assignment]
    FileResponse = Any  # type: ignore[misc,assignment]
    JSONResponse = Any  # type: ignore[misc,assignment]
    StaticFiles = Any  # type: ignore[misc,assignment]
    _FASTAPI_AVAILABLE = False
    _FASTAPI_IMPORT_ERROR = exc

from zenquant.app.live_state import DashboardStateStore
from zenquant.domain import (
    Candle,
    DirectionalBias,
    EventMeta,
    IndicatorPack,
    IntentReason,
    MarkPriceUpdate,
    OpenPositionState,
    RiskOverlayDecision,
    RiskSnapshot,
    TargetPosition,
    Timeframe,
)
from zenquant.domain.serde import ns_to_iso, to_jsonable
from zenquant.execution import ExecutionGateway, ExecutionKernel, SimulatedGateway
from zenquant.indicator import IndicatorEngine, IndicatorEngineConfig, IndicatorStore
from zenquant.ingestor import RealTimeIngestor, RealTimeIngestorConfig, SyntheticIngestor, SyntheticIngestorConfig
from zenquant.infra import InMemoryJetStream, OnlineStateStore, OnlineStateStoreConfig, StreamConfig
from zenquant.market import BarClockService, SnapshotBuilder, SnapshotBuilderConfig
from zenquant.risk import RiskOverlay
from zenquant.strategy import ARCStrategyEngine


@dataclass(slots=True)
class _IndicatorConnection:
    websocket: WebSocket
    symbol: str
    timeframe: Timeframe


@dataclass(slots=True)
class _StateConnection:
    websocket: WebSocket
    symbol: str
    tf: str


class ZenQuantWebRuntime:
    """Own the web runtime, event bus, and dashboard-facing subscriptions.

    ``demo`` mode uses the synthetic ingestor and local bar clock. ``live``
    mode consumes confirmed Binance bar-close events directly and uses the
    simulated execution gateway for paper trading.
    """

    def __init__(
        self,
        runtime_mode: str = "demo",
        tick_interval_s: float = 0.04,
        warmup_steps: int = 300,
    ) -> None:
        self._runtime_mode = runtime_mode
        self._tick_interval_s = tick_interval_s
        self._warmup_steps = max(0, warmup_steps)
        self._state_connections: list[_StateConnection] = []
        self._indicator_connections: list[_IndicatorConnection] = []
        self._tick_task: asyncio.Task[None] | None = None
        self._live_start_task: asyncio.Task[None] | None = None
        self._started = False
        self._live_symbols = ("BTCUSDT_PERP",)

        self.bus = InMemoryJetStream()
        self.bus.add_stream(StreamConfig(name="STREAM_MARKET_DATA", subjects=["md.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_CLOCK", subjects=["clock.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INDICATORS", subjects=["indicator.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INTENTS", subjects=["intent.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_EXEC", subjects=["exec.>"]))

        self.state = DashboardStateStore()
        self.state.on_runtime_status({"mode": self._runtime_mode})
        self.indicator_store = IndicatorStore()

        if self._runtime_mode == "live":
            # Live mode trusts exchange-confirmed bar closes, so it does not
            # synthesize candles from ticks locally.
            snapshot_config = SnapshotBuilderConfig(expected_venues=1)
            indicator_config = IndicatorEngineConfig(expected_symbols=("BTCUSDT_PERP",))
            self.snapshot_builder = SnapshotBuilder(bus=self.bus, config=snapshot_config)
            self.bar_clock = None
            self.indicator_engine = IndicatorEngine(
                bus=self.bus,
                store=self.indicator_store,
                config=indicator_config,
            )
            self.execution_gateway = SimulatedGateway()
            self.ingestor = RealTimeIngestor(
                bus=self.bus,
                config=RealTimeIngestorConfig(
                    symbols=self._live_symbols,
                    # Local development can skip warmup to shorten restart
                    # cycles, while production keeps bootstrap enabled.
                    bootstrap_enabled=os.getenv("ZENQUANT_SKIP_BOOTSTRAP", "").lower()
                    not in {"1", "true", "yes"},
                ),
            )
            self._event_iterator = None
        else:
            # Demo mode keeps the original synthetic path for local iteration.
            self.snapshot_builder = SnapshotBuilder(bus=self.bus)
            self.bar_clock = BarClockService(bus=self.bus)
            self.indicator_engine = IndicatorEngine(bus=self.bus, store=self.indicator_store)
            self.execution_gateway = ExecutionGateway()
            self.ingestor = SyntheticIngestor(bus=self.bus, config=SyntheticIngestorConfig(seed=11))
            self._event_iterator = self.ingestor.iter_events(
                steps=None,
                start_step=self._warmup_steps,
            )
        self.risk_overlay: RiskOverlay | None = None
        self.strategy_engine: ARCStrategyEngine | None = None
        self.execution_kernel: ExecutionKernel | None = None
        self.online_state: OnlineStateStore | None = None
        if self._runtime_mode != "live":
            self._initialize_trading_chain()

        self.bus.subscribe("md.candle.>", self._on_candle)
        self.bus.subscribe("md.mark.>", self._on_mark_price)
        self.bus.subscribe("indicator.>", self._on_indicator)
        self.bus.subscribe("strategy.bias.1d.>", self._on_directional_bias)
        self.bus.subscribe("strategy.position_state.>", self._on_position_state)
        self.bus.subscribe("risk.snapshot", self._on_risk_snapshot)
        self.bus.subscribe("risk.overlay.decision", self._on_risk_decision)
        self.bus.subscribe("intent.reason.>", self._on_reason)
        self.bus.subscribe("intent.target_position.final.>", self._on_target)
        self.bus.subscribe("exec.order.report", self._on_report)

    async def start(self) -> None:
        """Start background tasks and ingest market data for the selected mode."""
        if self._started:
            return
        self._started = True
        if self._runtime_mode == "live":
            self._initialize_trading_chain()
            self._start_online_state()
            self._live_start_task = asyncio.create_task(self._start_live_runtime())
            return
        self._start_online_state()
        self._tick_task = asyncio.create_task(self._run_tick_loop())

    async def stop(self) -> None:
        """Stop background tasks and freeze database status for the dashboard."""
        if not self._started:
            return
        self._started = False
        if self._runtime_mode == "live":
            if self._live_start_task is not None:
                self._live_start_task.cancel()
                try:
                    await self._live_start_task
                except asyncio.CancelledError:
                    pass
                self._live_start_task = None
            await self.ingestor.stop()
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None
        if self.online_state is not None:
            self.online_state.stop()
        self.state.on_runtime_status({"database": self._online_state_snapshot()})

    def prime_history(self) -> None:
        """Warm the demo-mode pipeline before the UI starts polling it."""
        if self._runtime_mode != "demo" or self._warmup_steps <= 0:
            return
        assert isinstance(self.ingestor, SyntheticIngestor)
        for subject, payload in self.ingestor.iter_events(steps=self._warmup_steps, start_step=0):
            self.bus.publish(subject, payload)

    def snapshot(self, symbol: str = "BTCUSDT_PERP", tf: str = "4h") -> dict[str, Any]:
        """Return the latest dashboard snapshot for one symbol/timeframe."""
        self.state.on_runtime_status({"database": self._online_state_snapshot()})
        return self.state.snapshot(symbol=symbol, timeframe=tf)

    async def connect_state(self, websocket: WebSocket, symbol: str, tf: str) -> None:
        """Attach a websocket client to dashboard-state updates."""
        await websocket.accept()
        self._state_connections.append(_StateConnection(websocket=websocket, symbol=symbol, tf=tf))
        try:
            await websocket.send_json(self.snapshot(symbol=symbol, tf=tf))
        except Exception:
            self.disconnect_state(websocket)
            return

    async def connect_indicators(self, websocket: WebSocket, symbol: str, tf: Timeframe) -> None:
        """Attach a websocket client to indicator-only updates."""
        await websocket.accept()
        conn = _IndicatorConnection(websocket=websocket, symbol=symbol, timeframe=tf)
        self._indicator_connections.append(conn)
        latest = self.indicator_store.latest(symbol=symbol, timeframe=tf)
        if latest is not None:
            try:
                await websocket.send_json(_indicator_payload(latest))
            except Exception:
                self.disconnect_indicators(websocket)
                return

    def disconnect_state(self, websocket: WebSocket) -> None:
        self._state_connections = [
            item for item in self._state_connections if item.websocket is not websocket
        ]

    def disconnect_indicators(self, websocket: WebSocket) -> None:
        self._indicator_connections = [
            item for item in self._indicator_connections if item.websocket is not websocket
        ]

    async def _run_tick_loop(self) -> None:
        """Drive demo-mode synthetic events at a fixed wall-clock cadence."""
        assert self._event_iterator is not None
        while self._started:
            subject, payload = next(self._event_iterator)
            self.bus.publish(subject, payload)
            await asyncio.sleep(self._tick_interval_s)

    async def _broadcast_state(self) -> None:
        """Push the latest dashboard snapshot to all connected state clients."""
        if not self._state_connections:
            return
        stale: list[WebSocket] = []
        for connection in self._state_connections:
            try:
                await connection.websocket.send_json(
                    self.snapshot(symbol=connection.symbol, tf=connection.tf)
                )
            except Exception:
                stale.append(connection.websocket)
        for websocket in stale:
            self.disconnect_state(websocket)

    async def _broadcast_indicator(self, pack: IndicatorPack) -> None:
        """Push one new indicator pack to matching indicator clients."""
        if not self._indicator_connections:
            return
        payload = _indicator_payload(pack)
        stale: list[WebSocket] = []
        for connection in self._indicator_connections:
            if connection.symbol != pack.symbol or connection.timeframe is not pack.timeframe:
                continue
            try:
                await connection.websocket.send_json(payload)
            except Exception:
                stale.append(connection.websocket)
        for websocket in stale:
            self.disconnect_indicators(websocket)

    def _schedule_state_broadcast(self) -> None:
        """Schedule a state push without blocking the event handler thread."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._broadcast_state())

    def _schedule_indicator_broadcast(self, pack: IndicatorPack) -> None:
        """Schedule indicator and state pushes after a new pack is emitted."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._broadcast_indicator(pack))
        loop.create_task(self._broadcast_state())

    def _initialize_trading_chain(self) -> None:
        """Attach risk, strategy, execution, and persistence subscribers once."""
        if self.risk_overlay is not None:
            return
        self.risk_overlay = RiskOverlay(bus=self.bus)
        self.strategy_engine = ARCStrategyEngine(bus=self.bus)
        self.execution_kernel = ExecutionKernel(
            bus=self.bus,
            gateway=self.execution_gateway,
            risk_overlay=self.risk_overlay,
        )
        self.online_state = OnlineStateStore(
            bus=self.bus,
            config=OnlineStateStoreConfig(dsn=os.getenv("ZENQUANT_PG_DSN")),
        )

    def _start_online_state(self) -> None:
        """Start optional PostgreSQL persistence after the trading chain exists."""
        if self.online_state is not None:
            self.online_state.start()
        self.state.on_runtime_status({"database": self._online_state_snapshot()})

    async def _start_live_runtime(self) -> None:
        """Warm live context in the background so the UI can report startup state."""
        bootstrap_history = getattr(self.ingestor, "bootstrap_history", None)
        if callable(bootstrap_history):
            self.state.on_runtime_status(
                {
                    # ``bootstrap_phase`` is intentionally string-based so the
                    # frontend can render startup state without decoding enums.
                    "bootstrap_phase": "bootstrapping",
                    "bootstrap_detail": "Warming 4H and 1D context before live feed",
                    "bootstrap_error": "",
                }
            )
            self._schedule_state_broadcast()
            try:
                await bootstrap_history()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.on_runtime_status(
                    {
                        "bootstrap_phase": "failed",
                        "bootstrap_detail": "Bootstrap failed; continuing with cold live feed",
                        "bootstrap_error": str(exc),
                    }
                )
            else:
                self.state.on_runtime_status(
                    {
                        "bootstrap_phase": "ready",
                        "bootstrap_detail": "Historical context warmed",
                        "bootstrap_error": "",
                    }
                )
        else:
            self.state.on_runtime_status(
                {
                    "bootstrap_phase": "skipped",
                    "bootstrap_detail": "No bootstrap adapter configured",
                    "bootstrap_error": "",
                }
            )
        if not self._started:
            return
        self._prime_live_signal()
        await self.ingestor.start()
        self.state.on_runtime_status({"bootstrap_live_started": True})
        self._schedule_state_broadcast()

    def _online_state_snapshot(self) -> dict[str, Any]:
        """Return database status even before the writer is initialized."""
        if self.online_state is None:
            return {
                "enabled": False,
                "connected": False,
                "writes": 0,
                "dropped": 0,
                "queue_depth": 0,
                "last_error": "",
            }
        return self.online_state.snapshot()

    def _prime_live_signal(self) -> None:
        """Turn warm indicator history into one current live decision state.

        Startup bootstrap warms indicator context but intentionally does not
        replay old orders. After the trading chain is attached, publish the
        latest closed-bar context once so the process begins from the current
        signal instead of waiting for the next 4h/1d close.
        """
        assert self.risk_overlay is not None
        assert self.strategy_engine is not None

        for symbol in self._live_symbols:
            latest_daily = self.indicator_store.latest(symbol=symbol, timeframe=Timeframe.TF_1D)
            latest_exec = self.indicator_store.latest(symbol=symbol, timeframe=Timeframe.TF_4H)
            latest_pack = latest_exec or latest_daily
            if latest_pack is None:
                continue

            last_price = float(latest_pack.values.get("last_price", 0.0) or 0.0)
            if last_price > 0:
                meta = EventMeta(
                    event_time_ns=latest_pack.cutoff_time_ns,
                    ingest_time_ns=time.time_ns(),
                    venue="BINANCE",
                    symbol=symbol,
                    stream_seq=0,
                )
                self.bus.publish(
                    f"md.mark.BINANCE.{symbol}",
                    MarkPriceUpdate(meta=meta, mark_price=last_price, index_price=last_price),
                )

            if latest_daily is not None:
                self.risk_overlay.prime_indicator(latest_daily)
                self.strategy_engine.prime_indicator(latest_daily)
            if latest_exec is not None:
                self.risk_overlay.prime_indicator(latest_exec)
                self.strategy_engine.prime_indicator(latest_exec)

    def _on_candle(self, _: str, payload: object) -> None:
        if isinstance(payload, Candle):
            self.state.on_candle(payload)
            self._schedule_state_broadcast()

    def _on_mark_price(self, _: str, payload: object) -> None:
        if not isinstance(payload, MarkPriceUpdate):
            return
        self.state.on_mark_price(
            symbol=payload.meta.symbol,
            ts_ns=payload.meta.event_time_ns,
            price=payload.mark_price,
        )
        self.state.on_runtime_status({"database": self._online_state_snapshot()})
        self._schedule_state_broadcast()

    def _on_indicator(self, _: str, payload: object) -> None:
        if not isinstance(payload, IndicatorPack):
            return
        self.state.on_indicator(payload)
        self.state.on_runtime_status({"database": self._online_state_snapshot()})
        self._schedule_indicator_broadcast(payload)

    def _on_risk_snapshot(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskSnapshot):
            self.state.on_risk_snapshot(payload)
            self.state.on_runtime_status({"database": self._online_state_snapshot()})
            self._schedule_state_broadcast()

    def _on_directional_bias(self, _: str, payload: object) -> None:
        if isinstance(payload, DirectionalBias):
            self.state.on_directional_bias(payload)
            self._schedule_state_broadcast()

    def _on_position_state(self, subject: str, payload: object) -> None:
        parts = subject.split(".")
        if len(parts) < 4:
            return
        timeframe = parts[2]
        symbol = parts[3]
        if payload is None or isinstance(payload, OpenPositionState):
            self.state.on_position_state(symbol=symbol, timeframe=timeframe, payload=payload)
            self._schedule_state_broadcast()

    def _on_risk_decision(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskOverlayDecision):
            self.state.on_risk_decision(payload)
            self._schedule_state_broadcast()

    def _on_reason(self, _: str, payload: object) -> None:
        if isinstance(payload, IntentReason):
            self.state.on_reason(payload)
            self._schedule_state_broadcast()

    def _on_target(self, _: str, payload: object) -> None:
        if isinstance(payload, TargetPosition):
            self.state.on_target_position(payload)
            self._schedule_state_broadcast()

    def _on_report(self, _: str, payload: object) -> None:
        from zenquant.domain import ExecutionReport

        if isinstance(payload, ExecutionReport):
            self.state.on_exec_report(to_jsonable(payload))
            self._schedule_state_broadcast()


def create_app(runtime_mode: str = "demo") -> FastAPI:
    """Create the FastAPI application used by ``zenquant-web``.

    Example:
        app = create_app(runtime_mode="live")
    """
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError(
            "fastapi is required for the web console. Install dependencies with "
            "`python3 -m pip install -e .` and retry."
        ) from _FASTAPI_IMPORT_ERROR

    runtime = ZenQuantWebRuntime(runtime_mode=runtime_mode)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime.prime_history()
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    title = "ZenQuant 4.0 Live Sim" if runtime_mode == "live" else "ZenQuant 4.0 Demo"
    app = FastAPI(title=title, version="0.3.0", lifespan=lifespan)
    web_root = Path(__file__).resolve().parents[1] / "web"

    app.mount("/static", StaticFiles(directory=str(web_root)), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": runtime_mode,
            "database": runtime._online_state_snapshot(),
        }

    @app.get("/api/state")
    async def get_state(symbol: str = "BTCUSDT_PERP", tf: str = "4h") -> JSONResponse:
        return JSONResponse(runtime.snapshot(symbol=symbol, tf=tf))

    @app.get("/api/indicators/latest")
    async def get_indicators_latest(symbol: str = "BTCUSDT_PERP", tf: str = "4h") -> JSONResponse:
        timeframe = _parse_timeframe(tf)
        pack = runtime.indicator_store.latest(symbol=symbol, timeframe=timeframe)
        if pack is None:
            raise HTTPException(status_code=404, detail="indicator not ready")
        return JSONResponse(_indicator_payload(pack))

    @app.get("/api/indicators/history")
    async def get_indicators_history(
        symbol: str = "BTCUSDT_PERP",
        tf: str = "4h",
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = None,
    ) -> JSONResponse:
        timeframe = _parse_timeframe(tf)
        from_ns = _iso_to_ns(from_) if from_ else None
        to_ns = _iso_to_ns(to) if to else None
        history = runtime.indicator_store.history(
            symbol=symbol,
            timeframe=timeframe,
            from_ns=from_ns,
            to_ns=to_ns,
        )
        return JSONResponse([_indicator_payload(point) for point in history])

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.websocket("/ws")
    async def websocket_state(
        websocket: WebSocket,
        symbol: str = "BTCUSDT_PERP",
        tf: str = "4h",
    ) -> None:
        await runtime.connect_state(websocket, symbol=symbol, tf=tf)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            runtime.disconnect_state(websocket)
        except Exception:
            runtime.disconnect_state(websocket)

    @app.websocket("/ws/indicators")
    async def websocket_indicators(
        websocket: WebSocket,
        symbol: str = "BTCUSDT_PERP",
        tf: str = "4h",
    ) -> None:
        timeframe = _parse_timeframe(tf)
        await runtime.connect_indicators(websocket, symbol=symbol, tf=timeframe)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            runtime.disconnect_indicators(websocket)
        except Exception:
            runtime.disconnect_indicators(websocket)

    return app


def _parse_timeframe(value: str) -> Timeframe:
    """Parse API/websocket timeframe parameters into the domain enum."""
    token = value.lower()
    if token == "4h":
        return Timeframe.TF_4H
    if token == "1d":
        return Timeframe.TF_1D
    raise HTTPException(status_code=400, detail="tf must be 4h or 1d")


def _iso_to_ns(value: str) -> int:
    """Convert an ISO-8601 timestamp string into nanoseconds since epoch."""
    cleaned = value.strip().replace("Z", "+00:00")
    ts = datetime.fromisoformat(cleaned)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp() * 1_000_000_000)


def _indicator_payload(pack: IndicatorPack) -> dict[str, Any]:
    """Serialize an indicator pack into a websocket/API-friendly payload."""
    return {
        "ts": ns_to_iso(pack.ts_ns),
        "ts_ns": pack.ts_ns,
        "symbol": pack.symbol,
        "tf": pack.timeframe.value,
        "cutoff_time": ns_to_iso(pack.cutoff_time_ns),
        "cutoff_time_ns": pack.cutoff_time_ns,
        "indicator_version": pack.indicator_version,
        "formula_hash": pack.formula_hash,
        "quality_flags": list(pack.quality_flags),
        "coverage": to_jsonable(pack.coverage),
        "components": to_jsonable(pack.components),
        "values": to_jsonable(pack.values),
    }


def main() -> None:
    """Run the web server using the runtime mode from environment variables."""
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "uvicorn is required for the web console. Install dependencies with "
            "`python3 -m pip install -e .` and retry."
        ) from exc

    parser = argparse.ArgumentParser(description="Run the ZenQuant web dashboard.")
    parser.add_argument(
        "--mode",
        default=os.getenv("ZENQUANT_RUNTIME_MODE", "live").strip().lower() or "live",
        choices=("demo", "live"),
        help="Runtime mode for the dashboard.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("ZENQUANT_WEB_HOST", "0.0.0.0").strip() or "0.0.0.0",
        help="Host interface to bind.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("ZENQUANT_WEB_PORT", "8090")),
        help="TCP port to bind.",
    )
    args = parser.parse_args()

    app = create_app(runtime_mode=args.mode)
    uvicorn.run(app, host=args.host, port=args.port, reload=False)
