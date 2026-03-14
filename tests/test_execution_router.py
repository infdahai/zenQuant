import unittest

from zenquant.domain import TargetPosition, Timeframe
from zenquant.execution import ExecutionGateway, ExecutionKernel
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay


class ExecutionRouterTests(unittest.TestCase):
    def test_reduce_only_ignores_zero_position(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        kernel = ExecutionKernel(bus, ExecutionGateway(), risk)

        reports = []
        bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))
        kernel._last_price["BTCUSDT_PERP"] = 100.0

        bus.publish(
            "intent.target_position.final.BTCUSDT_PERP",
            TargetPosition(
                intent_id="intent-ro",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=1,
                target_qty=0.0,
                target_leverage=0.0,
                reduce_only=True,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["EXIT_TRAILING_STOP"],
            ),
        )

        self.assertEqual(reports, [])
