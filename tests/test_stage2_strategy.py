import unittest

from zenquant.domain import (
    ExecutionReport,
    ExitDecision,
    IndicatorCoverage,
    IndicatorPack,
    PlaybookSelection,
    SizingDecision,
    Timeframe,
)
from zenquant.infra import InMemoryJetStream
from zenquant.state import StateEngine
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
            "structure_break": False,
        },
        components={},
    )


class Stage2StrategyTests(unittest.TestCase):
    def test_4h_state_publishes_matching_playbook(self) -> None:
        bus = InMemoryJetStream()
        ARCStrategyEngine(bus)
        StateEngine(bus)

        playbooks: list[PlaybookSelection] = []
        bus.subscribe(
            "strategy.playbook.>",
            lambda _subject, payload: playbooks.append(payload) if isinstance(payload, PlaybookSelection) else None,
        )

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                last_price=101.0,
                cvd_slope=0.6,
                structure_state="UPTREND",
                momentum_roc_z=1.3,
            ),
        )

        self.assertEqual(len(playbooks), 1)
        self.assertEqual(playbooks[0].playbook_id, "BULL_CONTINUATION")

    def test_4h_state_publishes_sizing_breakdown(self) -> None:
        bus = InMemoryJetStream()
        ARCStrategyEngine(bus)
        StateEngine(bus)

        sizing_events: list[SizingDecision] = []
        bus.subscribe(
            "strategy.sizing.>",
            lambda _subject, payload: sizing_events.append(payload) if isinstance(payload, SizingDecision) else None,
        )

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                last_price=101.0,
                cvd_slope=0.6,
                structure_state="UPTREND",
                momentum_roc_z=1.3,
            ),
        )

        self.assertEqual(len(sizing_events), 1)
        self.assertGreater(sizing_events[0].final_leverage, 0.0)
        self.assertIn("SIZE_BASE_VOL_TARGET", sizing_events[0].reason_codes)

    def test_1d_bias_blocks_conflicting_4h_direction_without_emitting_1d_target(self) -> None:
        bus = InMemoryJetStream()
        ARCStrategyEngine(bus)
        StateEngine(bus)

        targets = []
        bus.subscribe(
            "intent.target_position.arc.BTCUSDT_PERP",
            lambda _subject, payload: targets.append(payload),
        )

        bus.publish(
            "indicator.pack.v1.1d.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_1D,
                cutoff_time_ns=1,
                last_price=100.0,
                cvd_slope=-0.5,
                structure_state="DOWNTREND",
                momentum_roc_z=-1.2,
            ),
        )
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                last_price=101.0,
                cvd_slope=0.6,
                structure_state="UPTREND",
                momentum_roc_z=1.3,
            ),
        )

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].timeframe, Timeframe.TF_4H)
        self.assertAlmostEqual(targets[0].target_qty, 0.0)
        self.assertIn("BIAS_DIRECTION_BLOCK", targets[0].reason_codes)

    def test_trailing_stop_emits_reduce_only_exit(self) -> None:
        bus = InMemoryJetStream()
        ARCStrategyEngine(bus)
        StateEngine(bus)

        targets = []
        exits = []
        bus.subscribe(
            "intent.target_position.arc.BTCUSDT_PERP",
            lambda _subject, payload: targets.append(payload),
        )
        bus.subscribe(
            "strategy.exit.>",
            lambda _subject, payload: exits.append(payload) if isinstance(payload, ExitDecision) else None,
        )

        bus.publish(
            "exec.order.report",
            ExecutionReport(
                client_order_id="oid-1",
                symbol="BTCUSDT_PERP",
                side=1,
                status="FILLED",
                filled_qty=1.0,
                avg_price=100.0,
                fee=0.0,
                transaction_time_ns=1,
            ),
        )
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                last_price=100.0,
                cvd_slope=0.5,
                structure_state="UPTREND",
                momentum_roc_z=1.0,
                atr=2.0,
            ),
        )
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=3,
                last_price=94.0,
                cvd_slope=0.2,
                structure_state="UPTREND",
                momentum_roc_z=0.6,
                atr=2.0,
            ),
        )

        self.assertGreaterEqual(len(targets), 2)
        latest = targets[-1]
        self.assertEqual(latest.timeframe, Timeframe.TF_4H)
        self.assertTrue(latest.reduce_only)
        self.assertAlmostEqual(latest.target_qty, 0.0)
        self.assertIn("EXIT_TRAILING_STOP", latest.reason_codes)
        self.assertGreaterEqual(len(exits), 2)
        self.assertEqual(exits[-1].exit_type, "TRAILING")

    def test_short_position_tracking_handles_add_and_cover(self) -> None:
        bus = InMemoryJetStream()
        engine = ARCStrategyEngine(bus)
        StateEngine(bus)

        bus.publish(
            "exec.order.report",
            ExecutionReport(
                client_order_id="oid-1",
                symbol="BTCUSDT_PERP",
                side=-1,
                status="FILLED",
                filled_qty=1.0,
                avg_price=100.0,
                fee=0.0,
                transaction_time_ns=1,
            ),
        )
        position = engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]
        self.assertEqual(position.direction, -1.0)
        self.assertAlmostEqual(position.current_qty, 1.0)

        bus.publish(
            "exec.order.report",
            ExecutionReport(
                client_order_id="oid-2",
                symbol="BTCUSDT_PERP",
                side=-1,
                status="FILLED",
                filled_qty=0.5,
                avg_price=99.0,
                fee=0.0,
                transaction_time_ns=2,
            ),
        )
        position = engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]
        self.assertEqual(position.direction, -1.0)
        self.assertAlmostEqual(position.current_qty, 1.5)
        self.assertEqual(position.pyramid_count, 1)

        bus.publish(
            "exec.order.report",
            ExecutionReport(
                client_order_id="oid-3",
                symbol="BTCUSDT_PERP",
                side=1,
                status="FILLED",
                filled_qty=1.5,
                avg_price=98.0,
                fee=0.0,
                transaction_time_ns=3,
            ),
        )
        self.assertNotIn(("BTCUSDT_PERP", Timeframe.TF_4H), engine._positions)

    def test_stale_1d_bias_is_not_used_for_4h_blocking(self) -> None:
        bus = InMemoryJetStream()
        ARCStrategyEngine(bus)
        StateEngine(bus)

        targets = []
        bus.subscribe(
            "intent.target_position.arc.BTCUSDT_PERP",
            lambda _subject, payload: targets.append(payload),
        )

        bus.publish(
            "indicator.pack.v1.1d.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_1D,
                cutoff_time_ns=1,
                last_price=100.0,
                cvd_slope=-0.5,
                structure_state="DOWNTREND",
                momentum_roc_z=-1.2,
            ),
        )
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=(9 * Timeframe.TF_4H.seconds * 1_000_000_000),
                last_price=101.0,
                cvd_slope=0.6,
                structure_state="UPTREND",
                momentum_roc_z=1.3,
            ),
        )

        self.assertEqual(len(targets), 1)
        self.assertGreater(targets[0].target_qty, 0.0)
        self.assertIn("BIAS_STALE", targets[0].reason_codes)
        self.assertNotIn("BIAS_DIRECTION_BLOCK", targets[0].reason_codes)

    def test_degraded_1d_bias_is_not_used_for_4h_blocking(self) -> None:
        bus = InMemoryJetStream()
        ARCStrategyEngine(bus)
        StateEngine(bus)

        targets = []
        bus.subscribe(
            "intent.target_position.arc.BTCUSDT_PERP",
            lambda _subject, payload: targets.append(payload),
        )

        degraded = _pack(
            timeframe=Timeframe.TF_1D,
            cutoff_time_ns=1,
            last_price=100.0,
            cvd_slope=-0.5,
            structure_state="DOWNTREND",
            momentum_roc_z=-1.2,
        )
        degraded.quality_flags = ["BOOK_STALE"]
        bus.publish("indicator.pack.v1.1d.BTCUSDT_PERP", degraded)
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                last_price=101.0,
                cvd_slope=0.6,
                structure_state="UPTREND",
                momentum_roc_z=1.3,
            ),
        )

        self.assertEqual(len(targets), 1)
        self.assertGreater(targets[0].target_qty, 0.0)
        self.assertIn("BIAS_QUALITY_DEGRADED", targets[0].reason_codes)

    def test_invalid_atr_does_not_arm_trailing_stop(self) -> None:
        bus = InMemoryJetStream()
        ARCStrategyEngine(bus)
        StateEngine(bus)

        targets = []
        bus.subscribe(
            "intent.target_position.arc.BTCUSDT_PERP",
            lambda _subject, payload: targets.append(payload),
        )
        bus.publish(
            "exec.order.report",
            ExecutionReport(
                client_order_id="oid-atr",
                symbol="BTCUSDT_PERP",
                side=1,
                status="FILLED",
                filled_qty=1.0,
                avg_price=100.0,
                fee=0.0,
                transaction_time_ns=1,
            ),
        )
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                last_price=99.0,
                cvd_slope=0.5,
                structure_state="UPTREND",
                momentum_roc_z=0.8,
                atr=0.0,
            ),
        )

        self.assertEqual(len(targets), 1)
        self.assertNotIn("EXIT_TRAILING_STOP", targets[0].reason_codes)

    def test_position_flip_resets_bars_held(self) -> None:
        bus = InMemoryJetStream()
        engine = ARCStrategyEngine(bus)
        StateEngine(bus)

        bus.publish(
            "exec.order.report",
            ExecutionReport(
                client_order_id="oid-long",
                symbol="BTCUSDT_PERP",
                side=1,
                status="FILLED",
                filled_qty=1.0,
                avg_price=100.0,
                fee=0.0,
                transaction_time_ns=1,
            ),
        )
        position = engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]
        position.bars_held = 5

        bus.publish(
            "exec.order.report",
            ExecutionReport(
                client_order_id="oid-flip",
                symbol="BTCUSDT_PERP",
                side=-1,
                status="FILLED",
                filled_qty=2.0,
                avg_price=98.0,
                fee=0.0,
                transaction_time_ns=2,
            ),
        )

        flipped = engine._positions[("BTCUSDT_PERP", Timeframe.TF_4H)]
        self.assertEqual(flipped.direction, -1.0)
        self.assertEqual(flipped.bars_held, 0)
        self.assertAlmostEqual(flipped.current_qty, 1.0)


if __name__ == "__main__":
    unittest.main()
