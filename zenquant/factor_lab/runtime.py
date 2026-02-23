from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from zenquant.domain.models import AlphaSignal, PAPattern, SignalType, TradeTick
from zenquant.factor_lab.engine import FactorLabEngine, FactorLabEngineConfig
from zenquant.factor_lab.models import FactorObservation
from zenquant.factor_lab.stats import stddev
from zenquant.infra.event_bus import InMemoryJetStream


@dataclass(slots=True)
class FactorLabRuntimeConfig:
    horizon_ticks: int = 3
    return_window: int = 128


@dataclass(slots=True)
class _PendingSignal:
    symbol: str
    factor_id: str
    factor_value: float
    timestamp_ns: int
    price_at_signal: float
    ticks_remaining: int


class FactorLabRuntime:
    def __init__(
        self,
        bus: InMemoryJetStream,
        engine: FactorLabEngine | None = None,
        config: FactorLabRuntimeConfig | None = None,
    ) -> None:
        self._bus = bus
        self._config = config or FactorLabRuntimeConfig()
        self._engine = engine or FactorLabEngine(FactorLabEngineConfig(min_samples=24))
        self._last_price: dict[str, float] = {}
        self._pending: dict[str, deque[_PendingSignal]] = defaultdict(deque)
        self._recent_returns: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.return_window)
        )

        self._bus.subscribe("md.trade.>", self._on_trade)
        self._bus.subscribe("signal.alpha.>", self._on_signal)

    def _on_signal(self, _: str, payload: object) -> None:
        if not isinstance(payload, AlphaSignal):
            return
        price = self._last_price.get(payload.symbol)
        if price is None:
            return
        factor_value = _factor_value_from_signal(payload)
        if factor_value is None:
            return
        self._pending[payload.symbol].append(
            _PendingSignal(
                symbol=payload.symbol,
                factor_id=payload.factor_id,
                factor_value=factor_value,
                timestamp_ns=payload.timestamp_ns,
                price_at_signal=price,
                ticks_remaining=self._config.horizon_ticks,
            )
        )

    def _on_trade(self, _: str, payload: object) -> None:
        if not isinstance(payload, TradeTick):
            return
        self._last_price[payload.symbol] = payload.price
        symbol_pending = self._pending[payload.symbol]
        if not symbol_pending:
            return

        ready: list[_PendingSignal] = []
        for _ in range(len(symbol_pending)):
            entry = symbol_pending.popleft()
            entry.ticks_remaining -= 1
            if entry.ticks_remaining <= 0:
                ready.append(entry)
            else:
                symbol_pending.append(entry)

        for entry in ready:
            if entry.price_at_signal <= 0:
                continue
            forward_return = (payload.price - entry.price_at_signal) / entry.price_at_signal
            self._recent_returns[payload.symbol].append(forward_return)
            sigma = stddev(list(self._recent_returns[payload.symbol]))
            observation = FactorObservation(
                symbol=entry.symbol,
                factor_id=entry.factor_id,
                timestamp_ns=payload.local_timestamp_ns,
                factor_value=entry.factor_value,
                forward_return=forward_return,
            )
            score, weight = self._engine.evaluate(observation, portfolio_sigma=sigma)
            if score is not None:
                self._bus.publish(f"factor.score.{score.symbol}.{score.factor_id}", score)
            if weight is not None:
                self._bus.publish(f"factor.weight.{weight.symbol}.{weight.factor_id}", weight)


def _factor_value_from_signal(signal: AlphaSignal) -> float | None:
    if signal.type == SignalType.CONTINUOUS:
        return signal.continuous_value
    if signal.type == SignalType.DISCRETE_BOOL:
        if signal.pattern_event == PAPattern.H2_RESUMPTION:
            return 1.0
        if signal.pattern_event == PAPattern.MAJOR_TREND_REVERSAL:
            return -1.0
    return None

