from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    _FASTAPI_AVAILABLE = True
except ModuleNotFoundError as exc:
    FastAPI = Any  # type: ignore[misc,assignment]
    WebSocket = Any  # type: ignore[misc,assignment]
    WebSocketDisconnect = Exception  # type: ignore[misc,assignment]
    FileResponse = Any  # type: ignore[misc,assignment]
    JSONResponse = Any  # type: ignore[misc,assignment]
    StaticFiles = Any  # type: ignore[misc,assignment]
    _FASTAPI_AVAILABLE = False
    _FASTAPI_IMPORT_ERROR = exc

from zenquant.execution import ExecutionGateway, ExecutionRouter
from zenquant.factor_lab import FactorLabRuntime
from zenquant.ingestor import SyntheticIngestor, SyntheticIngestorConfig
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.risk import RiskGuard
from zenquant.strategy import StrategyEngine

from .live_state import DashboardStateStore


class ZenQuantWebRuntime:
    def __init__(self, symbol: str = "BTC_USDT", exchange: str = "BINANCE", tick_interval_s: float = 0.18) -> None:
        self._tick_interval_s = tick_interval_s
        self._connections: set[WebSocket] = set()
        self._tick_source_started = False
        self._tick_task: asyncio.Task[None] | None = None

        self.bus = InMemoryJetStream()
        self.bus.add_stream(StreamConfig(name="STREAM_MARKET_DATA", subjects=["md.>"], retention="limits"))
        self.bus.add_stream(StreamConfig(name="STREAM_SIGNALS", subjects=["signal.>"], retention="interest"))
        self.bus.add_stream(StreamConfig(name="STREAM_ORDERS", subjects=["exec.order.>"], retention="workqueue"))
        self.bus.add_stream(StreamConfig(name="STREAM_FACTORS", subjects=["factor.>"], retention="limits"))

        self.risk_guard = RiskGuard()
        self.state = DashboardStateStore()

        self.execution_gateway = ExecutionGateway(risk_guard=self.risk_guard)
        self.strategy_engine = StrategyEngine(bus=self.bus)
        self.factor_runtime = FactorLabRuntime(bus=self.bus)
        self.execution_router = ExecutionRouter(bus=self.bus, gateway=self.execution_gateway)
        self.ingestor = SyntheticIngestor(
            bus=self.bus,
            config=SyntheticIngestorConfig(symbol=symbol, exchange=exchange, seed=11),
        )
        self._event_iterator = self.ingestor.iter_events(steps=None, start_price=40_000.0)

        self.bus.subscribe("md.trade.>", self._on_trade)
        self.bus.subscribe("md.book.>", self._on_book)
        self.bus.subscribe("signal.alpha.>", self._on_signal)
        self.bus.subscribe("exec.order.report", self._on_report)
        self.bus.subscribe("factor.weight.>", self._on_weight)

    async def start(self) -> None:
        if self._tick_source_started:
            return
        self._tick_source_started = True
        self._tick_task = asyncio.create_task(self._run_tick_loop())

    async def stop(self) -> None:
        self._tick_source_started = False
        if self._tick_task is None:
            return
        self._tick_task.cancel()
        try:
            await self._tick_task
        except asyncio.CancelledError:
            pass
        self._tick_task = None

    def snapshot(self) -> dict:
        return self.state.snapshot(self.risk_guard)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        await websocket.send_json(self.snapshot())

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def _run_tick_loop(self) -> None:
        while self._tick_source_started:
            trade_tick, snapshot = next(self._event_iterator)
            self.bus.publish(f"md.trade.{self.ingestor.exchange}.{self.ingestor.symbol}", trade_tick)
            self.bus.publish(f"md.book.{self.ingestor.exchange}.{self.ingestor.symbol}", snapshot)
            await self._broadcast_snapshot()
            await asyncio.sleep(self._tick_interval_s)

    async def _broadcast_snapshot(self) -> None:
        if not self._connections:
            return
        payload = self.snapshot()
        stale_connections: list[WebSocket] = []
        for websocket in self._connections:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale_connections.append(websocket)
        for websocket in stale_connections:
            self._connections.discard(websocket)

    def _on_trade(self, _: str, payload: object) -> None:
        from zenquant.domain.models import TradeTick

        if isinstance(payload, TradeTick):
            self.state.on_trade(payload)

    def _on_book(self, _: str, payload: object) -> None:
        from zenquant.domain.models import OrderBookSnapshot

        if isinstance(payload, OrderBookSnapshot):
            self.state.on_orderbook(payload)

    def _on_signal(self, _: str, payload: object) -> None:
        from zenquant.domain.models import AlphaSignal

        if isinstance(payload, AlphaSignal):
            self.state.on_signal(payload)

    def _on_report(self, _: str, payload: object) -> None:
        from zenquant.domain.models import ExecutionReport

        if isinstance(payload, ExecutionReport):
            self.state.on_report(payload)

    def _on_weight(self, _: str, payload: object) -> None:
        from zenquant.factor_lab.models import FactorWeight

        if isinstance(payload, FactorWeight):
            self.state.on_weight(payload)


def create_app() -> FastAPI:
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError(
            "fastapi is required for the web console. Install dependencies with "
            "`python3 -m pip install -e .` and retry."
        ) from _FASTAPI_IMPORT_ERROR

    app = FastAPI(title="ZenQuant Web Console", version="0.1.0")
    runtime = ZenQuantWebRuntime()
    web_root = Path(__file__).resolve().parents[1] / "web"

    app.mount("/static", StaticFiles(directory=str(web_root)), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/state")
    async def get_state() -> JSONResponse:
        return JSONResponse(runtime.snapshot())

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.websocket("/ws")
    async def websocket_stream(websocket: WebSocket) -> None:
        await runtime.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            runtime.disconnect(websocket)
        except Exception:
            runtime.disconnect(websocket)

    @app.on_event("startup")
    async def startup_event() -> None:
        await runtime.start()

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        await runtime.stop()

    return app


def main() -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "uvicorn is required for the web console. Install dependencies with "
            "`python3 -m pip install -e .` and retry."
        ) from exc

    uvicorn.run("zenquant.app.server:create_app", host="0.0.0.0", port=8090, factory=True, reload=False)
