"""Minimal end-to-end demo pipeline for local deterministic runs.

The demo pipeline remains useful for sanity checks because it wires the same
core components as the live runtime, but drives them from the synthetic
ingestor instead of external market data.
"""

from __future__ import annotations

from dataclasses import dataclass

from zenquant.domain import IndicatorPack, TargetPosition
from zenquant.execution import ExecutionGateway, ExecutionKernel
from zenquant.indicator import IndicatorEngine, IndicatorStore
from zenquant.ingestor import SyntheticIngestor, SyntheticIngestorConfig
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.market import BarClockService, SnapshotBuilder
from zenquant.risk import RiskOverlay
from zenquant.state import StateEngine
from zenquant.strategy import ARCStrategyEngine


@dataclass(slots=True)
class PipelineResult:
    """Compact summary returned by ``ZenQuantPipeline.run``."""

    total_indicator_packs: int
    total_targets: int
    total_reports: int
    filled_reports: int
    latest_zen_pulse: float
    latest_regime: str
    final_position_btc: float


class ZenQuantPipeline:
    """Assemble the core trading pipeline for deterministic local tests.

    Example:
        result = ZenQuantPipeline(seed=11).run(steps=320)
        print(result.latest_regime)
    """

    def __init__(self, seed: int = 7) -> None:
        self.bus = InMemoryJetStream()
        self.bus.add_stream(StreamConfig(name="STREAM_MARKET_DATA", subjects=["md.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_CLOCK", subjects=["clock.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INDICATORS", subjects=["indicator.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INTENTS", subjects=["intent.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_EXEC", subjects=["exec.>"]))

        self.indicator_store = IndicatorStore()
        # ORDERING INVARIANT: SnapshotBuilder must be instantiated before
        # BarClockService so bar-close CVD is finalized before clock events.
        self.snapshot_builder = SnapshotBuilder(bus=self.bus)
        self.bar_clock = BarClockService(bus=self.bus)
        self.indicator_engine = IndicatorEngine(bus=self.bus, store=self.indicator_store)
        # ORDERING INVARIANT: RiskOverlay must subscribe before ARCStrategyEngine
        # so indicator state is fresh when intents are evaluated.
        self.risk_overlay = RiskOverlay(bus=self.bus)
        self.strategy_engine = ARCStrategyEngine(bus=self.bus)
        # ORDERING INVARIANT: StateEngine must subscribe after ARCStrategyEngine
        # so strategy caches the pack before the derived market state arrives.
        self.state_engine = StateEngine(bus=self.bus)
        self.execution_gateway = ExecutionGateway()
        self.execution_kernel = ExecutionKernel(
            bus=self.bus,
            gateway=self.execution_gateway,
            risk_overlay=self.risk_overlay,
        )
        self.ingestor = SyntheticIngestor(bus=self.bus, config=SyntheticIngestorConfig(seed=seed))

        self._indicators: list[IndicatorPack] = []
        self._targets: list[TargetPosition] = []
        self._reports: list[dict] = []
        self.bus.subscribe("indicator.>", self._collect_indicator)
        self.bus.subscribe("intent.target_position.final.>", self._collect_target)
        self.bus.subscribe("exec.order.report", self._collect_report)

    def run(self, steps: int = 320) -> PipelineResult:
        """Run the synthetic pipeline for a fixed number of event steps."""
        self.ingestor.run(steps=steps)

        latest_pack = None
        latest_zen_pulse = 0.0
        latest_regime = "UNKNOWN"
        if self._indicators:
            candidate = [item for item in self._indicators if item.symbol == "BTCUSDT_PERP" and item.timeframe.value == "4h"]
            if candidate:
                latest_pack = candidate[-1]
            else:
                latest_pack = self._indicators[-1]
        if latest_pack is not None:
            latest_zen_pulse = float(latest_pack.values.get("zen_pulse", 0.0) or 0.0)
        if self._targets:
            latest_regime = self._targets[-1].regime

        filled_reports = sum(1 for report in self._reports if report.get("status") == "FILLED")
        final_position_btc = self.execution_kernel.positions.get("BTCUSDT_PERP", 0.0)
        return PipelineResult(
            total_indicator_packs=len(self._indicators),
            total_targets=len(self._targets),
            total_reports=len(self._reports),
            filled_reports=filled_reports,
            latest_zen_pulse=latest_zen_pulse,
            latest_regime=latest_regime,
            final_position_btc=final_position_btc,
        )

    def _collect_indicator(self, _: str, payload: object) -> None:
        """Capture emitted indicator packs for test assertions."""
        if isinstance(payload, IndicatorPack):
            self._indicators.append(payload)

    def _collect_target(self, _: str, payload: object) -> None:
        """Capture final target intents for summary reporting."""
        if isinstance(payload, TargetPosition):
            self._targets.append(payload)

    def _collect_report(self, _: str, payload: object) -> None:
        """Capture execution reports in a JSON-friendly form."""
        from zenquant.domain import ExecutionReport

        if isinstance(payload, ExecutionReport):
            self._reports.append(
                {
                    "client_order_id": payload.client_order_id,
                    "symbol": payload.symbol,
                    "side": payload.side,
                    "status": payload.status,
                    "filled_qty": payload.filled_qty,
                    "avg_price": payload.avg_price,
                    "fee": payload.fee,
                    "transaction_time_ns": payload.transaction_time_ns,
                    "reject_reason": payload.reject_reason,
                }
            )
