from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from zenquant.domain.models import Candle, PAPattern


class MarketContext(Enum):
    BULL_TREND = "bull_trend"
    BEAR_TREND = "bear_trend"
    RANGE = "range"


@dataclass(slots=True)
class PAAnalyzerConfig:
    ema_period: int = 20
    atr_period: int = 14
    micro_channel_len: int = 4
    prior_extreme_tolerance: float = 0.003
    trend_break_atr_multiplier: float = 1.2


class PAAnalyzer:
    def __init__(self, config: PAAnalyzerConfig | None = None) -> None:
        self.config = config or PAAnalyzerConfig()
        self.context = MarketContext.RANGE
        self.micro_channel_active = False

        self._prev_candle: Candle | None = None
        self._ema: float | None = None
        self._prev_ema: float | None = None
        self._atr: float | None = None
        self._true_ranges: deque[float] = deque(maxlen=self.config.atr_period)
        self._recent_highs: deque[float] = deque(maxlen=64)

        self._micro_streak = 0
        self._pullback_started = False
        self._waiting_for_h2 = False
        self._h1_low: float | None = None

    def update(self, candle: Candle) -> PAPattern:
        if self._prev_candle is None:
            self._prime(candle)
            return PAPattern.NONE

        previous_context = self.context
        self._update_ema(candle.close)
        self._update_atr(candle, self._prev_candle)
        self._update_context(candle)
        self._update_micro_channel(candle, self._prev_candle)

        pattern = self._detect_mtr(candle, previous_context)
        if pattern == PAPattern.NONE:
            can_evaluate_pullback = (
                not self.micro_channel_active
                and (
                    self.context == MarketContext.BULL_TREND
                    or previous_context == MarketContext.BULL_TREND
                    or self._pullback_started
                    or self._waiting_for_h2
                )
            )
            if can_evaluate_pullback:
                pattern = self._detect_h1_h2(candle, self._prev_candle)
            elif self.context == MarketContext.BEAR_TREND and previous_context == MarketContext.BEAR_TREND:
                self._reset_pullback_state()
        else:
            self._reset_pullback_state()

        self._recent_highs.append(candle.high)
        self._prev_candle = candle
        return pattern

    def _prime(self, candle: Candle) -> None:
        self._ema = candle.close
        self._prev_ema = candle.close
        self._recent_highs.append(candle.high)
        self._prev_candle = candle

    def _update_ema(self, close: float) -> None:
        alpha = 2.0 / (self.config.ema_period + 1.0)
        if self._ema is None:
            self._ema = close
            self._prev_ema = close
            return
        self._prev_ema = self._ema
        self._ema = (alpha * close) + ((1.0 - alpha) * self._ema)

    def _update_atr(self, candle: Candle, prev: Candle) -> None:
        true_range = max(
            candle.high - candle.low,
            abs(candle.high - prev.close),
            abs(candle.low - prev.close),
        )
        self._true_ranges.append(true_range)
        self._atr = sum(self._true_ranges) / len(self._true_ranges)

    def _update_context(self, candle: Candle) -> None:
        if self._ema is None or self._prev_ema is None:
            self.context = MarketContext.RANGE
            return
        slope = self._ema - self._prev_ema
        if candle.close > self._ema and slope >= 0:
            self.context = MarketContext.BULL_TREND
            return
        if candle.close < self._ema and slope <= 0:
            self.context = MarketContext.BEAR_TREND
            return
        self.context = MarketContext.RANGE

    def _update_micro_channel(self, candle: Candle, prev: Candle) -> None:
        if self.context == MarketContext.BULL_TREND:
            if candle.low > prev.low:
                self._micro_streak += 1
            else:
                self._micro_streak = 0
        elif self.context == MarketContext.BEAR_TREND:
            if candle.high < prev.high:
                self._micro_streak += 1
            else:
                self._micro_streak = 0
        else:
            self._micro_streak = 0
        self.micro_channel_active = self._micro_streak >= self.config.micro_channel_len

    def _detect_h1_h2(self, candle: Candle, prev: Candle) -> PAPattern:
        if candle.close < prev.close:
            self._pullback_started = True

        if self._pullback_started and not self._waiting_for_h2 and candle.high > prev.high:
            self._waiting_for_h2 = True
            self._h1_low = candle.low
            return PAPattern.H1_PULLBACK

        if (
            self._waiting_for_h2
            and self._h1_low is not None
            and candle.low < self._h1_low
            and candle.high > prev.high
        ):
            self._reset_pullback_state()
            return PAPattern.H2_RESUMPTION

        return PAPattern.NONE

    def _detect_mtr(self, candle: Candle, previous_context: MarketContext) -> PAPattern:
        if previous_context != MarketContext.BULL_TREND:
            return PAPattern.NONE
        if self._ema is None or self._atr is None:
            return PAPattern.NONE

        candle_range = max(candle.high - candle.low, 1e-9)
        trend_broken = candle.close < self._ema and candle_range >= (
            self._atr * self.config.trend_break_atr_multiplier
        )
        if not trend_broken:
            return PAPattern.NONE

        prior_peak = max(self._recent_highs) if self._recent_highs else candle.high
        test_prior_extreme = candle.high >= prior_peak * (1.0 - self.config.prior_extreme_tolerance)
        bearish_close_percentage = (candle.close - candle.low) / candle_range

        if test_prior_extreme and bearish_close_percentage < 0.25:
            return PAPattern.MAJOR_TREND_REVERSAL
        return PAPattern.NONE

    def _reset_pullback_state(self) -> None:
        self._pullback_started = False
        self._waiting_for_h2 = False
        self._h1_low = None
