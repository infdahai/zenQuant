"""Deterministic Phase 4 playbook selection based on ``MarketState``."""

from __future__ import annotations

from dataclasses import dataclass

from zenquant.domain import MarketState, PlaybookSelection


@dataclass(slots=True)
class PlaybookEngineConfig:
    """Static thresholds and defaults for state-to-playbook routing."""

    preserve_capital_transition_risk: float = 0.60
    preserve_capital_min_overall_confidence: float = 0.35
    trade_directional_confidence_floor: float = 0.5
    bull_max_leverage: float = 2.50
    bear_max_leverage: float = 2.25
    range_max_leverage: float = 1.5
    preserve_capital_max_leverage: float = 0.35
    bull_trailing_stop_atr_mult: float = 2.5
    bear_trailing_stop_atr_mult: float = 2.5
    range_trailing_stop_atr_mult: float = 2.5
    preserve_capital_trailing_stop_atr_mult: float = 1.5
    range_time_decay_bars: int = 3
    preserve_capital_time_decay_bars: int = 1


class PlaybookEngine:
    """Select one of the four core playbooks from the current market state."""

    def __init__(self, config: PlaybookEngineConfig | None = None) -> None:
        self._config = config or PlaybookEngineConfig()

    def select(self, state: MarketState) -> PlaybookSelection:
        """Return the active playbook for this state snapshot."""
        if self._should_preserve_capital(state):
            return self._capital_preservation(state)
        if self._should_trade_bull(state):
            return self._bull_continuation(state)
        if self._should_trade_bear(state):
            return self._bear_defense(state)
        return self._range_light(state)

    def _should_preserve_capital(self, state: MarketState) -> bool:
        return (
            state.volatility_state == "DISTORTED"
            or state.quality_state in {"DEGRADED", "UNSAFE"}
            or state.transition_risk >= self._config.preserve_capital_transition_risk
            or state.overall_confidence() < self._config.preserve_capital_min_overall_confidence
        )

    def _should_trade_bull(self, state: MarketState) -> bool:
        return (
            state.directional_state == "UPTREND"
            and state.quality_state in {"TRADABLE", "NOISY"}
            and state.volatility_state != "DISTORTED"
            and state.positioning_state != "CROWDED_LONG"
            and state.directional_confidence >= self._config.trade_directional_confidence_floor
        )

    def _should_trade_bear(self, state: MarketState) -> bool:
        return (
            state.directional_state == "DOWNTREND"
            and state.quality_state in {"TRADABLE", "NOISY"}
            and state.volatility_state != "DISTORTED"
            and state.positioning_state != "CROWDED_SHORT"
            and state.directional_confidence >= self._config.trade_directional_confidence_floor
        )

    def _bull_continuation(self, state: MarketState) -> PlaybookSelection:
        confidence = min(
            1.0,
            state.directional_confidence * 0.6 + state.volatility_confidence * 0.2 + state.positioning_confidence * 0.2,
        )
        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id="BULL_CONTINUATION",
            playbook_confidence=round(confidence, 4),
            allowed_directions=[1.0, 0.0],
            max_leverage=self._config.bull_max_leverage,
            trailing_stop_atr_mult=self._config.bull_trailing_stop_atr_mult,
            allow_pyramid=True,
            max_pyramid_count=2,
            time_decay_bars=None,
            selection_reason="Uptrend state with acceptable volatility and crowding",
            reason_codes=[
                "PLAYBOOK_BULL_CONTINUATION",
                "PLAYBOOK_ALLOW_LONG",
            ],
        )

    def _bear_defense(self, state: MarketState) -> PlaybookSelection:
        confidence = min(
            1.0,
            state.directional_confidence * 0.6 + state.volatility_confidence * 0.2 + state.positioning_confidence * 0.2,
        )
        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id="BEAR_DEFENSE",
            playbook_confidence=round(confidence, 4),
            allowed_directions=[-1.0, 0.0],
            max_leverage=self._config.bear_max_leverage,
            trailing_stop_atr_mult=self._config.bear_trailing_stop_atr_mult,
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=None,
            selection_reason="Downtrend state with tactical short bias",
            reason_codes=[
                "PLAYBOOK_BEAR_DEFENSE",
                "PLAYBOOK_ALLOW_SHORT",
            ],
        )

    def _range_light(self, state: MarketState) -> PlaybookSelection:
        confidence = min(
            1.0,
            (1.0 - state.directional_confidence) * 0.5
            + state.volatility_confidence * 0.25
            + state.quality_confidence * 0.25,
        )
        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id="RANGE_LIGHT",
            playbook_confidence=round(confidence, 4),
            allowed_directions=[1.0, -1.0, 0.0],
            max_leverage=self._config.range_max_leverage,
            trailing_stop_atr_mult=self._config.range_trailing_stop_atr_mult,
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=self._config.range_time_decay_bars,
            selection_reason="Neutral or transition state with lighter mean-reversion posture",
            reason_codes=[
                "PLAYBOOK_RANGE_LIGHT",
                "PLAYBOOK_ALLOW_BOTH",
            ],
        )

    def _capital_preservation(self, state: MarketState) -> PlaybookSelection:
        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id="CAPITAL_PRESERVATION",
            playbook_confidence=0.9,
            allowed_directions=[0.0],
            max_leverage=self._config.preserve_capital_max_leverage,
            trailing_stop_atr_mult=self._config.preserve_capital_trailing_stop_atr_mult,
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=self._config.preserve_capital_time_decay_bars,
            selection_reason=(
                f"Capital preservation because quality={state.quality_state}, "
                f"volatility={state.volatility_state}, transition_risk={state.transition_risk:.2f}"
            ),
            reason_codes=[
                "PLAYBOOK_CAPITAL_PRESERVATION",
                "PLAYBOOK_REDUCE_ONLY",
            ],
        )
