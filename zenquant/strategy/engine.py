from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from zenquant.domain.models import AlphaSignal, Candle, PAPattern, SignalType, TradeTick
from zenquant.infra.event_bus import InMemoryJetStream
from zenquant.pa_analyzer import PAAnalyzer


@dataclass(slots=True)
class StrategyEngineConfig:
    trade_window: int = 32
    bar_size: int = 8
    continuous_scale: float = 24.0


@dataclass(slots=True)
class _SymbolState:
    prices: deque[float]
    ticks: list[TradeTick]
    analyzer: PAAnalyzer


class StrategyEngine:
    def __init__(self, bus: InMemoryJetStream, config: StrategyEngineConfig | None = None) -> None:
        self._bus = bus
        self._config = config or StrategyEngineConfig()
        self._states: dict[str, _SymbolState] = {}
        self._bus.subscribe("md.trade.>", self._on_trade)

    def _on_trade(self, _: str, payload: object) -> None:
        if not isinstance(payload, TradeTick):
            return
        state = self._states.get(payload.symbol)
        if state is None:
            state = _SymbolState(
                prices=deque(maxlen=self._config.trade_window),
                ticks=[],
                analyzer=PAAnalyzer(),
            )
            self._states[payload.symbol] = state

        state.prices.append(payload.price)
        state.ticks.append(payload)

        self._emit_continuous_signal(payload, state)
        if len(state.ticks) >= self._config.bar_size:
            self._emit_pa_signal(payload.symbol, state)
            state.ticks.clear()

    def _emit_continuous_signal(self, tick: TradeTick, state: _SymbolState) -> None:
        if len(state.prices) < 2:
            return
        mean_price = sum(state.prices) / len(state.prices)
        if mean_price <= 0:
            return
        raw_momentum = (tick.price - mean_price) / mean_price
        continuous_value = max(-1.0, min(1.0, raw_momentum * self._config.continuous_scale))
        confidence = min(abs(continuous_value), 1.0)
        signal = AlphaSignal(
            factor_id="MOMENTUM_TICK",
            symbol=tick.symbol,
            type=SignalType.CONTINUOUS,
            continuous_value=continuous_value,
            pattern_event=PAPattern.NONE,
            confidence_ic=confidence,
            timestamp_ns=tick.local_timestamp_ns,
        )
        self._bus.publish(f"signal.alpha.{tick.symbol}", signal)

    def _emit_pa_signal(self, symbol: str, state: _SymbolState) -> None:
        candle = _ticks_to_candle(symbol, state.ticks)
        pattern = state.analyzer.update(candle)
        if pattern == PAPattern.NONE:
            return
        signal = AlphaSignal(
            factor_id="PA_PATTERN",
            symbol=symbol,
            type=SignalType.DISCRETE_BOOL,
            continuous_value=0.0,
            pattern_event=pattern,
            confidence_ic=0.9 if pattern == PAPattern.H2_RESUMPTION else 1.0,
            timestamp_ns=candle.timestamp_ns,
        )
        self._bus.publish(f"signal.alpha.{symbol}", signal)


def _ticks_to_candle(symbol: str, ticks: list[TradeTick]) -> Candle:
    prices = [tick.price for tick in ticks]
    volumes = [tick.size for tick in ticks]
    return Candle(
        symbol=symbol,
        timestamp_ns=ticks[-1].local_timestamp_ns,
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=prices[-1],
        volume=sum(volumes),
    )
