"""Risk overlay that can rewrite strategy intent before execution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from zenquant.data.event_calendar import is_macro_event_bar
from zenquant.domain import (
    ExecutionReport,
    IndicatorPack,
    RiskOverlayDecision,
    RiskSnapshot,
    TargetPosition,
    Timeframe,
)
from zenquant.infra import EventBus


@dataclass(slots=True)
class RiskOverlayConfig:
    """Risk thresholds controlling leverage throttles and kill behavior."""

    portfolio: str = "arc"
    starting_equity: float = 100_000.0
    # PaulWei-calibrated: asymmetric DD tolerance (long vs short)
    # M2 monetary expansion → BTC long-term upward → longs recover, shorts don't
    max_leverage: float = 6.0
    # Long DD steps: patient (fiat debasement will bail out)
    dd_step_1_long: float = 0.15
    dd_step_2_long: float = 0.22
    dd_step_3_long: float = 0.28
    # Short DD steps: strict (unlimited risk in appreciating asset)
    dd_step_1_short: float = 0.08
    dd_step_2_short: float = 0.14
    dd_step_3_short: float = 0.20
    # DD step leverage multipliers (how much to reduce at each step)
    dd_step_1_mult: float = 0.65
    dd_step_2_mult: float = 0.40
    dd_step_3_mult: float = 0.20
    vol_shock_threshold: float = 0.95
    vol_shock_mult: float = 0.40
    crowding_threshold: float = 2.2
    crowding_mult: float = 0.70
    severe_crowding_threshold: float = 3.0
    daily_loss_limit: float = 0.10
    daily_safe_lev_cap: float = 0.50
    kill_switch_drawdown: float = 0.30
    # Strategy degradation detection
    degradation_sharpe_threshold: float = -2.0
    degradation_min_bars: int = 24
    degradation_mult: float = 0.40
    # Kill switch recovery leverage
    kill_recovery_mult: float = 0.30
    # Equity risk thresholds
    equity_risk_off_threshold: float = -2.0
    equity_risk_off_mult: float = 0.50
    equity_stress_threshold: float = -1.5
    equity_stress_mult: float = 0.80
    # COT crowding
    cot_crowding_threshold: float = 2.5
    cot_crowding_mult: float = 0.70
    # Overheated market
    overheated_cycle_threshold: float = 1.3
    overheated_funding_threshold: float = 1.5
    overheated_mult: float = 0.40
    high_cycle_mult: float = 0.70
    # GEX regime (gamma exposure from Deribit options)
    gex_pinning_mult: float = 1.15  # PINNING = vol compressed, can hold slightly more
    gex_amplifying_mult: float = 0.70  # AMPLIFYING = vol will expand, reduce exposure
    # Spot-perp basis extreme (premium too high = crowded longs)
    basis_extreme_threshold: float = 2.0  # z-score
    basis_extreme_mult: float = 0.80
    # Basis discount: perp trading below spot = short squeeze risk
    basis_discount_threshold: float = -2.0  # z-score
    basis_discount_mult: float = 0.80
    # VPIN toxic flow (Easley, Lopez de Prado & O'Hara 2012)
    vpin_toxic_threshold: float = 0.10  # p95 = 0.10, above = toxic flow
    vpin_toxic_mult: float = 0.70
    # Weekend vol adjustment (Hansen et al. 2021, verified in our data)
    # Sat=0.69x, Sun=0.83x vs weekday avg — weekend positions can be slightly larger
    # (lower vol = same risk at larger position)
    weekend_vol_scalar: float = 1.25  # inverse of 0.80 avg weekend/weekday ratio
    # Macro event calendar (FOMC/CPI) — reduce leverage during high-vol events
    macro_event_mult: float = 0.75  # Reduce 25% during FOMC/CPI bars
    # Kill switch recovery (Claude Code "fallback model" pattern)
    kill_recovery_threshold: float = 0.12  # DD must recover below this to start recovery
    kill_cooldown_bars: int = 36  # 36 bars = 6 days at 4H (cooldown before re-entry)
    kill_ramp_bars: int = 24  # 24 bars = 4 days to ramp from 0.2x to 1.0x leverage


class RiskOverlay:
    """Track actual portfolio state and enforce live-safe exposure caps.

    The overlay listens to indicator updates, intended positions, and filled
    executions. That keeps the risk decision anchored to actual portfolio
    state rather than theoretical strategy targets.
    """

    def __init__(self, bus: EventBus, config: RiskOverlayConfig | None = None) -> None:
        self._bus = bus
        self._config = config or RiskOverlayConfig()

        self._cash = self._config.starting_equity
        self._equity = self._config.starting_equity
        self._high_water_mark = self._config.starting_equity
        self._last_price: dict[str, float] = {}
        self._actual_positions: dict[str, float] = {}
        self._latest_indicator: dict[tuple[str, Timeframe], IndicatorPack] = {}
        self._day_anchor: tuple[int, float] | None = None
        # Kill switch recovery state
        self._kill_active = False
        self._kill_bar_count = 0  # Bars since kill switch first triggered
        self._recovery_bar_count = 0  # Bars since DD dropped below recovery threshold
        # Level 3 AI-native: strategy self-awareness (equity curve anomaly detection)
        from collections import deque

        self._equity_history: deque[float] = deque(maxlen=120)
        self._degraded_mode_bars = 0

        self._bus.subscribe("indicator.>", self._on_indicator)
        self._bus.subscribe("intent.target_position.>", self._on_target_position)
        self._bus.subscribe("exec.order.report", self._on_execution_report)

    @property
    def equity(self) -> float:
        """Current portfolio equity after mark-to-market revaluation."""
        return self._equity

    @property
    def high_water_mark(self) -> float:
        """Peak equity observed since process start."""
        return self._high_water_mark

    def _on_indicator(self, _: str, payload: object) -> None:
        if not isinstance(payload, IndicatorPack):
            return
        self.prime_indicator(payload)

    def prime_indicator(self, pack: IndicatorPack) -> None:
        """Seed indicator and price context without requiring a bus publish."""
        self._latest_indicator[(pack.symbol, pack.timeframe)] = pack
        last_price = float(pack.values.get("last_price", 0.0) or 0.0)
        if last_price > 0:
            self._last_price[pack.symbol] = last_price
            self._revalue_equity()

    def _on_execution_report(self, _: str, payload: object) -> None:
        if not isinstance(payload, ExecutionReport):
            return
        if payload.status != "FILLED" or payload.filled_qty <= 0:
            return

        current = self._actual_positions.get(payload.symbol, 0.0)
        self._actual_positions[payload.symbol] = current + (payload.side * payload.filled_qty)
        if payload.avg_price > 0:
            self._last_price[payload.symbol] = payload.avg_price

        self._cash -= payload.side * payload.filled_qty * payload.avg_price
        self._revalue_equity()

    def _on_target_position(self, subject: str, payload: object) -> None:
        if not isinstance(payload, TargetPosition):
            return
        if ".final." in subject:
            return

        revised, decision, snapshot = self._apply_overlay(payload)

        self._bus.publish("risk.overlay.decision", decision)
        self._bus.publish("risk.snapshot", snapshot)
        self._bus.publish(f"intent.target_position.final.{revised.symbol}", revised)

    def _apply_overlay(self, target: TargetPosition) -> tuple[TargetPosition, RiskOverlayDecision, RiskSnapshot]:
        """Rewrite a target using drawdown, volatility, crowding, and data quality."""
        self._revalue_equity()

        indicator = self._latest_indicator.get((target.symbol, target.timeframe))
        price = max(1e-9, self._last_price.get(target.symbol, 0.0))
        current_position = self._actual_positions.get(target.symbol, 0.0)

        flags: list[str] = []
        drawdown = self._drawdown()
        lev_cap = self._config.max_leverage

        # Level 3: Strategy self-awareness — detect equity curve degradation
        self._equity_history.append(self._equity)
        if len(self._equity_history) >= 60:
            recent = list(self._equity_history)
            # Rolling return over last 60 bars
            returns_60 = [(recent[i] - recent[i - 1]) / recent[i - 1]
                          for i in range(1, len(recent)) if recent[i - 1] > 0]
            if returns_60:
                import math

                avg_ret = sum(returns_60) / len(returns_60)
                var = sum((r - avg_ret) ** 2 for r in returns_60) / len(returns_60)
                rolling_sharpe = (avg_ret / math.sqrt(var)) * math.sqrt(len(returns_60)) if var > 1e-15 else 0
                if rolling_sharpe < self._config.degradation_sharpe_threshold:
                    self._degraded_mode_bars += 1
                    if self._degraded_mode_bars >= self._config.degradation_min_bars:
                        lev_cap *= self._config.degradation_mult
                        flags.append("STRATEGY_DEGRADED")
                else:
                    self._degraded_mode_bars = max(0, self._degraded_mode_bars - 1)
        # Asymmetric DD stepping: longs get patience, shorts get discipline
        # PaulWei: "暂时被套 → 等待 (M2 bail out)" for longs
        is_net_long = target.target_qty > 0
        dd_1 = self._config.dd_step_1_long if is_net_long else self._config.dd_step_1_short
        dd_2 = self._config.dd_step_2_long if is_net_long else self._config.dd_step_2_short
        dd_3 = self._config.dd_step_3_long if is_net_long else self._config.dd_step_3_short

        if drawdown >= dd_3:
            lev_cap *= self._config.dd_step_3_mult
            flags.append("DD_STEP_3")
        elif drawdown >= dd_2:
            lev_cap *= self._config.dd_step_2_mult
            flags.append("DD_STEP_2")
        elif drawdown >= dd_1:
            lev_cap *= self._config.dd_step_1_mult
            flags.append("DD_STEP_1")

        # Kill switch with recovery (Claude Code "fallback model" pattern)
        if drawdown >= self._config.kill_switch_drawdown:
            if not self._kill_active:
                self._kill_active = True
                self._kill_bar_count = 0
            self._kill_bar_count += 1
            # After cooldown, reset HWM and recover (break the deadlock)
            if self._kill_bar_count >= self._config.kill_cooldown_bars:
                self._high_water_mark = self._equity  # Fresh start
                self._kill_active = False
                self._kill_bar_count = 0
                lev_cap *= self._config.kill_recovery_mult
                flags.append("KILL_RECOVERED")
            else:
                flags.append("KILL_SWITCH")

        daily_loss = self._daily_loss(target.cutoff_time_ns)
        if daily_loss >= self._config.daily_loss_limit:
            lev_cap = min(lev_cap, self._config.daily_safe_lev_cap)
            flags.append("DAILY_LOSS_LIMIT")

        reduce_only = target.reduce_only or ("KILL_SWITCH" in flags)

        if indicator is not None:
            realized_vol = float(indicator.values.get("realized_vol", 0.0) or 0.0)
            funding_oi_z = float(indicator.values.get("funding_oi_z", 0.0) or 0.0)
            if realized_vol >= self._config.vol_shock_threshold:
                lev_cap *= self._config.vol_shock_mult
                flags.append("VOL_SHOCK")
            if abs(funding_oi_z) >= self._config.crowding_threshold:
                lev_cap *= self._config.crowding_mult
                flags.append("CROWDING_EXTREME")
            if abs(funding_oi_z) >= self._config.severe_crowding_threshold:
                reduce_only = True
                flags.append("SEVERE_CROWDING")

            # Equity market risk: when QQQ+IWM both crash → reduce BTC leverage
            # IC direction unstable but |IC|>0.05 → use as risk sizing, not direction
            qqq_z = float(indicator.values.get("qqq_z", 0.0) or 0.0)
            iwm_z = float(indicator.values.get("iwm_z", 0.0) or 0.0)
            if qqq_z < self._config.equity_risk_off_threshold and iwm_z < self._config.equity_risk_off_threshold:
                lev_cap *= self._config.equity_risk_off_mult
                flags.append("EQUITY_RISK_OFF")
            elif qqq_z < self._config.equity_stress_threshold or iwm_z < self._config.equity_stress_threshold:
                lev_cap *= self._config.equity_stress_mult
                flags.append("EQUITY_STRESS")

            # COT institutional crowding: only reduce when our position aligns with
            # extreme crowd positioning (contrarian — reduce when everyone agrees)
            cot_leveraged_z = float(indicator.values.get("cot_leveraged_z", 0.0) or 0.0)
            if (target.target_qty > 0 and cot_leveraged_z > self._config.cot_crowding_threshold) or (target.target_qty < 0 and cot_leveraged_z < -self._config.cot_crowding_threshold):
                lev_cap *= self._config.cot_crowding_mult
                flags.append("COT_CROWDING")

            # High position sizing: cycle_position + funding as "overheated" detector
            # PaulWei pattern: reduce size at highs, not stop trading
            # VPIN toxic flow: high informed trading probability → reduce exposure
            vpin = float(indicator.values.get("vpin", 0.0) or 0.0)
            if vpin > self._config.vpin_toxic_threshold:
                lev_cap *= self._config.vpin_toxic_mult
                flags.append("VPIN_TOXIC")

            # Spot-perp basis: extreme premium = crowded, reduce long exposure
            basis_z = float(indicator.values.get("basis_z", 0.0) or 0.0)
            if target.target_qty > 0 and basis_z > self._config.basis_extreme_threshold:
                lev_cap *= self._config.basis_extreme_mult
                flags.append("BASIS_PREMIUM_EXTREME")
            elif target.target_qty < 0 and basis_z < self._config.basis_discount_threshold:
                lev_cap *= self._config.basis_discount_mult
                flags.append("BASIS_DISCOUNT_EXTREME")

            # GEX regime: gamma exposure determines vol regime
            gex_regime = str(indicator.values.get("gex_regime", "") or "")
            if gex_regime == "PINNING":
                lev_cap *= self._config.gex_pinning_mult
                flags.append("GEX_PINNING")
            elif gex_regime == "AMPLIFYING":
                lev_cap *= self._config.gex_amplifying_mult
                flags.append("GEX_AMPLIFYING")

            # Weekend vol adjustment: lower vol = can hold slightly larger position
            bar_dt = datetime.fromtimestamp(target.cutoff_time_ns / 1e9, tz=UTC)
            if bar_dt.weekday() >= 5:  # Saturday=5, Sunday=6
                lev_cap *= self._config.weekend_vol_scalar
                flags.append("WEEKEND_VOL_ADJUST")

            # Macro event calendar: reduce leverage during FOMC/CPI bars
            if is_macro_event_bar(target.cutoff_time_ns):
                lev_cap *= self._config.macro_event_mult
                flags.append("MACRO_EVENT")

            cycle_pos = float(indicator.values.get("cycle_position", 1.0) or 1.0)
            if cycle_pos > self._config.overheated_cycle_threshold and funding_oi_z > self._config.overheated_funding_threshold:
                lev_cap *= self._config.overheated_mult
                flags.append("OVERHEATED_SIZING")
            elif cycle_pos > self._config.overheated_cycle_threshold:
                lev_cap *= self._config.high_cycle_mult
                flags.append("HIGH_CYCLE_SIZING")
            if any(
                flag in {"BOOK_STALE", "VENUE_DOWN", "DATA_STALE", "DATA_FUTURE"} for flag in indicator.quality_flags
            ):
                reduce_only = True
                lev_cap = min(lev_cap, 0.45)
                flags.append("LIQUIDITY_STRESS")

        revised_qty = target.target_qty
        if price > 0:
            max_abs_qty = (self._equity * lev_cap) / price
            revised_qty = max(-max_abs_qty, min(max_abs_qty, revised_qty))

        if reduce_only and abs(revised_qty) > abs(current_position):
            revised_qty = current_position
            flags.append("REDUCE_ONLY_BLOCK_ADD")

        revised = replace(
            target,
            target_qty=round(revised_qty, 6),
            target_leverage=round(min(target.target_leverage, lev_cap), 4),
            reduce_only=reduce_only,
            reason_codes=sorted(set(target.reason_codes + flags)),
        )

        decision = RiskOverlayDecision(
            symbol=target.symbol,
            cutoff_time_ns=target.cutoff_time_ns,
            original_target_qty=target.target_qty,
            revised_target_qty=revised.target_qty,
            lev_cap=round(lev_cap, 4),
            reduce_only=reduce_only,
            flags=sorted(set(flags)) or ["OK"],
        )

        snapshot = RiskSnapshot(
            portfolio=self._config.portfolio,
            ts_ns=target.cutoff_time_ns,
            equity=round(self._equity, 2),
            high_water_mark=round(self._high_water_mark, 2),
            drawdown=round(drawdown, 6),
            lev_cap=round(lev_cap, 4),
            reduce_only_mode=reduce_only,
            flags=decision.flags,
        )
        return revised, decision, snapshot

    def apply_fee(self, fee: float) -> None:
        """Apply fees after fills so equity stays aligned with execution."""
        if fee <= 0:
            return
        self._cash = max(0.0, self._cash - fee)
        self._revalue_equity()

    def _revalue_equity(self) -> None:
        """Mark cash plus actual filled positions to the latest known prices."""
        equity = self._cash
        for symbol, qty in self._actual_positions.items():
            price = self._last_price.get(symbol)
            if price is None:
                continue
            equity += qty * price
        self._equity = equity
        if self._equity > self._high_water_mark:
            self._high_water_mark = self._equity

    def _drawdown(self) -> float:
        """Return drawdown as a fraction of the current high-water mark."""
        if self._high_water_mark <= 0:
            return 0.0
        return max(0.0, (self._high_water_mark - self._equity) / self._high_water_mark)

    def _daily_loss(self, cutoff_time_ns: int) -> float:
        """Measure loss since the current UTC trading-day anchor."""
        # NOTE (MVP): This resets on process restart because anchor is in-memory.
        day_id = datetime.fromtimestamp(cutoff_time_ns / 1_000_000_000, tz=UTC).toordinal()
        if self._day_anchor is None or self._day_anchor[0] != day_id:
            self._day_anchor = (day_id, self._equity)
            return 0.0

        start_equity = self._day_anchor[1]
        if start_equity <= 0:
            return 0.0
        return max(0.0, (start_equity - self._equity) / start_equity)
