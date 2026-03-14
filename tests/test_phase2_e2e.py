import unittest

from zenquant.domain import IndicatorCoverage, IndicatorPack, Timeframe, TradeTick, EventMeta
from zenquant.execution import ExecutionGateway, ExecutionKernel
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay
from zenquant.strategy import ARCStrategyEngine


def _pack(
    *,
    timeframe: Timeframe,
    cutoff_time_ns: int,
    last_price: float,
    cvd_slope: float,
    structure_state: str,
    momentum_roc_z: float,
    atr: float = 2.0,
) -> IndicatorPack:
    return IndicatorPack(
        ts_ns=cutoff_time_ns,
        symbol="BTCUSDT_PERP",
        timeframe=timeframe,
        cutoff_time_ns=cutoff_time_ns,
        indicator_version="pack.v1",
        formula_hash="sha256:test",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=2, oi_coverage=1.0, staleness_ms=0),
        values={
            "realized_vol": 0.2,
            "cvd_slope": cvd_slope,
            "funding_oi_z": 0.1,
            "ob_liq_z": 0.0,
            "zen_pulse": 70.0,
            "last_price": last_price,
            "atr": atr,
            "structure_state": structure_state,
            "momentum_roc_z": momentum_roc_z,
        },
        components={},
    )


def _trade(price: float, event_time_ns: int) -> TradeTick:
    return TradeTick(
        meta=EventMeta(
            event_time_ns=event_time_ns,
            ingest_time_ns=event_time_ns,
            venue="BINANCE",
            symbol="BTCUSDT_PERP",
            stream_seq=event_time_ns,
        ),
        price=price,
        size=1.0,
        is_buyer_aggressor=True,
    )


class Phase2EndToEndTests(unittest.TestCase):
    def test_phase2_flow_bias_entry_and_trailing_exit(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        ARCStrategyEngine(bus)
        execution = ExecutionKernel(bus, ExecutionGateway(), risk)

        final_targets = []
        reports = []
        bus.subscribe(
            "intent.target_position.final.>",
            lambda _subject, payload: final_targets.append(payload),
        )
        bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))

        bus.publish("md.trade.BINANCE.BTCUSDT_PERP", _trade(100.0, 1))

        bus.publish(
            "indicator.pack.v1.1d.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_1D,
                cutoff_time_ns=2,
                last_price=100.0,
                cvd_slope=0.5,
                structure_state="UPTREND",
                momentum_roc_z=1.2,
            ),
        )
        self.assertEqual(len(final_targets), 0)
        self.assertEqual(len(reports), 0)

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=3,
                last_price=100.0,
                cvd_slope=0.6,
                structure_state="UPTREND",
                momentum_roc_z=1.3,
            ),
        )
        self.assertEqual(len(final_targets), 1)
        self.assertGreater(final_targets[-1].target_qty, 0.0)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[-1].status, "FILLED")
        self.assertGreater(execution.positions["BTCUSDT_PERP"], 0.0)

        bus.publish("md.trade.BINANCE.BTCUSDT_PERP", _trade(100.0, 4))
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=5,
                last_price=100.0,
                cvd_slope=0.2,
                structure_state="UPTREND",
                momentum_roc_z=0.4,
            ),
        )
        self.assertEqual(len(final_targets), 2)
        self.assertFalse(final_targets[-1].reduce_only)

        bus.publish("md.trade.BINANCE.BTCUSDT_PERP", _trade(94.0, 6))
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=7,
                last_price=94.0,
                cvd_slope=0.2,
                structure_state="UPTREND",
                momentum_roc_z=0.4,
            ),
        )

        self.assertEqual(len(final_targets), 3)
        self.assertTrue(final_targets[-1].reduce_only)
        self.assertAlmostEqual(final_targets[-1].target_qty, 0.0)
        self.assertEqual(len(reports), 3)
        self.assertEqual(reports[-1].side, -1)
        self.assertAlmostEqual(reports[-1].filled_qty, 2.0)
        self.assertAlmostEqual(execution.positions.get("BTCUSDT_PERP", 0.0), 0.0)
