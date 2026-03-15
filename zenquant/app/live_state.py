from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from zenquant.domain import (
    Candle,
    DirectionalBias,
    IndicatorPack,
    IntentReason,
    OpenPositionState,
    RiskOverlayDecision,
    RiskSnapshot,
    TargetPosition,
    Timeframe,
)
from zenquant.domain.serde import to_jsonable


@dataclass(slots=True)
class DashboardStateConfig:
    max_candles: int = 220
    max_alerts: int = 64
    max_equity_points: int = 256
    max_reports: int = 48
    max_reasons: int = 32
    bias_stale_after_4h_bars: int = 8


class DashboardStateStore:
    def __init__(self, config: DashboardStateConfig | None = None) -> None:
        self._config = config or DashboardStateConfig()
        self._candles: dict[tuple[str, str], deque[Candle]] = defaultdict(
            lambda: deque(maxlen=self._config.max_candles)
        )
        self._latest_indicator: dict[tuple[str, str], IndicatorPack] = {}
        self._indicator_history: dict[tuple[str, str], deque[IndicatorPack]] = defaultdict(
            lambda: deque(maxlen=self._config.max_candles)
        )
        self._alerts: deque[dict[str, Any]] = deque(maxlen=self._config.max_alerts)
        self._equity_curve: deque[dict[str, Any]] = deque(maxlen=self._config.max_equity_points)
        self._reports: deque[dict[str, Any]] = deque(maxlen=self._config.max_reports)
        self._reasons: deque[dict[str, Any]] = deque(maxlen=self._config.max_reasons)
        self._risk_snapshot: RiskSnapshot | None = None
        self._risk_decision: RiskOverlayDecision | None = None
        self._target_positions: dict[str, float] = {}
        self._latest_target: dict[tuple[str, str], TargetPosition] = {}
        self._directional_bias: dict[str, DirectionalBias] = {}
        self._position_state: dict[tuple[str, str], OpenPositionState] = {}
        self._last_price: dict[str, float] = {}
        self._runtime_status: dict[str, Any] = {
            "mode": "demo",
            "market_connected": False,
            "last_market_event_ns": 0,
            "database": {"enabled": False, "writes": 0, "dropped": 0, "queue_depth": 0, "last_error": ""},
        }
        self._default_symbol = "BTCUSDT_PERP"
        self._default_tf = "4h"

    def on_candle(self, candle: Candle) -> None:
        key = (candle.symbol, candle.timeframe.value)
        self._candles[key].append(candle)

    def on_indicator(self, pack: IndicatorPack) -> None:
        key = (pack.symbol, pack.timeframe.value)
        self._latest_indicator[key] = pack
        self._indicator_history[key].append(pack)
        last_price = float(pack.values.get("last_price", 0.0) or 0.0)
        if last_price > 0:
            self._last_price[pack.symbol] = last_price

        if pack.quality_flags != ["OK"]:
            self._alerts.appendleft(
                {
                    "ts_ns": pack.cutoff_time_ns,
                    "symbol": pack.symbol,
                    "tf": pack.timeframe.value,
                    "type": "DATA_QUALITY",
                    "flags": list(pack.quality_flags),
                }
            )

    def on_risk_snapshot(self, snapshot: RiskSnapshot) -> None:
        self._risk_snapshot = snapshot
        self._equity_curve.append(
            {
                "ts_ns": snapshot.ts_ns,
                "equity": snapshot.equity,
                "drawdown": snapshot.drawdown,
            }
        )
        if snapshot.flags != ["OK"]:
            self._alerts.appendleft(
                {
                    "ts_ns": snapshot.ts_ns,
                    "symbol": self._default_symbol,
                    "tf": self._default_tf,
                    "type": "STRESS",
                    "flags": list(snapshot.flags),
                }
            )

    def on_risk_decision(self, decision: RiskOverlayDecision) -> None:
        self._risk_decision = decision

    def on_target_position(self, target: TargetPosition) -> None:
        self._target_positions[target.symbol] = target.target_qty
        self._latest_target[(target.symbol, target.timeframe.value)] = target

    def on_reason(self, reason: IntentReason) -> None:
        self._reasons.appendleft(to_jsonable(reason))

    def on_exec_report(self, payload: dict[str, Any]) -> None:
        self._reports.appendleft(payload)

    def on_directional_bias(self, bias: DirectionalBias) -> None:
        self._directional_bias[bias.symbol] = bias

    def on_position_state(self, symbol: str, timeframe: str, payload: object) -> None:
        key = (symbol, timeframe)
        if isinstance(payload, OpenPositionState):
            self._position_state[key] = payload
            return
        self._position_state.pop(key, None)

    def on_mark_price(self, symbol: str, ts_ns: int, price: float) -> None:
        if price > 0:
            self._last_price[symbol] = price
        self._runtime_status["market_connected"] = True
        self._runtime_status["last_market_event_ns"] = ts_ns

    def on_runtime_status(self, payload: dict[str, Any]) -> None:
        self._runtime_status.update(payload)

    def snapshot(self, symbol: str | None = None, timeframe: str | None = None) -> dict[str, Any]:
        symbol = symbol or self._default_symbol
        timeframe = timeframe or self._default_tf
        key = (symbol, timeframe)
        latest = self._latest_indicator.get(key)
        bias = self._directional_bias.get(symbol)

        return {
            "symbol": symbol,
            "tf": timeframe,
            "last_price": self._last_price.get(symbol, 0.0),
            "target_position": self._target_positions.get(symbol, 0.0),
            "candles": [to_jsonable(item) for item in self._candles.get(key, [])],
            "latest_indicator": to_jsonable(latest) if latest is not None else None,
            "latest_target": to_jsonable(self._latest_target.get(key))
            if self._latest_target.get(key) is not None
            else None,
            "directional_bias": to_jsonable(bias) if bias is not None else None,
            "directional_bias_meta": self._directional_bias_meta(latest, bias),
            "position_state": to_jsonable(self._position_state.get(key))
            if self._position_state.get(key) is not None
            else None,
            "alerts": list(self._alerts),
            "risk_snapshot": to_jsonable(self._risk_snapshot) if self._risk_snapshot is not None else None,
            "risk_decision": to_jsonable(self._risk_decision) if self._risk_decision is not None else None,
            "equity_curve": list(self._equity_curve),
            "runtime_status": dict(self._runtime_status),
            "reasons": list(self._reasons),
            "reports": list(self._reports),
        }

    def latest_indicator(self, symbol: str, timeframe: str) -> IndicatorPack | None:
        return self._latest_indicator.get((symbol, timeframe))

    def indicator_history(
        self,
        symbol: str,
        timeframe: str,
        from_ns: int | None = None,
        to_ns: int | None = None,
    ) -> list[IndicatorPack]:
        points = list(self._indicator_history.get((symbol, timeframe), []))
        if from_ns is not None:
            points = [point for point in points if point.cutoff_time_ns >= from_ns]
        if to_ns is not None:
            points = [point for point in points if point.cutoff_time_ns <= to_ns]
        return points

    def _directional_bias_meta(
        self, latest: IndicatorPack | None, bias: DirectionalBias | None
    ) -> dict[str, Any] | None:
        if bias is None:
            return None

        reference_cutoff_ns = latest.cutoff_time_ns if latest is not None else bias.cutoff_time_ns
        age_ns = max(0, reference_cutoff_ns - bias.cutoff_time_ns)
        age_4h_bars = age_ns / (Timeframe.TF_4H.seconds * 1_000_000_000)
        stale_after_bars = float(self._config.bias_stale_after_4h_bars)
        return {
            "is_stale": age_4h_bars > stale_after_bars,
            "age_4h_bars": round(age_4h_bars, 2),
            "stale_after_4h_bars": stale_after_bars,
            "quality_flags": list(bias.quality_flags),
        }
