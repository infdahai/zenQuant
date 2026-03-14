from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Timeframe(StrEnum):
    TF_4H = "4h"
    TF_1D = "1d"

    @property
    def seconds(self) -> int:
        if self is Timeframe.TF_4H:
            return 4 * 60 * 60
        return 24 * 60 * 60


@dataclass(slots=True, frozen=True)
class EventMeta:
    event_time_ns: int
    ingest_time_ns: int
    venue: str
    symbol: str
    stream_seq: int
    schema_version: str = "v1"


@dataclass(slots=True, frozen=True)
class OrderBookLevel:
    price: float
    quantity: float


@dataclass(slots=True)
class TradeTick:
    meta: EventMeta
    price: float
    size: float
    is_buyer_aggressor: bool | None


@dataclass(slots=True)
class OrderBookSnapshot:
    meta: EventMeta
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    is_snapshot: bool = True


@dataclass(slots=True)
class FundingUpdate:
    meta: EventMeta
    funding_rate: float


@dataclass(slots=True)
class OpenInterestUpdate:
    meta: EventMeta
    open_interest: float


@dataclass(slots=True)
class BarCloseEvent:
    timeframe: Timeframe
    symbol: str
    bar_start_ns: int
    bar_end_ns: int


@dataclass(slots=True)
class Candle:
    symbol: str
    timeframe: Timeframe
    bar_start_ns: int
    bar_end_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    last_price: float
    bid_depth: float
    ask_depth: float
    book_age_ms: int
    venue_count: int
    cvd_delta: float
    funding_oi_weighted: float | None
    oi_coverage: float
    staleness_ms: int
    quality_flags: list[str]


@dataclass(slots=True)
class IndicatorCoverage:
    venue_count: int
    oi_coverage: float
    staleness_ms: int


@dataclass(slots=True)
class IndicatorPack:
    ts_ns: int
    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    indicator_version: str
    formula_hash: str
    quality_flags: list[str]
    coverage: IndicatorCoverage
    values: dict[str, Any]
    components: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class TargetPosition:
    intent_id: str
    portfolio: str
    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    target_qty: float
    target_leverage: float
    reduce_only: bool
    regime: str
    regime_confidence: float
    reason_codes: list[str]


@dataclass(slots=True)
class IntentReason:
    portfolio: str
    symbol: str
    cutoff_time_ns: int
    summary: str
    reason_codes: list[str]


@dataclass(slots=True)
class RiskOverlayDecision:
    symbol: str
    cutoff_time_ns: int
    original_target_qty: float
    revised_target_qty: float
    lev_cap: float
    reduce_only: bool
    flags: list[str]


@dataclass(slots=True)
class RiskSnapshot:
    portfolio: str
    ts_ns: int
    equity: float
    high_water_mark: float
    drawdown: float
    lev_cap: float
    reduce_only_mode: bool
    flags: list[str]


@dataclass(slots=True)
class ExecutionOrder:
    client_order_id: str
    symbol: str
    side: int
    quantity: float
    reduce_only: bool
    max_slippage_bps: float


@dataclass(slots=True)
class ExecutionReport:
    client_order_id: str
    symbol: str
    side: int
    status: str
    filled_qty: float
    avg_price: float
    fee: float
    transaction_time_ns: int
    reject_reason: str = ""


# ---------------------------------------------------------------------------
# Phase 2 domain types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SwingPoint:
    """A confirmed swing high or swing low with its bar timestamp."""

    bar_end_ns: int
    price: float
    is_high: bool  # True = swing high, False = swing low


@dataclass(slots=True)
class SwingStructure:
    """
    Price-structure state computed at each bar close.

    structure_state:
      - UPTREND   : confirmed HH + HL sequence
      - DOWNTREND : confirmed LH + LL sequence
      - NEUTRAL   : mixed or insufficient data
    """

    symbol: str
    timeframe: Timeframe
    cutoff_time_ns: int
    structure_state: str  # UPTREND | DOWNTREND | NEUTRAL
    last_swing_high: float  # price level for stop placement
    last_swing_low: float  # price level for stop placement
    structure_break: bool  # True on the bar where structure flips
    bars_since_break: int  # bars since last structure change
    pivot_lookback: int  # N used for pivot detection


@dataclass(slots=True)
class OpenPositionState:
    """
    Stateful tracking of an open position inside ARCStrategyEngine.
    Preserved across bars — this is the core Phase 2 addition.
    """

    symbol: str
    timeframe: Timeframe
    direction: float  # +1.0 = long, -1.0 = short
    entry_price: float
    entry_bar_ns: int
    base_qty: float  # original entry qty
    current_qty: float  # after pyramids/partial exits
    trailing_stop: float  # current stop price (absolute)
    pyramid_count: int  # how many adds have been made (0 = initial only)
    bars_held: int  # bars since entry


@dataclass(slots=True)
class DirectionalBias:
    """
    1D-timeframe directional bias published per symbol.
    4H strategy reads this before computing its own TargetPosition.
    """

    symbol: str
    cutoff_time_ns: int
    direction: float  # +1.0 LONG | -1.0 SHORT | 0.0 FLAT
    regime: str
    confidence: float


@dataclass(slots=True)
class BacktestMetrics:
    """Summary metrics from a single backtest window."""

    symbol: str
    start_ns: int
    end_ns: int
    total_bars: int
    net_pnl: float
    gross_pnl: float
    total_fees: float
    total_funding: float
    sharpe: float
    sortino: float
    max_drawdown: float
    max_dd_duration_bars: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    trade_count: int
    turnover_per_day: float
    fee_drag_pct: float  # fees / gross_pnl
