from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class SignalType(IntEnum):
    CONTINUOUS = 0
    DISCRETE_BOOL = 1


class PAPattern(IntEnum):
    NONE = 0
    H1_PULLBACK = 1
    H2_RESUMPTION = 2
    MAJOR_TREND_REVERSAL = 3


class OrderType(IntEnum):
    MARKET = 0
    LIMIT = 1
    POST_ONLY = 2
    FOK = 3
    IOC = 4


class OrderStatus(IntEnum):
    NEW = 0
    PARTIALLY_FILLED = 1
    FILLED = 2
    REJECTED = 3


@dataclass(slots=True)
class OrderBookLevel:
    price: float
    quantity: float


@dataclass(slots=True)
class OrderBookSnapshot:
    symbol: str
    exchange: str
    exchange_timestamp_ns: int
    local_timestamp_ns: int
    is_snapshot: bool
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]


@dataclass(slots=True)
class TradeTick:
    symbol: str
    exchange: str
    price: float
    size: float
    trade_id: int
    is_buyer_maker: bool
    exchange_timestamp_ns: int
    local_timestamp_ns: int


@dataclass(slots=True)
class AlphaSignal:
    factor_id: str
    symbol: str
    type: SignalType
    continuous_value: float
    pattern_event: PAPattern
    confidence_ic: float
    timestamp_ns: int


@dataclass(slots=True)
class SubmitOrder:
    client_order_id: str
    symbol: str
    type: OrderType
    limit_price: float
    quantity: float
    side: int
    reduce_only: bool
    max_slippage_tolerance: float
    enable_synthetic_arb: bool


@dataclass(slots=True)
class ExecutionReport:
    client_order_id: str
    exchange_order_id: str
    status: OrderStatus
    cumulative_filled_qty: float
    volume_weighted_avg_price: float
    commission: float
    commission_asset: str
    transaction_time_ns: int
    reject_reason: str = ""


@dataclass(slots=True)
class Candle:
    symbol: str
    timestamp_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float

