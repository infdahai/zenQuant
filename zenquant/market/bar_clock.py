from __future__ import annotations

from dataclasses import dataclass

from zenquant.domain import BarCloseEvent, Candle, Timeframe, TradeTick
from zenquant.infra import InMemoryJetStream


@dataclass(slots=True)
class BarClockConfig:
    timeframes: tuple[Timeframe, ...] = (Timeframe.TF_4H, Timeframe.TF_1D)


@dataclass(slots=True)
class _BarState:
    bar_start_ns: int
    bar_end_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int


class BarClockService:
    def __init__(self, bus: InMemoryJetStream, config: BarClockConfig | None = None) -> None:
        self._bus = bus
        self._config = config or BarClockConfig()
        self._states: dict[tuple[str, Timeframe], _BarState] = {}
        self._bus.subscribe("md.trade.>", self._on_trade)

    def _on_trade(self, _: str, payload: object) -> None:
        if not isinstance(payload, TradeTick):
            return
        for timeframe in self._config.timeframes:
            self._update_bar(payload, timeframe)

    def _update_bar(self, tick: TradeTick, timeframe: Timeframe) -> None:
        interval_ns = timeframe.seconds * 1_000_000_000
        bar_start_ns = (tick.meta.event_time_ns // interval_ns) * interval_ns
        bar_end_ns = bar_start_ns + interval_ns
        key = (tick.meta.symbol, timeframe)

        state = self._states.get(key)
        if state is None:
            self._states[key] = _BarState(
                bar_start_ns=bar_start_ns,
                bar_end_ns=bar_end_ns,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=tick.size,
                trade_count=1,
            )
            return

        if bar_start_ns < state.bar_start_ns:
            return

        if bar_start_ns > state.bar_start_ns:
            self._emit_closed_bar(symbol=tick.meta.symbol, timeframe=timeframe, state=state)
            self._states[key] = _BarState(
                bar_start_ns=bar_start_ns,
                bar_end_ns=bar_end_ns,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=tick.size,
                trade_count=1,
            )
            return

        state.high = max(state.high, tick.price)
        state.low = min(state.low, tick.price)
        state.close = tick.price
        state.volume += tick.size
        state.trade_count += 1

    def _emit_closed_bar(self, symbol: str, timeframe: Timeframe, state: _BarState) -> None:
        self._bus.publish(
            f"clock.bar_close.{timeframe.value}",
            BarCloseEvent(
                timeframe=timeframe,
                symbol=symbol,
                bar_start_ns=state.bar_start_ns,
                bar_end_ns=state.bar_end_ns,
            ),
        )
        self._bus.publish(
            f"md.candle.{timeframe.value}.{symbol}",
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                bar_start_ns=state.bar_start_ns,
                bar_end_ns=state.bar_end_ns,
                open=state.open,
                high=state.high,
                low=state.low,
                close=state.close,
                volume=state.volume,
                trade_count=state.trade_count,
            ),
        )
