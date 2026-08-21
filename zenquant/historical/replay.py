"""Historical replay/backtest runner built on the live runtime contracts.

The goal is not to create a separate research-only engine. Historical mode
publishes the same market events the live kernel consumes so that strategy,
risk, and execution behavior stays auditable across modes.
"""

from __future__ import annotations

import math
import time
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.data import (
    BasisDataStore,
    BasisStoreConfig,
    BinanceMetricsFactorStore,
    BinanceMetricsFactorStoreConfig,
    CotDataStore,
    CotStoreConfig,
    CrossAssetContextStore,
    CrossAssetContextStoreConfig,
    LiquidationDataStore,
    LiquidationStoreConfig,
    MacroDataStore,
    MacroDataStoreConfig,
    MicrostructureFeatureStore,
    MicrostructureFeatureStoreConfig,
    OnchainFactorStore,
    OnchainFactorStoreConfig,
    OptionsDataStore,
    OptionsDataStoreConfig,
)
from zenquant.data.eth_store import EthDataStore, EthStoreConfig
from zenquant.domain import (
    ArchitectureAlphaSnapshot,
    BacktestCycleContext,
    BacktestDiagnostics,
    BacktestMetrics,
    BarCloseEvent,
    Candle,
    DecisionSnapshot,
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
    PositionEpisode,
    RiskOverlayDecision,
    RiskSnapshot,
    SizingDecision,
    TargetPosition,
    Timeframe,
)
from zenquant.execution import ExecutionKernel, ExecutionKernelConfig, SimulatedGateway
from zenquant.indicator import IndicatorEngine, IndicatorEngineConfig, IndicatorStore
from zenquant.indicator.hf_features import HFFeatureStore, HFFeatureStoreConfig
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.plugins.profile import build_from_profile
from zenquant.risk import RiskOverlay, RiskOverlayConfig
from zenquant.state import StateEngine, StateEngineConfig
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
    direction_timeframe: Timeframe = Timeframe.TF_4H
    # Multi-layer: set to TF_1H when execution is TF_5M (1H constrains 5M)
    context_timeframe: Timeframe | None = None
    open_interest_timeframe: str = "4h"
    # Strategy config override (for parameter sweeps)
    strategy_config: ARCStrategyConfig | None = None
    # Risk config override (for risk appetite tuning)
    risk_config: RiskOverlayConfig | None = None
    # Execution config override (for variant comparison / sensitivity checks)
    execution_config: ExecutionKernelConfig | None = None
    # Signal weight overrides (for RL tuner — bypass default.toml weights)
    signal_weight_overrides: dict[str, float] | None = None
    # Research-only plugin config overrides. Default profiles remain the system
    # of record; candidate sweeps may explicitly enable disabled audit plugins.
    plugin_config_overrides: dict[str, dict[str, dict[str, Any]]] | None = None
    # Indicator config override (for RL tuner — vol_window, cvd_window, etc.)
    indicator_config: IndicatorEngineConfig | None = None
    # Execution config override (for RL tuner / execution sensitivity tests)
    execution_config: ExecutionKernelConfig | None = None
    # Domain randomization (for robustness testing)
    perturbation: PerturbationConfig | None = None
    # Macro data directory (VIX, DXY, Treasury yields)
    macro_dir: Path = Path("data/raw/macro")
    microstructure_sidecar_enabled: bool = False
    microstructure_sidecar_dir: Path = Path("data/raw/microstructure_sidecar/bybit/BTCUSDT/1000ms")
    # Options data directory (Deribit IV, skew, term structure)
    options_dir: Path = Path("data/raw/deribit_options")
    # CFTC COT data path
    cot_path: Path = Path("data/raw/cftc/cot_btc.parquet")
    # Aggregated liquidation data path
    liquidation_dir: Path = Path("data/raw/binance/liquidations/aggregated")
    # Research toggles for liquidation ablation
    liquidation_data_enabled: bool = True
    state_liquidation_overlay_enabled: bool = True
    # Plugin profile path (default.toml, etc.)
    profile_path: Path | None = Path("config/profiles/default.toml")
    # Research-only compute mode. Authoritative mode preserves the full path
    # used by stage2/acceptance; lightweight mode is only for stage1 search.
    research_compute_mode: str = "authoritative"
    # Research diagnostic only: collect decision ledgers while preserving
    # lightweight train-search execution and early-stop semantics.
    diagnostic_collect_ledgers: bool = False
    # Optional profiling for research-time hot path diagnosis.
    profiling_enabled: bool = False
    # Deterministic early-stop controls for lightweight train search only.
    early_stop_enabled: bool = False
    trial_runtime_budget_s: float | None = None
    early_stop_trade_check_ratio: float = 0.25
    early_stop_min_trade_count: int = 20
    early_stop_trade_floor_ratio: float = 0.25
    early_stop_max_drawdown: float = 0.35
    early_stop_mid_trade_floor_ratio: float = 0.35
    early_stop_late_trade_floor_ratio: float = 0.6
    early_stop_min_churn_processed_ratio: float = 0.35
    early_stop_min_churn_elapsed_days: float = 120.0
    early_stop_min_churn_trade_count: int = 50
    early_stop_max_trade_count_per_day: float = 1.5
    early_stop_trade_count_per_day_buffer: float = 1.5
    early_stop_max_fee_drag_pct: float = 0.025
    early_stop_fee_drag_buffer: float = 2.0
    early_stop_min_gross_pnl_fraction_for_fee_drag: float = 0.05
    replay_context: ReplayContext | None = None


@dataclass(slots=True)
class HistoricalBacktestResult:
    """Compact result returned by the backtest runner."""

    metrics: BacktestMetrics
    diagnostics: BacktestDiagnostics
    total_indicator_packs: int
    total_targets: int
    total_reports: int
    final_equity: float
    cycle_context: BacktestCycleContext
    profiling: dict[str, float] = field(default_factory=dict)
    terminated_early: bool = False
    termination_reason: str | None = None
    processed_event_ratio: float = 1.0
    position_episodes: tuple[PositionEpisode, ...] = ()


@dataclass(slots=True)
class ReplayContext:
    funding_rows: list[dict[str, Any]]
    open_interest_rows: list[dict[str, Any]]
    aggtrades: dict[str, dict[int, dict[str, Any]]]
    events: list[dict[str, Any]]
    macro_store: MacroDataStore
    options_store: OptionsDataStore
    hf_store: HFFeatureStore
    cot_store: CotDataStore
    liquidation_store: LiquidationDataStore
    eth_store: EthDataStore
    cross_asset_context_store: CrossAssetContextStore
    onchain_factor_store: OnchainFactorStore
    binance_metrics_factor_store: BinanceMetricsFactorStore
    basis_store: BasisDataStore
    cache_stats: dict[str, Any] = field(default_factory=dict)


_STORE_CACHE: dict[tuple[Any, ...], object] = {}
_RAW_ROWS_CACHE: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
_EVENT_LIST_CACHE: dict[tuple[Any, ...], list[dict[str, Any]]] = {}


