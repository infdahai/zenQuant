"""Historical replay/backtest runner built on the live runtime contracts.

The goal is not to create a separate research-only engine. Historical mode
publishes the same market events the live kernel consumes so that strategy,
risk, and execution behavior stays auditable across modes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zenquant.domain import (
    BacktestMetrics,
    BarCloseEvent,
    Candle,
    EventMeta,
    ExecutionReport,
    IndicatorPack,
    MarkPriceUpdate,
    MarketSnapshot,
    RiskSnapshot,
    TargetPosition,
    Timeframe,
)
from zenquant.execution import ExecutionKernel, SimulatedGateway
from zenquant.indicator import IndicatorEngine, IndicatorEngineConfig, IndicatorStore
from zenquant.infra import InMemoryJetStream, StreamConfig
from zenquant.risk import RiskOverlay, RiskOverlayConfig
from zenquant.state import StateEngine
from zenquant.strategy import ARCStrategyEngine

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
    open_interest_timeframe: str = "4h"


@dataclass(slots=True)
class HistoricalBacktestResult:
    """Compact result returned by the backtest runner."""

    metrics: BacktestMetrics
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
        self.indicator_engine = IndicatorEngine(
            bus=self.bus,
            store=self.indicator_store,
            config=IndicatorEngineConfig(expected_symbols=(self._config.portfolio_symbol,)),
        )
        self.risk_overlay = RiskOverlay(
            bus=self.bus,
            config=RiskOverlayConfig(starting_equity=self._config.starting_equity),
        )
        self.strategy_engine = ARCStrategyEngine(bus=self.bus)
        self.state_engine = StateEngine(bus=self.bus)
        self.execution_kernel = ExecutionKernel(
            bus=self.bus,
            gateway=SimulatedGateway(),
            risk_overlay=self.risk_overlay,
        )

        self._indicators: list[IndicatorPack] = []
        self._targets: list[TargetPosition] = []
        self._reports: list[ExecutionReport] = []
        self._risk_snapshots: list[RiskSnapshot] = []
        self._trade_ledger = _TradeLedger()
        self._funding_rows: list[dict[str, Any]] = []
        self._open_interest_rows: list[dict[str, Any]] = []
        self._funding_index = -1
        self._open_interest_index = -1

        self.bus.subscribe("indicator.>", self._collect_indicator)
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
        for event in events:
            self._publish_event(event)

        metrics = self._build_metrics(events)
        return HistoricalBacktestResult(
            metrics=metrics,
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
        self._funding_index = -1
        self._open_interest_index = -1

        candles: list[dict[str, Any]] = []
        for timeframe in (self._config.bias_timeframe, self._config.execution_timeframe):
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
                cvd_delta=_proxy_cvd_delta(row),
                funding_oi_weighted=float(funding_row.get("funding_rate", 0.0)) if funding_row else None,
                oi_coverage=1.0 if oi_row else 0.0,
                staleness_ms=_aux_staleness_ms(cutoff_ns, funding_row, oi_row),
                quality_flags=["OK"] if oi_row else ["MISSING_OI"],
            ),
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

    def _collect_report(self, _: str, payload: object) -> None:
        if isinstance(payload, ExecutionReport):
            self._reports.append(payload)
            self._trade_ledger.on_execution_report(payload)

    def _collect_risk_snapshot(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskSnapshot):
            self._risk_snapshots.append(payload)

    def _build_metrics(self, events: list[dict[str, Any]]) -> BacktestMetrics:
        if not events:
            raise RuntimeError("no historical events available for replay")
        returns = _equity_returns(self._risk_snapshots)
        sharpe = _annualized_ratio(returns, periods_per_year=6 * 365)
        sortino = _annualized_ratio(
            [value for value in returns if value < 0.0],
            periods_per_year=6 * 365,
            baseline_returns=returns,
        )
        max_drawdown, max_dd_duration = _max_drawdown(self._risk_snapshots)
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
            total_funding=0.0,
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
    if value == Timeframe.TF_1D.value:
        return Timeframe.TF_1D
    return Timeframe.TF_4H


def _timeframe_priority(value: str) -> int:
    """Keep 1d context ahead of 4h execution when bars share a close time."""
    return 0 if value == Timeframe.TF_1D.value else 1


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
