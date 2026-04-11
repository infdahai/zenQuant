"""Deterministic Phase 5 leverage sizing extracted from the strategy engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.domain import MarketState, PlaybookSelection, SizingDecision, Timeframe


@dataclass(slots=True)
class SizingEngineConfig:
    """Static sizing parameters shared across backtest, replay, and live."""

    target_vol_5m: float = 0.45
    target_vol_1h: float = 0.55
    target_vol_4h: float = 0.60
    target_vol_1d: float = 0.50
    min_realized_vol_ratio: float = 0.25
    crowding_reason_threshold: float = 1.8
    positioning_discount_max: float = 0.65
    positioning_discount_floor: float = 0.35
    volatility_distorted_multiple: float = 2.8
    volatility_high_multiple: float = 1.5
    volatility_distorted_discount: float = 0.75
    volatility_high_discount: float = 0.9


class SizingEngine:
    """Compute a leverage breakdown without coupling it to entry direction."""

    def __init__(self, config: SizingEngineConfig | None = None) -> None:
        self._config = config or SizingEngineConfig()

    def compute(
        self,
        state: MarketState,
        playbook: PlaybookSelection,
        equity: float,
        indicators: dict[str, Any],
    ) -> SizingDecision:
        """Return the full multiplicative sizing decision for one state snapshot."""
        target_vol = self._target_vol(state.timeframe)
        realized_vol = float(indicators.get("realized_vol", 0.0) or 0.0)
        funding_oi_z = float(indicators.get("funding_oi_z", 0.0) or 0.0)

        base_size = self._base_size(equity=equity, realized_vol=realized_vol, target_vol=target_vol)
        confidence_multiplier = self._confidence_multiplier(state, playbook)
        stress_discount = self._stress_discount(
            state=state,
            indicators={
                "realized_vol": realized_vol,
                "funding_oi_z": funding_oi_z,
                "target_vol": target_vol,
            },
        )
        execution_haircut = self._execution_haircut(state)

        unclamped = base_size * confidence_multiplier * stress_discount * execution_haircut
        final_leverage = min(playbook.max_leverage, unclamped)

        reason_codes = ["SIZE_BASE_VOL_TARGET", "SIZE_CONFIDENCE_BLEND"]
        if abs(funding_oi_z) > self._config.crowding_reason_threshold:
            reason_codes.append("CROWDING_DISCOUNT")
        if state.volatility_state == "DISTORTED":
            reason_codes.append("SIZE_VOLATILITY_STRESS")
        if state.quality_state in {"NOISY", "DEGRADED", "UNSAFE"}:
            reason_codes.append("QUALITY_DEGRADED")
        if execution_haircut < 1.0:
            reason_codes.append("SIZE_EXECUTION_HAIRCUT")
        if final_leverage < unclamped - 1e-9:
            reason_codes.append("SIZE_MAX_LEVERAGE_CAP")

        return SizingDecision(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            base_size=round(base_size, 6),
            confidence_multiplier=round(confidence_multiplier, 6),
            stress_discount=round(stress_discount, 6),
            execution_haircut=round(execution_haircut, 6),
            final_leverage=round(final_leverage, 4),
            reason_codes=reason_codes,
        )

    def _target_vol(self, timeframe: Timeframe) -> float:
        return {
            Timeframe.TF_5M: self._config.target_vol_5m,
            Timeframe.TF_1H: self._config.target_vol_1h,
            Timeframe.TF_4H: self._config.target_vol_4h,
            Timeframe.TF_1D: self._config.target_vol_1d,
        }.get(timeframe, self._config.target_vol_4h)

    def _base_size(self, equity: float, realized_vol: float, target_vol: float) -> float:
        """Vol-targeted leverage budget.

        ``equity`` is accepted to keep the contract stable for future
        extensions, but current output remains leverage-based rather than
        notional-based.
        """
        _ = equity
        realized_floor = max(realized_vol, target_vol * self._config.min_realized_vol_ratio)
        return target_vol / max(realized_floor, 1e-9)

    @staticmethod
    def _confidence_multiplier(state: MarketState, playbook: PlaybookSelection) -> float:
        state_conf = state.overall_confidence()
        playbook_conf = playbook.playbook_confidence
        return max(0.0, min(1.0, (state_conf + playbook_conf) / 2.0))

    def _stress_discount(self, state: MarketState, indicators: dict[str, float]) -> float:
        """Combine crowding, volatility, and quality discounts into one factor."""
        funding_oi_z = abs(float(indicators.get("funding_oi_z", 0.0) or 0.0))
        realized_vol = float(indicators.get("realized_vol", 0.0) or 0.0)
        target_vol = max(float(indicators.get("target_vol", self._target_vol(state.timeframe)) or 0.0), 1e-9)

        positioning_discount = max(
            self._config.positioning_discount_floor,
            1.0 - min(self._config.positioning_discount_max, funding_oi_z / 4.0),
        )
        if (
            realized_vol > target_vol * self._config.volatility_distorted_multiple
            or state.volatility_state == "DISTORTED"
        ):
            volatility_discount = self._config.volatility_distorted_discount
        elif realized_vol > target_vol * self._config.volatility_high_multiple or state.volatility_state == "HIGH":
            volatility_discount = self._config.volatility_high_discount
        else:
            volatility_discount = 1.0

        quality_discount = {
            "TRADABLE": 1.0,
            "NOISY": 1.0,
            "DEGRADED": 0.9,
            "UNSAFE": 0.0,
        }.get(state.quality_state, 0.9)
        return max(0.0, positioning_discount * volatility_discount * quality_discount)

    @staticmethod
    def _execution_haircut(state: MarketState) -> float:
        return {
            "TRADABLE": 1.0,
            "NOISY": 0.9,
            "DEGRADED": 0.7,
            "UNSAFE": 0.0,
        }.get(state.quality_state, 0.7)
