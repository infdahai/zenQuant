"""Regime-aware strategy engine for the flagship BTC-first trading path."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from zenquant.domain import (
    DirectionalBias,
    ExitDecision,
    ExecutionReport,
    IndicatorPack,
    IntentReason,
    MarketState,
    OpenPositionState,
    PlaybookSelection,
    RiskSnapshot,
    SizingDecision,
    TargetPosition,
    Timeframe,
)
from zenquant.infra import EventBus
from .exit_engine import ExitEngine, ExitEngineConfig
from .playbook_engine import PlaybookEngine
from .sizing_engine import SizingEngine, SizingEngineConfig


@dataclass(slots=True)
class _IndicatorValues:
    """Extracted indicator values for cleaner code."""

    realized_vol: float
    cvd_slope: float
    funding_oi_z: float
    ob_liq_z: float
    zen_pulse: float
    price: float
    atr: float
    structure_state: str
    momentum_roc_z: float


@dataclass(slots=True)
class ARCStrategyConfig:
    """Sizing and regime settings for the ARC flagship strategy."""

    portfolio: str = "arc"
    account_equity: float = 100_000.0
    max_leverage: float = 3.0
    # NOTE: ANNUALIZED vol targets — must match indicator engine units.
    # realized_vol is scaled by sqrt(365 * bars_per_day).
    target_vol_4h: float = 0.60
    target_vol_1d: float = 0.50
    min_trade_qty: float = 0.0001
    execution_timeframe: Timeframe = Timeframe.TF_4H
    bias_max_age_4h_bars: int = 8
    # Phase 2: trailing stop config
    trailing_stop_atr_mult: float = 2.5  # ATR multiplier for trailing stop
    min_bars_before_trail: int = 1  # minimum bars held before activating trail


class ARCStrategyEngine:
    """Convert indicator packs into target positions and reasons.

    The engine keeps to the repository contract: strategy emits target intent,
    not exchange orders. Risk and execution remain separate downstream stages.
    """

    def __init__(
        self, bus: EventBus, config: ARCStrategyConfig | None = None
    ) -> None:
        self._bus = bus
        self._config = config or ARCStrategyConfig()
        self._equity = self._config.account_equity
        self._latest_packs: dict[tuple[str, Timeframe], IndicatorPack] = {}
        self._market_states: dict[tuple[str, Timeframe], MarketState] = {}
        self._playbooks: dict[tuple[str, Timeframe], PlaybookSelection] = {}
        self._playbook_engine = PlaybookEngine()
        self._sizing_engine = SizingEngine(
            SizingEngineConfig(
                target_vol_4h=self._config.target_vol_4h,
                target_vol_1d=self._config.target_vol_1d,
            )
        )
        self._exit_engine = ExitEngine(
            ExitEngineConfig(min_bars_before_trail=self._config.min_bars_before_trail)
        )

        # Phase 2: multi-timeframe coordination
        self._directional_bias: dict[str, DirectionalBias] = {}  # symbol -> 1d bias

        # Phase 2: position tracking for trailing stops
        self._positions: dict[tuple[str, Timeframe], OpenPositionState] = {}

        self._bus.subscribe("indicator.>", self._on_indicator)
        self._bus.subscribe("state.market.>", self._on_market_state)
        self._bus.subscribe("risk.snapshot", self._on_risk_snapshot)
        self._bus.subscribe("exec.order.report", self._on_execution_report)

    def _on_risk_snapshot(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskSnapshot):
            self._equity = max(0.0, payload.equity)

    def _on_execution_report(self, _: str, payload: object) -> None:
        """Track filled positions for trailing stop logic."""
        if not isinstance(payload, ExecutionReport):
            return
        if payload.status != "FILLED":
            return

        pos_key = (payload.symbol, self._config.execution_timeframe)
        current_pos = self._positions.get(pos_key)
        filled_delta = payload.side * payload.filled_qty
        if abs(filled_delta) < self._config.min_trade_qty:
            return

        if current_pos is None:
            self._positions[pos_key] = self._new_position_state(
                symbol=payload.symbol,
                timeframe=self._config.execution_timeframe,
                signed_qty=filled_delta,
                avg_price=payload.avg_price,
                transaction_time_ns=payload.transaction_time_ns,
            )
            self._publish_position_state(payload.symbol, self._config.execution_timeframe)
            return

        current_signed_qty = current_pos.direction * current_pos.current_qty
        new_signed_qty = current_signed_qty + filled_delta
        if abs(new_signed_qty) < self._config.min_trade_qty:
            del self._positions[pos_key]
            self._publish_position_state(payload.symbol, self._config.execution_timeframe)
            return

        if current_signed_qty * new_signed_qty < 0:
            self._positions[pos_key] = self._new_position_state(
                symbol=payload.symbol,
                timeframe=self._config.execution_timeframe,
                signed_qty=new_signed_qty,
                avg_price=payload.avg_price,
                transaction_time_ns=payload.transaction_time_ns,
            )
            self._publish_position_state(payload.symbol, self._config.execution_timeframe)
            return

        if abs(new_signed_qty) > abs(current_signed_qty):
            current_pos.pyramid_count += 1
            previous_qty = abs(current_signed_qty)
            added_qty = abs(filled_delta)
            current_pos.entry_price = (
                (current_pos.entry_price * previous_qty) + (payload.avg_price * added_qty)
            ) / max(previous_qty + added_qty, 1e-9)
        current_pos.direction = 1.0 if new_signed_qty > 0 else -1.0
        current_pos.current_qty = abs(new_signed_qty)
        self._publish_position_state(payload.symbol, self._config.execution_timeframe)

    def _on_indicator(self, _: str, payload: object) -> None:
        """Cache the latest pack so state events can build targets deterministically."""
        if not isinstance(payload, IndicatorPack):
            return
        self._latest_packs[(payload.symbol, payload.timeframe)] = payload

    def _on_market_state(self, _: str, payload: object) -> None:
        if not isinstance(payload, MarketState):
            return
        self.prime_state(payload)

    def prime_indicator(self, pack: IndicatorPack) -> None:
        """Prime startup indicator context without creating state dependencies."""
        self._latest_packs[(pack.symbol, pack.timeframe)] = pack

    def prime_state(self, state: MarketState) -> None:
        """Prime live startup state from an already-classified market state."""
        self._market_states[(state.symbol, state.timeframe)] = state
        playbook = self._playbook_engine.select(state)
        self._playbooks[(state.symbol, state.timeframe)] = playbook
        self._bus.publish(
            f"strategy.playbook.{state.timeframe.value}.{state.symbol}",
            playbook,
        )
        if state.timeframe == Timeframe.TF_1D:
            self._update_directional_bias(state)
            return
        pack = self._latest_packs.get((state.symbol, state.timeframe))
        if pack is None:
            return
        target = self._build_target(pack, state, playbook)
        reason = self._build_reason(target)
        self._bus.publish(
            f"strategy.regime.{self._config.portfolio}",
            {"symbol": target.symbol, "regime": target.regime},
        )
        self._bus.publish(
            f"intent.target_position.{self._config.portfolio}.{target.symbol}", target
        )
        self._bus.publish(f"intent.reason.{self._config.portfolio}", reason)

    def _update_directional_bias(self, state: MarketState) -> None:
        """Compute and store 1d directional bias for multi-timeframe coordination."""
        bias = DirectionalBias(
            symbol=state.symbol,
            cutoff_time_ns=state.cutoff_time_ns,
            direction=state.direction_signal,
            regime=state.regime,
            confidence=state.regime_confidence,
            quality_flags=list(state.quality_flags),
        )
        self._directional_bias[state.symbol] = bias
        self._bus.publish(f"strategy.bias.1d.{state.symbol}", bias)

    def _build_target(
        self,
        pack: IndicatorPack,
        state: MarketState,
        playbook: PlaybookSelection,
    ) -> TargetPosition:
        """Translate one indicator pack into a target position for its bar close."""
        vals = self._extract_values(pack.values)
        regime = state.regime
        regime_confidence = state.regime_confidence

        pos_key = (pack.symbol, pack.timeframe)
        position = self._positions.get(pos_key)
        if position is not None:
            position.bars_held += 1
            exit_decision = self._exit_engine.check(
                position,
                state,
                playbook,
                indicators={"price": vals.price, "atr": vals.atr},
            )
            self._publish_exit(exit_decision)
            if exit_decision.should_exit():
                return self._build_exit_target(
                    pack=pack,
                    position=position,
                    state=state,
                    exit_decision=exit_decision,
                    price=vals.price,
                )

        # Compute base direction from indicators
        direction = self._direction(
            regime=regime,
            cvd_slope=vals.cvd_slope,
            ob_liq_z=vals.ob_liq_z,
            structure_state=vals.structure_state,
            momentum_roc_z=vals.momentum_roc_z,
        )

        # Phase 2: Multi-timeframe coordination for 4h
        reason_codes = [f"REGIME_{regime}", *playbook.reason_codes]
        if pack.timeframe == self._config.execution_timeframe:
            bias, bias_reason = self._active_directional_bias(
                pack.symbol, pack.cutoff_time_ns
            )
            if bias_reason:
                reason_codes.append(bias_reason)
            if bias is not None and bias.direction != 0.0:
                # Block 4h direction if it conflicts with 1d bias
                if direction != 0.0 and direction * bias.direction < 0:
                    # Opposite directions - block
                    direction = 0.0
                    reason_codes.append("BIAS_DIRECTION_BLOCK")

        if playbook.is_reduce_only() and direction != 0.0:
            if direction != 0.0:
                reason_codes.append("PLAYBOOK_DIRECTION_BLOCK")
            direction = 0.0

        sizing = self._sizing_engine.compute(
            state=state,
            playbook=playbook,
            equity=self._equity,
            indicators={
                "realized_vol": vals.realized_vol,
                "funding_oi_z": vals.funding_oi_z,
            },
        )
        self._publish_sizing(sizing)

        target_leverage = sizing.final_leverage
        reduce_only = playbook.is_reduce_only()
        if regime == "STRESS":
            target_leverage = min(target_leverage, playbook.max_leverage)
            reduce_only = True

        target_notional = self._equity * target_leverage * direction
        target_qty = target_notional / vals.price
        if abs(target_qty) < self._config.min_trade_qty:
            target_qty = 0.0

        if direction > 0:
            reason_codes.append("TREND_LONG")
        elif direction < 0:
            reason_codes.append("TREND_SHORT")
        else:
            reason_codes.append("NO_DIRECTION")
        reason_codes.extend(sizing.reason_codes)

        return TargetPosition(
            intent_id=self._intent_id(pack),
            portfolio=self._config.portfolio,
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            cutoff_time_ns=pack.cutoff_time_ns,
            target_qty=round(target_qty, 6),
            target_leverage=round(target_leverage, 4),
            reduce_only=reduce_only,
            regime=regime,
            regime_confidence=round(regime_confidence, 4),
            reason_codes=reason_codes,
        )

    def _active_directional_bias(
        self, symbol: str, cutoff_time_ns: int
    ) -> tuple[DirectionalBias | None, str]:
        """Return the usable 1d bias for a 4h decision, or the reason it is unusable."""
        bias = self._directional_bias.get(symbol)
        if bias is None:
            return None, ""
        if any(flag != "OK" for flag in bias.quality_flags):
            return None, "BIAS_QUALITY_DEGRADED"
        if cutoff_time_ns < bias.cutoff_time_ns:
            return None, "BIAS_FUTURE"

        max_age_ns = (
            self._config.bias_max_age_4h_bars
            * Timeframe.TF_4H.seconds
            * 1_000_000_000
        )
        if (cutoff_time_ns - bias.cutoff_time_ns) > max_age_ns:
            return None, "BIAS_STALE"
        return bias, ""

    def _publish_position_state(self, symbol: str, timeframe: Timeframe) -> None:
        """Expose open-position state for the dashboard and trailing-stop logic."""
        self._bus.publish(
            f"strategy.position_state.{timeframe.value}.{symbol}",
            self._positions.get((symbol, timeframe)),
        )

    def _publish_sizing(self, sizing: SizingDecision) -> None:
        """Expose the latest sizing breakdown for audit and dashboard display."""
        self._bus.publish(
            f"strategy.sizing.{sizing.timeframe.value}.{sizing.symbol}",
            sizing,
        )

    def _publish_exit(self, exit_decision: ExitDecision) -> None:
        """Expose the latest exit decision for one symbol/timeframe."""
        self._bus.publish(
            f"strategy.exit.{exit_decision.timeframe.value}.{exit_decision.symbol}",
            exit_decision,
        )

    def _build_exit_target(
        self,
        *,
        pack: IndicatorPack,
        position: OpenPositionState,
        state: MarketState,
        exit_decision: ExitDecision,
        price: float,
    ) -> TargetPosition:
        """Translate one exit decision into a reduce-only target position."""
        remaining_qty = 0.0
        if not exit_decision.is_immediate():
            remaining_qty = position.current_qty * max(
                0.0, 1.0 - exit_decision.target_reduction_pct
            )
        signed_qty = position.direction * remaining_qty
        if abs(signed_qty) < self._config.min_trade_qty:
            signed_qty = 0.0
        target_leverage = 0.0
        if abs(signed_qty) >= self._config.min_trade_qty and price > 0 and self._equity > 0:
            target_leverage = abs(signed_qty * price) / self._equity
        return TargetPosition(
            intent_id=self._intent_id(pack),
            portfolio=self._config.portfolio,
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            cutoff_time_ns=pack.cutoff_time_ns,
            target_qty=round(signed_qty, 6),
            target_leverage=round(target_leverage, 4),
            reduce_only=True,
            regime=state.regime,
            regime_confidence=round(state.regime_confidence, 4),
            reason_codes=[f"REGIME_{state.regime}", *exit_decision.reason_codes],
        )

    @staticmethod
    def _new_position_state(
        symbol: str,
        timeframe: Timeframe,
        signed_qty: float,
        avg_price: float,
        transaction_time_ns: int,
    ) -> OpenPositionState:
        return OpenPositionState(
            symbol=symbol,
            timeframe=timeframe,
            direction=1.0 if signed_qty > 0 else -1.0,
            entry_price=avg_price,
            entry_bar_ns=transaction_time_ns,
            base_qty=abs(signed_qty),
            current_qty=abs(signed_qty),
            trailing_stop=0.0,
            pyramid_count=0,
            bars_held=0,
        )

    @staticmethod
    def _extract_values(values: dict[str, Any]) -> _IndicatorValues:
        """Extract and normalize indicator values from pack."""
        return _IndicatorValues(
            realized_vol=float(values.get("realized_vol", 0.0) or 0.0),
            cvd_slope=float(values.get("cvd_slope", 0.0) or 0.0),
            funding_oi_z=float(values.get("funding_oi_z", 0.0) or 0.0),
            ob_liq_z=float(values.get("ob_liq_z", 0.0) or 0.0),
            zen_pulse=float(values.get("zen_pulse", 50.0) or 50.0),
            price=max(1e-9, float(values.get("last_price", 0.0) or 0.0)),
            atr=float(values.get("atr", 0.0) or 0.0),
            # Support both field names for compatibility
            structure_state=str(
                values.get("structure_state")
                or values.get("swing_structure")
                or "NEUTRAL"
            ),
            momentum_roc_z=float(values.get("momentum_roc_z", 0.0) or 0.0),
        )

    def _intent_id(self, pack: IndicatorPack) -> str:
        """Create a stable, content-addressed intent id for auditability."""
        name = (
            f"{self._config.portfolio}:{pack.symbol}:{pack.timeframe.value}:"
            f"{pack.cutoff_time_ns}:{pack.indicator_version}:{pack.formula_hash}"
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, name))

    @staticmethod
    def _classify_regime(
        last_regime: str,
        quality_flags: list[str],
        realized_vol: float,
        cvd_slope: float,
        zen_pulse: float,
    ) -> tuple[str, float]:
        """Classify bar-close context into TREND, RANGE, or STRESS."""
        degraded = any(
            flag in {"BOOK_STALE", "VENUE_DOWN", "DATA_STALE", "DATA_FUTURE"}
            for flag in quality_flags
        )
        if degraded or realized_vol > 2.8:
            return "STRESS", 0.9

        trend_strength = min(1.0, abs(cvd_slope) * 10.0)
        pulse_strength = min(1.0, max(0.0, (zen_pulse - 50.0) / 50.0))

        if last_regime == "TREND":
            trend_gate = trend_strength > 0.12 and zen_pulse >= 50.0
        else:
            trend_gate = trend_strength > 0.22 and zen_pulse >= 58.0

        if trend_gate:
            confidence = min(
                1.0, 0.45 + (trend_strength * 0.4) + (pulse_strength * 0.15)
            )
            return "TREND", confidence

        confidence = max(0.35, 1.0 - min(0.6, abs(cvd_slope) * 5.0))
        return "RANGE", confidence

    @staticmethod
    def _direction(
        regime: str,
        cvd_slope: float,
        ob_liq_z: float,
        structure_state: str,
        momentum_roc_z: float,
    ) -> float:
        """Phase 2 direction logic preserved while Phase 4 playbooks mature."""
        if regime == "STRESS":
            return 0.0

        if regime == "TREND":
            # Use structure + momentum for stronger confirmation
            if structure_state == "UPTREND" and momentum_roc_z > 0.3:
                return 1.0
            if structure_state == "DOWNTREND" and momentum_roc_z < -0.3:
                return -1.0
            # Fallback to CVD
            if cvd_slope > 0:
                return 1.0
            if cvd_slope < 0:
                return -1.0
            return 0.0

        # RANGE regime: mean reversion on liquidity imbalance
        if abs(cvd_slope) < 0.03 and abs(ob_liq_z) > 1.2:
            return -1.0 if ob_liq_z > 0 else 1.0
        return 0.0

    @staticmethod
    def _build_reason(target: TargetPosition) -> IntentReason:
        summary = (
            f"intent={target.intent_id[:8]} regime={target.regime} conf={target.regime_confidence:.2f} "
            f"lev={target.target_leverage:.2f} qty={target.target_qty:.6f}"
        )
        return IntentReason(
            portfolio=target.portfolio,
            symbol=target.symbol,
            cutoff_time_ns=target.cutoff_time_ns,
            summary=summary,
            reason_codes=target.reason_codes,
        )
