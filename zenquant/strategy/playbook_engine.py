"""Deterministic Phase 4 playbook selection based on ``MarketState``."""

from __future__ import annotations

from zenquant.domain import MarketState, PlaybookSelection


class PlaybookEngine:
    """Select one of the four core playbooks from the current market state."""

    def select(self, state: MarketState) -> PlaybookSelection:
        """Return the active playbook for this state snapshot."""
        if self._should_preserve_capital(state):
            return self._capital_preservation(state)
        if self._should_trade_bull(state):
            return self._bull_continuation(state)
        if self._should_trade_bear(state):
            return self._bear_defense(state)
        return self._range_light(state)

    @staticmethod
    def _should_preserve_capital(state: MarketState) -> bool:
        return (
            state.volatility_state == "DISTORTED"
            or state.quality_state in {"DEGRADED", "UNSAFE"}
            or state.transition_risk >= 0.65
            or state.overall_confidence() < 0.35
        )

    @staticmethod
    def _should_trade_bull(state: MarketState) -> bool:
        return (
            state.directional_state == "UPTREND"
            and state.quality_state in {"TRADABLE", "NOISY"}
            and state.volatility_state != "DISTORTED"
            and state.positioning_state != "CROWDED_LONG"
            and state.directional_confidence >= 0.5
        )

    @staticmethod
    def _should_trade_bear(state: MarketState) -> bool:
        return (
            state.directional_state == "DOWNTREND"
            and state.quality_state in {"TRADABLE", "NOISY"}
            and state.volatility_state != "DISTORTED"
            and state.positioning_state != "CROWDED_SHORT"
            and state.directional_confidence >= 0.5
        )

    @staticmethod
    def _bull_continuation(state: MarketState) -> PlaybookSelection:
        confidence = min(
            1.0,
            state.directional_confidence * 0.6
            + state.volatility_confidence * 0.2
            + state.positioning_confidence * 0.2,
        )
        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id="BULL_CONTINUATION",
            playbook_confidence=round(confidence, 4),
            allowed_directions=[1.0, 0.0],
            max_leverage=3.0,
            trailing_stop_atr_mult=2.5,
            allow_pyramid=True,
            max_pyramid_count=2,
            time_decay_bars=None,
            selection_reason="Uptrend state with acceptable volatility and crowding",
            reason_codes=[
                "PLAYBOOK_BULL_CONTINUATION",
                "PLAYBOOK_ALLOW_LONG",
            ],
        )

    @staticmethod
    def _bear_defense(state: MarketState) -> PlaybookSelection:
        confidence = min(
            1.0,
            state.directional_confidence * 0.6
            + state.volatility_confidence * 0.2
            + state.positioning_confidence * 0.2,
        )
        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id="BEAR_DEFENSE",
            playbook_confidence=round(confidence, 4),
            allowed_directions=[-1.0, 0.0],
            max_leverage=3.0,
            trailing_stop_atr_mult=2.5,
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=None,
            selection_reason="Downtrend state with tactical short bias",
            reason_codes=[
                "PLAYBOOK_BEAR_DEFENSE",
                "PLAYBOOK_ALLOW_SHORT",
            ],
        )

    @staticmethod
    def _range_light(state: MarketState) -> PlaybookSelection:
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
            max_leverage=3.0,
            trailing_stop_atr_mult=2.5,
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=3,
            selection_reason="Neutral or transition state with lighter mean-reversion posture",
            reason_codes=[
                "PLAYBOOK_RANGE_LIGHT",
                "PLAYBOOK_ALLOW_BOTH",
            ],
        )

    @staticmethod
    def _capital_preservation(state: MarketState) -> PlaybookSelection:
        return PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id="CAPITAL_PRESERVATION",
            playbook_confidence=0.9,
            allowed_directions=[0.0],
            max_leverage=0.35,
            trailing_stop_atr_mult=1.5,
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=1,
            selection_reason=(
                f"Capital preservation because quality={state.quality_state}, "
                f"volatility={state.volatility_state}, transition_risk={state.transition_risk:.2f}"
            ),
            reason_codes=[
                "PLAYBOOK_CAPITAL_PRESERVATION",
                "PLAYBOOK_REDUCE_ONLY",
            ],
        )
