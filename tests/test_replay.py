import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from zenquant.domain import IndicatorPack, MarketState, Timeframe
from zenquant.execution import ExecutionGateway, ExecutionKernel
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.historical.replay import _build_replay_context
from zenquant.historical.storage import HistoricalKlineRow, write_parquet_rows
from zenquant.indicator import IndicatorEngine, IndicatorStore
from zenquant.infra import DeterministicReplayer, InMemoryJetStream, StreamConfig
from zenquant.ingestor import SyntheticIngestor, SyntheticIngestorConfig
from zenquant.market import BarClockService, SnapshotBuilder
from zenquant.risk import RiskOverlay
from zenquant.state import StateEngine
from zenquant.strategy import ARCStrategyEngine


class ReplayTests(unittest.TestCase):
    def test_replay_context_cache_stats_report_reuse(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            five_min_dir = root / "klines" / "BTCUSDT" / "5m"
            four_hour_dir = root / "klines" / "BTCUSDT" / "4h"
            one_day_dir = root / "klines" / "BTCUSDT" / "1d"

            start_ns = 1_699_999_200_000_000_000
            write_parquet_rows(
                five_min_dir / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="5m",
                        open_time_ns=start_ns,
                        close_time_ns=start_ns + Timeframe.TF_5M.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.5,
                        volume=1.0,
                        trade_count=1,
                    )
                ],
            )
            write_parquet_rows(
                four_hour_dir / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="4h",
                        open_time_ns=start_ns,
                        close_time_ns=start_ns + Timeframe.TF_4H.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.5,
                        volume=1.0,
                        trade_count=1,
                    )
                ],
            )
            write_parquet_rows(
                one_day_dir / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="1d",
                        open_time_ns=start_ns,
                        close_time_ns=start_ns + Timeframe.TF_1D.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.5,
                        volume=1.0,
                        trade_count=1,
                    )
                ],
            )

            config = HistoricalBacktestConfig(
                root_dir=root,
                symbol="BTCUSDT",
                execution_timeframe=Timeframe.TF_5M,
                direction_timeframe=Timeframe.TF_4H,
                context_timeframe=Timeframe.TF_1H,
                start_ns=start_ns,
                end_ns=start_ns + Timeframe.TF_1D.seconds * 1_000_000_000,
            )
            with patch("zenquant.historical.replay.HFFeatureStore.load", return_value=None):
                first = _build_replay_context(config)
                second = _build_replay_context(config)

        self.assertFalse(bool(first.cache_stats["event_list_cache_hit"]))
        self.assertTrue(bool(second.cache_stats["event_list_cache_hit"]))
        self.assertGreaterEqual(float(second.cache_stats["raw_source_rows_cache_hit_rate"]), 0.5)
        self.assertGreaterEqual(float(second.cache_stats["store_cache_hit_rate"]), 0.5)

    def test_historical_replay_derives_1h_context_from_5m_when_train_window_missing_native_1h(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            five_min_dir = root / "klines" / "BTCUSDT" / "5m"
            four_hour_dir = root / "klines" / "BTCUSDT" / "4h"
            one_day_dir = root / "klines" / "BTCUSDT" / "1d"

            start_ns = 1_699_999_200_000_000_000
            five_min_rows = []
            for idx in range(12):
                open_ns = start_ns + idx * Timeframe.TF_5M.seconds * 1_000_000_000
                five_min_rows.append(
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="5m",
                        open_time_ns=open_ns,
                        close_time_ns=open_ns + Timeframe.TF_5M.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0 + idx,
                        high=101.0 + idx,
                        low=99.0 + idx,
                        close=100.5 + idx,
                        volume=10.0,
                        trade_count=5,
                    )
                )
            write_parquet_rows(five_min_dir / "data.parquet", five_min_rows)
            write_parquet_rows(
                four_hour_dir / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="4h",
                        open_time_ns=start_ns,
                        close_time_ns=start_ns + Timeframe.TF_4H.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=120.0,
                        low=95.0,
                        close=118.0,
                        volume=120.0,
                        trade_count=60,
                    )
                ],
            )
            write_parquet_rows(
                one_day_dir / "data.parquet",
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="1d",
                        open_time_ns=start_ns,
                        close_time_ns=start_ns + Timeframe.TF_1D.seconds * 1_000_000_000 - 1_000_000,
                        open=100.0,
                        high=130.0,
                        low=90.0,
                        close=125.0,
                        volume=240.0,
                        trade_count=120,
                    )
                ],
            )

            with patch("zenquant.historical.replay.HFFeatureStore.load", return_value=None):
                replay = HistoricalBacktest(
                    HistoricalBacktestConfig(
                        root_dir=root,
                        symbol="BTCUSDT",
                        execution_timeframe=Timeframe.TF_5M,
                        direction_timeframe=Timeframe.TF_4H,
                        context_timeframe=Timeframe.TF_1H,
                        start_ns=start_ns,
                        end_ns=start_ns + Timeframe.TF_1H.seconds * 1_000_000_000,
                    )
                )

                events = replay._load_events()

        timeframes = [event["event_timeframe"] for event in events]
        self.assertIn("1h", timeframes)
        one_hour_events = [event for event in events if event["event_timeframe"] == "1h"]
        self.assertEqual(len(one_hour_events), 1)

    def test_indicator_refactor_preserves_key_pack_values_and_profiling_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily_dir = root / "klines" / "BTCUSDT" / "1d"
            four_hour_dir = root / "klines" / "BTCUSDT" / "4h"
            funding_dir = root / "funding" / "BTCUSDT"
            oi_dir = root / "open_interest" / "BTCUSDT" / "4h"
            base_ns = 1_704_067_200_000_000_000
            day_ns = Timeframe.TF_1D.seconds * 1_000_000_000
            four_hour_ns = Timeframe.TF_4H.seconds * 1_000_000_000
            daily_rows = []
            four_hour_rows = []
            funding_rows = []
            oi_rows = []
            for idx in range(220):
                open_ns = base_ns + idx * day_ns
                px = 40_000 + idx * 50
                daily_rows.append(
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="1d",
                        open_time_ns=open_ns,
                        close_time_ns=open_ns + day_ns - 1_000_000,
                        open=px,
                        high=px + 200,
                        low=px - 150,
                        close=px + 30,
                        volume=1000.0 + idx,
                        trade_count=100,
                    )
                )
            for idx in range(220 * 6):
                open_ns = base_ns + idx * four_hour_ns
                px = 40_000 + idx * 8
                four_hour_rows.append(
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe="4h",
                        open_time_ns=open_ns,
                        close_time_ns=open_ns + four_hour_ns - 1_000_000,
                        open=px,
                        high=px + 80,
                        low=px - 60,
                        close=px + 20,
                        volume=150.0 + idx,
                        trade_count=50,
                    )
                )
                oi_rows.append(
                    {
                        "symbol": "BTCUSDT",
                        "timeframe": "4h",
                        "ts_ns": open_ns + four_hour_ns - 1_000_000,
                        "open_interest": 50_000.0 + idx * 5,
                    }
                )
                if idx % 2 == 0:
                    funding_rows.append(
                        {
                            "symbol": "BTCUSDT",
                            "funding_time_ns": open_ns + four_hour_ns - 1_000_000,
                            "funding_rate": 0.0001,
                        }
                    )
            write_parquet_rows(daily_dir / "data.parquet", daily_rows)
            write_parquet_rows(four_hour_dir / "data.parquet", four_hour_rows)
            write_parquet_rows(funding_dir / "data.parquet", funding_rows)
            write_parquet_rows(
                oi_dir / "data.parquet",
                oi_rows,
            )

            first_bt = HistoricalBacktest(
                HistoricalBacktestConfig(
                    root_dir=root,
                    symbol="BTCUSDT",
                    profiling_enabled=True,
                )
            )
            first = first_bt.run()
            second_bt = HistoricalBacktest(
                HistoricalBacktestConfig(
                    root_dir=root,
                    symbol="BTCUSDT",
                    profiling_enabled=True,
                )
            )
            second = second_bt.run()

            first_pack = [p for p in first_bt._indicators if p.timeframe == Timeframe.TF_4H][-1]
            second_pack = [p for p in second_bt._indicators if p.timeframe == Timeframe.TF_4H][-1]

        keys = [
            "realized_vol",
            "atr",
            "ob_liq_z",
            "funding_oi_z",
            "zen_pulse",
            "structure_state",
            "momentum_roc_z",
            "cycle_position",
        ]
        for key in keys:
            self.assertEqual(first_pack.values.get(key), second_pack.values.get(key), key)
        self.assertIn("indicator_layer_timing", first.profiling)
        self.assertIn("indicator_fast_path_enabled", first.profiling)
        self.assertIn("slow_enrichment_deferred", first.profiling)
        self.assertIn("core_execution_indicators_s", first.profiling["indicator_layer_timing"])

    def test_replay_preserves_message_order(self) -> None:
        source_bus = InMemoryJetStream()
        source_bus.add_stream(StreamConfig(name="SRC", subjects=["md.>"]))
        payloads = ["a", "b", "c", "d"]
        for payload in payloads:
            source_bus.publish("md.trade.BINANCE.BTC_USDT", payload)

        replayer = DeterministicReplayer.from_stream(source_bus, "SRC")
        target_bus = InMemoryJetStream()
        replayed: list[str] = []
        target_bus.subscribe("md.>", lambda _subject, data: replayed.append(data))
        replayer.replay(target_bus)

        self.assertEqual(replayed, payloads)

    def test_replay_parity_for_indicators_intents_and_execution(self) -> None:
        source_bus = InMemoryJetStream()
        source_bus.add_stream(
            StreamConfig(
                name="STREAM_RAW_MARKET_DATA",
                subjects=[
                    "md.trade.>",
                    "md.book.>",
                    "md.funding.>",
                    "md.oi.>",
                ],
            )
        )

        source_store = IndicatorStore()
        SnapshotBuilder(source_bus)
        BarClockService(source_bus)
        IndicatorEngine(source_bus, source_store)
        source_risk = RiskOverlay(source_bus)
        ARCStrategyEngine(source_bus)
        StateEngine(source_bus)
        ExecutionKernel(source_bus, ExecutionGateway(), source_risk)

        src_indicators = []
        src_states = []
        src_intents = []
        src_reports = []

        def _on_indicator(_subject: str, payload: object) -> None:
            # Phase 2: filter out non-IndicatorPack messages (e.g., SwingStructure)
            if not isinstance(payload, IndicatorPack):
                return
            src_indicators.append(
                (
                    payload.symbol,
                    payload.timeframe.value,
                    payload.cutoff_time_ns,
                    round(float(payload.values.get("zen_pulse", 0.0)), 6),
                )
            )

        source_bus.subscribe("indicator.>", _on_indicator)
        source_bus.subscribe(
            "state.market.>",
            lambda _subject, payload: (
                src_states.append(
                    (
                        payload.symbol,
                        payload.timeframe.value,
                        payload.cutoff_time_ns,
                        payload.regime,
                        round(payload.transition_risk, 6),
                    )
                )
                if isinstance(payload, MarketState)
                else None
            ),
        )
        source_bus.subscribe(
            "intent.target_position.final.>",
            lambda _subject, payload: src_intents.append(
                (
                    payload.symbol,
                    payload.timeframe.value,
                    payload.cutoff_time_ns,
                    round(payload.target_qty, 6),
                    round(payload.target_leverage, 6),
                    payload.reduce_only,
                )
            ),
        )
        source_bus.subscribe(
            "exec.order.report",
            lambda _subject, payload: src_reports.append(
                (
                    payload.symbol,
                    payload.status,
                    payload.side,
                    round(payload.filled_qty, 6),
                    payload.transaction_time_ns,
                )
            ),
        )

        ingestor = SyntheticIngestor(source_bus, config=SyntheticIngestorConfig(seed=23))
        ingestor.run(steps=360)

        replayer = DeterministicReplayer.from_stream(source_bus, "STREAM_RAW_MARKET_DATA")

        target_bus = InMemoryJetStream()
        target_store = IndicatorStore()
        SnapshotBuilder(target_bus)
        BarClockService(target_bus)
        IndicatorEngine(target_bus, target_store)
        target_risk = RiskOverlay(target_bus)
        ARCStrategyEngine(target_bus)
        StateEngine(target_bus)
        ExecutionKernel(target_bus, ExecutionGateway(), target_risk)

        dst_indicators = []
        dst_states = []
        dst_intents = []
        dst_reports = []

        def _on_replay_indicator(_subject: str, payload: object) -> None:
            # Phase 2: filter out non-IndicatorPack messages
            if not isinstance(payload, IndicatorPack):
                return
            dst_indicators.append(
                (
                    payload.symbol,
                    payload.timeframe.value,
                    payload.cutoff_time_ns,
                    round(float(payload.values.get("zen_pulse", 0.0)), 6),
                )
            )

        target_bus.subscribe("indicator.>", _on_replay_indicator)
        target_bus.subscribe(
            "state.market.>",
            lambda _subject, payload: (
                dst_states.append(
                    (
                        payload.symbol,
                        payload.timeframe.value,
                        payload.cutoff_time_ns,
                        payload.regime,
                        round(payload.transition_risk, 6),
                    )
                )
                if isinstance(payload, MarketState)
                else None
            ),
        )
        target_bus.subscribe(
            "intent.target_position.final.>",
            lambda _subject, payload: dst_intents.append(
                (
                    payload.symbol,
                    payload.timeframe.value,
                    payload.cutoff_time_ns,
                    round(payload.target_qty, 6),
                    round(payload.target_leverage, 6),
                    payload.reduce_only,
                )
            ),
        )
        target_bus.subscribe(
            "exec.order.report",
            lambda _subject, payload: dst_reports.append(
                (
                    payload.symbol,
                    payload.status,
                    payload.side,
                    round(payload.filled_qty, 6),
                    payload.transaction_time_ns,
                )
            ),
        )

        replayer.replay(target_bus)

        self.assertEqual(src_indicators, dst_indicators)
        self.assertEqual(src_states, dst_states)
        self.assertEqual(src_intents, dst_intents)
        self.assertEqual(src_reports, dst_reports)


if __name__ == "__main__":
    unittest.main()
