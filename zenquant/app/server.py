from __future__ import annotations

import asyncio
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
    IndicatorPack,
    IntentReason,
    RiskOverlayDecision,
    RiskSnapshot,
    TargetPosition,
    Timeframe,
)
from zenquant.domain.serde import ns_to_iso, to_jsonable
from zenquant.execution import ExecutionGateway, ExecutionKernel
from zenquant.indicator import IndicatorEngine, IndicatorStore
from zenquant.ingestor import SyntheticIngestor, SyntheticIngestorConfig
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.market import BarClockService, SnapshotBuilder
from zenquant.risk import RiskOverlay
from zenquant.strategy import ARCStrategyEngine


@dataclass(slots=True)
class _IndicatorConnection:
    websocket: WebSocket
    symbol: str
    timeframe: Timeframe


class ZenQuantWebRuntime:
    def __init__(self, tick_interval_s: float = 0.04, warmup_steps: int = 300) -> None:
        self._tick_interval_s = tick_interval_s
        self._warmup_steps = max(0, warmup_steps)
        self._state_connections: set[WebSocket] = set()
        self._indicator_connections: list[_IndicatorConnection] = []
        self._tick_source_started = False
        self._tick_task: asyncio.Task[None] | None = None

        self.bus = InMemoryJetStream()
        self.bus.add_stream(StreamConfig(name="STREAM_MARKET_DATA", subjects=["md.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_CLOCK", subjects=["clock.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INDICATORS", subjects=["indicator.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INTENTS", subjects=["intent.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_EXEC", subjects=["exec.>"]))

        self.state = DashboardStateStore()
        self.indicator_store = IndicatorStore()

        self.snapshot_builder = SnapshotBuilder(bus=self.bus)
        self.bar_clock = BarClockService(bus=self.bus)
        self.indicator_engine = IndicatorEngine(bus=self.bus, store=self.indicator_store)
        self.strategy_engine = ARCStrategyEngine(bus=self.bus)
        self.risk_overlay = RiskOverlay(bus=self.bus)
        self.execution_gateway = ExecutionGateway()
        self.execution_kernel = ExecutionKernel(
            bus=self.bus,
            gateway=self.execution_gateway,
            risk_overlay=self.risk_overlay,
        )
        self.ingestor = SyntheticIngestor(bus=self.bus, config=SyntheticIngestorConfig(seed=11))

        self.bus.subscribe("md.candle.>", self._on_candle)
        self.bus.subscribe("indicator.pack.v1.>", self._on_indicator)
        self.bus.subscribe("risk.snapshot", self._on_risk_snapshot)
        self.bus.subscribe("risk.overlay.decision", self._on_risk_decision)
        self.bus.subscribe("intent.reason.>", self._on_reason)
        self.bus.subscribe("intent.target_position.final.>", self._on_target)
        self.bus.subscribe("exec.order.report", self._on_report)

        self._prime_history()
        self._event_iterator = self.ingestor.iter_events(steps=None, start_step=self._warmup_steps)

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

    def snapshot(self, symbol: str = "BTCUSDT_PERP", tf: str = "4h") -> dict[str, Any]:
        return self.state.snapshot(symbol=symbol, timeframe=tf)

    async def connect_state(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._state_connections.add(websocket)
        await websocket.send_json(self.snapshot())

    async def connect_indicators(self, websocket: WebSocket, symbol: str, tf: Timeframe) -> None:
        await websocket.accept()
        conn = _IndicatorConnection(websocket=websocket, symbol=symbol, timeframe=tf)
        self._indicator_connections.append(conn)
        latest = self.indicator_store.latest(symbol=symbol, timeframe=tf)
        if latest is not None:
            await websocket.send_json(_indicator_payload(latest))

    def disconnect_state(self, websocket: WebSocket) -> None:
        self._state_connections.discard(websocket)

    def disconnect_indicators(self, websocket: WebSocket) -> None:
        self._indicator_connections = [item for item in self._indicator_connections if item.websocket is not websocket]

    async def _run_tick_loop(self) -> None:
        while self._tick_source_started:
            subject, payload = next(self._event_iterator)
            self.bus.publish(subject, payload)
            await asyncio.sleep(self._tick_interval_s)

    def _prime_history(self) -> None:
        if self._warmup_steps <= 0:
            return
        for subject, payload in self.ingestor.iter_events(steps=self._warmup_steps, start_step=0):
            self.bus.publish(subject, payload)

    async def _broadcast_state(self) -> None:
        if not self._state_connections:
            return
        payload = self.snapshot()
        stale: list[WebSocket] = []
        for websocket in self._state_connections:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self._state_connections.discard(websocket)

    async def _broadcast_indicator(self, pack: IndicatorPack) -> None:
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

    def _on_candle(self, _: str, payload: object) -> None:
        if isinstance(payload, Candle):
            self.state.on_candle(payload)

    def _on_indicator(self, _: str, payload: object) -> None:
        if not isinstance(payload, IndicatorPack):
            return
        self.state.on_indicator(payload)
        asyncio.create_task(self._broadcast_indicator(payload))
        asyncio.create_task(self._broadcast_state())

    def _on_risk_snapshot(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskSnapshot):
            self.state.on_risk_snapshot(payload)

    def _on_risk_decision(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskOverlayDecision):
            self.state.on_risk_decision(payload)

    def _on_reason(self, _: str, payload: object) -> None:
        if isinstance(payload, IntentReason):
            self.state.on_reason(payload)

    def _on_target(self, _: str, payload: object) -> None:
        if isinstance(payload, TargetPosition):
            self.state.on_target_position(payload)

    def _on_report(self, _: str, payload: object) -> None:
        from zenquant.domain import ExecutionReport

        if isinstance(payload, ExecutionReport):
            self.state.on_exec_report(to_jsonable(payload))


def create_app() -> FastAPI:
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError(
            "fastapi is required for the web console. Install dependencies with "
            "`python3 -m pip install -e .` and retry."
        ) from _FASTAPI_IMPORT_ERROR

    app = FastAPI(title="ZenQuant 4.0 Phase 1", version="0.2.0")
    runtime = ZenQuantWebRuntime()
    web_root = Path(__file__).resolve().parents[1] / "web"

    app.mount("/static", StaticFiles(directory=str(web_root)), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

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
        history = runtime.indicator_store.history(symbol=symbol, timeframe=timeframe, from_ns=from_ns, to_ns=to_ns)
        return JSONResponse([_indicator_payload(point) for point in history])

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.websocket("/ws")
    async def websocket_state(websocket: WebSocket) -> None:
        await runtime.connect_state(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            runtime.disconnect_state(websocket)
        except Exception:
            runtime.disconnect_state(websocket)

    @app.websocket("/ws/indicators")
    async def websocket_indicators(websocket: WebSocket, symbol: str = "BTCUSDT_PERP", tf: str = "4h") -> None:
        timeframe = _parse_timeframe(tf)
        await runtime.connect_indicators(websocket, symbol=symbol, tf=timeframe)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            runtime.disconnect_indicators(websocket)
        except Exception:
            runtime.disconnect_indicators(websocket)

    @app.on_event("startup")
    async def startup_event() -> None:
        await runtime.start()

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        await runtime.stop()

    return app


def _parse_timeframe(value: str) -> Timeframe:
    token = value.lower()
    if token == "4h":
        return Timeframe.TF_4H
    if token == "1d":
        return Timeframe.TF_1D
    raise HTTPException(status_code=400, detail="tf must be 4h or 1d")


def _iso_to_ns(value: str) -> int:
    cleaned = value.strip().replace("Z", "+00:00")
    ts = datetime.fromisoformat(cleaned)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp() * 1_000_000_000)


def _indicator_payload(pack: IndicatorPack) -> dict[str, Any]:
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
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "uvicorn is required for the web console. Install dependencies with "
            "`python3 -m pip install -e .` and retry."
        ) from exc

    uvicorn.run("zenquant.app.server:create_app", host="0.0.0.0", port=8090, factory=True, reload=False)
