"""Regime-aware strategy engine for the flagship BTC-first trading path."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from math import ceil
from typing import Any

from zenquant.domain import (
    ArchitectureAlphaSnapshot,
    DirectionalBias,
    ExecutionReport,
    ExitDecision,
    IndicatorPack,
    IntentReason,
    MarketState,
    OpenPositionState,
    PlaybookSelection,
    RiskOverlayDecision,
    RiskSnapshot,
    SizingDecision,
    TargetPosition,
    Timeframe,
    target_position_hash,
)
from zenquant.domain.serde import to_jsonable
from zenquant.infra import EventBus
from zenquant.plugins.orchestrator import compose_exits, compose_filters, compose_signals, compose_sizers
from zenquant.plugins.registry import PluginRegistry

from .decision_snapshot import build_decision_snapshot
from .exit_engine import ExitEngine, ExitEngineConfig
from .playbook_engine import PlaybookEngine, PlaybookEngineConfig
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
class _IntentConfirmationState:
    direction: float
    bars: int


@dataclass(slots=True)
class _IntentReduceConfirmationState:
    direction: float
    target_qty: float
    bars: int


@dataclass(slots=True, frozen=True)
class _DecisionSnapshotEnvelope:
    """Immutable pre-risk context waiting for the risk-final target."""

    formula_hash: str
    config_hash: str
    indicator_version: str
    data_identity: str
    alpha_direction: float
    alpha_raw_score: float
    alpha_confidence: float
    directional_state: str
    volatility_state: str
    positioning_state: str
    quality_state: str
    regime_risk_multiplier: float
    annualized_volatility: float
    volatility_target: float
    exposure_cap: float
    quality_flags: tuple[str, ...]
    options_state_id: str | None
    portfolio: str
    timeframe: Timeframe
    original_target_qty: float
    original_intent_id: str
    original_target_hash: str


# Minimum absolute signal strength to take a directional position.
# Below this threshold, the plugin-composed signal is treated as neutral (0.0).
DIRECTION_THRESHOLD: float = 0.15


@dataclass(slots=True)
class ARCStrategyConfig:
    """Sizing and regime settings for the ARC flagship strategy."""

    portfolio: str = "arc"
    account_equity: float = 100_000.0
    max_leverage: float = 6.0
    # NOTE: ANNUALIZED vol targets — must match indicator engine units.
    # realized_vol is scaled by sqrt(365 * bars_per_day).
    # PaulWei-calibrated: higher targets = larger positions = more conviction
    target_vol_5m: float = 0.70
    target_vol_1h: float = 0.80
    target_vol_4h: float = 1.10
    target_vol_1d: float = 1.30
    min_trade_qty: float = 0.0001  # SNOOPING_OK: exchange min order size
    execution_timeframe: Timeframe = Timeframe.TF_4H
    bias_max_age_bars: int = 8
    # Phase 2: trailing stop config
    trailing_stop_atr_mult: float = 2.5  # ATR multiplier for trailing stop
    min_bars_before_trail: int = 1  # minimum bars held before activating trail
    min_hours_before_trail: float = 0.0  # timeframe-normalized minimum hold before activating trail
    # Phase 4-6: engine-specific configs (can be overridden)
    playbook_config: PlaybookEngineConfig | None = None
    sizing_config: SizingEngineConfig | None = None
    exit_config: ExitEngineConfig | None = None
    enable_adaptive_signal_weights: bool = False
    direction_signal_threshold: float = DIRECTION_THRESHOLD
    intent_entry_confirmation_bars: int = 1
    intent_flip_confirmation_bars: int = 1
    intent_flat_confirmation_bars: int = 1
    intent_flat_reduce_step_pct: float = 0.0
    intent_flat_step_cadence_bars: int = 0
    intent_range_entry_confirmation_bars: int = 0
    intent_range_reentry_cooldown_bars: int = 0
    intent_bearish_override_confirmation_bars: int = 0
    intent_bearish_override_chop_guard_enabled: bool = False
    intent_bearish_override_chop_max_confidence: float = 0.58
    intent_bearish_override_chop_min_transition_risk: float = 0.40
    intent_unconfirmed_flip_reduce_only: bool = False
    intent_add_signal_threshold: float = 0.0
    intent_add_confirmation_bars: int = 1
    intent_time_leverage_hold_enabled: bool = False
    intent_time_leverage_min_hold_bars: int = 0
    intent_time_leverage_flip_signal_threshold: float = 0.55
    intent_time_leverage_confidence_floor: float = 0.55
    intent_time_leverage_transition_risk_floor: float = 0.35
    intent_time_leverage_trailing_stop_atr_mult: float = 0.0
    intent_time_leverage_trailing_exit_hold_enabled: bool = False
    intent_time_leverage_trailing_exit_max_adverse_atr: float = 2.5
    intent_same_direction_reduce_confirmation_bars: int = 1
    intent_time_leverage_size_hysteresis_enabled: bool = False
    intent_time_leverage_add_deadband_pct: float = 0.0
    intent_time_leverage_reduce_deadband_pct: float = 0.0
    intent_time_leverage_long_bias_enabled: bool = False
    intent_time_leverage_long_bias_confidence_floor: float = 0.65
    intent_time_leverage_long_bias_transition_ceiling: float = 0.45
    intent_time_leverage_long_bias_block_short: bool = True
    intent_time_leverage_long_bias_hold_reductions: bool = True
    intent_time_leverage_dip_accumulation_enabled: bool = False
    intent_time_leverage_dip_min_adverse_atr: float = 0.75
    intent_time_leverage_dip_max_adverse_atr: float = 2.5
    intent_time_leverage_dip_add_pct: float = 0.15
    intent_time_leverage_dip_max_leverage: float = 3.0
    intent_range_bear_profit_run_override_enabled: bool = False
    intent_range_bear_profit_run_direction_signal: float = -0.25
    intent_range_bear_profit_run_momentum_threshold: float = -0.20
    intent_range_bear_profit_run_confidence_floor: float = 0.50
    intent_range_bear_profit_run_max_transition_risk: float = 0.65
    emit_decision_snapshots_v2: bool = False
    emit_architecture_alpha_snapshots_v2: bool = False


class ARCStrategyEngine:
    """Convert indicator packs into target positions and reasons.

    The engine keeps to the repository contract: strategy emits target intent,
    not exchange orders. Risk and execution remain separate downstream stages.
    """

    def __init__(
        self,
        bus: EventBus,
        config: ARCStrategyConfig | None = None,
        plugin_registry: PluginRegistry | None = None,
    ) -> None:
        self._bus = bus
        self._config = config or ARCStrategyConfig()
        self._plugins = plugin_registry
        self._equity = self._config.account_equity
        self._latest_packs: dict[tuple[str, Timeframe], IndicatorPack] = {}
        self._market_states: dict[tuple[str, Timeframe], MarketState] = {}
        self._playbooks: dict[tuple[str, Timeframe], PlaybookSelection] = {}

        # Initialize engines with config from top-level ARCStrategyConfig
        self._playbook_engine = PlaybookEngine(config=self._config.playbook_config)
        self._sizing_engine = SizingEngine(
            config=self._config.sizing_config
            or SizingEngineConfig(
                target_vol_1h=self._config.target_vol_1h,
                target_vol_5m=self._config.target_vol_5m,
                target_vol_4h=self._config.target_vol_4h,
                target_vol_1d=self._config.target_vol_1d,
            )
        )
        self._exit_engine = ExitEngine(
            config=self._config.exit_config
            or ExitEngineConfig(min_bars_before_trail=self._config.min_bars_before_trail)
        )

        # Adaptive signal weighting is research-only unless explicitly enabled.
        from collections import defaultdict, deque

        self._signal_values: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=120))
        self._forward_returns: deque[float] = deque(maxlen=120)
        self._last_close: dict[str, float] = {}
        self._adaptive_weights: dict[str, float] = {}
        self._plugin_signal_context: dict[tuple[str, Timeframe], dict[str, Any]] = {}

        # Phase 2: multi-timeframe coordination
        self._directional_bias: dict[str, DirectionalBias] = {}  # symbol -> 1d bias
        self._context_bias: dict[str, DirectionalBias] = {}  # symbol -> 4h context bias (dual-layer)
        # Multi-layer execution: 4H target -> 1H context -> 5M timing
        self._pending_4h_target: dict[str, TargetPosition] = {}
        self._pending_4h_bars: dict[str, int] = {}  # bars waited since 4H target
        self._pending_1h_target: dict[str, TargetPosition] = {}
        self._pending_1h_bars: dict[str, int] = {}
        self._range_reentry_cooldown_until_ns: dict[tuple[str, Timeframe], int] = {}
        self._intent_confirmation: dict[str, _IntentConfirmationState] = {}
        self._intent_add_confirmation: dict[tuple[str, Timeframe], _IntentConfirmationState] = {}
        self._intent_reduce_confirmation: dict[tuple[str, Timeframe], _IntentReduceConfirmationState] = {}
        self._intent_flat_step_cooldowns: dict[tuple[str, Timeframe], int] = {}
        self._pending_decision_snapshots: dict[tuple[str, str, int], _DecisionSnapshotEnvelope] = {}
        self._risk_final_decisions: dict[tuple[str, str, int], RiskOverlayDecision] = {}
        self._latest_staged_decision_cutoff: dict[tuple[str, str], int] = {}

        # Phase 2: position tracking for trailing stops
        self._positions: dict[tuple[str, Timeframe], OpenPositionState] = {}

        self._bus.subscribe("indicator.>", self._on_indicator)
        self._bus.subscribe("state.market.>", self._on_market_state)
        self._bus.subscribe("risk.snapshot", self._on_risk_snapshot)
        self._bus.subscribe("risk.overlay.decision", self._on_risk_overlay_decision)
        self._bus.subscribe("exec.order.report", self._on_execution_report)
        self._bus.subscribe("intent.target_position.final.>", self._on_final_target)

    def _on_risk_snapshot(self, _: str, payload: object) -> None:
        if isinstance(payload, RiskSnapshot):
            self._equity = max(0.0, payload.equity)

    def _on_risk_overlay_decision(self, _: str, payload: object) -> None:
        if (
            self._config.emit_decision_snapshots_v2
            and isinstance(payload, RiskOverlayDecision)
            and payload.portfolio
            and payload.timeframe == Timeframe.TF_4H
            and payload.original_intent_id
        ):
            authority_key = (payload.portfolio, payload.symbol)
            decision_key = (payload.portfolio, payload.symbol, payload.cutoff_time_ns)
            if (
                self._latest_staged_decision_cutoff.get(authority_key) == payload.cutoff_time_ns
                and decision_key in self._pending_decision_snapshots
                and payload.original_target_hash == self._pending_decision_snapshots[decision_key].original_target_hash
                and payload.revised_target_hash
            ):
                self._risk_final_decisions[decision_key] = payload

    def _on_execution_report(self, _: str, payload: object) -> None:
        """Track filled positions for trailing stop logic."""
        if not isinstance(payload, ExecutionReport):
            return
        if payload.filled_qty <= 0:
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

    def _on_final_target(self, _: str, payload: object) -> None:
        """Publish a V2 decision only after risk has approved the final target."""
        if not isinstance(payload, TargetPosition) or not self._config.emit_decision_snapshots_v2:
            return
        authority_key = (payload.portfolio, payload.symbol)
        if self._latest_staged_decision_cutoff.get(authority_key) != payload.cutoff_time_ns:
            return
        key = (payload.portfolio, payload.symbol, payload.cutoff_time_ns)
        envelope = self._pending_decision_snapshots.pop(key, None)
        risk_decision = self._risk_final_decisions.pop(key, None)
        if envelope is None or risk_decision is None:
            return
        if (
            abs(risk_decision.revised_target_qty - payload.target_qty) > 1e-9
            or abs(risk_decision.original_target_qty - envelope.original_target_qty) > 1e-9
            or risk_decision.portfolio != envelope.portfolio
            or payload.portfolio != envelope.portfolio
            or risk_decision.timeframe != envelope.timeframe
            or payload.timeframe != envelope.timeframe
            or risk_decision.original_intent_id != envelope.original_intent_id
            or payload.intent_id != envelope.original_intent_id
            or risk_decision.original_target_hash != envelope.original_target_hash
            or risk_decision.revised_target_hash != target_position_hash(payload)
            or risk_decision.reduce_only != payload.reduce_only
            or payload.target_leverage > risk_decision.lev_cap + 1e-9
        ):
            return
        snapshot = build_decision_snapshot(
            target=payload,
            formula_hash=envelope.formula_hash,
            config_hash=envelope.config_hash,
            indicator_version=envelope.indicator_version,
            data_identity=envelope.data_identity,
            alpha_direction=envelope.alpha_direction,
            alpha_raw_score=envelope.alpha_raw_score,
            alpha_confidence=envelope.alpha_confidence,
            directional_state=envelope.directional_state,
            volatility_state=envelope.volatility_state,
            positioning_state=envelope.positioning_state,
            quality_state=envelope.quality_state,
            regime_risk_multiplier=envelope.regime_risk_multiplier,
            annualized_volatility=envelope.annualized_volatility,
            volatility_target=envelope.volatility_target,
            exposure_cap=min(envelope.exposure_cap, risk_decision.lev_cap),
            quality_flags=envelope.quality_flags,
            options_state_id=envelope.options_state_id,
        )
        self._bus.publish(f"strategy.decision_snapshot.4h.{payload.symbol}", snapshot)

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
        pack = self._latest_packs.get((state.symbol, state.timeframe))
        if pack is not None:
            self._publish_architecture_alpha_if_enabled(pack, state)
        if state.timeframe == Timeframe.TF_1D:
            self._update_directional_bias(state)
            return
        # Multi-layer: 4H computes target but stores as pending for lower-timeframe execution.
        if state.timeframe == Timeframe.TF_4H and self._config.execution_timeframe in {
            Timeframe.TF_1H,
            Timeframe.TF_5M,
        }:
            self._update_context_bias(state)
            pack = self._latest_packs.get((state.symbol, state.timeframe))
            if pack is not None:
                target = self._build_target(pack, state, playbook)
                target = self._confirm_directional_intent(
                    target, price=float(pack.values.get("last_price", 0.0) or 0.0)
                )
                if target is not None:
                    self._publish_decision_snapshot_if_enabled(pack, state, playbook, target)
                    self._pending_4h_target[state.symbol] = target
                    self._pending_4h_bars[state.symbol] = 0
                    self._pending_1h_target.pop(state.symbol, None)
                    self._pending_1h_bars.pop(state.symbol, None)
                else:
                    self._pending_4h_target.pop(state.symbol, None)
                    self._pending_4h_bars.pop(state.symbol, None)
                    self._pending_1h_target.pop(state.symbol, None)
                    self._pending_1h_bars.pop(state.symbol, None)
            return

        # 1H entry optimization for direct 1H execution.
        if state.timeframe == Timeframe.TF_1H and self._config.execution_timeframe == Timeframe.TF_1H:
            target = self._try_1h_entry(state)
            if target is None:
                pack = self._latest_packs.get((state.symbol, Timeframe.TF_1H))
                position = self._positions.get((state.symbol, Timeframe.TF_1H))
                if pack is not None and position is not None:
                    target = self._build_execution_hold_or_exit(pack, state, playbook, position)
            if target is not None:
                reason = self._build_reason(target)
                self._bus.publish(
                    f"strategy.regime.{self._config.portfolio}",
                    {"symbol": target.symbol, "regime": target.regime},
                )
                self._bus.publish(f"intent.target_position.{self._config.portfolio}.{target.symbol}", target)
                self._bus.publish(f"intent.reason.{self._config.portfolio}", reason)
            return

        # 1H context confirmation for 5M final timing.
        if state.timeframe == Timeframe.TF_1H and self._config.execution_timeframe == Timeframe.TF_5M:
            self._stage_5m_entry(state)
            return

        if state.timeframe == Timeframe.TF_5M and self._config.execution_timeframe == Timeframe.TF_5M:
            target = self._try_5m_entry_exit(state)
            if target is None:
                pack = self._latest_packs.get((state.symbol, Timeframe.TF_5M))
                position = self._positions.get((state.symbol, Timeframe.TF_5M))
                if pack is not None and position is not None:
                    target = self._build_execution_hold_or_exit(pack, state, playbook, position)
            if target is not None:
                reason = self._build_reason(target)
                self._bus.publish(
                    f"strategy.regime.{self._config.portfolio}",
                    {"symbol": target.symbol, "regime": target.regime},
                )
                self._bus.publish(f"intent.target_position.{self._config.portfolio}.{target.symbol}", target)
                self._bus.publish(f"intent.reason.{self._config.portfolio}", reason)
            return

        pack = self._latest_packs.get((state.symbol, state.timeframe))
        if pack is None:
            return
        target = self._build_target(pack, state, playbook)
        target = self._confirm_directional_intent(target, price=float(pack.values.get("last_price", 0.0) or 0.0))
        if target is None:
            return
        self._publish_decision_snapshot_if_enabled(pack, state, playbook, target)
        reason = self._build_reason(target)
        self._bus.publish(
            f"strategy.regime.{self._config.portfolio}",
            {"symbol": target.symbol, "regime": target.regime},
        )
        self._bus.publish(f"intent.target_position.{self._config.portfolio}.{target.symbol}", target)
        self._bus.publish(f"intent.reason.{self._config.portfolio}", reason)

    def _publish_architecture_alpha_if_enabled(self, pack: IndicatorPack, state: MarketState) -> None:
        """Publish a pure market alpha before position, exit, or execution logic."""
        if (
            not self._config.emit_architecture_alpha_snapshots_v2
            or state.timeframe != Timeframe.TF_4H
            or self._plugins is None
        ):
            return

        direction_plugins = sorted(
            (plugin for plugin in self._plugins.signals if plugin.role == "direction"),
            key=lambda plugin: plugin.meta.name,
        )
        if not direction_plugins:
            return
        direction_plugin_names = tuple(plugin.meta.name for plugin in direction_plugins)
        weights = {plugin.meta.name: self._plugins.signal_weights[plugin.meta.name] for plugin in direction_plugins}
        pure_market_state = {
            "regime": state.regime,
            "directional_state": state.directional_state,
            "volatility_state": state.volatility_state,
            "positioning_state": state.positioning_state,
            "quality_state": state.quality_state,
            "direction_signal": state.direction_signal,
            "overall_confidence": state.overall_confidence(),
            "directional_confidence": state.directional_confidence,
            "volatility_confidence": state.volatility_confidence,
            "positioning_confidence": state.positioning_confidence,
            "quality_confidence": state.quality_confidence,
            "transition_risk": state.transition_risk,
            "timeframe": state.timeframe.value,
        }
        composed = compose_signals(direction_plugins, weights, dict(pack.values), pure_market_state)
        alpha_contract = {
            "plugins": [
                {
                    "name": plugin.meta.name,
                    "version": plugin.meta.version,
                    "config": to_jsonable(plugin.config),
                    "weight": weights[plugin.meta.name],
                }
                for plugin in direction_plugins
            ],
            "timeframe": Timeframe.TF_4H.value,
        }
        alpha_config_hash = f"sha256:{hashlib.sha256(json.dumps(alpha_contract, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"
        raw_score = float(composed.direction)
        alpha_identity = {
            "symbol": pack.symbol,
            "cutoff_time_ns": pack.cutoff_time_ns,
            "formula_hash": pack.formula_hash,
            "alpha_config_hash": alpha_config_hash,
            "raw_score": raw_score,
            "confidence": float(composed.confidence),
            "regime": state.regime,
        }
        alpha_id = f"sha256:{hashlib.sha256(json.dumps(alpha_identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"
        snapshot = ArchitectureAlphaSnapshot(
            alpha_id=alpha_id,
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            cutoff_time_ns=pack.cutoff_time_ns,
            formula_hash=pack.formula_hash,
            indicator_version=pack.indicator_version,
            alpha_config_hash=alpha_config_hash,
            raw_score=raw_score,
            confidence=float(composed.confidence),
            direction=1.0 if raw_score > 0 else (-1.0 if raw_score < 0 else 0.0),
            regime=state.regime,
            direction_plugin_names=direction_plugin_names,
        )
        self._bus.publish(f"strategy.architecture_alpha.4h.{pack.symbol}", snapshot)

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

    def _update_context_bias(self, state: MarketState) -> None:
        """Store 4H context bias for dual-layer 1H execution."""
        bias = DirectionalBias(
            symbol=state.symbol,
            cutoff_time_ns=state.cutoff_time_ns,
            direction=state.direction_signal,
            regime=state.regime,
            confidence=state.regime_confidence,
            quality_flags=list(state.quality_flags),
        )
        self._context_bias[state.symbol] = bias
        self._bus.publish(f"strategy.bias.4h.{state.symbol}", bias)

    def _try_1h_entry(self, state: MarketState) -> TargetPosition | None:
        """Check if 1H bar provides optimal entry for pending 4H target.

        Entry optimization logic:
        - Long pending: enter on 1H pullback (close < open) or timeout
        - Short pending: enter on 1H bounce (close > open) or timeout
        - Crash: enter immediately on extreme 1H bar (ret < -3%)
        - Timeout: execute after 3 1H bars regardless
        """
        symbol = state.symbol
        pending = self._pending_4h_target.get(symbol)
        if pending is None:
            return None

        bars_waited = self._pending_4h_bars.get(symbol, 0)
        self._pending_4h_bars[symbol] = bars_waited + 1

        pack = self._latest_packs.get((symbol, Timeframe.TF_1H))
        if pack is None:
            return None

        price = float(pack.values.get("last_price", 0) or 0)
        if price <= 0:
            return None
        if not self._range_reentry_cooldown_ready(
            pending=pending,
            symbol=symbol,
            timeframe=Timeframe.TF_1H,
            cutoff_time_ns=state.cutoff_time_ns,
        ):
            return None
        if not self._bearish_override_chop_guard_ready(
            pending=pending,
            state=state,
            timeframe=Timeframe.TF_1H,
            bars_waited=bars_waited,
        ):
            return None
        if not self._range_entry_confirmation_ready(
            pending=pending,
            symbol=symbol,
            timeframe=Timeframe.TF_1H,
            bars_waited=bars_waited,
            cutoff_time_ns=state.cutoff_time_ns,
        ):
            return None

        # Multi-factor entry scoring (richer than simple momentum check)
        pending_direction = 1.0 if pending.target_qty > 0 else -1.0
        is_long = pending.target_qty > 0
        timeout = bars_waited >= 3

        entry_score = 0.0

        # 1. Momentum pullback/bounce (existing logic, refined)
        momentum = float(pack.values.get("momentum_roc", 0) or 0)
        if is_long and momentum < 0:
            entry_score += min(0.5, abs(momentum) * 5)  # Deeper dip = better entry
        elif not is_long and momentum > 0:
            entry_score += min(0.5, abs(momentum) * 5)  # Higher bounce = better entry

        # 2. Structure break confirmation (1H swing structure aligns with 4H)
        structure_break = float(pack.values.get("structure_break", 0) or 0)
        if structure_break * pending_direction > 0:
            entry_score += 0.4  # 1H structure confirms 4H direction

        # 3. CVD flow confirmation (real order flow aligns with direction)
        cvd_slope = float(pack.values.get("cvd_slope", 0) or 0)
        if cvd_slope * pending_direction > 0:
            entry_score += 0.3  # Buy/sell pressure confirms

        # 4. Volatility contraction (lower vol = tighter entry)
        rv = float(pack.values.get("realized_vol", 0) or 0)
        vol_ratio = float(pack.values.get("vol_ratio_rv", 1.0) or 1.0)
        if vol_ratio < 1.0:
            entry_score += 0.2  # Vol contracting = calm entry

        # 5. Crash reversal (extreme 1H dip for long entry)
        if is_long and momentum < -0.02:
            entry_score += 0.5  # Crash + pending long = immediate

        should_enter = entry_score >= 0.5 or timeout

        if not should_enter:
            return None

        executed = self._promote_pending_target(
            pending,
            timeframe=Timeframe.TF_1H,
            cutoff_time_ns=state.cutoff_time_ns,
            reason_code="1H_ENTRY_OPTIMIZED",
        )

        # Clear pending
        self._pending_4h_target.pop(symbol, None)
        self._pending_4h_bars.pop(symbol, None)

        return executed

    def _stage_5m_entry(self, state: MarketState) -> None:
        symbol = state.symbol
        pending = self._pending_4h_target.get(symbol)
        if pending is None:
            return

        bars_waited = self._pending_4h_bars.get(symbol, 0)
        self._pending_4h_bars[symbol] = bars_waited + 1
        pack = self._latest_packs.get((symbol, Timeframe.TF_1H))
        if pack is None:
            return
        if not self._range_reentry_cooldown_ready(
            pending=pending,
            symbol=symbol,
            timeframe=Timeframe.TF_5M,
            cutoff_time_ns=state.cutoff_time_ns,
        ):
            return
        if not self._bearish_override_chop_guard_ready(
            pending=pending,
            state=state,
            timeframe=Timeframe.TF_5M,
            bars_waited=bars_waited,
        ):
            return
        if not self._range_entry_confirmation_ready(
            pending=pending,
            symbol=symbol,
            timeframe=Timeframe.TF_5M,
            bars_waited=bars_waited,
            cutoff_time_ns=state.cutoff_time_ns,
        ):
            return

        pending_direction = 1.0 if pending.target_qty > 0 else -1.0
        timeout = bars_waited >= 3
        entry_score = self._score_lower_timeframe_entry(
            pack=pack,
            pending_direction=pending_direction,
            crash_bonus_threshold=-0.02,
        )
        if entry_score < 0.5 and not timeout:
            return

        staged = self._promote_pending_target(
            pending,
            timeframe=Timeframe.TF_5M,
            cutoff_time_ns=state.cutoff_time_ns,
            reason_code="1H_CONTEXT_CONFIRMED",
        )
        self._pending_1h_target[symbol] = staged
        self._pending_1h_bars[symbol] = 0
        self._pending_4h_target.pop(symbol, None)
        self._pending_4h_bars.pop(symbol, None)

    def _try_5m_entry_exit(self, state: MarketState) -> TargetPosition | None:
        symbol = state.symbol
        pending = self._pending_1h_target.get(symbol)
        if pending is None:
            return None

        bars_waited = self._pending_1h_bars.get(symbol, 0)
        self._pending_1h_bars[symbol] = bars_waited + 1
        pack = self._latest_packs.get((symbol, Timeframe.TF_5M))
        if pack is None:
            return None

        pending_direction = 1.0 if pending.target_qty > 0 else -1.0
        timeout = bars_waited >= 12
        entry_score = self._score_lower_timeframe_entry(
            pack=pack,
            pending_direction=pending_direction,
            crash_bonus_threshold=-0.008,
        )
        vpin = float(pack.values.get("vpin", 0.0) or 0.0)
        large_trade_count = float(pack.values.get("large_trade_count", 0.0) or 0.0)
        if vpin > 0.08 and pending_direction > 0:
            entry_score -= 0.2
        if vpin > 0.08 and pending_direction < 0:
            entry_score += 0.1
        if large_trade_count > 0 and pending_direction * float(pack.values.get("cvd_slope", 0.0) or 0.0) > 0:
            entry_score += 0.15

        if not self._range_reentry_cooldown_ready(
            pending=pending,
            symbol=symbol,
            timeframe=Timeframe.TF_5M,
            cutoff_time_ns=state.cutoff_time_ns,
        ):
            return None

        if not self._bearish_override_chop_guard_ready(
            pending=pending,
            state=state,
            timeframe=Timeframe.TF_5M,
            bars_waited=bars_waited,
        ):
            return None

        if not self._range_entry_confirmation_ready(
            pending=pending,
            symbol=symbol,
            timeframe=Timeframe.TF_5M,
            bars_waited=bars_waited,
            cutoff_time_ns=state.cutoff_time_ns,
        ):
            return None

        if entry_score < 0.45 and not timeout:
            return None

        executed = self._promote_pending_target(
            pending,
            timeframe=Timeframe.TF_5M,
            cutoff_time_ns=state.cutoff_time_ns,
            reason_code="5M_TIMING_CONFIRMED",
        )
        self._pending_1h_target.pop(symbol, None)
        self._pending_1h_bars.pop(symbol, None)
        return executed

    def _score_lower_timeframe_entry(
        self,
        *,
        pack: IndicatorPack,
        pending_direction: float,
        crash_bonus_threshold: float,
    ) -> float:
        is_long = pending_direction > 0
        entry_score = 0.0

        momentum = float(pack.values.get("momentum_roc", 0) or 0)
        if (is_long and momentum < 0) or (not is_long and momentum > 0):
            entry_score += min(0.5, abs(momentum) * 5)

        structure_break = float(pack.values.get("structure_break", 0) or 0)
        if structure_break * pending_direction > 0:
            entry_score += 0.4

        cvd_slope = float(pack.values.get("cvd_slope", 0) or 0)
        if cvd_slope * pending_direction > 0:
            entry_score += 0.3

        vol_ratio = float(pack.values.get("vol_ratio_rv", 1.0) or 1.0)
        if vol_ratio < 1.0:
            entry_score += 0.2

        if is_long and momentum < crash_bonus_threshold:
            entry_score += 0.5
        if not is_long and momentum > abs(crash_bonus_threshold):
            entry_score += 0.35
        return entry_score

    @staticmethod
    def _promote_pending_target(
        pending: TargetPosition,
        *,
        timeframe: Timeframe,
        cutoff_time_ns: int,
        reason_code: str,
    ) -> TargetPosition:
        return TargetPosition(
            intent_id=pending.intent_id,
            portfolio=pending.portfolio,
            symbol=pending.symbol,
            timeframe=timeframe,
            cutoff_time_ns=cutoff_time_ns,
            target_qty=pending.target_qty,
            target_leverage=pending.target_leverage,
            reduce_only=pending.reduce_only,
            regime=pending.regime,
            regime_confidence=pending.regime_confidence,
            reason_codes=[*pending.reason_codes, reason_code],
            realized_vol=pending.realized_vol,
            atr=pending.atr,
        )

    def _confirm_directional_intent(
        self, target: TargetPosition, *, price: float | None = None
    ) -> TargetPosition | None:
        direction = self._target_direction(target.target_qty)
        if target.reduce_only:
            self._intent_confirmation.pop(target.symbol, None)
            self._intent_flat_step_cooldowns.pop((target.symbol, target.timeframe), None)
            return target

        current_direction = self._current_execution_position_direction(target.symbol, target.timeframe)
        if direction == 0.0:
            if current_direction == 0.0:
                self._intent_confirmation.pop(target.symbol, None)
                self._intent_flat_step_cooldowns.pop((target.symbol, target.timeframe), None)
                return target
            cooldown_hold = self._hold_flat_step_cooldown_if_needed(target, price=price)
            if cooldown_hold is not None:
                return cooldown_hold
            required_bars = max(1, int(self._config.intent_flat_confirmation_bars))
            if required_bars <= 1:
                self._intent_confirmation.pop(target.symbol, None)
                return self._apply_flat_step_reduce(target, price=price)
            confirmation = self._intent_confirmation.get(target.symbol)
            if confirmation is None or confirmation.direction != 0.0:
                confirmation = _IntentConfirmationState(direction=0.0, bars=1)
            else:
                confirmation.bars += 1
            self._intent_confirmation[target.symbol] = confirmation
            if confirmation.bars < required_bars:
                self._bus.publish(
                    f"strategy.intent_confirmation.{target.timeframe.value}.{target.symbol}",
                    {
                        "symbol": target.symbol,
                        "timeframe": target.timeframe.value,
                        "cutoff_time_ns": target.cutoff_time_ns,
                        "direction": direction,
                        "bars": confirmation.bars,
                        "required_bars": required_bars,
                        "reason": "INTENT_FLAT_CONFIRMATION_PENDING",
                    },
                )
                return self._hold_current_position_for_flat_confirmation(target, price=price)
            target.reason_codes.append(f"INTENT_FLAT_CONFIRMED_{required_bars}_BARS")
            self._bus.publish(
                f"strategy.intent_confirmation.{target.timeframe.value}.{target.symbol}",
                {
                    "symbol": target.symbol,
                    "timeframe": target.timeframe.value,
                    "cutoff_time_ns": target.cutoff_time_ns,
                    "direction": direction,
                    "bars": confirmation.bars,
                    "required_bars": required_bars,
                    "reason": "INTENT_FLAT_CONFIRMED",
                },
            )
            return self._apply_flat_step_reduce(target, price=price)

        if current_direction == direction:
            self._intent_confirmation.pop(target.symbol, None)
            self._intent_flat_step_cooldowns.pop((target.symbol, target.timeframe), None)
            return target
        self._intent_flat_step_cooldowns.pop((target.symbol, target.timeframe), None)

        required_bars = (
            self._config.intent_flip_confirmation_bars
            if current_direction != 0.0 and current_direction != direction
            else self._config.intent_entry_confirmation_bars
        )
        if "STATE_BEARISH_DIRECTION_OVERRIDE" in target.reason_codes:
            required_bars = max(required_bars, self._config.intent_bearish_override_confirmation_bars)
        required_bars = max(1, int(required_bars))
        if required_bars <= 1:
            self._intent_confirmation.pop(target.symbol, None)
            return target

        confirmation = self._intent_confirmation.get(target.symbol)
        if confirmation is None or confirmation.direction != direction:
            confirmation = _IntentConfirmationState(direction=direction, bars=1)
        else:
            confirmation.bars += 1
        self._intent_confirmation[target.symbol] = confirmation

        if confirmation.bars < required_bars:
            self._bus.publish(
                f"strategy.intent_confirmation.{target.timeframe.value}.{target.symbol}",
                {
                    "symbol": target.symbol,
                    "timeframe": target.timeframe.value,
                    "cutoff_time_ns": target.cutoff_time_ns,
                    "direction": direction,
                    "bars": confirmation.bars,
                    "required_bars": required_bars,
                    "reason": "INTENT_CONFIRMATION_PENDING",
                },
            )
            if current_direction != 0.0 and self._config.intent_unconfirmed_flip_reduce_only:
                return self._flatten_unconfirmed_flip(target)
            return None

        target.reason_codes.append(f"INTENT_CONFIRMED_{required_bars}_BARS")
        self._bus.publish(
            f"strategy.intent_confirmation.{target.timeframe.value}.{target.symbol}",
            {
                "symbol": target.symbol,
                "timeframe": target.timeframe.value,
                "cutoff_time_ns": target.cutoff_time_ns,
                "direction": direction,
                "bars": confirmation.bars,
                "required_bars": required_bars,
                "reason": "INTENT_CONFIRMED",
            },
        )
        return target

    @staticmethod
    def _flatten_unconfirmed_flip(target: TargetPosition) -> TargetPosition:
        return TargetPosition(
            intent_id=target.intent_id,
            portfolio=target.portfolio,
            symbol=target.symbol,
            timeframe=target.timeframe,
            cutoff_time_ns=target.cutoff_time_ns,
            target_qty=0.0,
            target_leverage=0.0,
            reduce_only=True,
            regime=target.regime,
            regime_confidence=target.regime_confidence,
            reason_codes=[
                *target.reason_codes,
                "INTENT_FLIP_CONFIRMATION_PENDING",
                "INTENT_FLIP_FLATTEN_FIRST",
            ],
            realized_vol=target.realized_vol,
            atr=target.atr,
        )

    def _hold_current_position_for_flat_confirmation(
        self,
        target: TargetPosition,
        *,
        price: float | None,
    ) -> TargetPosition:
        position = self._current_execution_position(target.symbol, target.timeframe)
        if position is None:
            return target
        signed_qty = position.direction * position.current_qty
        target_leverage = target.target_leverage
        mark_price = float(price or 0.0)
        if mark_price <= 0.0:
            mark_price = position.entry_price
        if mark_price > 0.0 and self._equity > 0.0:
            target_leverage = abs(signed_qty * mark_price) / self._equity
        return TargetPosition(
            intent_id=target.intent_id,
            portfolio=target.portfolio,
            symbol=target.symbol,
            timeframe=target.timeframe,
            cutoff_time_ns=target.cutoff_time_ns,
            target_qty=round(signed_qty, 6),
            target_leverage=round(target_leverage, 4),
            reduce_only=False,
            regime=target.regime,
            regime_confidence=target.regime_confidence,
            reason_codes=[
                *target.reason_codes,
                "INTENT_FLAT_CONFIRMATION_PENDING",
                "INTENT_FLAT_HELD",
            ],
            realized_vol=target.realized_vol,
            atr=target.atr,
        )

    def _hold_flat_step_cooldown_if_needed(
        self,
        target: TargetPosition,
        *,
        price: float | None,
    ) -> TargetPosition | None:
        key = (target.symbol, target.timeframe)
        remaining = self._intent_flat_step_cooldowns.get(key, 0)
        if remaining <= 0:
            self._intent_flat_step_cooldowns.pop(key, None)
            return None
        if remaining <= 1:
            self._intent_flat_step_cooldowns.pop(key, None)
        else:
            self._intent_flat_step_cooldowns[key] = remaining - 1
        held = self._hold_current_position_for_flat_confirmation(target, price=price)
        held.reason_codes = [
            code for code in held.reason_codes if code not in {"INTENT_FLAT_CONFIRMATION_PENDING", "INTENT_FLAT_HELD"}
        ]
        held.reason_codes.extend(["INTENT_FLAT_STEP_CADENCE", "INTENT_FLAT_HELD"])
        self._bus.publish(
            f"strategy.intent_flat_step.{target.timeframe.value}.{target.symbol}",
            {
                "symbol": target.symbol,
                "timeframe": target.timeframe.value,
                "cutoff_time_ns": target.cutoff_time_ns,
                "remaining_bars": remaining,
                "reason": "INTENT_FLAT_STEP_CADENCE",
            },
        )
        return held

    def _apply_flat_step_reduce(
        self,
        target: TargetPosition,
        *,
        price: float | None,
    ) -> TargetPosition:
        step_pct = max(0.0, min(1.0, float(self._config.intent_flat_reduce_step_pct)))
        if step_pct <= 0.0:
            self._intent_flat_step_cooldowns.pop((target.symbol, target.timeframe), None)
            return target

        position = self._current_execution_position(target.symbol, target.timeframe)
        if position is None or position.current_qty < self._config.min_trade_qty:
            self._intent_flat_step_cooldowns.pop((target.symbol, target.timeframe), None)
            return target

        signed_qty = position.direction * position.current_qty
        stepped_qty = signed_qty * max(0.0, 1.0 - step_pct)
        if abs(stepped_qty) < self._config.min_trade_qty:
            stepped_qty = 0.0

        target_leverage = 0.0
        mark_price = float(price or 0.0)
        if mark_price <= 0.0:
            mark_price = position.entry_price
        if mark_price > 0.0 and self._equity > 0.0:
            target_leverage = abs(stepped_qty * mark_price) / self._equity

        if stepped_qty != 0.0:
            cadence_bars = max(0, int(self._config.intent_flat_step_cadence_bars))
            if cadence_bars > 0:
                self._intent_flat_step_cooldowns[(target.symbol, target.timeframe)] = cadence_bars
        else:
            self._intent_flat_step_cooldowns.pop((target.symbol, target.timeframe), None)

        self._bus.publish(
            f"strategy.intent_flat_step.{target.timeframe.value}.{target.symbol}",
            {
                "symbol": target.symbol,
                "timeframe": target.timeframe.value,
                "cutoff_time_ns": target.cutoff_time_ns,
                "current_qty": round(signed_qty, 6),
                "target_qty": round(stepped_qty, 6),
                "step_pct": round(step_pct, 6),
                "cadence_bars": max(0, int(self._config.intent_flat_step_cadence_bars)),
                "reason": "INTENT_FLAT_STEP_REDUCE",
            },
        )
        return TargetPosition(
            intent_id=target.intent_id,
            portfolio=target.portfolio,
            symbol=target.symbol,
            timeframe=target.timeframe,
            cutoff_time_ns=target.cutoff_time_ns,
            target_qty=round(stepped_qty, 6),
            target_leverage=round(target_leverage, 4),
            reduce_only=False,
            regime=target.regime,
            regime_confidence=target.regime_confidence,
            reason_codes=[*target.reason_codes, "INTENT_FLAT_STEP_REDUCE"],
            realized_vol=target.realized_vol,
            atr=target.atr,
        )

    def _current_execution_position_direction(self, symbol: str, target_timeframe: Timeframe) -> float:
        position = self._current_execution_position(symbol, target_timeframe)
        if position is not None and position.current_qty >= self._config.min_trade_qty:
            return 1.0 if position.direction > 0 else -1.0
        return 0.0

    def _current_execution_position(self, symbol: str, target_timeframe: Timeframe) -> OpenPositionState | None:
        preferred_keys = (
            (symbol, self._config.execution_timeframe),
            (symbol, target_timeframe),
        )
        for key in preferred_keys:
            position = self._positions.get(key)
            if position is not None and position.current_qty >= self._config.min_trade_qty:
                return position
        return None

    @staticmethod
    def _target_direction(target_qty: float) -> float:
        if target_qty > 0:
            return 1.0
        if target_qty < 0:
            return -1.0
        return 0.0

    def _is_range_directional_target(self, pending: TargetPosition) -> bool:
        if self._target_direction(pending.target_qty) == 0.0:
            return False
        return "PLAYBOOK_RANGE_LIGHT" in pending.reason_codes or "REGIME_RANGE" in pending.reason_codes

    def _arm_range_reentry_cooldown_if_needed(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        cutoff_time_ns: int,
        playbook: PlaybookSelection,
        exit_decision: ExitDecision,
    ) -> None:
        cooldown_bars = max(0, int(self._config.intent_range_reentry_cooldown_bars))
        if cooldown_bars <= 0:
            return
        if playbook.playbook_id != "RANGE_LIGHT" or "EXIT_TRAILING_STOP" not in exit_decision.reason_codes:
            return
        bar_ns = timeframe.seconds * 1_000_000_000
        cooldown_until_ns = cutoff_time_ns + (cooldown_bars * bar_ns)
        key = (symbol, timeframe)
        self._range_reentry_cooldown_until_ns[key] = max(
            cooldown_until_ns,
            self._range_reentry_cooldown_until_ns.get(key, 0),
        )
        self._bus.publish(
            f"strategy.intent_range_reentry_cooldown.{timeframe.value}.{symbol}",
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "cutoff_time_ns": cutoff_time_ns,
                "cooldown_bars": cooldown_bars,
                "cooldown_until_ns": self._range_reentry_cooldown_until_ns[key],
                "reason": "INTENT_RANGE_REENTRY_COOLDOWN_ARMED",
            },
        )

    def _range_reentry_cooldown_ready(
        self,
        *,
        pending: TargetPosition,
        symbol: str,
        timeframe: Timeframe,
        cutoff_time_ns: int,
    ) -> bool:
        if not self._is_range_directional_target(pending):
            return True
        key = (symbol, timeframe)
        cooldown_until_ns = self._range_reentry_cooldown_until_ns.get(key, 0)
        if cooldown_until_ns <= cutoff_time_ns:
            self._range_reentry_cooldown_until_ns.pop(key, None)
            return True
        bar_ns = timeframe.seconds * 1_000_000_000
        remaining_bars = max(1, int((cooldown_until_ns - cutoff_time_ns + bar_ns - 1) // bar_ns))
        self._bus.publish(
            f"strategy.intent_range_reentry_cooldown.{timeframe.value}.{symbol}",
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "cutoff_time_ns": cutoff_time_ns,
                "cooldown_until_ns": cooldown_until_ns,
                "remaining_bars": remaining_bars,
                "reason": "INTENT_RANGE_REENTRY_COOLDOWN_PENDING",
            },
        )
        return False

    def _range_entry_confirmation_ready(
        self,
        *,
        pending: TargetPosition,
        symbol: str,
        timeframe: Timeframe,
        bars_waited: int,
        cutoff_time_ns: int,
    ) -> bool:
        required_bars = max(0, int(self._config.intent_range_entry_confirmation_bars))
        if required_bars <= 0:
            return True
        if not self._is_range_directional_target(pending):
            return True
        pending_direction = self._target_direction(pending.target_qty)

        bars_seen = bars_waited + 1
        if bars_seen >= required_bars:
            confirmed_reason = f"INTENT_RANGE_ENTRY_CONFIRMED_{required_bars}_BARS"
            if confirmed_reason not in pending.reason_codes:
                pending.reason_codes.append(confirmed_reason)
            return True

        self._bus.publish(
            f"strategy.intent_range_entry_confirmation.{timeframe.value}.{symbol}",
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "cutoff_time_ns": cutoff_time_ns,
                "pending_direction": pending_direction,
                "bars": bars_seen,
                "required_bars": required_bars,
                "reason": "INTENT_RANGE_ENTRY_CONFIRMATION_PENDING",
            },
        )
        return False

    def _bearish_override_chop_guard_ready(
        self,
        *,
        pending: TargetPosition,
        state: MarketState,
        timeframe: Timeframe,
        bars_waited: int,
    ) -> bool:
        if not self._config.intent_bearish_override_chop_guard_enabled:
            return True
        if "STATE_BEARISH_DIRECTION_OVERRIDE" not in pending.reason_codes:
            return True
        if self._target_direction(pending.target_qty) >= 0.0:
            return True

        max_confidence = max(0.0, float(self._config.intent_bearish_override_chop_max_confidence))
        min_transition_risk = max(0.0, float(self._config.intent_bearish_override_chop_min_transition_risk))
        chop_like = (
            state.directional_state == "TRANSITION"
            and state.quality_state == "NOISY"
            and float(state.directional_confidence) <= max_confidence
            and float(state.transition_risk) >= min_transition_risk
        )
        if not chop_like:
            return True

        self._bus.publish(
            f"strategy.intent_bearish_override_chop_guard.{timeframe.value}.{state.symbol}",
            {
                "symbol": state.symbol,
                "timeframe": timeframe.value,
                "cutoff_time_ns": state.cutoff_time_ns,
                "bars_waited": bars_waited + 1,
                "directional_state": state.directional_state,
                "directional_confidence": round(float(state.directional_confidence), 6),
                "quality_state": state.quality_state,
                "transition_risk": round(float(state.transition_risk), 6),
                "reason": "INTENT_BEARISH_OVERRIDE_CHOP_GUARD_PENDING",
            },
        )
        return False

    def _apply_same_direction_add_gate(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        target_qty: float,
        target_leverage: float,
        price: float,
        reason_codes: list[str],
    ) -> tuple[float, float]:
        confirmation_key = (symbol, timeframe)
        threshold = max(0.0, float(self._config.intent_add_signal_threshold))
        if threshold <= 0.0 or abs(target_qty) < self._config.min_trade_qty:
            self._intent_add_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage

        position = self._current_execution_position(symbol, timeframe)
        if position is None:
            self._intent_add_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage

        current_signed_qty = position.direction * position.current_qty
        if current_signed_qty * target_qty <= 0.0:
            self._intent_add_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage
        if abs(target_qty) <= abs(current_signed_qty) + self._config.min_trade_qty:
            self._intent_add_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage

        context = self._plugin_signal_context.get((symbol, timeframe), {})
        signal_strength = abs(float(context.get("effective_direction", 0.0) or 0.0))
        if signal_strength < threshold:
            self._intent_add_confirmation.pop(confirmation_key, None)
            capped_qty, capped_leverage = self._current_position_target(
                current_signed_qty=current_signed_qty,
                target_leverage=target_leverage,
                price=price,
            )
            reason_codes.append("INTENT_ADD_SIGNAL_GATE")
            return capped_qty, capped_leverage

        required_bars = max(1, int(self._config.intent_add_confirmation_bars))
        if required_bars <= 1:
            self._intent_add_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage

        direction = self._target_direction(target_qty)
        confirmation = self._intent_add_confirmation.get(confirmation_key)
        if confirmation is None or confirmation.direction != direction:
            confirmation = _IntentConfirmationState(direction=direction, bars=1)
        else:
            confirmation.bars += 1
        self._intent_add_confirmation[confirmation_key] = confirmation

        if confirmation.bars >= required_bars:
            self._intent_add_confirmation.pop(confirmation_key, None)
            reason_codes.append(f"INTENT_ADD_CONFIRMED_{required_bars}_BARS")
            self._bus.publish(
                f"strategy.intent_add_confirmation.{timeframe.value}.{symbol}",
                {
                    "symbol": symbol,
                    "timeframe": timeframe.value,
                    "direction": direction,
                    "bars": confirmation.bars,
                    "required_bars": required_bars,
                    "signal_strength": signal_strength,
                    "reason": "INTENT_ADD_CONFIRMED",
                },
            )
            return target_qty, target_leverage

        capped_qty, capped_leverage = self._current_position_target(
            current_signed_qty=current_signed_qty,
            target_leverage=target_leverage,
            price=price,
        )
        reason_codes.extend(["INTENT_ADD_CONFIRMATION_PENDING", "INTENT_ADD_HELD"])
        self._bus.publish(
            f"strategy.intent_add_confirmation.{timeframe.value}.{symbol}",
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "direction": direction,
                "bars": confirmation.bars,
                "required_bars": required_bars,
                "signal_strength": signal_strength,
                "reason": "INTENT_ADD_CONFIRMATION_PENDING",
            },
        )
        return capped_qty, capped_leverage

    def _apply_same_direction_reduce_confirmation(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        target_qty: float,
        target_leverage: float,
        price: float,
        state: MarketState,
        reduce_only: bool,
        reason_codes: list[str],
    ) -> tuple[float, float]:
        confirmation_key = (symbol, timeframe)
        required_bars = max(1, int(self._config.intent_same_direction_reduce_confirmation_bars))
        if required_bars <= 1 or reduce_only:
            self._intent_reduce_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage

        position = self._current_execution_position(symbol, timeframe)
        if position is None:
            self._intent_reduce_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage

        current_signed_qty = position.direction * position.current_qty
        if abs(current_signed_qty) < self._config.min_trade_qty:
            self._intent_reduce_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage
        if abs(target_qty) < self._config.min_trade_qty or current_signed_qty * target_qty <= 0.0:
            self._intent_reduce_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage
        if abs(target_qty) >= abs(current_signed_qty) - self._config.min_trade_qty:
            self._intent_reduce_confirmation.pop(confirmation_key, None)
            return target_qty, target_leverage

        if self._same_direction_reduce_confirmation_bypassed(position=position, state=state):
            self._intent_reduce_confirmation.pop(confirmation_key, None)
            reason_codes.append("INTENT_REDUCE_CONFIRMATION_BYPASS")
            return target_qty, target_leverage

        direction = self._target_direction(target_qty)
        confirmation = self._intent_reduce_confirmation.get(confirmation_key)
        if confirmation is None or confirmation.direction != direction:
            confirmation = _IntentReduceConfirmationState(direction=direction, target_qty=target_qty, bars=1)
        else:
            confirmation.bars += 1
            if abs(target_qty) < abs(confirmation.target_qty):
                confirmation.target_qty = target_qty
        self._intent_reduce_confirmation[confirmation_key] = confirmation

        if confirmation.bars >= required_bars:
            self._intent_reduce_confirmation.pop(confirmation_key, None)
            confirmed_qty = confirmation.target_qty
            confirmed_leverage = target_leverage
            if price > 0.0 and self._equity > 0.0:
                confirmed_leverage = abs(confirmed_qty * price) / self._equity
            reason_codes.append(f"INTENT_REDUCE_CONFIRMED_{required_bars}_BARS")
            if abs(confirmed_qty) < abs(target_qty):
                reason_codes.append("INTENT_REDUCE_AGGREGATED")
            self._bus.publish(
                f"strategy.intent_reduce_confirmation.{timeframe.value}.{symbol}",
                {
                    "symbol": symbol,
                    "timeframe": timeframe.value,
                    "cutoff_time_ns": state.cutoff_time_ns,
                    "direction": direction,
                    "bars": confirmation.bars,
                    "required_bars": required_bars,
                    "current_qty": round(current_signed_qty, 6),
                    "requested_target_qty": round(target_qty, 6),
                    "confirmed_target_qty": round(confirmed_qty, 6),
                    "reason": "INTENT_REDUCE_CONFIRMED",
                },
            )
            return confirmed_qty, confirmed_leverage

        capped_qty, capped_leverage = self._current_position_target(
            current_signed_qty=current_signed_qty,
            target_leverage=target_leverage,
            price=price,
        )
        reason_codes.extend(["INTENT_REDUCE_CONFIRMATION_PENDING", "INTENT_REDUCE_HELD"])
        self._bus.publish(
            f"strategy.intent_reduce_confirmation.{timeframe.value}.{symbol}",
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "cutoff_time_ns": state.cutoff_time_ns,
                "direction": direction,
                "bars": confirmation.bars,
                "required_bars": required_bars,
                "current_qty": round(current_signed_qty, 6),
                "requested_target_qty": round(target_qty, 6),
                "pending_target_qty": round(confirmation.target_qty, 6),
                "reason": "INTENT_REDUCE_CONFIRMATION_PENDING",
            },
        )
        return capped_qty, capped_leverage

    @staticmethod
    def _same_direction_reduce_confirmation_bypassed(
        *,
        position: OpenPositionState,
        state: MarketState,
    ) -> bool:
        opposing_trend_state = "DOWNTREND" if position.direction > 0 else "UPTREND"
        return (
            state.directional_state == opposing_trend_state
            or state.quality_state == "UNSAFE"
            or state.volatility_state == "DISTORTED"
        )

    def _current_position_target(
        self,
        *,
        current_signed_qty: float,
        target_leverage: float,
        price: float,
    ) -> tuple[float, float]:
        capped_qty = current_signed_qty
        capped_leverage = target_leverage
        if price > 0.0 and self._equity > 0.0:
            capped_leverage = abs(capped_qty * price) / self._equity
        return capped_qty, capped_leverage

    def _apply_time_leverage_hold_guard(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        target_qty: float,
        target_leverage: float,
        price: float,
        state: MarketState,
        reason_codes: list[str],
    ) -> tuple[float, float]:
        if not self._config.intent_time_leverage_hold_enabled:
            return target_qty, target_leverage

        position = self._current_execution_position(symbol, timeframe)
        if position is None or price <= 0.0 or self._equity <= 0.0:
            return target_qty, target_leverage

        current_signed_qty = position.direction * position.current_qty
        target_direction = self._target_direction(target_qty)
        if target_direction == 0.0 or current_signed_qty * target_qty >= 0.0:
            return target_qty, target_leverage

        context = self._plugin_signal_context.get((symbol, timeframe), {})
        signal_strength = abs(float(context.get("effective_direction", state.direction_signal) or 0.0))
        flip_threshold = max(0.0, float(self._config.intent_time_leverage_flip_signal_threshold))
        confidence_floor = max(0.0, float(self._config.intent_time_leverage_confidence_floor))
        transition_floor = max(0.0, float(self._config.intent_time_leverage_transition_risk_floor))
        min_hold_bars = max(0, int(self._config.intent_time_leverage_min_hold_bars))
        opposing_trend_state = "DOWNTREND" if position.direction > 0 else "UPTREND"
        opposing_trend_confirmed = (
            state.directional_state == opposing_trend_state
            and state.directional_confidence >= confidence_floor
            and state.transition_risk >= transition_floor
            and signal_strength >= flip_threshold
        )
        min_hold_satisfied = position.bars_held >= min_hold_bars
        if min_hold_satisfied and opposing_trend_confirmed:
            reason_codes.append("TIME_LEVERAGE_FLIP_CONFIRMED")
            return target_qty, target_leverage

        held_leverage = abs(current_signed_qty * price) / self._equity
        reason_codes.append("TIME_LEVERAGE_FLIP_HELD")
        if not min_hold_satisfied:
            reason_codes.append("TIME_LEVERAGE_MIN_HOLD")
        if state.directional_state != opposing_trend_state:
            reason_codes.append("TIME_LEVERAGE_TREND_STILL_ALIGNED")
        if signal_strength < flip_threshold:
            reason_codes.append("TIME_LEVERAGE_SIGNAL_TOO_WEAK")
        if state.directional_confidence < confidence_floor or state.transition_risk < transition_floor:
            reason_codes.append("TIME_LEVERAGE_REVERSAL_NOT_CONFIRMED")
        self._bus.publish(
            f"strategy.time_leverage_hold.{timeframe.value}.{symbol}",
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "cutoff_time_ns": state.cutoff_time_ns,
                "bars_held": position.bars_held,
                "min_hold_bars": min_hold_bars,
                "current_direction": position.direction,
                "blocked_direction": target_direction,
                "signal_strength": round(signal_strength, 6),
                "directional_confidence": round(float(state.directional_confidence), 6),
                "transition_risk": round(float(state.transition_risk), 6),
                "reason": "TIME_LEVERAGE_FLIP_HELD",
            },
        )
        return current_signed_qty, held_leverage

    def _time_leverage_trailing_exit_hold_target(
        self,
        *,
        pack: IndicatorPack,
        state: MarketState,
        position: OpenPositionState,
        exit_decision: ExitDecision,
        price: float,
        atr: float,
    ) -> TargetPosition | None:
        if (
            not self._config.intent_time_leverage_hold_enabled
            or not self._config.intent_time_leverage_trailing_exit_hold_enabled
        ):
            return None
        if "EXIT_TRAILING_STOP" not in exit_decision.reason_codes:
            return None
        if position.direction <= 0.0:
            return None
        if price <= 0.0 or self._equity <= 0.0:
            return None
        if state.quality_state == "UNSAFE" or state.volatility_state == "DISTORTED":
            return None

        context = self._plugin_signal_context.get((pack.symbol, pack.timeframe), {})
        signal_strength = abs(float(context.get("effective_direction", state.direction_signal) or 0.0))
        flip_threshold = max(0.0, float(self._config.intent_time_leverage_flip_signal_threshold))
        confidence_floor = max(0.0, float(self._config.intent_time_leverage_confidence_floor))
        transition_floor = max(0.0, float(self._config.intent_time_leverage_transition_risk_floor))
        min_hold_bars = max(0, int(self._config.intent_time_leverage_min_hold_bars))
        opposing_trend_confirmed = (
            state.directional_state == "DOWNTREND"
            and state.directional_confidence >= confidence_floor
            and state.transition_risk >= transition_floor
            and signal_strength >= flip_threshold
        )
        min_hold_satisfied = position.bars_held >= min_hold_bars
        if min_hold_satisfied and opposing_trend_confirmed:
            return None

        adverse_atr = 0.0
        if atr > 0.0 and position.entry_price > price:
            adverse_atr = (position.entry_price - price) / atr
        max_adverse_atr = max(0.0, float(self._config.intent_time_leverage_trailing_exit_max_adverse_atr))
        if max_adverse_atr > 0.0 and adverse_atr > max_adverse_atr:
            return None

        signed_qty = position.direction * position.current_qty
        target_leverage = abs(signed_qty * price) / self._equity
        reason_codes = [
            f"REGIME_{state.regime}",
            "HOLD_EXECUTION_POSITION",
            *exit_decision.reason_codes,
            "TIME_LEVERAGE_TRAILING_EXIT_HELD",
        ]
        if not min_hold_satisfied:
            reason_codes.append("TIME_LEVERAGE_MIN_HOLD")
        if state.directional_state != "DOWNTREND":
            reason_codes.append("TIME_LEVERAGE_TREND_STILL_ALIGNED")
        if signal_strength < flip_threshold:
            reason_codes.append("TIME_LEVERAGE_SIGNAL_TOO_WEAK")
        if state.directional_confidence < confidence_floor or state.transition_risk < transition_floor:
            reason_codes.append("TIME_LEVERAGE_REVERSAL_NOT_CONFIRMED")

        self._bus.publish(
            f"strategy.time_leverage_trailing_exit_hold.{pack.timeframe.value}.{pack.symbol}",
            {
                "symbol": pack.symbol,
                "timeframe": pack.timeframe.value,
                "cutoff_time_ns": pack.cutoff_time_ns,
                "bars_held": position.bars_held,
                "min_hold_bars": min_hold_bars,
                "adverse_atr": round(adverse_atr, 6),
                "max_adverse_atr": round(max_adverse_atr, 6),
                "signal_strength": round(signal_strength, 6),
                "directional_state": state.directional_state,
                "directional_confidence": round(float(state.directional_confidence), 6),
                "transition_risk": round(float(state.transition_risk), 6),
                "exit_type": exit_decision.exit_type,
                "reason": "TIME_LEVERAGE_TRAILING_EXIT_HELD",
            },
        )
        return TargetPosition(
            intent_id=self._intent_id(pack),
            portfolio=self._config.portfolio,
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            cutoff_time_ns=pack.cutoff_time_ns,
            target_qty=round(signed_qty, 6),
            target_leverage=round(target_leverage, 4),
            reduce_only=False,
            regime=state.regime,
            regime_confidence=round(state.regime_confidence, 4),
            reason_codes=reason_codes,
            realized_vol=float(pack.values.get("realized_vol", 0) or 0),
            atr=float(pack.values.get("atr", 0) or 0),
        )

    def _apply_time_leverage_size_hysteresis(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        target_qty: float,
        target_leverage: float,
        price: float,
        state: MarketState,
        reduce_only: bool,
        reason_codes: list[str],
    ) -> tuple[float, float]:
        if not self._config.intent_time_leverage_size_hysteresis_enabled or reduce_only:
            return target_qty, target_leverage

        position = self._current_execution_position(symbol, timeframe)
        if position is None or price <= 0.0 or self._equity <= 0.0:
            return target_qty, target_leverage

        current_signed_qty = position.direction * position.current_qty
        target_direction = self._target_direction(target_qty)
        if target_direction == 0.0 or current_signed_qty * target_qty <= 0.0:
            return target_qty, target_leverage

        current_abs = abs(current_signed_qty)
        target_abs = abs(target_qty)
        add_resize = target_abs > current_abs + self._config.min_trade_qty
        reduce_resize = target_abs < current_abs - self._config.min_trade_qty
        if not add_resize and not reduce_resize:
            return target_qty, target_leverage

        deadband = (
            self._config.intent_time_leverage_add_deadband_pct
            if add_resize
            else self._config.intent_time_leverage_reduce_deadband_pct
        )
        deadband = max(0.0, float(deadband))
        if deadband <= 0.0:
            return target_qty, target_leverage

        change_pct = abs(target_abs - current_abs) / max(current_abs, self._config.min_trade_qty)
        if change_pct >= deadband:
            return target_qty, target_leverage

        opposing_trend_state = "DOWNTREND" if position.direction > 0 else "UPTREND"
        if reduce_resize and state.directional_state == opposing_trend_state:
            return target_qty, target_leverage
        if reduce_resize and state.quality_state == "UNSAFE":
            return target_qty, target_leverage
        if reduce_resize and state.volatility_state == "DISTORTED":
            return target_qty, target_leverage

        held_leverage = abs(current_signed_qty * price) / self._equity
        reason_codes.append("TIME_LEVERAGE_SIZE_HELD")
        reason_codes.append("TIME_LEVERAGE_ADD_DEADBAND" if add_resize else "TIME_LEVERAGE_REDUCE_DEADBAND")
        self._bus.publish(
            f"strategy.time_leverage_size_hysteresis.{timeframe.value}.{symbol}",
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "cutoff_time_ns": state.cutoff_time_ns,
                "current_qty": round(current_signed_qty, 6),
                "blocked_target_qty": round(target_qty, 6),
                "change_pct": round(change_pct, 6),
                "deadband": round(deadband, 6),
                "resize": "add" if add_resize else "reduce",
                "directional_state": state.directional_state,
                "quality_state": state.quality_state,
                "volatility_state": state.volatility_state,
                "reason": "TIME_LEVERAGE_SIZE_HELD",
            },
        )
        return current_signed_qty, held_leverage

    def _time_leverage_long_bias_active(self, symbol: str, cutoff_time_ns: int, state: MarketState) -> tuple[bool, str]:
        if not self._config.intent_time_leverage_long_bias_enabled:
            return False, "disabled"
        if state.quality_state == "UNSAFE" or state.volatility_state == "DISTORTED":
            return False, "unsafe_state"

        confidence_floor = max(0.0, float(self._config.intent_time_leverage_long_bias_confidence_floor))
        transition_ceiling = max(0.0, float(self._config.intent_time_leverage_long_bias_transition_ceiling))
        if state.directional_state == "DOWNTREND" and state.directional_confidence >= confidence_floor:
            return False, "confirmed_downtrend"

        bias, _ = self._active_directional_bias(symbol, cutoff_time_ns)
        if bias is not None:
            if bias.direction > 0.0 and bias.confidence >= confidence_floor:
                return True, "1d_bias"
            if bias.direction < 0.0 and bias.confidence >= confidence_floor:
                return False, "1d_bias_opposes"

        if (
            state.directional_state == "UPTREND"
            and state.directional_confidence >= confidence_floor
            and state.transition_risk <= transition_ceiling
        ):
            return True, "4h_uptrend"
        return False, "no_long_bias"

    def _time_leverage_trailing_stop_atr_mult(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        state: MarketState,
        position: OpenPositionState,
        default_mult: float,
    ) -> float:
        configured_mult = max(0.0, float(self._config.intent_time_leverage_trailing_stop_atr_mult))
        if (
            not self._config.intent_time_leverage_hold_enabled
            or configured_mult <= default_mult
            or position.direction <= 0.0
        ):
            return default_mult

        long_bias_active, long_bias_source = self._time_leverage_long_bias_active(symbol, state.cutoff_time_ns, state)
        if not long_bias_active:
            return default_mult

        self._bus.publish(
            f"strategy.time_leverage_trailing_stop.{timeframe.value}.{symbol}",
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "cutoff_time_ns": state.cutoff_time_ns,
                "default_atr_mult": round(default_mult, 6),
                "time_leverage_atr_mult": round(configured_mult, 6),
                "long_bias_source": long_bias_source,
                "directional_state": state.directional_state,
                "directional_confidence": round(float(state.directional_confidence), 6),
                "transition_risk": round(float(state.transition_risk), 6),
                "reason": "TIME_LEVERAGE_TRAILING_STOP_WIDENED",
            },
        )
        return configured_mult

    def _min_bars_before_trail(self, timeframe: Timeframe) -> int:
        min_bars = max(0, int(self._config.min_bars_before_trail))
        min_hours = max(0.0, float(self._config.min_hours_before_trail))
        if min_hours <= 0.0:
            return min_bars
        timeframe_bars = max(1, ceil((min_hours * 3600.0) / timeframe.seconds))
        return max(min_bars, timeframe_bars)

    def _apply_time_leverage_long_bias_guard(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        target_qty: float,
        target_leverage: float,
        price: float,
        atr: float,
        state: MarketState,
        reduce_only: bool,
        reason_codes: list[str],
    ) -> tuple[float, float]:
        if not self._config.intent_time_leverage_long_bias_enabled or reduce_only:
            return target_qty, target_leverage

        long_bias_active, long_bias_source = self._time_leverage_long_bias_active(symbol, state.cutoff_time_ns, state)
        if not long_bias_active:
            return target_qty, target_leverage

        position = self._current_execution_position(symbol, timeframe)
        target_direction = self._target_direction(target_qty)
        if position is None:
            if target_direction < 0.0 and self._config.intent_time_leverage_long_bias_block_short:
                reason_codes.extend(["TIME_LEVERAGE_LONG_BIAS_ACTIVE", "TIME_LEVERAGE_SHORT_BLOCKED"])
                self._publish_time_leverage_long_bias_event(
                    symbol=symbol,
                    timeframe=timeframe,
                    state=state,
                    long_bias_source=long_bias_source,
                    action="short_blocked_flat",
                    current_qty=0.0,
                    blocked_target_qty=target_qty,
                    adverse_atr=0.0,
                )
                return 0.0, 0.0
            return target_qty, target_leverage

        current_signed_qty = position.direction * position.current_qty
        if position.direction < 0.0:
            if target_direction < 0.0 and self._config.intent_time_leverage_long_bias_block_short:
                reason_codes.extend(["TIME_LEVERAGE_LONG_BIAS_ACTIVE", "TIME_LEVERAGE_SHORT_BLOCKED"])
                self._publish_time_leverage_long_bias_event(
                    symbol=symbol,
                    timeframe=timeframe,
                    state=state,
                    long_bias_source=long_bias_source,
                    action="short_blocked_flat",
                    current_qty=current_signed_qty,
                    blocked_target_qty=target_qty,
                    adverse_atr=0.0,
                )
                return 0.0, 0.0
            return target_qty, target_leverage
        if position.direction == 0.0:
            return target_qty, target_leverage
        if target_direction > 0.0 and target_qty >= current_signed_qty:
            return target_qty, target_leverage
        if target_direction < 0.0 and not self._config.intent_time_leverage_long_bias_block_short:
            return target_qty, target_leverage
        if target_direction >= 0.0 and not self._config.intent_time_leverage_long_bias_hold_reductions:
            return target_qty, target_leverage

        held_qty = current_signed_qty
        held_leverage = abs(held_qty * price) / self._equity if price > 0.0 and self._equity > 0.0 else target_leverage
        adverse_atr = 0.0
        if atr > 0.0 and price > 0.0 and position.entry_price > price:
            adverse_atr = (position.entry_price - price) / atr
        action = "reduction_held"

        if target_direction < 0.0:
            reason_codes.append("TIME_LEVERAGE_SHORT_BLOCKED")
            action = "short_blocked"
        else:
            reason_codes.append("TIME_LEVERAGE_REDUCTION_HELD")
        reason_codes.append("TIME_LEVERAGE_LONG_BIAS_ACTIVE")

        dip_min = max(0.0, float(self._config.intent_time_leverage_dip_min_adverse_atr))
        dip_max = max(dip_min, float(self._config.intent_time_leverage_dip_max_adverse_atr))
        dip_add_pct = max(0.0, float(self._config.intent_time_leverage_dip_add_pct))
        if (
            self._config.intent_time_leverage_dip_accumulation_enabled
            and dip_add_pct > 0.0
            and dip_min <= adverse_atr <= dip_max
            and price > 0.0
            and self._equity > 0.0
        ):
            max_leverage = min(self._config.max_leverage, max(0.0, self._config.intent_time_leverage_dip_max_leverage))
            max_qty = (self._equity * max_leverage) / price
            desired_qty = position.current_qty * (1.0 + dip_add_pct)
            accumulated_qty = min(max_qty, desired_qty)
            if accumulated_qty > position.current_qty + self._config.min_trade_qty:
                held_qty = accumulated_qty
                held_leverage = abs(held_qty * price) / self._equity
                reason_codes.append("TIME_LEVERAGE_DIP_ACCUMULATION")
                action = "dip_accumulation"

        self._publish_time_leverage_long_bias_event(
            symbol=symbol,
            timeframe=timeframe,
            state=state,
            long_bias_source=long_bias_source,
            action=action,
            current_qty=current_signed_qty,
            blocked_target_qty=target_qty,
            adverse_atr=adverse_atr,
        )
        return held_qty, held_leverage

    def _publish_time_leverage_long_bias_event(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        state: MarketState,
        long_bias_source: str,
        action: str,
        current_qty: float,
        blocked_target_qty: float,
        adverse_atr: float,
    ) -> None:
        self._bus.publish(
            f"strategy.time_leverage_long_bias.{timeframe.value}.{symbol}",
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "cutoff_time_ns": state.cutoff_time_ns,
                "long_bias_source": long_bias_source,
                "action": action,
                "current_qty": round(current_qty, 6),
                "blocked_target_qty": round(blocked_target_qty, 6),
                "adverse_atr": round(adverse_atr, 6),
                "directional_state": state.directional_state,
                "directional_confidence": round(float(state.directional_confidence), 6),
                "transition_risk": round(float(state.transition_risk), 6),
                "quality_state": state.quality_state,
                "volatility_state": state.volatility_state,
            },
        )

    def _range_bear_profit_run_override_ready(
        self,
        *,
        pack: IndicatorPack,
        state: MarketState,
        playbook: PlaybookSelection,
    ) -> bool:
        if not self._config.intent_range_bear_profit_run_override_enabled:
            return False
        if playbook.playbook_id not in {"RANGE_LIGHT", "BEAR_DEFENSE"}:
            return False
        if state.quality_state == "UNSAFE" or state.volatility_state == "DISTORTED":
            return False
        if state.positioning_state == "CROWDED_SHORT":
            return False
        max_transition_risk = max(0.0, float(self._config.intent_range_bear_profit_run_max_transition_risk))
        if state.transition_risk > max_transition_risk:
            return False

        momentum_threshold = float(self._config.intent_range_bear_profit_run_momentum_threshold)
        momentum_roc_z = float(pack.values.get("momentum_roc_z", 0.0) or 0.0)
        multi_momentum_z = float(pack.values.get("multi_momentum_z", momentum_roc_z) or momentum_roc_z)
        structure_state = str(pack.values.get("structure_state") or "NEUTRAL")
        cycle_position = float(pack.values.get("cycle_position", 1.0) or 1.0)
        negative_momentum = momentum_roc_z <= momentum_threshold or multi_momentum_z <= momentum_threshold
        bearish_structure = structure_state == "DOWNTREND" or cycle_position <= 0.95
        direction_signal_ceiling = float(self._config.intent_range_bear_profit_run_direction_signal)
        confidence_floor = max(0.0, float(self._config.intent_range_bear_profit_run_confidence_floor))
        bearish_state = (
            state.directional_state == "DOWNTREND"
            or state.direction_signal <= direction_signal_ceiling
            or (
                cycle_position <= 0.95
                and state.directional_state != "UPTREND"
                and state.direction_signal <= max(0.0, direction_signal_ceiling)
                and state.directional_confidence >= confidence_floor * 0.5
            )
        )
        return negative_momentum and bearish_structure and bearish_state

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

        # ── Exit check ──
        if position is not None:
            position.bars_held += 1
            # Track peak unrealized PnL for profit_lock_exit
            last_price = float(pack.values.get("last_price", 0.0) or 0.0)
            if last_price > 0 and position.entry_price > 0:
                unrealized_pnl_pct = (last_price - position.entry_price) / position.entry_price * position.direction
                if unrealized_pnl_pct > position.peak_pnl_pct:
                    position.peak_pnl_pct = unrealized_pnl_pct

            if self._plugins and self._plugins.exits:
                # Build shared dicts once per bar (avoid 3x redundant construction)
                _ind_dict = dict(pack.values)
                _state_dict = self._state_dict(state, playbook)
                exit_decision = self._plugin_exit(pack, state, playbook, position, _ind_dict, _state_dict)
            else:
                exit_decision = self._exit_engine.check(
                    position,
                    state,
                    playbook,
                    indicators={"price": vals.price, "atr": vals.atr},
                )

            if exit_decision is not None:
                self._publish_exit(exit_decision)
                if exit_decision.should_exit():
                    self._arm_range_reentry_cooldown_if_needed(
                        symbol=pack.symbol,
                        timeframe=pack.timeframe,
                        cutoff_time_ns=pack.cutoff_time_ns,
                        playbook=playbook,
                        exit_decision=exit_decision,
                    )
                    return self._build_exit_target(
                        pack=pack,
                        position=position,
                        state=state,
                        exit_decision=exit_decision,
                        price=vals.price,
                    )
            else:
                # Plugin path returned None (no exit) — publish no-op for audit
                no_exit = self._exit_engine._no_exit(position, cutoff_time_ns=state.cutoff_time_ns)
                self._publish_exit(no_exit)

        # ── Direction + Sizing: plugin path vs legacy path ──
        if self._plugins and self._plugins.signals:
            # Plugin path — reuse shared dicts if already built by exit check
            if "_ind_dict" not in dir():
                _ind_dict = dict(pack.values)
                _state_dict = self._state_dict(state, playbook)
            direction, plugin_reason_codes = self._plugin_direction(pack, state, playbook, _ind_dict, _state_dict)
            reason_codes = [f"REGIME_{regime}", *playbook.reason_codes, *plugin_reason_codes]

            if playbook.is_reduce_only() and direction != 0.0:
                reason_codes.append("PLAYBOOK_DIRECTION_BLOCK")
                direction = 0.0

            if (
                direction == 0.0
                and playbook.playbook_id == "BEAR_DEFENSE"
                and state.direction_signal < 0.0
                and state.directional_confidence >= 0.5
            ):
                direction = -1.0
                reason_codes.append("STATE_BEARISH_DIRECTION_OVERRIDE")
            elif direction == 0.0 and self._range_bear_profit_run_override_ready(
                pack=pack,
                state=state,
                playbook=playbook,
            ):
                direction = -1.0
                reason_codes.append("STATE_RANGE_BEAR_PROFIT_RUN_OVERRIDE")

            if self._plugins.sizers:
                _state_dict = self._state_dict(state, playbook)
                target_leverage, sizer_codes = self._plugin_sizing(pack, state, playbook, _ind_dict, _state_dict)
                # Apply execution haircut based on data quality (parity with legacy path)
                execution_haircut = SizingEngine._execution_haircut(state)
                base_leverage = target_leverage
                target_leverage *= execution_haircut
                if execution_haircut < 1.0:
                    sizer_codes.append("SIZE_EXECUTION_HAIRCUT")
                reason_codes.extend(sizer_codes)
                # Publish a sizing decision for audit compatibility
                sizing = SizingDecision(
                    symbol=state.symbol,
                    timeframe=state.timeframe,
                    cutoff_time_ns=state.cutoff_time_ns,
                    base_size=base_leverage,
                    confidence_multiplier=1.0,
                    stress_discount=1.0,
                    execution_haircut=round(execution_haircut, 6),
                    final_leverage=target_leverage,
                    reason_codes=sizer_codes,
                )
            else:
                sizing = self._sizing_engine.compute(
                    state=state,
                    playbook=playbook,
                    equity=self._equity,
                    indicators={"realized_vol": vals.realized_vol, "funding_oi_z": vals.funding_oi_z},
                )
                target_leverage = sizing.final_leverage
            self._publish_sizing(sizing)

            reduce_only = playbook.is_reduce_only()
            if regime == "STRESS":
                target_leverage = min(target_leverage, playbook.max_leverage)
                reduce_only = True
        else:
            # Legacy path — unchanged
            direction = self._direction(
                regime=regime,
                cvd_slope=vals.cvd_slope,
                ob_liq_z=vals.ob_liq_z,
                structure_state=vals.structure_state,
                momentum_roc_z=vals.momentum_roc_z,
            )

            reason_codes = [f"REGIME_{regime}", *playbook.reason_codes]
            if pack.timeframe == self._config.execution_timeframe:
                bias, bias_reason = self._active_directional_bias(pack.symbol, pack.cutoff_time_ns)
                if bias_reason:
                    reason_codes.append(bias_reason)
                if bias is not None and bias.direction != 0.0:
                    if direction != 0.0 and direction * bias.direction < 0:
                        direction = 0.0
                        reason_codes.append("BIAS_DIRECTION_BLOCK")

            if playbook.is_reduce_only() and direction != 0.0:
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
        target_qty, target_leverage = self._apply_time_leverage_long_bias_guard(
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            target_qty=target_qty,
            target_leverage=target_leverage,
            price=vals.price,
            atr=vals.atr,
            state=state,
            reduce_only=reduce_only,
            reason_codes=reason_codes,
        )
        target_qty, target_leverage = self._apply_time_leverage_hold_guard(
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            target_qty=target_qty,
            target_leverage=target_leverage,
            price=vals.price,
            state=state,
            reason_codes=reason_codes,
        )
        target_qty, target_leverage = self._apply_time_leverage_size_hysteresis(
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            target_qty=target_qty,
            target_leverage=target_leverage,
            price=vals.price,
            state=state,
            reduce_only=reduce_only,
            reason_codes=reason_codes,
        )
        target_qty, target_leverage = self._apply_same_direction_reduce_confirmation(
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            target_qty=target_qty,
            target_leverage=target_leverage,
            price=vals.price,
            state=state,
            reduce_only=reduce_only,
            reason_codes=reason_codes,
        )
        target_qty, target_leverage = self._apply_same_direction_add_gate(
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            target_qty=target_qty,
            target_leverage=target_leverage,
            price=vals.price,
            reason_codes=reason_codes,
        )

        if direction > 0:
            reason_codes.append("TREND_LONG")
        elif direction < 0:
            reason_codes.append("TREND_SHORT")
        else:
            reason_codes.append("NO_DIRECTION")
        # Only append sizing reason_codes for the legacy path — the plugin path
        # already added them at line 294 above to avoid duplication (H4 fix).
        if not (self._plugins and self._plugins.signals):
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
            realized_vol=float(pack.values.get("realized_vol", 0) or 0),
            atr=float(pack.values.get("atr", 0) or 0),
        )

    def _build_execution_hold_or_exit(
        self,
        pack: IndicatorPack,
        state: MarketState,
        playbook: PlaybookSelection,
        position: OpenPositionState,
    ) -> TargetPosition | None:
        vals = self._extract_values(pack.values)
        position.bars_held += 1
        last_price = float(pack.values.get("last_price", 0.0) or 0.0)
        if last_price > 0 and position.entry_price > 0:
            unrealized_pnl_pct = (last_price - position.entry_price) / position.entry_price * position.direction
            if unrealized_pnl_pct > position.peak_pnl_pct:
                position.peak_pnl_pct = unrealized_pnl_pct

        if self._plugins and self._plugins.exits:
            ind_dict = dict(pack.values)
            state_dict = self._state_dict(state, playbook)
            exit_decision = self._plugin_exit(pack, state, playbook, position, ind_dict, state_dict)
        else:
            exit_decision = self._exit_engine.check(
                position,
                state,
                playbook,
                indicators={"price": vals.price, "atr": vals.atr},
            )

        if exit_decision is not None:
            self._publish_exit(exit_decision)
            if exit_decision.should_exit():
                hold_target = self._time_leverage_trailing_exit_hold_target(
                    pack=pack,
                    state=state,
                    position=position,
                    exit_decision=exit_decision,
                    price=vals.price,
                    atr=vals.atr,
                )
                if hold_target is not None:
                    return hold_target
                self._arm_range_reentry_cooldown_if_needed(
                    symbol=pack.symbol,
                    timeframe=pack.timeframe,
                    cutoff_time_ns=pack.cutoff_time_ns,
                    playbook=playbook,
                    exit_decision=exit_decision,
                )
                return self._build_exit_target(
                    pack=pack,
                    position=position,
                    state=state,
                    exit_decision=exit_decision,
                    price=vals.price,
                )
        else:
            self._publish_exit(self._exit_engine._no_exit(position, cutoff_time_ns=state.cutoff_time_ns))

        signed_qty = position.direction * position.current_qty
        target_leverage = 0.0
        if vals.price > 0 and self._equity > 0:
            target_leverage = abs(signed_qty * vals.price) / self._equity
        return TargetPosition(
            intent_id=self._intent_id(pack),
            portfolio=self._config.portfolio,
            symbol=pack.symbol,
            timeframe=pack.timeframe,
            cutoff_time_ns=pack.cutoff_time_ns,
            target_qty=round(signed_qty, 6),
            target_leverage=round(target_leverage, 4),
            reduce_only=False,
            regime=state.regime,
            regime_confidence=round(state.regime_confidence, 4),
            reason_codes=[f"REGIME_{state.regime}", "HOLD_EXECUTION_POSITION"],
            realized_vol=float(pack.values.get("realized_vol", 0) or 0),
            atr=float(pack.values.get("atr", 0) or 0),
        )

    def _active_directional_bias(self, symbol: str, cutoff_time_ns: int) -> tuple[DirectionalBias | None, str]:
        """Return the usable 1d bias for a 4h decision, or the reason it is unusable."""
        bias = self._directional_bias.get(symbol)
        if bias is None:
            return None, ""
        if any(flag != "OK" for flag in bias.quality_flags):
            return None, "BIAS_QUALITY_DEGRADED"
        if cutoff_time_ns < bias.cutoff_time_ns:
            return None, "BIAS_FUTURE"

        max_age_ns = self._config.bias_max_age_bars * self._config.execution_timeframe.seconds * 1_000_000_000
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

    def _publish_decision_snapshot_if_enabled(
        self,
        pack: IndicatorPack,
        state: MarketState,
        playbook: PlaybookSelection,
        target: TargetPosition,
    ) -> None:
        """Stage immutable V2 context until risk publishes its final target."""
        if not self._config.emit_decision_snapshots_v2:
            return
        if pack.timeframe != Timeframe.TF_4H or target.timeframe != Timeframe.TF_4H:
            return
        if not target.symbol.upper().startswith("BTC"):
            return
        plugin_context = self._plugin_signal_context.get((pack.symbol, pack.timeframe), {})
        if plugin_context.get("cutoff_time_ns") != pack.cutoff_time_ns:
            plugin_context = {}
        alpha_raw_score = float(plugin_context.get("effective_direction", state.direction_signal) or 0.0)
        alpha_direction = 1.0 if alpha_raw_score > 0.0 else -1.0 if alpha_raw_score < 0.0 else 0.0
        authority_key = (target.portfolio, target.symbol)
        latest_cutoff = self._latest_staged_decision_cutoff.get(authority_key)
        if latest_cutoff is not None and target.cutoff_time_ns < latest_cutoff:
            return
        key = (target.portfolio, target.symbol, target.cutoff_time_ns)
        existing = self._pending_decision_snapshots.get(key)
        if existing is not None and (
            existing.original_intent_id != target.intent_id
            or abs(existing.original_target_qty - target.target_qty) > 1e-9
        ):
            return
        if latest_cutoff is None or target.cutoff_time_ns > latest_cutoff:
            self._latest_staged_decision_cutoff[authority_key] = target.cutoff_time_ns
            stale_keys = [
                pending_key
                for pending_key in self._pending_decision_snapshots
                if pending_key[:2] == authority_key and pending_key[2] < target.cutoff_time_ns
            ]
            for stale_key in stale_keys:
                self._pending_decision_snapshots.pop(stale_key, None)
                self._risk_final_decisions.pop(stale_key, None)
        self._pending_decision_snapshots[key] = _DecisionSnapshotEnvelope(
            formula_hash=pack.formula_hash,
            config_hash=self._decision_snapshot_config_hash(),
            indicator_version=pack.indicator_version,
            data_identity=self._decision_snapshot_data_identity(pack),
            alpha_direction=alpha_direction,
            alpha_raw_score=alpha_raw_score,
            alpha_confidence=float(plugin_context.get("signal_confidence", state.directional_confidence) or 0.0),
            directional_state=state.directional_state,
            volatility_state=state.volatility_state,
            positioning_state=state.positioning_state,
            quality_state=state.quality_state,
            regime_risk_multiplier=1.0,
            annualized_volatility=target.realized_vol,
            volatility_target=self._config.target_vol_4h,
            exposure_cap=min(self._config.max_leverage, playbook.max_leverage),
            quality_flags=tuple(pack.quality_flags),
            options_state_id=None,
            portfolio=target.portfolio,
            timeframe=target.timeframe,
            original_target_qty=target.target_qty,
            original_intent_id=target.intent_id,
            original_target_hash=target_position_hash(target),
        )

    def _decision_snapshot_config_hash(self) -> str:
        """Fingerprint the active strategy configuration in a canonical form."""
        payload = json.dumps(
            asdict(self._config),
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _decision_snapshot_data_identity(pack: IndicatorPack) -> str:
        """Fingerprint the point-in-time 4h input data without wall-clock metadata."""
        payload = {
            "components": pack.components,
            "coverage": {
                "oi_coverage": pack.coverage.oi_coverage,
                "staleness_ms": pack.coverage.staleness_ms,
                "venue_count": pack.coverage.venue_count,
            },
            "cutoff_time_ns": pack.cutoff_time_ns,
            "formula_hash": pack.formula_hash,
            "indicator_version": pack.indicator_version,
            "quality_flags": pack.quality_flags,
            "symbol": pack.symbol,
            "timeframe": pack.timeframe.value,
            "values": pack.values,
        }
        canonical = json.dumps(
            to_jsonable(payload),
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

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
            remaining_qty = position.current_qty * max(0.0, 1.0 - exit_decision.target_reduction_pct)
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
            realized_vol=float(pack.values.get("realized_vol", 0) or 0),
            atr=float(pack.values.get("atr", 0) or 0),
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
            structure_state=str(values.get("structure_state") or values.get("swing_structure") or "NEUTRAL"),
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
        degraded = any(flag in {"BOOK_STALE", "VENUE_DOWN", "DATA_STALE", "DATA_FUTURE"} for flag in quality_flags)
        if degraded or realized_vol > 2.8:
            return "STRESS", 0.9

        trend_strength = min(1.0, abs(cvd_slope) * 10.0)
        pulse_strength = min(1.0, max(0.0, (zen_pulse - 50.0) / 50.0))

        if last_regime == "TREND":
            trend_gate = trend_strength > 0.12 and zen_pulse >= 50.0
        else:
            trend_gate = trend_strength > 0.22 and zen_pulse >= 58.0

        if trend_gate:
            confidence = min(1.0, 0.45 + (trend_strength * 0.4) + (pulse_strength * 0.15))
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

    def _plugin_direction(
        self,
        pack: IndicatorPack,
        state: MarketState,
        playbook: PlaybookSelection,
        ind_dict: dict[str, Any] | None = None,
        state_dict: dict[str, Any] | None = None,
    ) -> tuple[float, list[str]]:
        """Compute direction via plugin signal composition + filter chain."""
        assert self._plugins is not None

        if ind_dict is None:
            ind_dict = dict(pack.values)
        if state_dict is None:
            state_dict = self._state_dict(state, playbook)

        # Update rolling IC tracker only when research profile explicitly enables it.
        close = float(ind_dict.get("close", ind_dict.get("last_price", 0)) or 0)
        symbol = pack.symbol if hasattr(pack, "symbol") else ""
        if self._config.enable_adaptive_signal_weights:
            prev_close = self._last_close.get(symbol, 0)
            if prev_close > 0 and close > 0:
                self._forward_returns.append((close - prev_close) / prev_close)
            self._last_close[symbol] = close

        # Compute adaptive weights from rolling IC (every bar, lightweight)
        effective_weights = dict(self._plugins.signal_weights)
        if self._config.enable_adaptive_signal_weights and len(self._forward_returns) >= 60:
            import math

            rets = list(self._forward_returns)
            for sig_name, sig_deque in self._signal_values.items():
                vals = list(sig_deque)[-len(rets) :]
                if len(vals) < 60:
                    continue
                aligned = min(len(vals), len(rets))
                v, r = vals[-aligned:], rets[-aligned:]
                # Simplified rank correlation
                n = len(v)
                mv, mr2 = sum(v) / n, sum(r) / n
                cov = sum((v[i] - mv) * (r[i] - mr2) for i in range(n))
                sv = math.sqrt(sum((v[i] - mv) ** 2 for i in range(n)))
                sr = math.sqrt(sum((r[i] - mr2) ** 2 for i in range(n)))
                ic = cov / (sv * sr) if sv > 1e-12 and sr > 1e-12 else 0.0
                # Scale weight: IC>0 → full weight, IC<0 → reduce to 20%
                base_w = self._plugins.signal_weights.get(sig_name, 1.0)
                scale = max(0.2, min(1.0, 0.5 + ic * 5.0))
                effective_weights[sig_name] = base_w * scale
                reason_scale = round(scale, 4)
                if abs(reason_scale - 1.0) > 1e-6:
                    self._adaptive_weights[sig_name] = round(base_w * scale, 6)

        # Compose signals → weighted direction
        composed = compose_signals(
            self._plugins.signals,
            effective_weights,
            ind_dict,
            state_dict,
        )
        effective_direction = composed.direction * composed.state_confidence_haircut
        self._plugin_signal_context[(pack.symbol, pack.timeframe)] = {
            "cutoff_time_ns": pack.cutoff_time_ns,
            "state_confidence_haircut": composed.state_confidence_haircut,
            "execution_bias": composed.execution_bias,
            "effective_direction": effective_direction,
            "signal_confidence": composed.confidence,
            "role_contributions": composed.role_contributions,
            "adaptive_signal_weights_enabled": self._config.enable_adaptive_signal_weights,
            "effective_signal_weights": effective_weights,
        }
        reason_codes = list(composed.reason_codes)
        if self._config.enable_adaptive_signal_weights:
            reason_codes.append("ADAPTIVE_SIGNAL_WEIGHTS_ENABLED")

        # Track each signal's contribution for rolling IC
        for sig_name, contrib in composed.contributions.items():
            self._signal_values[sig_name].append(contrib)

        # Discretize to {-1, 0, 1} based on direction threshold
        direction_threshold = max(0.0, self._config.direction_signal_threshold)
        direction = 0.0
        if effective_direction > direction_threshold:
            direction = 1.0
        elif effective_direction < -direction_threshold:
            direction = -1.0
        if composed.state_confidence_haircut < 0.9999:
            reason_codes.append("STATE_CONFIDENCE_HAIRCUT")
        if abs(composed.execution_bias) > 1e-6:
            reason_codes.append("EXECUTION_MODIFIER_ACTIVE")

        # Apply 1D bias with quality/staleness checks (same as legacy path)
        if pack.timeframe == self._config.execution_timeframe:
            bias, bias_reason = self._active_directional_bias(pack.symbol, pack.cutoff_time_ns)
            if bias_reason:
                reason_codes.append(bias_reason)
            if bias is not None and bias.direction != 0.0:
                if direction != 0.0 and direction * bias.direction < 0:
                    direction = 0.0
                    reason_codes.append("BIAS_DIRECTION_BLOCK")

            # Dual-layer: 4H context constrains 1H execution
            ctx_bias = self._context_bias.get(pack.symbol)
            if ctx_bias is not None and ctx_bias.direction != 0.0:
                ctx_age_ns = pack.cutoff_time_ns - ctx_bias.cutoff_time_ns
                # 4H bias valid for ~6 bars of 1H (= 1 full 4H bar + buffer)
                if 0 < ctx_age_ns < 6 * Timeframe.TF_1H.seconds * 1_000_000_000:
                    if direction != 0.0 and direction * ctx_bias.direction < 0:
                        direction = 0.0
                        reason_codes.append("CTX_4H_DIRECTION_BLOCK")

        # Apply filters
        if direction != 0.0 and self._plugins.filters:
            # Inject signal direction into state for trend_filter
            state_dict["signal_direction"] = direction
            bias = self._directional_bias.get(pack.symbol)
            bias_usable, _ = self._active_directional_bias(pack.symbol, pack.cutoff_time_ns)
            state_dict["bias_direction"] = bias_usable.direction if bias_usable else 0.0

            verdict = compose_filters(self._plugins.filters, ind_dict, state_dict)
            if not verdict.passed:
                direction = 0.0
                reason_codes.extend(verdict.reason_codes)

        return direction, reason_codes

    def _plugin_sizing(
        self,
        pack: IndicatorPack,
        state: MarketState,
        playbook: PlaybookSelection,
        ind_dict: dict[str, Any] | None = None,
        state_dict: dict[str, Any] | None = None,
    ) -> tuple[float, list[str]]:
        """Compute leverage via plugin sizer chain."""
        assert self._plugins is not None

        if ind_dict is None:
            ind_dict = dict(pack.values)
        if state_dict is None:
            state_dict = self._state_dict(state, playbook)

        composed = compose_sizers(self._plugins.sizers, ind_dict, state_dict)
        execution_bias = float(state_dict.get("execution_bias", 0.0) or 0.0)
        leverage = min(playbook.max_leverage, composed.factor * max(0.75, 1.0 + execution_bias))
        return leverage, list(composed.reason_codes)

    def _plugin_exit(
        self,
        pack: IndicatorPack,
        state: MarketState,
        playbook: PlaybookSelection,
        position: OpenPositionState,
        ind_dict: dict[str, Any] | None = None,
        state_dict: dict[str, Any] | None = None,
    ) -> ExitDecision | None:
        """Check exits via plugin priority chain.

        The trailing stop ratchet is handled here (not in the plugin) because
        plugins are pure functions and cannot mutate position state.  This
        mirrors the legacy ExitEngine._check_trailing_stop() ratchet logic.
        """
        assert self._plugins is not None

        if ind_dict is None:
            ind_dict = dict(pack.values)
        price = float(ind_dict.get("last_price", 0.0) or 0.0)
        atr = float(ind_dict.get("atr", 0.0) or 0.0)

        # ── Trailing stop ratchet (engine responsibility) ──
        if price > 0 and atr > 0 and position.bars_held >= self._min_bars_before_trail(pack.timeframe):
            trailing_stop_atr_mult = self._time_leverage_trailing_stop_atr_mult(
                symbol=pack.symbol,
                timeframe=pack.timeframe,
                state=state,
                position=position,
                default_mult=playbook.trailing_stop_atr_mult,
            )
            stop_distance = atr * trailing_stop_atr_mult
            if position.trailing_stop == 0.0:
                # Initialize
                if position.direction > 0:
                    position.trailing_stop = position.entry_price - stop_distance
                else:
                    position.trailing_stop = position.entry_price + stop_distance
            else:
                # Advance (ratchet)
                if position.direction > 0:
                    new_stop = price - stop_distance
                    if new_stop > position.trailing_stop:
                        position.trailing_stop = new_stop
                else:
                    new_stop = price + stop_distance
                    if new_stop < position.trailing_stop:
                        position.trailing_stop = new_stop

        if state_dict is None:
            state_dict = self._state_dict(state, playbook)
        pos_dict = {
            "direction": position.direction,
            "entry_price": position.entry_price,
            "current_qty": position.current_qty,
            "bars_held": position.bars_held,
            "trailing_stop": position.trailing_stop,
            "pyramid_count": position.pyramid_count,
            "peak_pnl_pct": position.peak_pnl_pct,
            "unrealized_pnl_pct": (
                (price - position.entry_price) / max(position.entry_price, 1e-9) * position.direction
                if position.entry_price > 0
                else 0.0
            ),
        }

        result = compose_exits(self._plugins.exits, ind_dict, state_dict, pos_dict)
        if result is None:
            return None

        return ExitDecision(
            symbol=position.symbol,
            timeframe=position.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            exit_type=result.exit_type,
            exit_urgency=result.urgency,
            target_reduction_pct=result.reduction_pct,
            stop_price=result.stop_price,
            reason_code=result.exit_type,
            reason_detail=result.reason,
            reason_codes=result.reason_codes,
        )

    def _state_dict(self, state: MarketState, playbook: PlaybookSelection) -> dict[str, Any]:
        """Build the flat dict that plugins receive as ``state``."""
        # Compute position unrealized PnL for sizers (PaulWei pyramid pattern)
        pos_key = (state.symbol, state.timeframe)
        pos = self._positions.get(pos_key)
        plugin_ctx = self._plugin_signal_context.get((state.symbol, state.timeframe), {})
        position_unrealized_pnl = 0.0
        if pos is not None and abs(pos.current_qty) > 0:
            price = self._latest_packs.get(pos_key)
            if price:
                last_px = float(price.values.get("close", price.values.get("last_price", 0)) or 0)
                if last_px > 0 and pos.entry_price > 0:
                    position_unrealized_pnl = (last_px - pos.entry_price) * pos.current_qty

        return {
            "regime": state.regime,
            "directional_state": state.directional_state,
            "volatility_state": state.volatility_state,
            "positioning_state": state.positioning_state,
            "quality_state": state.quality_state,
            "direction_signal": state.direction_signal,
            "overall_confidence": state.overall_confidence(),
            "directional_confidence": state.directional_confidence,
            "volatility_confidence": state.volatility_confidence,
            "positioning_confidence": state.positioning_confidence,
            "quality_confidence": state.quality_confidence,
            "transition_risk": state.transition_risk,
            "playbook_id": playbook.playbook_id,
            "playbook_confidence": playbook.playbook_confidence,
            "max_leverage": playbook.max_leverage,
            "time_decay_bars": playbook.time_decay_bars,
            "timeframe": state.timeframe.value,
            "equity": self._equity,
            "position_unrealized_pnl": position_unrealized_pnl,
            "state_confidence_haircut": float(plugin_ctx.get("state_confidence_haircut", 1.0)),
            "execution_bias": float(plugin_ctx.get("execution_bias", 0.0)),
            "signal_role_contributions": dict(plugin_ctx.get("role_contributions", {})),
        }

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
