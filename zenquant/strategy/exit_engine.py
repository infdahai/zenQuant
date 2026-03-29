"""Deterministic Phase 6 exit logic extracted from the strategy engine."""

from __future__ import annotations

from dataclasses import dataclass

from zenquant.domain import ExitDecision, MarketState, OpenPositionState, PlaybookSelection


@dataclass(slots=True)
class ExitEngineConfig:
    """Static parameters controlling exit behavior."""

    time_decay_enabled: bool = True
    regime_change_enabled: bool = True
    min_bars_before_trail: int = 1
    transition_risk_exit_threshold: float = 0.80


class ExitEngine:
    """Check exits in fixed priority order for live/replay parity.

    Priority:
      1. Invalidation
      2. Trailing stop
      3. Time decay
      4. Regime change
    """

    def __init__(self, config: ExitEngineConfig | None = None) -> None:
        self._config = config or ExitEngineConfig()

    def check(
        self,
        position: OpenPositionState,
        state: MarketState,
        playbook: PlaybookSelection,
        indicators: dict[str, float],
    ) -> ExitDecision:
        """Return the highest-priority exit decision for the current bar close."""
        decision = self._check_invalidation(position, state, playbook)
        if decision.should_exit():
            return decision

        decision = self._check_trailing_stop(position, state, indicators, playbook)
        if decision.should_exit():
            return decision

        if self._config.time_decay_enabled:
            decision = self._check_time_decay(position, state, playbook, indicators)
            if decision.should_exit():
                return decision

        if self._config.regime_change_enabled:
            decision = self._check_regime_change(position, state, playbook)
            if decision.should_exit():
                return decision

        return self._no_exit(position, cutoff_time_ns=state.cutoff_time_ns)

    def _check_invalidation(
        self,
        position: OpenPositionState,
        state: MarketState,
        playbook: PlaybookSelection,
    ) -> ExitDecision:
        """Exit immediately when the playbook's directional assumption flips."""
        if position.direction > 0 and "BULL" in playbook.playbook_id and state.directional_state == "DOWNTREND":
            return ExitDecision(
                symbol=position.symbol,
                timeframe=position.timeframe,
                cutoff_time_ns=state.cutoff_time_ns,
                exit_type="INVALIDATION",
                exit_urgency="IMMEDIATE",
                target_reduction_pct=1.0,
                stop_price=None,
                reason_code="BULL_INVALIDATED_BY_DOWNTREND",
                reason_detail="Bull playbook invalidated by downtrend state",
                reason_codes=["EXIT_INVALIDATION", "DIRECTIONAL_FLIP"],
            )
        if position.direction < 0 and "BEAR" in playbook.playbook_id and state.directional_state == "UPTREND":
            return ExitDecision(
                symbol=position.symbol,
                timeframe=position.timeframe,
                cutoff_time_ns=state.cutoff_time_ns,
                exit_type="INVALIDATION",
                exit_urgency="IMMEDIATE",
                target_reduction_pct=1.0,
                stop_price=None,
                reason_code="BEAR_INVALIDATED_BY_UPTREND",
                reason_detail="Bear playbook invalidated by uptrend state",
                reason_codes=["EXIT_INVALIDATION", "DIRECTIONAL_FLIP"],
            )
        return self._no_exit(position, cutoff_time_ns=state.cutoff_time_ns, reason_code="NO_INVALIDATION")

    def _check_trailing_stop(
        self,
        position: OpenPositionState,
        state: MarketState,
        indicators: dict[str, float],
        playbook: PlaybookSelection,
    ) -> ExitDecision:
        """Update and check the trailing stop using the active playbook ATR multiple."""
        price = float(indicators.get("price", 0.0) or 0.0)
        atr = float(indicators.get("atr", 0.0) or 0.0)
        if price <= 0 or atr <= 0 or position.bars_held < self._config.min_bars_before_trail:
            return self._no_exit(position, cutoff_time_ns=state.cutoff_time_ns)

        stop_distance = atr * playbook.trailing_stop_atr_mult
        if position.trailing_stop == 0.0:
            if position.direction > 0:
                position.trailing_stop = position.entry_price - stop_distance
            else:
                position.trailing_stop = position.entry_price + stop_distance
            return self._no_exit(position, cutoff_time_ns=state.cutoff_time_ns)

        if position.direction > 0:
            new_stop = price - stop_distance
            if new_stop > position.trailing_stop:
                position.trailing_stop = new_stop
            breached = price <= position.trailing_stop
        else:
            new_stop = price + stop_distance
            if new_stop < position.trailing_stop:
                position.trailing_stop = new_stop
            breached = price >= position.trailing_stop

        if breached:
            return ExitDecision(
                symbol=position.symbol,
                timeframe=position.timeframe,
                cutoff_time_ns=state.cutoff_time_ns,
                exit_type="TRAILING",
                exit_urgency="IMMEDIATE",
                target_reduction_pct=1.0,
                stop_price=position.trailing_stop,
                reason_code="TRAILING_STOP_BREACH",
                reason_detail=f"Price {price:.2f} breached trailing stop {position.trailing_stop:.2f}",
                reason_codes=["EXIT_TRAILING_STOP"],
            )
        return self._no_exit(position, cutoff_time_ns=state.cutoff_time_ns)

    def _check_time_decay(
        self,
        position: OpenPositionState,
        state: MarketState,
        playbook: PlaybookSelection,
        indicators: dict[str, float],
    ) -> ExitDecision:
        """Reduce stale positions that have aged past the playbook horizon and are losing."""
        if playbook.time_decay_bars is None or position.bars_held <= playbook.time_decay_bars:
            return self._no_exit(position, cutoff_time_ns=state.cutoff_time_ns)

        price = float(indicators.get("price", 0.0) or 0.0)
        if price <= 0:
            return self._no_exit(position, cutoff_time_ns=state.cutoff_time_ns)

        unrealized_pnl = (price - position.entry_price) * position.direction * position.current_qty
        if unrealized_pnl < 0:
            return ExitDecision(
                symbol=position.symbol,
                timeframe=position.timeframe,
                cutoff_time_ns=state.cutoff_time_ns,
                exit_type="TIME_DECAY",
                exit_urgency="GRADUAL",
                target_reduction_pct=0.5,
                stop_price=None,
                reason_code="TIME_DECAY_UNPROFITABLE",
                reason_detail=f"Held {position.bars_held} bars without profit",
                reason_codes=["EXIT_TIME_DECAY", "UNPROFITABLE"],
            )
        return self._no_exit(position, cutoff_time_ns=state.cutoff_time_ns)

    def _check_regime_change(
        self,
        position: OpenPositionState,
        state: MarketState,
        playbook: PlaybookSelection,
    ) -> ExitDecision:
        """Respond to softer state deterioration that does not fully invalidate the playbook."""
        # Handoff rule: once transition risk is high enough to switch the
        # playbook into capital-preservation mode, the strategy should stop
        # adding risk but does not automatically flatten existing exposure.
        # ExitEngine only forces a regime-change reduction at the higher
        # ``transition_risk_exit_threshold``.
        if state.quality_state == "UNSAFE":
            return ExitDecision(
                symbol=position.symbol,
                timeframe=position.timeframe,
                cutoff_time_ns=state.cutoff_time_ns,
                exit_type="REGIME_CHANGE",
                exit_urgency="IMMEDIATE",
                target_reduction_pct=1.0,
                stop_price=None,
                reason_code="QUALITY_UNSAFE",
                reason_detail="Data quality unsafe; flatten position",
                reason_codes=["EXIT_REGIME_CHANGE", "QUALITY_UNSAFE"],
            )
        if state.volatility_state == "DISTORTED":
            return ExitDecision(
                symbol=position.symbol,
                timeframe=position.timeframe,
                cutoff_time_ns=state.cutoff_time_ns,
                exit_type="REGIME_CHANGE",
                exit_urgency="GRADUAL",
                target_reduction_pct=0.5,
                stop_price=None,
                reason_code="VOLATILITY_DISTORTED",
                reason_detail="Volatility regime distorted; de-risk position",
                reason_codes=["EXIT_REGIME_CHANGE", "VOLATILITY_STRESS"],
            )
        if (
            playbook.playbook_id != "CAPITAL_PRESERVATION"
            and state.transition_risk >= self._config.transition_risk_exit_threshold
        ):
            return ExitDecision(
                symbol=position.symbol,
                timeframe=position.timeframe,
                cutoff_time_ns=state.cutoff_time_ns,
                exit_type="REGIME_CHANGE",
                exit_urgency="GRADUAL",
                target_reduction_pct=0.5,
                stop_price=None,
                reason_code="STATE_TRANSITION_RISK",
                reason_detail="State transition risk is elevated; de-risk position",
                reason_codes=["EXIT_REGIME_CHANGE", "TRANSITION_RISK"],
            )
        return self._no_exit(position, cutoff_time_ns=state.cutoff_time_ns)

    def _no_exit(
        self,
        position: OpenPositionState,
        *,
        cutoff_time_ns: int | None = None,
        reason_code: str = "NO_EXIT",
    ) -> ExitDecision:
        """Return a stable no-op exit decision for auditability."""
        return ExitDecision(
            symbol=position.symbol,
            timeframe=position.timeframe,
            cutoff_time_ns=position.entry_bar_ns if cutoff_time_ns is None else cutoff_time_ns,
            exit_type="NONE",
            exit_urgency="NONE",
            target_reduction_pct=0.0,
            stop_price=None,
            reason_code=reason_code,
            reason_detail="No exit condition met",
            reason_codes=[],
        )
