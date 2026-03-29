import unittest

from zenquant.domain import EventMeta, IndicatorCoverage, IndicatorPack, Timeframe, TradeTick
from zenquant.execution import ExecutionGateway, ExecutionKernel
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay
from zenquant.state import StateEngine
from zenquant.strategy import ARCStrategyEngine


def _pack(
    *,
    symbol: str = "BTCUSDT_PERP",
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
        symbol=symbol,
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


def _trade(price: float, event_time_ns: int, symbol: str = "BTCUSDT_PERP") -> TradeTick:
    return TradeTick(
        meta=EventMeta(
            event_time_ns=event_time_ns,
            ingest_time_ns=event_time_ns,
            venue="BINANCE",
            symbol=symbol,
            stream_seq=event_time_ns,
        ),
        price=price,
        size=1.0,
        is_buyer_aggressor=True,
    )


class DecisionChainEndToEndTests(unittest.TestCase):
    def test_each_4h_indicator_emits_one_complete_decision_chain(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        ARCStrategyEngine(bus)
        StateEngine(bus)
        execution = ExecutionKernel(bus, ExecutionGateway(), risk)

        collected: dict[str, list[object]] = {
            "state": [],
            "playbook": [],
            "sizing": [],
            "exit": [],
            "target": [],
        }
        bus.subscribe("state.market.>", lambda _subject, payload: collected["state"].append(payload))
        bus.subscribe("strategy.playbook.>", lambda _subject, payload: collected["playbook"].append(payload))
        bus.subscribe("strategy.sizing.>", lambda _subject, payload: collected["sizing"].append(payload))
        bus.subscribe("strategy.exit.>", lambda _subject, payload: collected["exit"].append(payload))
        bus.subscribe("intent.target_position.final.>", lambda _subject, payload: collected["target"].append(payload))

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
        bus.publish("md.trade.BINANCE.BTCUSDT_PERP", _trade(101.0, 4))
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _pack(
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=5,
                last_price=101.0,
                cvd_slope=0.55,
                structure_state="UPTREND",
                momentum_roc_z=1.0,
            ),
        )
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

        for cutoff in (3, 5):
            state = [
                item
                for item in collected["state"]
                if item.timeframe == Timeframe.TF_4H and item.cutoff_time_ns == cutoff
            ]
            playbook = [
                item
                for item in collected["playbook"]
                if item.timeframe == Timeframe.TF_4H and item.cutoff_time_ns == cutoff
            ]
            sizing = [
                item
                for item in collected["sizing"]
                if item.timeframe == Timeframe.TF_4H and item.cutoff_time_ns == cutoff
            ]
            target = [
                item
                for item in collected["target"]
                if item.timeframe == Timeframe.TF_4H and item.cutoff_time_ns == cutoff
            ]

            self.assertEqual(len(state), 1)
            self.assertEqual(len(playbook), 1)
            self.assertEqual(len(sizing), 1)
            self.assertEqual(len(target), 1)

            self.assertEqual(state[0].symbol, "BTCUSDT_PERP")
            self.assertEqual(playbook[0].symbol, "BTCUSDT_PERP")
            self.assertEqual(sizing[0].symbol, "BTCUSDT_PERP")
            self.assertEqual(target[0].symbol, "BTCUSDT_PERP")

        forced_exit_state = [
            item for item in collected["state"] if item.timeframe == Timeframe.TF_4H and item.cutoff_time_ns == 7
        ]
        forced_exit_playbook = [
            item for item in collected["playbook"] if item.timeframe == Timeframe.TF_4H and item.cutoff_time_ns == 7
        ]
        forced_exit_target = [
            item for item in collected["target"] if item.timeframe == Timeframe.TF_4H and item.cutoff_time_ns == 7
        ]
        self.assertEqual(len(forced_exit_state), 1)
        self.assertEqual(len(forced_exit_playbook), 1)
        self.assertEqual(len(forced_exit_target), 1)

        actionable_exits = [
            item for item in collected["exit"] if item.timeframe == Timeframe.TF_4H and item.should_exit()
        ]
        self.assertEqual(len(actionable_exits), 1)
        self.assertEqual(actionable_exits[0].cutoff_time_ns, 7)
        self.assertTrue(collected["target"][-1].reduce_only)
        self.assertAlmostEqual(execution.positions.get("BTCUSDT_PERP", 0.0), 0.0)

    def test_bar_close_chain_keeps_cutoff_consistent(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        ARCStrategyEngine(bus)
        StateEngine(bus)
        ExecutionKernel(bus, ExecutionGateway(), risk)

        collected: dict[str, list[object]] = {
            "state": [],
            "playbook": [],
            "sizing": [],
            "exit": [],
            "target": [],
        }
        bus.subscribe("state.market.>", lambda _subject, payload: collected["state"].append(payload))
        bus.subscribe("strategy.playbook.>", lambda _subject, payload: collected["playbook"].append(payload))
        bus.subscribe("strategy.sizing.>", lambda _subject, payload: collected["sizing"].append(payload))
        bus.subscribe("strategy.exit.>", lambda _subject, payload: collected["exit"].append(payload))
        bus.subscribe("intent.target_position.final.>", lambda _subject, payload: collected["target"].append(payload))

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

        self.assertGreaterEqual(len(collected["state"]), 2)
        self.assertEqual(len(collected["target"]), 1)

        state_4h = [item for item in collected["state"] if item.timeframe == Timeframe.TF_4H]
        playbook_4h = [item for item in collected["playbook"] if item.timeframe == Timeframe.TF_4H]
        sizing_4h = [item for item in collected["sizing"] if item.timeframe == Timeframe.TF_4H]
        targets_4h = [item for item in collected["target"] if item.timeframe == Timeframe.TF_4H]

        self.assertEqual(len(state_4h), 1)
        self.assertEqual(len(playbook_4h), 1)
        self.assertEqual(len(sizing_4h), 1)
        self.assertEqual(len(targets_4h), 1)

        cutoff = 3
        self.assertEqual(state_4h[0].cutoff_time_ns, cutoff)
        self.assertEqual(playbook_4h[0].cutoff_time_ns, cutoff)
        self.assertEqual(sizing_4h[0].cutoff_time_ns, cutoff)
        self.assertEqual(targets_4h[0].cutoff_time_ns, cutoff)
        self.assertFalse(targets_4h[0].reduce_only)

    def test_exit_decision_flows_into_reduce_only_target(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        ARCStrategyEngine(bus)
        StateEngine(bus)
        execution = ExecutionKernel(bus, ExecutionGateway(), risk)

        exits = []
        final_targets = []
        bus.subscribe("strategy.exit.>", lambda _subject, payload: exits.append(payload))
        bus.subscribe(
            "intent.target_position.final.>",
            lambda _subject, payload: final_targets.append(payload),
        )

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

        actionable_exits = [decision for decision in exits if decision.should_exit()]

        self.assertGreaterEqual(len(actionable_exits), 1)
        self.assertGreaterEqual(len(final_targets), 3)

        exit_decision = actionable_exits[-1]
        target = final_targets[-1]

        self.assertTrue(exit_decision.should_exit())
        self.assertTrue(target.reduce_only)
        self.assertEqual(target.cutoff_time_ns, exit_decision.cutoff_time_ns)
        self.assertAlmostEqual(target.target_qty, 0.0)
        self.assertIn("EXIT_TRAILING_STOP", target.reason_codes)
        self.assertAlmostEqual(execution.positions.get("BTCUSDT_PERP", 0.0), 0.0)

    def test_decision_chain_is_isolated_by_symbol(self) -> None:
        bus = InMemoryJetStream()
        risk = RiskOverlay(bus)
        ARCStrategyEngine(bus)
        StateEngine(bus)
        execution = ExecutionKernel(bus, ExecutionGateway(), risk)

        final_targets = []
        bus.subscribe(
            "intent.target_position.final.>",
            lambda _subject, payload: final_targets.append(payload),
        )

        bus.publish("md.trade.BINANCE.BTCUSDT_PERP", _trade(100.0, 1))
        bus.publish("md.trade.BINANCE.ETHUSDT_PERP", _trade(2000.0, 1, symbol="ETHUSDT_PERP"))
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
        bus.publish(
            "indicator.pack.v1.1d.ETHUSDT_PERP",
            _pack(
                symbol="ETHUSDT_PERP",
                timeframe=Timeframe.TF_1D,
                cutoff_time_ns=2,
                last_price=2000.0,
                cvd_slope=-0.4,
                structure_state="DOWNTREND",
                momentum_roc_z=-1.1,
            ),
        )
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
        bus.publish(
            "indicator.pack.v1.4h.ETHUSDT_PERP",
            _pack(
                symbol="ETHUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=3,
                last_price=2000.0,
                cvd_slope=-0.6,
                structure_state="DOWNTREND",
                momentum_roc_z=-1.4,
            ),
        )

        btc_targets = [
            payload
            for payload in final_targets
            if payload.symbol == "BTCUSDT_PERP" and payload.timeframe == Timeframe.TF_4H and payload.cutoff_time_ns == 3
        ]
        eth_targets = [
            payload
            for payload in final_targets
            if payload.symbol == "ETHUSDT_PERP" and payload.timeframe == Timeframe.TF_4H and payload.cutoff_time_ns == 3
        ]

        self.assertEqual(len(btc_targets), 1)
        self.assertEqual(len(eth_targets), 1)
        self.assertGreater(btc_targets[0].target_qty, 0.0)
        self.assertLess(eth_targets[0].target_qty, 0.0)
        self.assertGreater(execution.positions.get("BTCUSDT_PERP", 0.0), 0.0)
        self.assertLess(execution.positions.get("ETHUSDT_PERP", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