class HistoricalBacktest:
    """Replay historical Parquet data through the main trading kernel.

    The runner intentionally uses the production indicator, strategy, risk,
    and execution components. Historical mode is therefore a transport layer
    adaptation, not a second decision stack.
    """

    def __init__(self, config: HistoricalBacktestConfig | None = None) -> None:
        self._config = config or HistoricalBacktestConfig()
        self._lightweight_train_search = self._config.research_compute_mode == "lightweight_train_search"
        self._collect_decision_ledgers = (not self._lightweight_train_search) or bool(
            self._config.diagnostic_collect_ledgers
        )
        self._profiling_enabled = bool(self._config.profiling_enabled or self._lightweight_train_search)
        self.bus = InMemoryJetStream()
        self.bus.add_stream(StreamConfig(name="STREAM_MARKET_DATA", subjects=["md.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_CLOCK", subjects=["clock.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INDICATORS", subjects=["indicator.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_INTENTS", subjects=["intent.>"]))
        self.bus.add_stream(StreamConfig(name="STREAM_EXEC", subjects=["exec.>"]))

        self.indicator_store = IndicatorStore()
        self._replay_context = self._config.replay_context or _build_replay_context(self._config)
        self._profiling_totals: defaultdict[str, float] = defaultdict(float)
        self._macro_store = self._replay_context.macro_store
        self._options_store = self._replay_context.options_store
        self._hf_store = self._replay_context.hf_store
        self._cot_store = self._replay_context.cot_store
        self._liquidation_store = self._replay_context.liquidation_store
        self._eth_store = self._replay_context.eth_store
        self._basis_store = self._replay_context.basis_store
        microstructure_store: MicrostructureFeatureStore | None = None
        if self._config.microstructure_sidecar_enabled:
            microstructure_store = MicrostructureFeatureStore(
                MicrostructureFeatureStoreConfig(
                    data_dir=self._config.microstructure_sidecar_dir,
                    max_staleness_ns=self._config.execution_timeframe.seconds * 1_000_000_000,
                )
            )
            microstructure_store.load()
        indicator_config = self._config.indicator_config or IndicatorEngineConfig(
            expected_symbols=(self._config.portfolio_symbol,)
        )
        if self._lightweight_train_search:
            indicator_config = replace(indicator_config, defer_transfer_entropy=True)
        self.indicator_engine = self._subscribe_with_profile(
            "indicator_step_total_s",
            lambda: IndicatorEngine(
                bus=self.bus,
                store=self.indicator_store,
                config=indicator_config,
                macro_store=self._macro_store if self._macro_store.loaded else None,
                options_store=self._options_store if self._options_store.loaded else None,
                hf_store=self._hf_store if self._hf_store.loaded else None,
                cot_store=self._cot_store if self._cot_store.loaded else None,
                basis_store=self._basis_store if self._basis_store.loaded else None,
                eth_store=self._eth_store if self._eth_store.loaded else None,
                cross_asset_context_store=(
                    self._replay_context.cross_asset_context_store
                    if self._replay_context.cross_asset_context_store.loaded
                    else None
                ),
                onchain_factor_store=(
                    self._replay_context.onchain_factor_store
                    if self._replay_context.onchain_factor_store.loaded
                    else None
                ),
                binance_metrics_factor_store=(
                    self._replay_context.binance_metrics_factor_store
                    if self._replay_context.binance_metrics_factor_store.loaded
                    else None
                ),
                liquidation_store=(
                    self._liquidation_store
                    if self._config.liquidation_data_enabled and self._liquidation_store.loaded
                    else None
                ),
                microstructure_store=microstructure_store
                if microstructure_store and microstructure_store.loaded
                else None,
            ),
        )
        self.risk_overlay = self._subscribe_with_profile(
            "risk_step_total_s",
            lambda: RiskOverlay(
                bus=self.bus,
                config=self._config.risk_config or RiskOverlayConfig(starting_equity=self._config.starting_equity),
            ),
        )
        # Load plugin registry if profile path provided
        plugin_registry = None
        if self._config.profile_path and self._config.profile_path.exists():
            plugin_root = Path(__file__).parent.parent / "plugins"
            _, plugin_registry = build_from_profile(
                self._config.profile_path,
                plugin_root=plugin_root,
                config_overrides=self._config.plugin_config_overrides,
            )
            # Apply RL tuner weight overrides (dynamic, bypass default.toml)
            if self._config.signal_weight_overrides and plugin_registry is not None:
                plugin_registry.weight_overrides = dict(self._config.signal_weight_overrides)
        strategy_config = self._config.strategy_config
        execution_config = self._config.execution_config
        if execution_config is not None and execution_config.execution_authority_mode == "target_tracking_v2":
            if self._config.execution_timeframe != Timeframe.TF_4H:
                raise ValueError("target_tracking_v2 requires a 4h execution timeframe")
            strategy_config = replace(
                strategy_config or ARCStrategyConfig(execution_timeframe=Timeframe.TF_4H),
                emit_decision_snapshots_v2=True,
            )
        self.strategy_engine = self._subscribe_with_profile(
            "strategy_step_total_s",
            lambda: ARCStrategyEngine(
                bus=self.bus,
                config=strategy_config,
                plugin_registry=plugin_registry,
            ),
        )
        self.state_engine = self._subscribe_with_profile(
            "state_step_total_s",
            lambda: StateEngine(
                bus=self.bus,
                config=StateEngineConfig(enable_liquidation_overlay=self._config.state_liquidation_overlay_enabled),
            ),
        )
        self.execution_kernel = self._subscribe_with_profile(
            "execution_step_total_s",
            lambda: ExecutionKernel(
                bus=self.bus,
                gateway=SimulatedGateway(),
                risk_overlay=self.risk_overlay,
                config=self._config.execution_config,
            ),
        )

        self._indicators: list[IndicatorPack] = []
        self._market_states: list[MarketState] = []
        self._biases: list[DirectionalBias] = []
        self._playbooks: list[PlaybookSelection] = []
        self._sizings: list[SizingDecision] = []
        self._exits: list[ExitDecision] = []
        self._position_states: list[OpenPositionState] = []
        self._targets: list[TargetPosition] = []
        self._decision_snapshots: list[DecisionSnapshot] = []
        self._architecture_alphas: list[ArchitectureAlphaSnapshot] = []
        self._reports: list[ExecutionReport] = []
        self._risk_decisions: list[RiskOverlayDecision] = []
        self._risk_snapshots: list[RiskSnapshot] = []
        self._execution_rows: list[dict[str, Any]] = []
        self._confirmed_four_hour_rows: list[dict[str, Any]] = []
        self._current_event_cutoff_ns = 0
        self._trade_ledger = _TradeLedger()
        from zenquant.research.episode_ledger import PositionEpisodeLedger

        self._episode_ledger = PositionEpisodeLedger()
        self._episode_funding_time_ns = 0
        self._funding_accumulator = _FundingAccumulator()
        self._funding_rows: list[dict[str, Any]] = []
        self._open_interest_rows: list[dict[str, Any]] = []
        self._funding_index = -1
        self._open_interest_index = -1

        if self._collect_decision_ledgers:
            self.bus.subscribe("indicator.>", self._collect_indicator)
            self.bus.subscribe("state.market.>", self._collect_market_state)
            self.bus.subscribe("strategy.bias.1d.>", self._collect_bias)
            self.bus.subscribe("strategy.playbook.>", self._collect_playbook)
            self.bus.subscribe("strategy.sizing.>", self._collect_sizing)
            self.bus.subscribe("strategy.exit.>", self._collect_exit)
            self.bus.subscribe("strategy.position_state.>", self._collect_position_state)
            self.bus.subscribe("strategy.decision_snapshot.4h.>", self._collect_decision_snapshot)
            self.bus.subscribe("strategy.architecture_alpha.4h.>", self._collect_architecture_alpha)
            self.bus.subscribe("risk.overlay.decision", self._collect_risk_decision)
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
        profiling: dict[str, float] = {}
        processed_events = 0
        execution_events_total = sum(
            1 for event in events if event["event_timeframe"] == self._config.execution_timeframe.value
        )
        terminated_early = False
        termination_reason: str | None = None
        event_loop_started_at = datetime.now(UTC).timestamp()
        first_event_open_ns = int(events[0]["open_time_ns"])
        for event in events:
            self._publish_event(event)
            processed_events += 1
            if (
                self._config.trial_runtime_budget_s is not None
                and (datetime.now(UTC).timestamp() - event_loop_started_at) > self._config.trial_runtime_budget_s
            ):
                terminated_early = True
                termination_reason = "trial_runtime_budget_exceeded"
                break
            if (
                self._config.early_stop_enabled
                and self._lightweight_train_search
                and execution_events_total > 0
                and event["event_timeframe"] == self._config.execution_timeframe.value
            ):
                should_stop, reason = self._should_early_stop(
                    current_event=event,
                    first_event_open_ns=first_event_open_ns,
                    processed_events=processed_events,
                    total_events=len(events),
                    execution_events_total=execution_events_total,
                )
                if should_stop:
                    terminated_early = True
                    termination_reason = reason
                    break
        event_loop_finished_at = datetime.now(UTC).timestamp()

        metrics_started_at = datetime.now(UTC).timestamp()
        processed = events[:processed_events]
        metrics = self._build_metrics(processed)
        metrics_finished_at = datetime.now(UTC).timestamp()
        diagnostics_started_at = metrics_finished_at
        diagnostics = (
            self._build_diagnostics()
            if self._collect_decision_ledgers
            else BacktestDiagnostics(
                playbook_counts={},
                actionable_exit_counts={},
                risk_flag_counts={},
                top_target_reason_codes={},
            )
        )
        diagnostics_finished_at = datetime.now(UTC).timestamp()
        cycle_started_at = diagnostics_finished_at
        cycle_context = self._build_cycle_context(processed)
        cycle_finished_at = datetime.now(UTC).timestamp()
        if self._profiling_enabled:
            profiling = {
                "event_loop_total_s": round(event_loop_finished_at - event_loop_started_at, 6),
                "indicator_step_total_s": round(
                    float(self._profiling_totals.get("indicator_step_total_s", 0.0) or 0.0), 6
                ),
                "strategy_step_total_s": round(
                    float(self._profiling_totals.get("strategy_step_total_s", 0.0) or 0.0), 6
                ),
                "risk_step_total_s": round(float(self._profiling_totals.get("risk_step_total_s", 0.0) or 0.0), 6),
                "execution_step_total_s": round(
                    float(self._profiling_totals.get("execution_step_total_s", 0.0) or 0.0), 6
                ),
                "accounting_step_total_s": round(
                    float(self._profiling_totals.get("accounting_step_total_s", 0.0) or 0.0), 6
                ),
                "metrics_finalize_s": round(metrics_finished_at - metrics_started_at, 6),
                "diagnostics_finalize_s": round(diagnostics_finished_at - diagnostics_started_at, 6),
                "cycle_context_finalize_s": round(cycle_finished_at - cycle_started_at, 6),
            }
            profiling.update(self._replay_context.cache_stats)
            profiling.update(self.indicator_engine.profiling_snapshot())
        return HistoricalBacktestResult(
            metrics=metrics,
            diagnostics=diagnostics,
            total_indicator_packs=len(self._indicators),
            total_targets=len(self._targets),
            total_reports=len(self._reports),
            final_equity=self.risk_overlay.equity,
            cycle_context=cycle_context,
            profiling=profiling,
            terminated_early=terminated_early,
            termination_reason=termination_reason,
            processed_event_ratio=(round(processed_events / max(1, len(events)), 6) if events else 1.0),
            position_episodes=self._episode_ledger.episodes,
        )

    def _should_early_stop(
        self,
        *,
        current_event: dict[str, Any],
        first_event_open_ns: int,
        processed_events: int,
        total_events: int,
        execution_events_total: int,
    ) -> tuple[bool, str | None]:
        processed_ratio = processed_events / max(1, total_events)
        if processed_ratio < float(self._config.early_stop_trade_check_ratio):
            return False, None
        current_trade_count = len(self._reports)
        if processed_ratio >= 0.75:
            floor_ratio = max(float(self._config.early_stop_late_trade_floor_ratio), 0.35)
        elif processed_ratio >= 0.50:
            floor_ratio = max(float(self._config.early_stop_mid_trade_floor_ratio), 0.2)
        else:
            floor_ratio = max(float(self._config.early_stop_trade_floor_ratio), 0.1)
        trade_floor = max(
            1,
            math.floor(float(self._config.early_stop_min_trade_count) * floor_ratio),
        )
        if processed_ratio >= 0.50 and current_trade_count == 0:
            return True, "no_trade_progress"
        if current_trade_count < trade_floor:
            return True, "trade_count_too_low_for_processed_ratio"
        if self._risk_snapshots:
            max_drawdown, _ = _max_drawdown(self._risk_snapshots)
            if max_drawdown >= float(self._config.early_stop_max_drawdown):
                return True, "drawdown_hard_fail"
        churn_stop, churn_reason = self._should_early_stop_churn(
            current_event=current_event,
            first_event_open_ns=first_event_open_ns,
            processed_ratio=processed_ratio,
            trade_count=current_trade_count,
        )
        if churn_stop:
            return True, churn_reason
        return False, None

    def _should_early_stop_churn(
        self,
        *,
        current_event: dict[str, Any],
        first_event_open_ns: int,
        processed_ratio: float,
        trade_count: int,
    ) -> tuple[bool, str | None]:
        if processed_ratio < float(self._config.early_stop_min_churn_processed_ratio):
            return False, None
        if trade_count < int(self._config.early_stop_min_churn_trade_count):
            return False, None
        close_time_ns = int(current_event.get("close_time_ns", first_event_open_ns))
        elapsed_days = max(1.0, (close_time_ns - first_event_open_ns) / 1_000_000_000 / 86_400)
        if elapsed_days < float(self._config.early_stop_min_churn_elapsed_days):
            return False, None
        max_trade_count_per_day = float(self._config.early_stop_max_trade_count_per_day)
        if max_trade_count_per_day > 0.0:
            trade_count_per_day = trade_count / elapsed_days
            trade_count_limit = max_trade_count_per_day * float(self._config.early_stop_trade_count_per_day_buffer)
            if trade_count_per_day > trade_count_limit:
                return True, "trade_count_per_day_hard_fail"

        max_fee_drag_pct = float(self._config.early_stop_max_fee_drag_pct)
        if max_fee_drag_pct <= 0.0:
            return False, None
        gross_pnl = self.risk_overlay.equity - self._config.starting_equity + self._trade_ledger.total_fees
        min_gross_pnl = self._config.starting_equity * float(
            self._config.early_stop_min_gross_pnl_fraction_for_fee_drag
        )
        if gross_pnl < min_gross_pnl:
            return False, None
        fee_drag_pct = self._trade_ledger.total_fees / gross_pnl
        fee_drag_limit = max_fee_drag_pct * float(self._config.early_stop_fee_drag_buffer)
        if fee_drag_pct > fee_drag_limit:
            return True, "fee_drag_hard_fail"
        return False, None

    def _subscribe_with_profile(self, bucket: str, factory: Any) -> Any:
        if not self._profiling_enabled:
            return factory()
        original_subscribe = self.bus.subscribe

        def profiled_subscribe(subject_pattern: str, handler: Any) -> None:
            def wrapped(subject: str, payload: Any) -> None:
                started_at = time.perf_counter()
                try:
                    handler(subject, payload)
                finally:
                    self._profiling_totals[bucket] += time.perf_counter() - started_at

            original_subscribe(subject_pattern, wrapped)

        self.bus.subscribe = profiled_subscribe
        try:
            return factory()
        finally:
            self.bus.subscribe = original_subscribe

    def _load_events(self) -> list[dict[str, Any]]:
        """Load candles and auxiliary market series in replay order."""
        self._funding_rows = self._replay_context.funding_rows
        self._open_interest_rows = self._replay_context.open_interest_rows
        self._aggtrades = self._replay_context.aggtrades
        self._funding_index = -1
        self._open_interest_index = -1
        return self._replay_context.events

    def _publish_event(self, row: dict[str, Any]) -> None:
        """Publish the same core event families the live runtime consumes."""
        timeframe = _parse_timeframe(str(row["event_timeframe"]))
        cutoff_ns = int(row["close_time_ns"])
        self._current_event_cutoff_ns = cutoff_ns
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
        if timeframe == self._config.execution_timeframe:
            self._update_episode_market_accounting(row, funding_row)
        if timeframe == Timeframe.TF_4H:
            self._confirmed_four_hour_rows.append(dict(row))

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
            accounting_started_at = time.perf_counter()
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
            if self._profiling_enabled:
                self._profiling_totals["accounting_step_total_s"] += time.perf_counter() - accounting_started_at

    def _latest_funding(self, cutoff_ns: int) -> dict[str, Any] | None:
        while (
            self._funding_index + 1 < len(self._funding_rows)
            and int(self._funding_rows[self._funding_index + 1]["funding_time_ns"]) <= cutoff_ns
        ):
            self._funding_index += 1
        if self._funding_index < 0:
            return None
        return self._funding_rows[self._funding_index]

    def _update_episode_market_accounting(
        self,
        row: dict[str, Any],
        funding_row: dict[str, Any] | None,
    ) -> None:
        """Accrue funding and excursions before any close-triggered fill."""
        from zenquant.research.episode_ledger import FundingAllocation, PriceObservation

        symbol = self._config.portfolio_symbol
        close_price = float(row["close"])
        if funding_row is not None:
            funding_time_ns = int(funding_row.get("funding_time_ns", row["close_time_ns"]))
            if funding_time_ns > self._episode_funding_time_ns:
                position_qty = self._episode_ledger.position_qty(symbol)
                funding_cost = position_qty * close_price * float(funding_row.get("funding_rate", 0.0))
                self._episode_ledger.allocate_funding(
                    FundingAllocation(
                        symbol=symbol,
                        # Auxiliary rows can become visible after their economic
                        # source timestamp. Order ledger events by the confirmed
                        # cutoff at which replay can first consume the row; using
                        # the stale source timestamp would move the ledger clock
                        # backwards after prior bar observations/fills.
                        timestamp_ns=int(row["close_time_ns"]),
                        pnl=-funding_cost,
                    )
                )
                self._episode_funding_time_ns = funding_time_ns
        self._episode_ledger.observe_price(
            PriceObservation(
                symbol=symbol,
                timestamp_ns=int(row["close_time_ns"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=close_price,
            )
        )

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

    def _collect_decision_snapshot(self, _: str, payload: object) -> None:
        if isinstance(payload, DecisionSnapshot):
            self._decision_snapshots.append(payload)

    def _collect_architecture_alpha(self, _: str, payload: object) -> None:
        if isinstance(payload, ArchitectureAlphaSnapshot):
            visible_at_ns = int(self._current_event_cutoff_ns)
            if payload.available_at_ns != payload.cutoff_time_ns or visible_at_ns != payload.available_at_ns:
                raise ValueError(
                    "backdated architecture alpha rejected: "
                    f"cutoff_time_ns={payload.cutoff_time_ns} "
                    f"available_at_ns={payload.available_at_ns} "
                    f"visible_at_ns={visible_at_ns}"
                )
            self._architecture_alphas.append(payload)

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
            visible_cutoff_ns = getattr(self, "_current_event_cutoff_ns", payload.transaction_time_ns)
            observed_report = (
                replace(payload, transaction_time_ns=visible_cutoff_ns)
                if payload.transaction_time_ns < visible_cutoff_ns
                else payload
            )
            self._reports.append(observed_report)
            self._trade_ledger.on_execution_report(observed_report)
            self._episode_ledger.on_fill(observed_report)

    def _collect_risk_snapshot(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskSnapshot):
            self._risk_snapshots.append(payload)

    def _collect_risk_decision(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskOverlayDecision):
            self._risk_decisions.append(payload)

    def _build_metrics(self, events: list[dict[str, Any]]) -> BacktestMetrics:
        returns = _equity_returns(self._risk_snapshots)
        periods_per_year = {
            Timeframe.TF_5M: 288 * 365,
            Timeframe.TF_1H: 24 * 365,
            Timeframe.TF_4H: 6 * 365,
            Timeframe.TF_1D: 365,
        }.get(self._config.execution_timeframe, 6 * 365)
        sharpe = _annualized_ratio(returns, periods_per_year=periods_per_year)
        sortino = _annualized_ratio(
            [value for value in returns if value < 0.0],
            periods_per_year=periods_per_year,
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
        action_realized = {key: list(values) for key, values in sorted(self._trade_ledger.action_realized_pnls.items())}
        from zenquant.research.gate_v3 import calculate_episode_profit_factor

        closed_episodes = self._episode_ledger.closed_episodes
        open_episodes = self._episode_ledger.open_episodes
        episode_pf = calculate_episode_profit_factor(closed_episodes)
        position_switch_count = self._episode_ledger.position_switch_count
        position_switches_per_day = position_switch_count / elapsed_days
        execution_kernel = getattr(self, "execution_kernel", None)
        purity_counters = (
            execution_kernel.purity_counters
            if execution_kernel is not None
            else {
                "executor_created_exposure": 0,
                "unexpected_reversal": 0,
                "target_overshoot": 0,
            }
        )
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
            action_trade_counts=dict(sorted(self._trade_ledger.action_trade_counts.items())),
            action_fees={key: round(value, 6) for key, value in sorted(self._trade_ledger.action_fees.items())},
            action_fee_drag_pct={
                key: round((value / gross_pnl) if gross_pnl > 0 else 0.0, 6)
                for key, value in sorted(self._trade_ledger.action_fees.items())
            },
            action_realized_counts={key: len(values) for key, values in action_realized.items()},
            action_realized_pnl={key: round(sum(values), 6) for key, values in action_realized.items()},
            action_profit_factor={
                key: round(
                    (sum(value for value in values if value > 0) / abs(sum(value for value in values if value < 0)))
                    if any(value > 0 for value in values)
                    and any(value < 0 for value in values)
                    and sum(value for value in values if value < 0) != 0
                    else 0.0,
                    6,
                )
                for key, values in action_realized.items()
            },
            episode_profit_factor=round(episode_pf.value, 6) if episode_pf.value is not None else None,
            episode_profit_factor_status=episode_pf.status.value,
            closed_episode_count=len(closed_episodes),
            open_episode_count=len(open_episodes),
            position_switch_count=position_switch_count,
            position_switches_per_day=round(position_switches_per_day, 6),
            executor_created_exposure=purity_counters["executor_created_exposure"],
            unexpected_reversal=purity_counters["unexpected_reversal"],
            target_overshoot=purity_counters["target_overshoot"],
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

    def _build_cycle_context(self, events: list[dict[str, Any]]) -> BacktestCycleContext:
        execution_events = [
            item for item in events if item["event_timeframe"] == self._config.execution_timeframe.value
        ]
        if not execution_events:
            return BacktestCycleContext(
                dominant_cycle=0,
                dominant_phase="UNKNOWN",
                cycle_distribution={},
                phase_distribution={},
                phase_bar_counts={},
                sharpe_by_phase={},
            )

        phase_counts: Counter[str] = Counter()
        cycle_counts: Counter[int] = Counter()
        for row in execution_events:
            cycle_num, phase = _get_cycle_phase(int(row["close_time_ns"]))
            phase_counts[phase] += 1
            cycle_counts[cycle_num] += 1

        total_bars = len(execution_events)
        dominant_cycle = max(cycle_counts.items(), key=lambda item: (item[1], item[0]))[0]
        dominant_phase = max(phase_counts.items(), key=lambda item: (item[1], item[0]))[0]
        return BacktestCycleContext(
            dominant_cycle=dominant_cycle,
            dominant_phase=dominant_phase,
            cycle_distribution={
                str(cycle): round(count / total_bars, 4) for cycle, count in sorted(cycle_counts.items())
            },
            phase_distribution={phase: round(count / total_bars, 4) for phase, count in sorted(phase_counts.items())},
            phase_bar_counts=dict(sorted(phase_counts.items())),
            sharpe_by_phase=_sharpe_by_phase(self._risk_snapshots),
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
        risk_decision_map = {payload.cutoff_time_ns: payload for payload in self._risk_decisions}
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
            risk_decision = risk_decision_map.get(cutoff_ns)
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
                        "multi_momentum_z": float(indicator.values.get("multi_momentum_z", 0.0) or 0.0),
                        "cycle_position": float(indicator.values.get("cycle_position", 1.0) or 1.0),
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

            if risk_decision is not None:
                row.update(
                    {
                        "risk_original_target_qty": risk_decision.original_target_qty,
                        "risk_revised_target_qty": risk_decision.revised_target_qty,
                        "risk_reduce_only": risk_decision.reduce_only,
                        "risk_decision_flags": "|".join(risk_decision.flags),
                        "risk_qty_delta": risk_decision.revised_target_qty - risk_decision.original_target_qty,
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

    def build_architecture_attribution_rows(
        self,
        *,
        horizon: int = 1,
    ) -> dict[str, Any]:
        """Build evaluable A1 rows from retained confirmed 4h closes only."""
        if horizon <= 0:
            raise ValueError("horizon must be positive")

        confirmed_rows = sorted(self._confirmed_four_hour_rows, key=lambda row: int(row["close_time_ns"]))
        confirmed_counts = Counter(int(row["close_time_ns"]) for row in confirmed_rows)
        duplicate_confirmed = sorted(cutoff for cutoff, count in confirmed_counts.items() if count != 1)
        if duplicate_confirmed:
            raise ValueError(f"duplicate confirmed 4h rows at cutoffs={duplicate_confirmed!r}")

        alpha_snapshots = [alpha for alpha in self._architecture_alphas if alpha.timeframe == Timeframe.TF_4H]
        alpha_counts = Counter(alpha.cutoff_time_ns for alpha in alpha_snapshots)
        duplicate_alpha = sorted(cutoff for cutoff, count in alpha_counts.items() if count != 1)
        if duplicate_alpha:
            missing_count = len(set(confirmed_counts) - set(alpha_counts))
            raise ValueError(
                "architecture alpha cutoff reconciliation failed before terminal exclusion: "
                f"confirmed={len(confirmed_rows)} alpha={len(alpha_snapshots)} "
                f"missing={missing_count} terminal_excluded=0 duplicate_cutoffs={duplicate_alpha!r}"
            )
        confirmed_cutoffs = set(confirmed_counts)
        alpha_cutoffs = set(alpha_counts)
        missing_cutoffs = sorted(confirmed_cutoffs - alpha_cutoffs)
        extra_cutoffs = sorted(alpha_cutoffs - confirmed_cutoffs)
        if missing_cutoffs or extra_cutoffs:
            raise ValueError(
                "architecture alpha cutoff reconciliation requires one bar-close alpha per confirmed 4h row: "
                f"confirmed={len(confirmed_rows)} alpha={len(alpha_snapshots)} "
                f"missing={len(missing_cutoffs)} "
                f"missing_cutoffs={missing_cutoffs!r} extra_cutoffs={extra_cutoffs!r}"
            )
        alphas = {alpha.cutoff_time_ns: alpha for alpha in alpha_snapshots}
        rows: list[dict[str, Any]] = []
        terminal_excluded_count = 0
        for index, event_row in enumerate(confirmed_rows):
            cutoff_time_ns = int(event_row["close_time_ns"])
            alpha = alphas.get(cutoff_time_ns)
            if alpha is None:
                terminal_excluded_count += 1
                continue
            forward_return = _forward_return(confirmed_rows, index, horizon)
            if forward_return is None:
                terminal_excluded_count += 1
                continue
            rows.append(
                {
                    "alpha_id": alpha.alpha_id,
                    "cutoff_time_ns": cutoff_time_ns,
                    "available_at_ns": alpha.available_at_ns,
                    "regime": alpha.regime,
                    "raw_score": alpha.raw_score,
                    "frozen_confidence": alpha.confidence,
                    "forward_return": forward_return,
                    "formula_hash": alpha.formula_hash,
                    "alpha_config_hash": alpha.alpha_config_hash,
                }
            )
        return {
            "observations": rows,
            "excluded_non_evaluable_count": terminal_excluded_count,
            "confirmed_4h_row_count": len(confirmed_rows),
            "alpha_snapshot_count": len(alpha_snapshots),
            "missing_alpha_count": 0,
            "terminal_excluded_count": terminal_excluded_count,
        }

    def build_architecture_stage_rows(
        self,
        *,
        exchange_min_quantity: float,
        estimated_fee_bps: float,
        estimated_slippage_bps: float,
    ) -> dict[str, Any]:
        """Join complete point-in-time A2-A5 inputs to eligible A1 cutoffs."""
        batch = self.build_architecture_attribution_rows(horizon=1)
        confirmed_rows = sorted(self._confirmed_four_hour_rows, key=lambda row: int(row["close_time_ns"]))
        confirmed_index = {int(row["close_time_ns"]): index for index, row in enumerate(confirmed_rows)}
        indicator_points = [item for item in self._indicators if item.timeframe == Timeframe.TF_4H]
        state_points = [item for item in self._market_states if item.timeframe == Timeframe.TF_4H]
        _reject_duplicate_stage_points(indicator_points, label="indicator")
        _reject_duplicate_stage_points(state_points, label="market_state")
        indicators = {item.cutoff_time_ns: item for item in indicator_points}
        states = {item.cutoff_time_ns: item for item in state_points}
        risk_snapshots = _architecture_risk_donors(
            self._risk_snapshots,
            decision_cutoffs=tuple(int(item["cutoff_time_ns"]) for item in batch["observations"]),
        )

        rows: list[dict[str, Any]] = []
        missing: list[int] = []
        for alpha_row in batch["observations"]:
            cutoff = int(alpha_row["cutoff_time_ns"])
            indicator = indicators.get(cutoff)
            state = states.get(cutoff)
            index = confirmed_index.get(cutoff)
            if indicator is None or state is None or index is None:
                missing.append(cutoff)
                continue
            risk_snapshot, risk_snapshot_status = risk_snapshots[cutoff]
            risk_regime = _architecture_risk_regime(state)
            multiplier = {
                "TRADABLE": 1.0,
                "NOISY": 0.5,
                "STRESS": 0.1,
                "UNSAFE": 0.0,
            }[risk_regime]
            safety_reasons: set[str] = set()
            if risk_regime == "UNSAFE":
                safety_reasons.add("UNSAFE")
            if risk_snapshot is not None and any("KILL" in flag.upper() for flag in risk_snapshot.flags):
                safety_reasons.add("KILL")
            row = {
                **alpha_row,
                "annualized_volatility": float(indicator.values.get("realized_vol", 0.0) or 0.0),
                "risk_regime": risk_regime,
                "frozen_regime_multiplier": multiplier,
                "exchange_min_quantity": exchange_min_quantity,
                "reduce_only": bool(risk_snapshot.reduce_only_mode) if risk_snapshot is not None else False,
                "safety_reasons": tuple(sorted(safety_reasons)),
                "risk_snapshot_status": risk_snapshot_status,
                "risk_snapshot_ts_ns": risk_snapshot.ts_ns if risk_snapshot is not None else None,
                "estimated_fee_bps": estimated_fee_bps,
                "estimated_slippage_bps": estimated_slippage_bps,
                "reference_price": float(confirmed_rows[index]["close"]),
                "actual_fill_price": None,
                "forward_return_3": _forward_return(confirmed_rows, index, 3),
                "forward_return_6": _forward_return(confirmed_rows, index, 6),
                "directional_state": state.directional_state,
                "volatility_state": state.volatility_state,
                "positioning_state": state.positioning_state,
                "quality_state": state.quality_state,
                "quality_flags": tuple(indicator.quality_flags),
            }
            rows.append(row)
        if missing:
            raise ValueError(f"missing point-in-time A2-A5 stage inputs at cutoffs={missing!r}")
        funding_settlements = self._build_architecture_funding_settlements(
            tuple(int(row["cutoff_time_ns"]) for row in rows)
        )
        return {
            **batch,
            "observations": rows,
            "missing_stage_input_count": 0,
            "funding_settlements_by_cutoff": funding_settlements,
        }

    def _build_architecture_funding_settlements(
        self,
        decision_cutoffs: tuple[int, ...],
    ) -> dict[int, tuple[Any, ...]]:
        """Assign each PIT funding row to its first actionable 4h cutoff."""
        from zenquant.research.architecture_stage_replay import ArchitectureFundingSettlement

        if not decision_cutoffs:
            return {}
        ordered_cutoffs = tuple(sorted(decision_cutoffs))
        funding_rows = sorted(
            getattr(self, "_funding_rows", ()),
            key=lambda row: int(row["funding_time_ns"]),
        )
        source_counts = Counter(int(row["funding_time_ns"]) for row in funding_rows)
        duplicates = sorted(source for source, count in source_counts.items() if count != 1)
        if duplicates:
            raise ValueError(f"duplicate funding settlements at source times={duplicates!r}")
        price_rows = sorted(
            getattr(self, "_execution_rows", ()),
            key=lambda row: int(row["close_time_ns"]),
        )
        price_cutoffs = [int(row["close_time_ns"]) for row in price_rows]
        grouped: defaultdict[int, list[ArchitectureFundingSettlement]] = defaultdict(list)
        for funding_row in funding_rows:
            source_time_ns = int(funding_row["funding_time_ns"])
            decision_index = bisect_left(ordered_cutoffs, source_time_ns)
            if decision_index >= len(ordered_cutoffs):
                continue
            price_index = bisect_right(price_cutoffs, source_time_ns) - 1
            if price_index < 0:
                raise ValueError(
                    "funding settlement has no point-in-time execution price: "
                    f"source_time_ns={source_time_ns}"
                )
            available_at_ns = ordered_cutoffs[decision_index]
            grouped[available_at_ns].append(
                ArchitectureFundingSettlement(
                    source_time_ns=source_time_ns,
                    available_at_ns=available_at_ns,
                    funding_rate=float(funding_row.get("funding_rate", 0.0)),
                    mark_price=float(price_rows[price_index]["close"]),
                )
            )
        return {cutoff: tuple(settlements) for cutoff, settlements in sorted(grouped.items())}


class _TradeLedger:
    """Track realized PnL from fills without introducing a second execution model."""

    def __init__(self) -> None:
        self.position_qty = 0.0
        self.avg_entry = 0.0
        self.realized_pnls: list[float] = []
        self.total_fees = 0.0
        self.turnover_notional = 0.0
        self.action_trade_counts: Counter[str] = Counter()
        self.action_fees: Counter[str] = Counter()
        self.action_realized_pnls: defaultdict[str, list[float]] = defaultdict(list)

    def on_execution_report(self, report: ExecutionReport) -> None:
        """Update realized PnL state from a single filled report."""
        if report.filled_qty <= 0 or report.avg_price <= 0:
            return
        signed_qty = report.side * report.filled_qty
        next_position = self.position_qty + signed_qty
        action = self._execution_action(self.position_qty, next_position)
        self.action_trade_counts[action] += 1
        self.action_fees[action] += report.fee
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
        self.action_realized_pnls[action].append(pnl)
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

    @staticmethod
    def _execution_action(previous_qty: float, next_qty: float) -> str:
        if abs(previous_qty) <= 0.0 and abs(next_qty) > 0.0:
            return "entry"
        if abs(next_qty) <= 0.0:
            return "exit"
        if previous_qty * next_qty < 0:
            return "flip"
        if abs(next_qty) > abs(previous_qty):
            return "add"
        if abs(next_qty) < abs(previous_qty):
            return "reduce"
        return "unchanged"


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


def _load_store_cached(factory_key: tuple[Any, ...], factory: Any) -> tuple[object, bool]:
    cached = _STORE_CACHE.get(factory_key)
    if cached is not None:
        return cached, True
    store = factory()
    _STORE_CACHE[factory_key] = store
    return store, False


def _read_rows_cached(
    *,
    cache_key: tuple[Any, ...],
    path_glob: str,
    order_by: str,
    where_sql: str = "",
    params: tuple[Any, ...] = (),
) -> tuple[list[dict[str, Any]], bool]:
    cached = _RAW_ROWS_CACHE.get(cache_key)
    if cached is not None:
        return cached, True
    rows = read_parquet_rows(path_glob, where_sql=where_sql, params=params, order_by=order_by)
    _RAW_ROWS_CACHE[cache_key] = rows
    return rows, False


def _filter_rows_by_time(
    rows: list[dict[str, Any]],
    *,
    column: str,
    start_ns: int | None,
    end_ns: int | None,
) -> list[dict[str, Any]]:
    filtered = rows
    if start_ns is not None:
        filtered = [row for row in filtered if int(row[column]) >= start_ns]
    if end_ns is not None:
        filtered = [row for row in filtered if int(row[column]) <= end_ns]
    return filtered


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
    return {"5m": Timeframe.TF_5M, "1h": Timeframe.TF_1H, "4h": Timeframe.TF_4H, "1d": Timeframe.TF_1D}.get(
        value, Timeframe.TF_4H
    )


def _timeframe_priority(value: str) -> int:
    """Order: 1d context first, then 4h, then 1h, then 5m execution last."""
    return {"1d": 0, "4h": 1, "1h": 2, "5m": 3}.get(value, 1)


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


_HALVINGS_NS: tuple[tuple[int, int], ...] = (
    (int(datetime(2012, 11, 28, tzinfo=UTC).timestamp() * 1_000_000_000), 1),
    (int(datetime(2016, 7, 9, tzinfo=UTC).timestamp() * 1_000_000_000), 2),
    (int(datetime(2020, 5, 11, tzinfo=UTC).timestamp() * 1_000_000_000), 3),
    (int(datetime(2024, 4, 20, tzinfo=UTC).timestamp() * 1_000_000_000), 4),
)


def _get_cycle_phase(timestamp_ns: int) -> tuple[int, str]:
    cycle_start_ns = 0
    cycle_num = 0
    for halving_ns, current_cycle in _HALVINGS_NS:
        if timestamp_ns >= halving_ns:
            cycle_start_ns = halving_ns
            cycle_num = current_cycle
        else:
            break
    if cycle_num == 0:
        return 0, "UNKNOWN"

    months_since = (timestamp_ns - cycle_start_ns) / 1_000_000_000 / 86_400 / 30.0
    if months_since < 6:
        return cycle_num, "POST_HALVING"
    if months_since < 12:
        return cycle_num, "BULL_INITIAL"
    if months_since < 18:
        return cycle_num, "BULL_PEAK"
    if months_since < 24:
        return cycle_num, "CRASH"
    if months_since < 36:
        return cycle_num, "BEAR"
    return cycle_num, "RECOVERY"


def _sharpe_by_phase(snapshots: list[RiskSnapshot]) -> dict[str, float]:
    if len(snapshots) < 2:
        return {}

    phase_returns: dict[str, list[float]] = {}
    previous_equity: float | None = None
    for snapshot in sorted(snapshots, key=lambda item: item.ts_ns):
        equity = float(snapshot.equity)
        if previous_equity is None or previous_equity <= 0:
            previous_equity = equity
            continue
        phase = _get_cycle_phase(snapshot.ts_ns)[1]
        phase_returns.setdefault(phase, []).append((equity - previous_equity) / previous_equity)
        previous_equity = equity

    return {
        phase: round(_annualized_ratio(returns, periods_per_year=6 * 365), 4)
        for phase, returns in sorted(phase_returns.items())
        if returns
    }


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


def _reject_duplicate_stage_points(points: list[Any], *, label: str) -> None:
    counts = Counter(int(item.cutoff_time_ns) for item in points)
    duplicates = sorted(cutoff for cutoff, count in counts.items() if count != 1)
    if duplicates:
        raise ValueError(f"duplicate point-in-time {label} inputs at cutoffs={duplicates!r}")


def _architecture_risk_donors(
    snapshots: list[RiskSnapshot],
    *,
    decision_cutoffs: tuple[int, ...],
) -> dict[int, tuple[RiskSnapshot | None, str]]:
    """Freeze the latest authoritative risk snapshot visible at each cutoff."""
    by_source_time: dict[int, RiskSnapshot] = {}
    for snapshot in snapshots:
        source_time = int(snapshot.ts_ns)
        prior = by_source_time.get(source_time)
        if prior is not None and prior != snapshot:
            raise ValueError(f"conflicting risk snapshots at source_time_ns={source_time}")
        by_source_time[source_time] = snapshot
    source_times = sorted(by_source_time)
    donors: dict[int, tuple[RiskSnapshot | None, str]] = {}
    for cutoff in decision_cutoffs:
        source_index = bisect_right(source_times, cutoff) - 1
        if source_index < 0:
            donors[cutoff] = (None, "NO_PRIOR_SNAPSHOT_NEUTRAL")
            continue
        donors[cutoff] = (by_source_time[source_times[source_index]], "ASOF")
    return donors


def _architecture_risk_regime(state: MarketState) -> str:
    quality = state.quality_state.upper()
    volatility = state.volatility_state.upper()
    directional = state.directional_state.upper()
    regime = state.regime.upper()
    if quality in {"UNSAFE", "DEGRADED"} or volatility == "DISTORTED":
        return "UNSAFE"
    if regime == "STRESS":
        return "STRESS"
    if quality == "NOISY" or directional == "TRANSITION":
        return "NOISY"
    return "TRADABLE"


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


def _aggregate_five_min_rows_to_one_hour(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive deterministic 1H candles from 5M bars when train coverage is missing."""
    if not rows:
        return []

    hour_ns = Timeframe.TF_1H.seconds * 1_000_000_000
    bucketed: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        open_ns = int(row["open_time_ns"])
        hour_start_ns = open_ns - (open_ns % hour_ns)
        bucketed[hour_start_ns].append(row)

    aggregated: list[dict[str, Any]] = []
    expected_step_ns = Timeframe.TF_5M.seconds * 1_000_000_000
    for hour_start_ns in sorted(bucketed):
        bucket = sorted(bucketed[hour_start_ns], key=lambda item: int(item["open_time_ns"]))
        if len(bucket) != 12:
            continue
        contiguous = all(
            int(bucket[idx]["open_time_ns"]) - int(bucket[idx - 1]["open_time_ns"]) == expected_step_ns
            for idx in range(1, len(bucket))
        )
        if not contiguous:
            continue
        first = bucket[0]
        last = bucket[-1]
        aggregated.append(
            {
                "symbol": first.get("symbol", ""),
                "timeframe": Timeframe.TF_1H.value,
                "open_time_ns": int(first["open_time_ns"]),
                "close_time_ns": int(last["close_time_ns"]),
                "open": float(first["open"]),
                "high": max(float(item["high"]) for item in bucket),
                "low": min(float(item["low"]) for item in bucket),
                "close": float(last["close"]),
                "volume": sum(float(item["volume"]) for item in bucket),
                "trade_count": sum(int(item["trade_count"]) for item in bucket),
            }
        )
    return aggregated


def _build_replay_context(config: HistoricalBacktestConfig) -> ReplayContext:
    cache_stats: dict[str, Any] = {
        "store_cache_hits": 0,
        "store_cache_requests": 0,
        "raw_source_rows_cache_hits": 0,
        "raw_source_rows_cache_requests": 0,
        "event_list_cache_hit": False,
        "event_list_cache_hit_rate": 0.0,
        "raw_source_rows_cache_hit_rate": 0.0,
    }

    macro_store, macro_store_hit = _load_store_cached(
        ("macro_store", str(config.macro_dir)),
        lambda: MacroDataStore(MacroDataStoreConfig(macro_dir=config.macro_dir)),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(macro_store_hit)
    options_store, options_store_hit = _load_store_cached(
        ("options_store", str(config.options_dir), "BTC"),
        lambda: OptionsDataStore(OptionsDataStoreConfig(options_dir=config.options_dir, currency="BTC")),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(options_store_hit)
    hf_dir = config.root_dir / "klines" / config.symbol / "5m"
    hf_store, hf_store_hit = _load_store_cached(
        ("hf_store", str(hf_dir)),
        lambda: HFFeatureStore(HFFeatureStoreConfig(data_dir=hf_dir)),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(hf_store_hit)
    cot_store, cot_store_hit = _load_store_cached(
        ("cot_store", str(config.cot_path)),
        lambda: CotDataStore(CotStoreConfig(cot_path=config.cot_path)),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(cot_store_hit)
    liquidation_store, liquidation_store_hit = _load_store_cached(
        ("liquidation_store", str(config.liquidation_dir)),
        lambda: LiquidationDataStore(LiquidationStoreConfig(liquidation_dir=config.liquidation_dir)),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(liquidation_store_hit)
    eth_store, eth_store_hit = _load_store_cached(
        ("eth_store", str(config.root_dir / "klines" / "ETHUSDT" / "4h")),
        lambda: EthDataStore(EthStoreConfig(eth_kline_path=config.root_dir / "klines" / "ETHUSDT" / "4h")),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(eth_store_hit)
    cross_asset_context_store, cross_asset_context_store_hit = _load_store_cached(
        ("cross_asset_context_store", "data/research/cross_asset/sol_bnb_btc_context.parquet"),
        lambda: CrossAssetContextStore(
            CrossAssetContextStoreConfig(data_path=Path("data/research/cross_asset/sol_bnb_btc_context.parquet"))
        ),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(cross_asset_context_store_hit)
    onchain_factor_store, onchain_factor_store_hit = _load_store_cached(
        ("onchain_factor_store", "data/raw/coinmetrics/community/BTC/derived_onchain_factors.parquet"),
        lambda: OnchainFactorStore(
            OnchainFactorStoreConfig(
                data_path=Path("data/raw/coinmetrics/community/BTC/derived_onchain_factors.parquet")
            )
        ),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(onchain_factor_store_hit)
    binance_metrics_factor_store, binance_metrics_factor_store_hit = _load_store_cached(
        ("binance_metrics_factor_store", "data/raw/binance/metrics/BTCUSDT/derived_metrics_factors.parquet"),
        lambda: BinanceMetricsFactorStore(
            BinanceMetricsFactorStoreConfig(
                data_path=Path("data/raw/binance/metrics/BTCUSDT/derived_metrics_factors.parquet")
            )
        ),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(binance_metrics_factor_store_hit)
    basis_store, basis_store_hit = _load_store_cached(
        (
            "basis_store",
            str(Path("data/raw/binance_spot/klines") / config.symbol / "4h"),
            str(config.root_dir / "klines" / config.symbol / "4h"),
        ),
        lambda: BasisDataStore(
            BasisStoreConfig(
                spot_dir=Path("data/raw/binance_spot/klines") / config.symbol / "4h",
                perp_dir=config.root_dir / "klines" / config.symbol / "4h",
            )
        ),
    )
    cache_stats["store_cache_requests"] += 1
    cache_stats["store_cache_hits"] += int(basis_store_hit)

    if not getattr(macro_store, "loaded", False):
        macro_store.load()
    if not getattr(options_store, "loaded", False):
        options_store.load()
    if not getattr(hf_store, "loaded", False):
        hf_store.load()
    if not getattr(cot_store, "loaded", False):
        cot_store.load()
    if not getattr(liquidation_store, "loaded", False):
        liquidation_store.load()
    if not getattr(eth_store, "loaded", False):
        eth_store.load()
    if not getattr(cross_asset_context_store, "loaded", False):
        cross_asset_context_store.load()
    if not getattr(onchain_factor_store, "loaded", False):
        onchain_factor_store.load()
    if not getattr(binance_metrics_factor_store, "loaded", False):
        binance_metrics_factor_store.load()
    if not getattr(basis_store, "loaded", False):
        basis_store.load()

    funding_rows_raw, funding_rows_hit = _read_rows_cached(
        cache_key=("funding_rows", str(config.root_dir), config.symbol),
        path_glob=str(config.root_dir / "funding" / config.symbol / "*.parquet"),
        order_by="funding_time_ns ASC",
    )
    cache_stats["raw_source_rows_cache_requests"] += 1
    cache_stats["raw_source_rows_cache_hits"] += int(funding_rows_hit)
    funding_rows = _filter_rows_by_time(
        funding_rows_raw,
        column="funding_time_ns",
        start_ns=config.start_ns,
        end_ns=config.end_ns,
    )
    open_interest_rows_raw, open_interest_rows_hit = _read_rows_cached(
        cache_key=("open_interest_rows", str(config.root_dir), config.symbol, config.open_interest_timeframe),
        path_glob=str(config.root_dir / "open_interest" / config.symbol / config.open_interest_timeframe / "*.parquet"),
        order_by="ts_ns ASC",
    )
    cache_stats["raw_source_rows_cache_requests"] += 1
    cache_stats["raw_source_rows_cache_hits"] += int(open_interest_rows_hit)
    open_interest_rows = _filter_rows_by_time(
        open_interest_rows_raw,
        column="ts_ns",
        start_ns=config.start_ns,
        end_ns=config.end_ns,
    )
    aggtrades: dict[str, dict[int, dict[str, Any]]] = {}
    for tf_label in ("4h", "1h", "5m"):
        agg_glob = str(config.root_dir / "aggtrades" / config.symbol / tf_label / "*.parquet")
        agg_rows, agg_rows_hit = _read_rows_cached(
            cache_key=("aggtrades_rows", str(config.root_dir), config.symbol, tf_label),
            path_glob=agg_glob,
            order_by="ts_ns ASC",
        )
        cache_stats["raw_source_rows_cache_requests"] += 1
        cache_stats["raw_source_rows_cache_hits"] += int(agg_rows_hit)
        if agg_rows:
            aggtrades[tf_label] = {int(row["ts_ns"]): row for row in agg_rows}

    timeframes_to_load: list[Timeframe] = [config.bias_timeframe]
    if config.direction_timeframe not in timeframes_to_load:
        timeframes_to_load.append(config.direction_timeframe)
    if config.context_timeframe and config.context_timeframe not in timeframes_to_load:
        timeframes_to_load.append(config.context_timeframe)
    if config.execution_timeframe not in timeframes_to_load:
        timeframes_to_load.append(config.execution_timeframe)
    event_cache_key = (
        str(config.root_dir),
        config.symbol,
        tuple(sorted(tf.value for tf in timeframes_to_load)),
        config.start_ns,
        config.end_ns,
    )
    candles = _EVENT_LIST_CACHE.get(event_cache_key)
    cache_stats["event_list_cache_hit"] = candles is not None
    if candles is None:
        candles = []
        loaded_rows: dict[Timeframe, list[dict[str, Any]]] = {}
        for timeframe in timeframes_to_load:
            raw_rows, raw_rows_hit = _read_rows_cached(
                cache_key=("kline_rows", str(config.root_dir), config.symbol, timeframe.value),
                path_glob=str(config.root_dir / "klines" / config.symbol / timeframe.value / "*.parquet"),
                order_by="close_time_ns ASC",
            )
            cache_stats["raw_source_rows_cache_requests"] += 1
            cache_stats["raw_source_rows_cache_hits"] += int(raw_rows_hit)
            rows = _filter_rows_by_time(
                raw_rows,
                column="close_time_ns",
                start_ns=config.start_ns,
                end_ns=config.end_ns,
            )
            loaded_rows[timeframe] = rows
            if not rows and timeframe == Timeframe.TF_1H and config.execution_timeframe == Timeframe.TF_5M:
                five_min_rows = loaded_rows.get(Timeframe.TF_5M)
                if five_min_rows is None:
                    five_min_raw_rows, five_min_rows_hit = _read_rows_cached(
                        cache_key=("kline_rows", str(config.root_dir), config.symbol, Timeframe.TF_5M.value),
                        path_glob=str(config.root_dir / "klines" / config.symbol / Timeframe.TF_5M.value / "*.parquet"),
                        order_by="close_time_ns ASC",
                    )
                    cache_stats["raw_source_rows_cache_requests"] += 1
                    cache_stats["raw_source_rows_cache_hits"] += int(five_min_rows_hit)
                    five_min_rows = _filter_rows_by_time(
                        five_min_raw_rows,
                        column="close_time_ns",
                        start_ns=config.start_ns,
                        end_ns=config.end_ns,
                    )
                    loaded_rows[Timeframe.TF_5M] = five_min_rows
                rows = _aggregate_five_min_rows_to_one_hour(five_min_rows)
                loaded_rows[timeframe] = rows
            for row in loaded_rows[timeframe]:
                enriched = dict(row)
                enriched["event_timeframe"] = timeframe.value
                candles.append(enriched)
        candles.sort(key=lambda item: (int(item["close_time_ns"]), _timeframe_priority(str(item["event_timeframe"]))))
        _EVENT_LIST_CACHE[event_cache_key] = candles
    cache_stats["event_list_cache_hit_rate"] = 1.0 if cache_stats["event_list_cache_hit"] else 0.0
    cache_stats["raw_source_rows_cache_hit_rate"] = round(
        float(cache_stats["raw_source_rows_cache_hits"]) / max(1, int(cache_stats["raw_source_rows_cache_requests"])),
        4,
    )
    cache_stats["store_cache_hit_rate"] = round(
        float(cache_stats["store_cache_hits"]) / max(1, int(cache_stats["store_cache_requests"])),
        4,
    )
    return ReplayContext(
        funding_rows=funding_rows,
        open_interest_rows=open_interest_rows,
        aggtrades=aggtrades,
        events=candles,
        macro_store=macro_store,
        options_store=options_store,
        hf_store=hf_store,
        cot_store=cot_store,
        liquidation_store=liquidation_store,
        eth_store=eth_store,
        cross_asset_context_store=cross_asset_context_store,
        onchain_factor_store=onchain_factor_store,
        binance_metrics_factor_store=binance_metrics_factor_store,
        basis_store=basis_store,
        cache_stats=cache_stats,
    )
