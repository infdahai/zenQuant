from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from zenquant.domain.models import AlphaSignal, ExecutionReport, OrderBookSnapshot, TradeTick
from zenquant.factor_lab.models import FactorWeight
from zenquant.risk import RiskGuard


@dataclass(slots=True)
class DashboardStateConfig:
    max_points: int = 180
    max_signals: int = 24
    max_reports: int = 32
    max_weights: int = 24


class DashboardStateStore:
    def __init__(self, config: DashboardStateConfig | None = None) -> None:
        self._config = config or DashboardStateConfig()
        self._prices: deque[dict[str, float | int]] = deque(maxlen=self._config.max_points)
        self._signals: deque[dict[str, Any]] = deque(maxlen=self._config.max_signals)
        self._reports: deque[dict[str, Any]] = deque(maxlen=self._config.max_reports)
        self._weights: deque[dict[str, Any]] = deque(maxlen=self._config.max_weights)
        self._bids: list[dict[str, float]] = []
        self._asks: list[dict[str, float]] = []
        self._symbol = "BTC_USDT"
        self._exchange = "BINANCE"
        self._last_price = 0.0
        self._last_spread = 0.0
        self._last_ts = 0

    def on_trade(self, tick: TradeTick) -> None:
        self._symbol = tick.symbol
        self._exchange = tick.exchange
        self._last_price = tick.price
        self._last_ts = tick.local_timestamp_ns
        self._prices.append(
            {
                "t": tick.local_timestamp_ns,
                "p": tick.price,
                "s": tick.size,
            }
        )

    def on_orderbook(self, snapshot: OrderBookSnapshot) -> None:
        self._symbol = snapshot.symbol
        self._exchange = snapshot.exchange
        self._last_ts = snapshot.local_timestamp_ns
        self._bids = [{"price": level.price, "qty": level.quantity} for level in snapshot.bids]
        self._asks = [{"price": level.price, "qty": level.quantity} for level in snapshot.asks]
        if self._bids and self._asks:
            self._last_spread = max(0.0, self._asks[0]["price"] - self._bids[0]["price"])

    def on_signal(self, signal: AlphaSignal) -> None:
        self._signals.appendleft(
            {
                "factor_id": signal.factor_id,
                "symbol": signal.symbol,
                "type": int(signal.type),
                "continuous_value": signal.continuous_value,
                "pattern_event": int(signal.pattern_event),
                "confidence_ic": signal.confidence_ic,
                "timestamp_ns": signal.timestamp_ns,
            }
        )

    def on_report(self, report: ExecutionReport) -> None:
        self._reports.appendleft(
            {
                "client_order_id": report.client_order_id,
                "exchange_order_id": report.exchange_order_id,
                "status": int(report.status),
                "filled_qty": report.cumulative_filled_qty,
                "vwap": report.volume_weighted_avg_price,
                "commission": report.commission,
                "commission_asset": report.commission_asset,
                "timestamp_ns": report.transaction_time_ns,
                "reject_reason": report.reject_reason,
            }
        )

    def on_weight(self, weight: FactorWeight) -> None:
        self._weights.appendleft(
            {
                "symbol": weight.symbol,
                "factor_id": weight.factor_id,
                "weight": weight.weight,
                "ic_mean": weight.ic_mean,
                "orthogonal_penalty": weight.orthogonal_penalty,
                "portfolio_sigma": weight.portfolio_sigma,
                "timestamp_ns": weight.timestamp_ns,
            }
        )

    def snapshot(self, risk_guard: RiskGuard) -> dict[str, Any]:
        position = risk_guard.positions.get(self._symbol, 0.0)
        return {
            "symbol": self._symbol,
            "exchange": self._exchange,
            "last_price": self._last_price,
            "spread": self._last_spread,
            "timestamp_ns": self._last_ts,
            "prices": list(self._prices),
            "bids": self._bids,
            "asks": self._asks,
            "signals": list(self._signals),
            "reports": list(self._reports),
            "weights": list(self._weights),
            "position": position,
            "kill_switch_active": risk_guard.kill_switch_active,
            "equity": risk_guard.equity,
            "high_water_mark": risk_guard.high_water_mark,
        }

