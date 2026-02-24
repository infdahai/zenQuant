from __future__ import annotations

from dataclasses import dataclass

from zenquant.domain import IndicatorPack, IntentReason, TargetPosition, Timeframe
from zenquant.infra import InMemoryJetStream


@dataclass(slots=True)
class ARCStrategyConfig:
    portfolio: str = "arc"
    account_equity: float = 100_000.0
    max_leverage: float = 3.0
    target_vol_4h: float = 0.025
    target_vol_1d: float = 0.04
    min_trade_qty: float = 0.0001


class ARCStrategyEngine:
    def __init__(self, bus: InMemoryJetStream, config: ARCStrategyConfig | None = None) -> None:
        self._bus = bus
        self._config = config or ARCStrategyConfig()
        self._bus.subscribe("indicator.pack.v1.>", self._on_indicator)

    def _on_indicator(self, _: str, payload: object) -> None:
        if not isinstance(payload, IndicatorPack):
            return

        target = self._build_target(payload)
        reason = self._build_reason(target)
        self._bus.publish(f"strategy.regime.{self._config.portfolio}", {"symbol": target.symbol, "regime": target.regime})
        self._bus.publish(f"intent.target_position.{self._config.portfolio}.{target.symbol}", target)
        self._bus.publish(f"intent.reason.{self._config.portfolio}", reason)

    def _build_target(self, pack: IndicatorPack) -> TargetPosition:
        values = pack.values
        realized_vol = float(values.get("realized_vol", 0.0) or 0.0)
        cvd_slope = float(values.get("cvd_slope", 0.0) or 0.0)
        funding_oi_z = float(values.get("funding_oi_z", 0.0) or 0.0)
        ob_liq_z = float(values.get("ob_liq_z", 0.0) or 0.0)
        zen_pulse = float(values.get("zen_pulse", 50.0) or 50.0)
        price = max(1e-9, float(values.get("last_price", 0.0) or 0.0))

        regime, regime_confidence = self._classify_regime(
            quality_flags=pack.quality_flags,
            realized_vol=realized_vol,
            cvd_slope=cvd_slope,
            zen_pulse=zen_pulse,
        )
        direction = self._direction(regime=regime, cvd_slope=cvd_slope, ob_liq_z=ob_liq_z)

        target_vol = self._config.target_vol_4h if pack.timeframe is Timeframe.TF_4H else self._config.target_vol_1d
        vol_budget = target_vol / max(realized_vol, target_vol * 0.25)
        crowding_discount = max(0.35, 1.0 - min(0.65, abs(funding_oi_z) / 4.0))

        target_leverage = min(self._config.max_leverage, vol_budget * regime_confidence * crowding_discount)
        reduce_only = False
        if regime == "STRESS":
            target_leverage = min(target_leverage, 0.35)
            reduce_only = True

        target_notional = self._config.account_equity * target_leverage * direction
        target_qty = target_notional / price
        if abs(target_qty) < self._config.min_trade_qty:
            target_qty = 0.0

        reason_codes = [f"REGIME_{regime}"]
        if direction > 0:
            reason_codes.append("TREND_LONG")
        elif direction < 0:
            reason_codes.append("TREND_SHORT")
        else:
            reason_codes.append("NO_DIRECTION")
        if abs(funding_oi_z) > 1.8:
            reason_codes.append("CROWDING_DISCOUNT")
        if pack.quality_flags != ["OK"]:
            reason_codes.append("QUALITY_DEGRADED")

        return TargetPosition(
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

    @staticmethod
    def _classify_regime(
        quality_flags: list[str],
        realized_vol: float,
        cvd_slope: float,
        zen_pulse: float,
    ) -> tuple[str, float]:
        degraded = any(flag in {"BOOK_STALE", "VENUE_DOWN", "DATA_STALE"} for flag in quality_flags)
        if degraded or realized_vol > 2.8:
            return "STRESS", 0.9

        trend_strength = min(1.0, abs(cvd_slope) * 10.0)
        pulse_strength = min(1.0, max(0.0, (zen_pulse - 50.0) / 50.0))
        if trend_strength > 0.18 and zen_pulse >= 55.0:
            confidence = min(1.0, 0.45 + (trend_strength * 0.4) + (pulse_strength * 0.15))
            return "TREND", confidence

        confidence = max(0.35, 1.0 - min(0.6, abs(cvd_slope) * 5.0))
        return "RANGE", confidence

    @staticmethod
    def _direction(regime: str, cvd_slope: float, ob_liq_z: float) -> float:
        if regime == "STRESS":
            return 0.0
        if regime == "TREND":
            if cvd_slope > 0:
                return 1.0
            if cvd_slope < 0:
                return -1.0
            return 0.0
        if abs(cvd_slope) < 0.03 and abs(ob_liq_z) > 1.2:
            return -1.0 if ob_liq_z > 0 else 1.0
        return 0.0

    @staticmethod
    def _build_reason(target: TargetPosition) -> IntentReason:
        summary = (
            f"regime={target.regime} conf={target.regime_confidence:.2f} "
            f"lev={target.target_leverage:.2f} qty={target.target_qty:.6f}"
        )
        return IntentReason(
            portfolio=target.portfolio,
            symbol=target.symbol,
            cutoff_time_ns=target.cutoff_time_ns,
            summary=summary,
            reason_codes=target.reason_codes,
        )
