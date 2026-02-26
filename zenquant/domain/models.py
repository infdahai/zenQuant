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
