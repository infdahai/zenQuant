from __future__ import annotations

from dataclasses import dataclass, replace

from zenquant.domain import IndicatorPack, RiskOverlayDecision, RiskSnapshot, TargetPosition
from zenquant.infra import InMemoryJetStream


@dataclass(slots=True)
class RiskOverlayConfig:
    starting_equity: float = 100_000.0
    max_leverage: float = 2.5
    dd_step_1: float = 0.05
    dd_step_2: float = 0.1
    dd_step_3: float = 0.16
    vol_shock_threshold: float = 0.95
    crowding_threshold: float = 2.2
    severe_crowding_threshold: float = 3.0


class RiskOverlay:
    def __init__(self, bus: InMemoryJetStream, config: RiskOverlayConfig | None = None) -> None:
        self._bus = bus
        self._config = config or RiskOverlayConfig()

        self._equity = self._config.starting_equity
        self._high_water_mark = self._config.starting_equity
        self._last_price: dict[str, float] = {}
        self._position_proxy: dict[str, float] = {}
        self._latest_indicator: dict[str, IndicatorPack] = {}

        self._bus.subscribe("indicator.pack.v1.>", self._on_indicator)
        self._bus.subscribe("intent.target_position.>", self._on_target_position)

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def high_water_mark(self) -> float:
        return self._high_water_mark

    def _on_indicator(self, _: str, payload: object) -> None:
        if not isinstance(payload, IndicatorPack):
            return
        self._latest_indicator[payload.symbol] = payload
        last_price = float(payload.values.get("last_price", 0.0) or 0.0)
        if last_price > 0:
            self._last_price[payload.symbol] = last_price

    def _on_target_position(self, subject: str, payload: object) -> None:
        if not isinstance(payload, TargetPosition):
            return
        if ".final." in subject:
            return

        revised, decision, snapshot = self._apply_overlay(payload)
        self._position_proxy[revised.symbol] = revised.target_qty

        self._bus.publish("risk.overlay.decision", decision)
        self._bus.publish("risk.snapshot", snapshot)
        self._bus.publish(f"intent.target_position.final.{revised.symbol}", revised)

    def _apply_overlay(self, target: TargetPosition) -> tuple[TargetPosition, RiskOverlayDecision, RiskSnapshot]:
        indicator = self._latest_indicator.get(target.symbol)
        price = max(1e-9, self._last_price.get(target.symbol, 0.0))
        current_position = self._position_proxy.get(target.symbol, 0.0)

        flags: list[str] = []
        drawdown = self._drawdown()
        lev_cap = self._config.max_leverage
        if drawdown >= self._config.dd_step_3:
            lev_cap *= 0.2
            flags.append("DD_STEP_3")
        elif drawdown >= self._config.dd_step_2:
            lev_cap *= 0.4
            flags.append("DD_STEP_2")
        elif drawdown >= self._config.dd_step_1:
            lev_cap *= 0.65
            flags.append("DD_STEP_1")

        reduce_only = target.reduce_only

        if indicator is not None:
            realized_vol = float(indicator.values.get("realized_vol", 0.0) or 0.0)
            funding_oi_z = float(indicator.values.get("funding_oi_z", 0.0) or 0.0)
            if realized_vol >= self._config.vol_shock_threshold:
                lev_cap *= 0.4
                flags.append("VOL_SHOCK")
            if abs(funding_oi_z) >= self._config.crowding_threshold:
                lev_cap *= 0.7
                flags.append("CROWDING_EXTREME")
            if abs(funding_oi_z) >= self._config.severe_crowding_threshold:
                reduce_only = True
                flags.append("SEVERE_CROWDING")
            if any(flag in {"BOOK_STALE", "VENUE_DOWN", "DATA_STALE"} for flag in indicator.quality_flags):
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
        if fee <= 0:
            return
        self._equity = max(0.0, self._equity - fee)
        if self._equity > self._high_water_mark:
            self._high_water_mark = self._equity

    def _drawdown(self) -> float:
        if self._high_water_mark <= 0:
            return 0.0
        return max(0.0, (self._high_water_mark - self._equity) / self._high_water_mark)
