from __future__ import annotations

from dataclasses import dataclass

from zenquant.domain.models import AlphaSignal, ExecutionReport, OrderStatus
from zenquant.execution import ExecutionGateway, ExecutionRouter
from zenquant.factor_lab import FactorLabRuntime, FactorWeight
from zenquant.ingestor import SyntheticIngestor, SyntheticIngestorConfig
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.risk import RiskGuard
from zenquant.strategy import StrategyEngine


@dataclass(slots=True)
class PipelineResult:
    total_signals: int
    total_reports: int
    filled_reports: int
    rejected_reports: int
    total_factor_weights: int
    final_position: float
    kill_switch_active: bool


class ZenQuantPipeline:
    def __init__(self, symbol: str = "BTC_USDT", exchange: str = "BINANCE", seed: int = 7) -> None:
        self.bus = InMemoryJetStream()
        self.bus.add_stream(StreamConfig(name="STREAM_MARKET_DATA", subjects=["md.>"], retention="limits"))
        self.bus.add_stream(StreamConfig(name="STREAM_SIGNALS", subjects=["signal.>"], retention="interest"))
        self.bus.add_stream(StreamConfig(name="STREAM_ORDERS", subjects=["exec.order.>"], retention="workqueue"))
        self.bus.add_stream(StreamConfig(name="STREAM_FACTORS", subjects=["factor.>"], retention="limits"))

        self.risk_guard = RiskGuard()
        self.execution_gateway = ExecutionGateway(risk_guard=self.risk_guard)
        self.strategy_engine = StrategyEngine(bus=self.bus)
        self.factor_runtime = FactorLabRuntime(bus=self.bus)
        self.execution_router = ExecutionRouter(bus=self.bus, gateway=self.execution_gateway)
        self.ingestor = SyntheticIngestor(
            bus=self.bus,
            config=SyntheticIngestorConfig(symbol=symbol, exchange=exchange, seed=seed),
        )

        self._signals: list[AlphaSignal] = []
        self._reports: list[ExecutionReport] = []
        self._weights: list[FactorWeight] = []
        self.bus.subscribe("signal.alpha.>", self._collect_signal)
        self.bus.subscribe("exec.order.report", self._collect_report)
        self.bus.subscribe("factor.weight.>", self._collect_weight)

    def run(self, steps: int = 240, start_price: float = 40_000.0) -> PipelineResult:
        self.ingestor.run(steps=steps, start_price=start_price)
        filled_reports = sum(
            1 for report in self._reports if report.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
        )
        rejected_reports = sum(1 for report in self._reports if report.status == OrderStatus.REJECTED)
        return PipelineResult(
            total_signals=len(self._signals),
            total_reports=len(self._reports),
            filled_reports=filled_reports,
            rejected_reports=rejected_reports,
            total_factor_weights=len(self._weights),
            final_position=self.risk_guard.positions.get(self.ingestor.symbol, 0.0),
            kill_switch_active=self.risk_guard.kill_switch_active,
        )

    def _collect_signal(self, _: str, payload: object) -> None:
        if isinstance(payload, AlphaSignal):
            self._signals.append(payload)

    def _collect_report(self, _: str, payload: object) -> None:
        if isinstance(payload, ExecutionReport):
            self._reports.append(payload)

    def _collect_weight(self, _: str, payload: object) -> None:
        if isinstance(payload, FactorWeight):
            self._weights.append(payload)
