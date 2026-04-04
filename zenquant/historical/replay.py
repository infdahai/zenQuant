"""Historical replay/backtest runner built on the live runtime contracts.

The goal is not to create a separate research-only engine. Historical mode
publishes the same market events the live kernel consumes so that strategy,
risk, and execution behavior stays auditable across modes.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zenquant.data import (
    BasisDataStore,
    BasisStoreConfig,
    CotDataStore,
    CotStoreConfig,
    MacroDataStore,
    MacroDataStoreConfig,
    OptionsDataStore,
    OptionsDataStoreConfig,
)
from zenquant.domain import (
    BacktestDiagnostics,
    BacktestMetrics,
    BarCloseEvent,
    Candle,
    DirectionalBias,
    EventMeta,
    ExecutionReport,
    ExitDecision,
    IndicatorPack,
    MarketSnapshot,
    MarketState,
    MarkPriceUpdate,
    OpenPositionState,
    PlaybookSelection,
    RiskSnapshot,
    SizingDecision,
    TargetPosition,
    Timeframe,
)
from zenquant.execution import ExecutionKernel, SimulatedGateway
from zenquant.indicator import IndicatorEngine, IndicatorEngineConfig, IndicatorStore
from zenquant.indicator.hf_features import HFFeatureStore, HFFeatureStoreConfig
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.plugins.profile import build_from_profile
from zenquant.risk import RiskOverlay, RiskOverlayConfig
from zenquant.state import StateEngine
from zenquant.strategy import ARCStrategyConfig, ARCStrategyEngine

from .perturbation import PerturbationConfig, perturb_events
from .storage import read_parquet_rows


@dataclass(slots=True)
class HistoricalBacktestConfig:
    """Configuration for a single-symbol historical replay run."""

    root_dir: Path = Path("data/raw/binance")
    symbol: str = "BTCUSDT"
    portfolio_symbol: str = "BTCUSDT_PERP"
    start_ns: int | None = None
    end_ns: int | None = None
    starting_equity: float = 100_000.0
    execution_timeframe: Timeframe = Timeframe.TF_4H
    bias_timeframe: Timeframe = Timeframe.TF_1D
    # Dual-layer: set to TF_4H when execution is TF_1H (4H constrains 1H)
    context_timeframe: Timeframe | None = None
    open_interest_timeframe: str = "4h"
    # Strategy config override (for parameter sweeps)
    strategy_config: ARCStrategyConfig | None = None
    # Risk config override (for risk appetite tuning)
    risk_config: RiskOverlayConfig | None = None
    # Domain randomization (for robustness testing)
    perturbation: PerturbationConfig | None = None
    # Macro data directory (VIX, DXY, Treasury yields)
    macro_dir: Path = Path("data/raw/macro")
    # Options data directory (Deribit IV, skew, term structure)
    options_dir: Path = Path("data/raw/deribit_options")
    # CFTC COT data path
    cot_path: Path = Path("data/raw/cftc/cot_btc.parquet")
    # Plugin profile path (default.toml, etc.)
    profile_path: Path | None = Path("config/profiles/default.toml")


@dataclass(slots=True)
class HistoricalBacktestResult:
    """Compact result returned by the backtest runner."""

    metrics: BacktestMetrics
    diagnostics: BacktestDiagnostics
    total_indicator_packs: int
    total_targets: int
    total_reports: int
    final_equity: float


class HistoricalBacktest:
    """Replay historical Parquet data through the main trading kernel.

    The runner intentionally uses the production indicator, strategy, risk,
    and execution components. Historical mode is therefore a transport layer
    adaptation, not a second decision stack.
    """

    def __init__(self, config: HistoricalBacktestConfig | None = None) -> None:
        self._config = config or HistoricalBacktestConfig()
        self.bus = InMemoryJetStream()
        self.bus.add_stream(StreamConfig(name="STREAM_MARKET_DATA", subjects=["md.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_CLOCK", subjects=["clock.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INDICATORS", subjects=["indicator.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INTENTS", subjects=["intent.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_EXEC", subjects=["exec.>"]))

        self.indicator_store = IndicatorStore()
        # Load macro data if available
        self._macro_store = MacroDataStore(MacroDataStoreConfig(macro_dir=self._config.macro_dir))
        self._macro_store.load()
        # Load options data if available
        self._options_store = OptionsDataStore(
            OptionsDataStoreConfig(options_dir=self._config.options_dir, currency="BTC")
        )
        self._options_store.load()
        # Load HF (5min) features if available
        hf_dir = self._config.root_dir / "klines" / self._config.symbol / "5m"
        self._hf_store = HFFeatureStore(HFFeatureStoreConfig(data_dir=hf_dir))
        self._hf_store.load()
        # Load CFTC COT data if available
        self._cot_store = CotDataStore(CotStoreConfig(cot_path=self._config.cot_path))
        self._cot_store.load()
        # Load spot-perp basis data if available
        self._basis_store = BasisDataStore(BasisStoreConfig(
            spot_dir=Path("data/raw/binance_spot/klines") / self._config.symbol / "4h",
            perp_dir=self._config.root_dir / "klines" / self._config.symbol / "4h",
        ))
        self._basis_store.load()
        self.indicator_engine = IndicatorEngine(
            bus=self.bus,
            store=self.indicator_store,
            config=IndicatorEngineConfig(expected_symbols=(self._config.portfolio_symbol,)),
            macro_store=self._macro_store if self._macro_store.loaded else None,
            options_store=self._options_store if self._options_store.loaded else None,
            hf_store=self._hf_store if self._hf_store.loaded else None,
            cot_store=self._cot_store if self._cot_store.loaded else None,
            basis_store=self._basis_store if self._basis_store.loaded else None,
        )
        self.risk_overlay = RiskOverlay(
            bus=self.bus,
            config=self._config.risk_config or RiskOverlayConfig(starting_equity=self._config.starting_equity),
        )
        # Load plugin registry if profile path provided
        plugin_registry = None
        if self._config.profile_path and self._config.profile_path.exists():
            plugin_root = Path(__file__).parent.parent / "plugins"
            _, plugin_registry = build_from_profile(self._config.profile_path, plugin_root=plugin_root)
        self.strategy_engine = ARCStrategyEngine(
            bus=self.bus,
            config=self._config.strategy_config,
            plugin_registry=plugin_registry,
        )
        self.state_engine = StateEngine(bus=self.bus)
        self.execution_kernel = ExecutionKernel(
            bus=self.bus,
            gateway=SimulatedGateway(),
            risk_overlay=self.risk_overlay,
        )

        self._indicators: list[IndicatorPack] = []
        self._market_states: list[MarketState] = []
        self._biases: list[DirectionalBias] = []
        self._playbooks: list[PlaybookSelection] = []
        self._sizings: list[SizingDecision] = []
        self._exits: list[ExitDecision] = []
        self._position_states: list[OpenPositionState] = []
        self._targets: list[TargetPosition] = []
        self._reports: list[ExecutionReport] = []
        self._risk_snapshots: list[RiskSnapshot] = []
        self._execution_rows: list[dict[str, Any]] = []
        self._trade_ledger = _TradeLedger()
        self._funding_accumulator = _FundingAccumulator()
        self._funding_rows: list[dict[str, Any]] = []
        self._open_interest_rows: list[dict[str, Any]] = []
        self._funding_index = -1
        self._open_interest_index = -1

        self.bus.subscribe("indicator.>", self._collect_indicator)
        self.bus.subscribe("state.market.>", self._collect_market_state)
        self.bus.subscribe("strategy.bias.1d.>", self._collect_bias)
        self.bus.subscribe("strategy.playbook.>", self._collect_playbook)
        self.bus.subscribe("strategy.sizing.>", self._collect_sizing)
        self.bus.subscribe("strategy.exit.>", self._collect_exit)
        self.bus.subscribe("strategy.position_state.>", self._collect_position_state)
        self.bus.subscribe("intent.target_position.final.>", self._collect_target)
        self.bus.subscribe("exec.order.report", self._collect_report)
        self.bus.subscribe("risk.snapshot", self._collect_risk_snapshot)

    def run(self) -> HistoricalBacktestResult:
        """Run the historical replay and return summary metrics.

        Example:
            result = HistoricalBacktest(HistoricalBacktestConfig(root_dir=Path("data/raw/binance"))).run()
            print(result.metrics.net_pnl)
        """

        events = self._load_events()
        if not events:
            raise RuntimeError(
                "no historical events available for replay; "
                f"expected Parquet data under {self._config.root_dir} for symbol {self._config.symbol}"
            )
        if self._config.perturbation is not None:
            events = perturb_events(events, self._config.perturbation)
        for event in events:
            self._publish_event(event)

        metrics = self._build_metrics(events)
        diagnostics = self._build_diagnostics()
        return HistoricalBacktestResult(
            metrics=metrics,
            diagnostics=diagnostics,
            total_indicator_packs=len(self._indicators),
            total_targets=len(self._targets),
            total_reports=len(self._reports),
            final_equity=self.risk_overlay.equity,
        )

    def _load_events(self) -> list[dict[str, Any]]:
        """Load candles and auxiliary market series in replay order."""
        self._funding_rows = read_parquet_rows(
            str(self._config.root_dir / "funding" / self._config.symbol / "*.parquet"),
            where_sql=_time_filter_sql(self._config.start_ns, self._config.end_ns, column="funding_time_ns"),
            params=_time_filter_params(self._config.start_ns, self._config.end_ns),
            order_by="funding_time_ns ASC",
        )
        self._open_interest_rows = read_parquet_rows(
            str(
                self._config.root_dir
                / "open_interest"
                / self._config.symbol
                / self._config.open_interest_timeframe
                / "*.parquet"
            ),
            where_sql=_time_filter_sql(self._config.start_ns, self._config.end_ns, column="ts_ns"),
            params=_time_filter_params(self._config.start_ns, self._config.end_ns),
            order_by="ts_ns ASC",
        )
        # Load pre-aggregated aggTrades (real order flow CVD)
        self._aggtrades: dict[str, dict[int, dict[str, Any]]] = {}
        for tf_label in ("4h", "1h"):
            agg_glob = str(self._config.root_dir / "aggtrades" / self._config.symbol / tf_label / "*.parquet")
            agg_rows = read_parquet_rows(agg_glob, order_by="ts_ns ASC")
            if agg_rows:
                bucket = {}
                for agg_row in agg_rows:
                    bucket[int(agg_row["ts_ns"])] = agg_row
                self._aggtrades[tf_label] = bucket
                print(f"  Loaded {len(bucket)} {tf_label} aggTrades buckets (real CVD)")

        self._funding_index = -1
        self._open_interest_index = -1

        candles: list[dict[str, Any]] = []
        # Build unique set of timeframes to load (bias → context → execution)
        timeframes_to_load: list[Timeframe] = [self._config.bias_timeframe]
        if self._config.context_timeframe and self._config.context_timeframe not in timeframes_to_load:
            timeframes_to_load.append(self._config.context_timeframe)
        if self._config.execution_timeframe not in timeframes_to_load:
            timeframes_to_load.append(self._config.execution_timeframe)

        for timeframe in timeframes_to_load:
            rows = read_parquet_rows(
                str(self._config.root_dir / "klines" / self._config.symbol / timeframe.value / "*.parquet"),
                where_sql=_time_filter_sql(self._config.start_ns, self._config.end_ns, column="close_time_ns"),
                params=_time_filter_params(self._config.start_ns, self._config.end_ns),
                order_by="close_time_ns ASC",
            )
            for row in rows:
                row["event_timeframe"] = timeframe.value
                candles.append(row)

        candles.sort(key=lambda item: (int(item["close_time_ns"]), _timeframe_priority(str(item["event_timeframe"]))))
        return candles

    def _publish_event(self, row: dict[str, Any]) -> None:
        """Publish the same core event families the live runtime consumes."""
        timeframe = _parse_timeframe(str(row["event_timeframe"]))
        cutoff_ns = int(row["close_time_ns"])
        funding_row = self._latest_funding(cutoff_ns)
        oi_row = self._latest_open_interest(cutoff_ns)
        meta = EventMeta(
            event_time_ns=cutoff_ns,
            ingest_time_ns=cutoff_ns,
            venue="BINANCE_HISTORICAL",
            symbol=self._config.portfolio_symbol,
            stream_seq=cutoff_ns,
        )
        last_price = float(row["close"])

        self.bus.publish(
            f"md.mark.BINANCE_HISTORICAL.{self._config.portfolio_symbol}",
            MarkPriceUpdate(meta=meta, mark_price=last_price, index_price=last_price),
        )
        self.bus.publish(
            f"clock.bar_close.{timeframe.value}",
            BarCloseEvent(
                timeframe=timeframe,
                symbol=self._config.portfolio_symbol,
                bar_start_ns=int(row["open_time_ns"]),
                bar_end_ns=cutoff_ns,
            ),
        )
        self.bus.publish(
            f"md.candle.{timeframe.value}.{self._config.portfolio_symbol}",
            Candle(
                symbol=self._config.portfolio_symbol,
                timeframe=timeframe,
                bar_start_ns=int(row["open_time_ns"]),
                bar_end_ns=cutoff_ns,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=last_price,
                volume=float(row["volume"]),
                trade_count=int(row["trade_count"]),
            ),
        )
        # Use real aggTrades CVD if available, otherwise fall back to volume proxy
        tf_label = str(row.get("event_timeframe", "4h"))
        agg_buckets = self._aggtrades.get(tf_label, self._aggtrades.get("4h", {}))
        agg_bucket = agg_buckets.get(int(row["open_time_ns"]))
        if agg_bucket is not None:
            cvd_delta = float(agg_bucket["cvd_delta"])
            vpin = float(agg_bucket.get("vpin", 0.0))
            large_trade_count = int(agg_bucket.get("large_trade_count", 0))
        else:
            cvd_delta = _proxy_cvd_delta(row)
            vpin = 0.0
            large_trade_count = 0

        self.bus.publish(
            f"md.snapshot.{timeframe.value}.{self._config.portfolio_symbol}",
            MarketSnapshot(
                symbol=self._config.portfolio_symbol,
                timeframe=timeframe,
                cutoff_time_ns=cutoff_ns,
                last_price=last_price,
                bid_depth=max(float(row["volume"]), 1.0),
                ask_depth=max(float(row["volume"]), 1.0),
                book_age_ms=0,
                venue_count=1,
                cvd_delta=cvd_delta,
                funding_oi_weighted=float(funding_row.get("funding_rate", 0.0)) if funding_row else None,
                oi_coverage=1.0 if oi_row else 0.0,
                staleness_ms=_aux_staleness_ms(cutoff_ns, funding_row, oi_row),
                quality_flags=["OK"] if oi_row else ["MISSING_OI"],
                vpin=vpin,
                large_trade_count=large_trade_count,
            ),
        )
        if timeframe == self._config.execution_timeframe:
            self._execution_rows.append(dict(row))
            # Accumulate funding costs on open positions
            if funding_row is not None:
                self._funding_accumulator.accrue(
                    funding_rate=float(funding_row.get("funding_rate", 0.0)),
                    position_qty=self._trade_ledger.position_qty,
                    mark_price=last_price,
                    funding_time_ns=int(funding_row.get("funding_time_ns", cutoff_ns)),
                    cutoff_ns=cutoff_ns,
                )

    def _latest_funding(self, cutoff_ns: int) -> dict[str, Any] | None:
        while (
            self._funding_index + 1 < len(self._funding_rows)
            and int(self._funding_rows[self._funding_index + 1]["funding_time_ns"]) <= cutoff_ns
        ):
            self._funding_index += 1
        if self._funding_index < 0:
            return None
        return self._funding_rows[self._funding_index]

    def _latest_open_interest(self, cutoff_ns: int) -> dict[str, Any] | None:
        while (
            self._open_interest_index + 1 < len(self._open_interest_rows)
            and int(self._open_interest_rows[self._open_interest_index + 1]["ts_ns"]) <= cutoff_ns
        ):
            self._open_interest_index += 1
        if self._open_interest_index < 0:
            return None
        return self._open_interest_rows[self._open_interest_index]

    def _collect_indicator(self, _: str, payload: object) -> None:
        if isinstance(payload, IndicatorPack):
            self._indicators.append(payload)

    def _collect_target(self, _: str, payload: object) -> None:
        if isinstance(payload, TargetPosition):
            self._targets.append(payload)

    def _collect_market_state(self, _: str, payload: object) -> None:
        if isinstance(payload, MarketState):
            self._market_states.append(payload)

    def _collect_bias(self, _: str, payload: object) -> None:
        if isinstance(payload, DirectionalBias):
            self._biases.append(payload)

    def _collect_playbook(self, _: str, payload: object) -> None:
        if isinstance(payload, PlaybookSelection):
            self._playbooks.append(payload)

    def _collect_sizing(self, _: str, payload: object) -> None:
        if isinstance(payload, SizingDecision):
            self._sizings.append(payload)

    def _collect_exit(self, _: str, payload: object) -> None:
        if isinstance(payload, ExitDecision):
            self._exits.append(payload)

    def _collect_position_state(self, _: str, payload: object) -> None:
        if isinstance(payload, OpenPositionState):
            self._position_states.append(payload)

    def _collect_report(self, _: str, payload: object) -> None:
        if isinstance(payload, ExecutionReport):
            self._reports.append(payload)
            self._trade_ledger.on_execution_report(payload)

    def _collect_risk_snapshot(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskSnapshot):
            self._risk_snapshots.append(payload)

    def _build_metrics(self, events: list[dict[str, Any]]) -> BacktestMetrics:
        returns = _equity_returns(self._risk_snapshots)
        sharpe = _annualized_ratio(returns, periods_per_year=6 * 365)
        sortino = _annualized_ratio(
            [value for value in returns if value < 0.0],
            periods_per_year=6 * 365,
            baseline_returns=returns,
        )
        max_drawdown, max_dd_duration = _max_drawdown(self._risk_snapshots)
        total_funding = self._funding_accumulator.total_funding_cost
        gross_pnl = self.risk_overlay.equity - self._config.starting_equity + self._trade_ledger.total_fees
        elapsed_days = max(
            1.0,
            (int(events[-1]["close_time_ns"]) - int(events[0]["open_time_ns"])) / 1_000_000_000 / 86_400,
        )
        realized = self._trade_ledger.realized_pnls
        wins = [value for value in realized if value > 0]
        losses = [value for value in realized if value < 0]
        total_bars = sum(1 for item in events if item["event_timeframe"] == self._config.execution_timeframe.value)
        return BacktestMetrics(
            symbol=self._config.portfolio_symbol,
            start_ns=int(events[0]["open_time_ns"]),
            end_ns=int(events[-1]["close_time_ns"]),
            total_bars=total_bars,
            net_pnl=round(self.risk_overlay.equity - self._config.starting_equity, 2),
            gross_pnl=round(gross_pnl, 2),
            total_fees=round(self._trade_ledger.total_fees, 2),
            total_funding=round(total_funding, 2),
            sharpe=round(sharpe, 4),
            sortino=round(sortino, 4),
            max_drawdown=round(max_drawdown, 6),
            max_dd_duration_bars=max_dd_duration,
            win_rate=round((len(wins) / len(realized)) if realized else 0.0, 6),
            profit_factor=round(
                (sum(wins) / abs(sum(losses))) if wins and losses and sum(losses) != 0 else 0.0,
                6,
            ),
            avg_win=round((sum(wins) / len(wins)) if wins else 0.0, 2),
            avg_loss=round((sum(losses) / len(losses)) if losses else 0.0, 2),
            trade_count=len(self._reports),
            turnover_per_day=round(self._trade_ledger.turnover_notional / elapsed_days, 2),
            fee_drag_pct=round((self._trade_ledger.total_fees / gross_pnl) if gross_pnl > 0 else 0.0, 6),
        )

    def _build_diagnostics(self) -> BacktestDiagnostics:
        playbook_counts = Counter(payload.playbook_id for payload in self._playbooks)
        actionable_exit_counts = Counter(payload.exit_type for payload in self._exits if payload.should_exit())
        risk_flag_counts = Counter()
        for snapshot in self._risk_snapshots:
            for flag in snapshot.flags:
                risk_flag_counts[flag] += 1
        target_reason_counts = Counter()
        for target in self._targets:
            for code in target.reason_codes:
                target_reason_counts[code] += 1

        return BacktestDiagnostics(
            playbook_counts=dict(playbook_counts),
            actionable_exit_counts=dict(actionable_exit_counts),
            risk_flag_counts=dict(risk_flag_counts),
            top_target_reason_codes=dict(target_reason_counts.most_common(50)),
        )

    def build_analysis_rows(self, *, horizons: tuple[int, ...] = (1, 3, 6)) -> list[dict[str, Any]]:
        """Build one joined research row per execution-timeframe cutoff."""
        execution_rows = sorted(
            self._execution_rows,
            key=lambda row: int(row["close_time_ns"]),
        )
        if not execution_rows:
            return []

        indicator_map = {
            payload.cutoff_time_ns: payload
            for payload in self._indicators
            if payload.timeframe == self._config.execution_timeframe
        }
        state_map = {
            payload.cutoff_time_ns: payload
            for payload in self._market_states
            if payload.timeframe == self._config.execution_timeframe
        }
        playbook_map = {
            payload.cutoff_time_ns: payload
            for payload in self._playbooks
            if payload.timeframe == self._config.execution_timeframe
        }
        sizing_map = {
            payload.cutoff_time_ns: payload
            for payload in self._sizings
            if payload.timeframe == self._config.execution_timeframe
        }
        exit_map = {
            payload.cutoff_time_ns: payload
            for payload in self._exits
            if payload.timeframe == self._config.execution_timeframe
        }
        target_map = {
            payload.cutoff_time_ns: payload
            for payload in self._targets
            if payload.timeframe == self._config.execution_timeframe
        }
        risk_map = {payload.ts_ns: payload for payload in self._risk_snapshots}
        bias_points = sorted(self._biases, key=lambda payload: payload.cutoff_time_ns)
        bias_index = 0
        latest_bias: DirectionalBias | None = None

        rows: list[dict[str, Any]] = []
        total = len(execution_rows)
        for index, event_row in enumerate(execution_rows):
            cutoff_ns = int(event_row["close_time_ns"])
            while bias_index < len(bias_points) and bias_points[bias_index].cutoff_time_ns <= cutoff_ns:
                latest_bias = bias_points[bias_index]
                bias_index += 1

            indicator = indicator_map.get(cutoff_ns)
            state = state_map.get(cutoff_ns)
            playbook = playbook_map.get(cutoff_ns)
            sizing = sizing_map.get(cutoff_ns)
            exit_decision = exit_map.get(cutoff_ns)
            target = target_map.get(cutoff_ns)
            risk_snapshot = risk_map.get(cutoff_ns)
            close_price = float(event_row["close"])

            row = {
                "symbol": self._config.portfolio_symbol,
                "timeframe": self._config.execution_timeframe.value,
                "cutoff_time_ns": cutoff_ns,
                "close_price": close_price,
                "open_price": float(event_row["open"]),
                "high_price": float(event_row["high"]),
                "low_price": float(event_row["low"]),
                "volume": float(event_row["volume"]),
                "trade_count": int(event_row["trade_count"]),
                "bars_to_end": total - index - 1,
            }
            for horizon in horizons:
                row[f"future_return_{horizon}"] = _forward_return(execution_rows, index, horizon)
            row["mae_3"] = _max_adverse_excursion(execution_rows, index, 3)
            row["mfe_3"] = _max_favorable_excursion(execution_rows, index, 3)

            if indicator is not None:
                row.update(
                    {
                        "indicator_quality_flags": "|".join(indicator.quality_flags),
                        "realized_vol": float(indicator.values.get("realized_vol", 0.0) or 0.0),
                        "cvd_slope": float(indicator.values.get("cvd_slope", 0.0) or 0.0),
                        "funding_oi_z": float(indicator.values.get("funding_oi_z", 0.0) or 0.0),
                        "ob_liq_z": float(indicator.values.get("ob_liq_z", 0.0) or 0.0),
                        "zen_pulse": float(indicator.values.get("zen_pulse", 0.0) or 0.0),
                        "atr": float(indicator.values.get("atr", 0.0) or 0.0),
                        "momentum_roc_z": float(indicator.values.get("momentum_roc_z", 0.0) or 0.0),
                        "structure_state": str(
                            indicator.values.get("structure_state")
                            or indicator.values.get("swing_structure")
                            or "NEUTRAL"
                        ),
                    }
                )

            if state is not None:
                row.update(
                    {
                        "directional_state": state.directional_state,
                        "directional_confidence": state.directional_confidence,
                        "volatility_state": state.volatility_state,
                        "volatility_confidence": state.volatility_confidence,
                        "positioning_state": state.positioning_state,
                        "positioning_confidence": state.positioning_confidence,
                        "quality_state": state.quality_state,
                        "quality_confidence": state.quality_confidence,
                        "regime": state.regime,
                        "regime_confidence": state.regime_confidence,
                        "direction_signal": state.direction_signal,
                        "transition_risk": state.transition_risk,
                        "state_quality_flags": "|".join(state.quality_flags),
                        "state_reason_codes": "|".join(state.reason_codes),
                    }
                )

            if latest_bias is not None:
                row.update(
                    {
                        "bias_cutoff_time_ns": latest_bias.cutoff_time_ns,
                        "bias_direction": latest_bias.direction,
                        "bias_regime": latest_bias.regime,
                        "bias_confidence": latest_bias.confidence,
                        "bias_quality_flags": "|".join(latest_bias.quality_flags),
                    }
                )

            if playbook is not None:
                row.update(
                    {
                        "playbook_id": playbook.playbook_id,
                        "playbook_confidence": playbook.playbook_confidence,
                        "playbook_max_leverage": playbook.max_leverage,
                        "trailing_stop_atr_mult": playbook.trailing_stop_atr_mult,
                        "allow_pyramid": playbook.allow_pyramid,
                        "max_pyramid_count": playbook.max_pyramid_count,
                        "time_decay_bars": playbook.time_decay_bars,
                        "playbook_reason_codes": "|".join(playbook.reason_codes),
                    }
                )

            if sizing is not None:
                row.update(
                    {
                        "base_size": sizing.base_size,
                        "confidence_multiplier": sizing.confidence_multiplier,
                        "stress_discount": sizing.stress_discount,
                        "execution_haircut": sizing.execution_haircut,
                        "final_leverage": sizing.final_leverage,
                        "sizing_reason_codes": "|".join(sizing.reason_codes),
                    }
                )

            if exit_decision is not None:
                row.update(
                    {
                        "exit_type": exit_decision.exit_type,
                        "exit_urgency": exit_decision.exit_urgency,
                        "target_reduction_pct": exit_decision.target_reduction_pct,
                        "stop_price": exit_decision.stop_price,
                        "exit_reason_code": exit_decision.reason_code,
                        "exit_reason_detail": exit_decision.reason_detail,
                        "exit_reason_codes": "|".join(exit_decision.reason_codes),
                    }
                )

            if target is not None:
                row.update(
                    {
                        "target_qty": target.target_qty,
                        "target_leverage": target.target_leverage,
                        "reduce_only": target.reduce_only,
                        "target_regime": target.regime,
                        "target_regime_confidence": target.regime_confidence,
                        "target_reason_codes": "|".join(target.reason_codes),
                        "target_direction": 1.0 if target.target_qty > 0 else -1.0 if target.target_qty < 0 else 0.0,
                    }
                )

            if risk_snapshot is not None:
                row.update(
                    {
                        "equity": risk_snapshot.equity,
                        "drawdown": risk_snapshot.drawdown,
                        "lev_cap": risk_snapshot.lev_cap,
                        "reduce_only_mode": risk_snapshot.reduce_only_mode,
                        "risk_flags": "|".join(risk_snapshot.flags),
                    }
                )

            rows.append(row)
        return rows


class _TradeLedger:
    """Track realized PnL from fills without introducing a second execution model."""

    def __init__(self) -> None:
        self.position_qty = 0.0
        self.avg_entry = 0.0
        self.realized_pnls: list[float] = []
        self.total_fees = 0.0
        self.turnover_notional = 0.0

    def on_execution_report(self, report: ExecutionReport) -> None:
        """Update realized PnL state from a single filled report."""
        if report.status != "FILLED" or report.filled_qty <= 0 or report.avg_price <= 0:
            return
        signed_qty = report.side * report.filled_qty
        self.total_fees += report.fee
        self.turnover_notional += abs(signed_qty) * report.avg_price

        if self.position_qty == 0 or self.position_qty * signed_qty > 0:
            total_qty = abs(self.position_qty) + abs(signed_qty)
            if total_qty > 0:
                self.avg_entry = (
                    (self.avg_entry * abs(self.position_qty)) + (report.avg_price * abs(signed_qty))
                ) / total_qty
            self.position_qty += signed_qty
            return

        close_qty = min(abs(self.position_qty), abs(signed_qty))
        pnl = (report.avg_price - self.avg_entry) * close_qty * (1.0 if self.position_qty > 0 else -1.0)
        self.realized_pnls.append(pnl)
        remaining_qty = self.position_qty + signed_qty
        if remaining_qty == 0:
            self.position_qty = 0.0
            self.avg_entry = 0.0
            return
        if self.position_qty * remaining_qty < 0:
            self.position_qty = remaining_qty
            self.avg_entry = report.avg_price
            return
        self.position_qty = remaining_qty


class _FundingAccumulator:
    """Track cumulative funding costs on open positions.

    Binance perpetual contracts settle funding every 8 hours.  This accumulator
    applies the funding rate to the open position notional whenever a new
    funding event is observed at bar close.

    Convention: positive funding rate means longs pay shorts.
    A long position with positive funding_rate incurs a *cost* (negative PnL).
    """

    def __init__(self) -> None:
        self.total_funding_cost = 0.0
        self._last_funding_time_ns = 0

    def accrue(
        self,
        funding_rate: float,
        position_qty: float,
        mark_price: float,
        funding_time_ns: int,
        cutoff_ns: int,
    ) -> None:
        """Accrue funding cost if this is a new funding settlement."""
        if funding_time_ns <= self._last_funding_time_ns:
            return  # Already processed this funding event
        if position_qty == 0.0 or mark_price <= 0:
            self._last_funding_time_ns = funding_time_ns
            return

        # funding_cost = position_notional × funding_rate
        # positive position_qty = long; positive funding_rate = longs pay
        # so cost = position_qty × mark_price × funding_rate (positive = cost to holder)
        notional = position_qty * mark_price
        cost = notional * funding_rate
        self.total_funding_cost += cost
        self._last_funding_time_ns = funding_time_ns


def _time_filter_sql(start_ns: int | None, end_ns: int | None, *, column: str) -> str:
    """Build a point-in-time-safe SQL filter for DuckDB scans."""
    clauses: list[str] = []
    if start_ns is not None:
        clauses.append(f"{column} >= ?")
    if end_ns is not None:
        clauses.append(f"{column} <= ?")
    return " AND ".join(clauses)


def _time_filter_params(start_ns: int | None, end_ns: int | None) -> tuple[Any, ...]:
    """Return the positional parameters paired with ``_time_filter_sql``."""
    params: list[Any] = []
    if start_ns is not None:
        params.append(start_ns)
    if end_ns is not None:
        params.append(end_ns)
    return tuple(params)


def _proxy_cvd_delta(row: dict[str, Any]) -> float:
    """Approximate per-bar CVD from candle direction when tick history is absent."""
    volume = float(row["volume"])
    open_px = float(row["open"])
    close_px = float(row["close"])
    if close_px > open_px:
        return volume
    if close_px < open_px:
        return -volume
    return 0.0


def _aux_staleness_ms(
    cutoff_ns: int,
    funding_row: dict[str, Any] | None,
    oi_row: dict[str, Any] | None,
) -> int:
    """Compute the worst auxiliary-data age visible at the decision cutoff."""
    ages_ms: list[int] = []
    if funding_row is not None:
        ages_ms.append(max(0, int((cutoff_ns - int(funding_row["funding_time_ns"])) / 1_000_000)))
    if oi_row is not None:
        ages_ms.append(max(0, int((cutoff_ns - int(oi_row["ts_ns"])) / 1_000_000)))
    return max(ages_ms) if ages_ms else 0


def _parse_timeframe(value: str) -> Timeframe:
    """Map storage tokens to the domain timeframe enum."""
    return {"1h": Timeframe.TF_1H, "4h": Timeframe.TF_4H, "1d": Timeframe.TF_1D}.get(
        value, Timeframe.TF_4H
    )


def _timeframe_priority(value: str) -> int:
    """Order: 1d context first (0), then 4h (1), then 1h execution last (2)."""
    return {"1d": 0, "4h": 1, "1h": 2}.get(value, 1)


def _equity_returns(snapshots: list[RiskSnapshot]) -> list[float]:
    """Convert risk snapshots into sequential equity returns."""
    returns: list[float] = []
    previous = None
    for snapshot in snapshots:
        if previous is not None and previous > 0:
            returns.append((snapshot.equity - previous) / previous)
        previous = snapshot.equity
    return returns


def _annualized_ratio(
    sample_returns: list[float],
    *,
    periods_per_year: int,
    baseline_returns: list[float] | None = None,
) -> float:
    """Compute an annualized Sharpe/Sortino-style ratio from bar returns."""
    source = baseline_returns if baseline_returns is not None else sample_returns
    if not source:
        return 0.0
    if baseline_returns is not None and not sample_returns:
        return 0.0
    mean_return = sum(source) / len(source)
    variance_source = sample_returns if baseline_returns is None else sample_returns
    if len(variance_source) < 2:
        return 0.0
    mean_for_std = sum(variance_source) / len(variance_source)
    variance = sum((value - mean_for_std) ** 2 for value in variance_source) / (len(variance_source) - 1)
    std = math.sqrt(max(variance, 0.0))
    if std <= 0:
        return 0.0
    return (mean_return / std) * math.sqrt(periods_per_year)


def _max_drawdown(snapshots: list[RiskSnapshot]) -> tuple[float, int]:
    """Return maximum drawdown depth and duration from equity snapshots."""
    peak = 0.0
    max_drawdown = 0.0
    current_duration = 0
    max_duration = 0
    for snapshot in snapshots:
        equity = snapshot.equity
        if equity >= peak:
            peak = equity
            current_duration = 0
            continue
        current_duration += 1
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        max_duration = max(max_duration, current_duration)
    return max_drawdown, max_duration


def _forward_return(rows: list[dict[str, Any]], index: int, horizon: int) -> float | None:
    """Return forward close-to-close return for one horizon, if available."""
    future_index = index + horizon
    if future_index >= len(rows):
        return None
    current_close = float(rows[index]["close"])
    future_close = float(rows[future_index]["close"])
    if current_close <= 0:
        return None
    return (future_close - current_close) / current_close


def _max_adverse_excursion(rows: list[dict[str, Any]], index: int, horizon: int) -> float | None:
    """Return the worst low excursion over the next ``horizon`` bars."""
    end_index = min(len(rows), index + horizon + 1)
    if end_index <= index + 1:
        return None
    current_close = float(rows[index]["close"])
    if current_close <= 0:
        return None
    future_lows = [float(row["low"]) for row in rows[index + 1 : end_index]]
    return (min(future_lows) - current_close) / current_close


def _max_favorable_excursion(rows: list[dict[str, Any]], index: int, horizon: int) -> float | None:
    """Return the best high excursion over the next ``horizon`` bars."""
    end_index = min(len(rows), index + horizon + 1)
    if end_index <= index + 1:
        return None
    current_close = float(rows[index]["close"])
    if current_close <= 0:
        return None
    future_highs = [float(row["high"]) for row in rows[index + 1 : end_index]]
    return (max(future_highs) - current_close) / current_close
