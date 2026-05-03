"""Research-only sizing haircut from audited Binance metrics/OI factors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zenquant.plugins.protocols import PluginMeta, SizerResult


@dataclass(frozen=True, slots=True)
class Config:
    edge_threshold: float = 0.2
    edge_discount_scale: float = 0.25
    crowding_threshold: float = 1.0
    crowding_discount_scale: float = 0.08
    discount_floor: float = 0.65
    max_discount: float = 0.35


meta = PluginMeta(
    name="binance_metrics_risk_sizer",
    version="1.0.0",
    plugin_type="sizer",
    description="Discount leverage when audited Binance OI state conflicts with selected direction",
    config_schema=Config,
)


def compute(
    indicators: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
) -> SizerResult:
    oi_value_growth = float(indicators.get("binance_oi_value_growth_24h_z", 0.0) or 0.0)
    top_account_lsr = float(indicators.get("binance_top_trader_account_lsr_z", 0.0) or 0.0)
    unwind_pressure = float(indicators.get("binance_crowding_unwind_pressure", 0.0) or 0.0)
    account_divergence = float(indicators.get("binance_top_account_global_divergence", 0.0) or 0.0)
    oi_continuation = float(indicators.get("binance_oi_expansion_price_continuation", 0.0) or 0.0)
    taker_oi_exhaustion = float(indicators.get("binance_taker_oi_exhaustion", 0.0) or 0.0)
    unwind_vol_adjusted = float(indicators.get("binance_crowded_unwind_vol_adjusted", 0.0) or 0.0)
    signal_direction = float(state.get("signal_direction", 0.0) or 0.0)

    # Train-only audit signs:
    # - OI value growth had negative IC.
    # - top-account L/S, account/global divergence, OI continuation, taker/OI
    #   exhaustion, and vol-adjusted unwind pressure had positive IC.
    audited_edge = (
        (-0.45 * oi_value_growth)
        + (0.20 * top_account_lsr)
        + (0.30 * account_divergence)
        + (0.25 * oi_continuation)
        + (0.20 * taker_oi_exhaustion)
        + (0.20 * unwind_pressure)
        + (0.15 * unwind_vol_adjusted)
    )

    edge_threshold = float(config.get("edge_threshold", 0.2))
    edge_discount_scale = float(config.get("edge_discount_scale", 0.25))
    crowding_threshold = float(config.get("crowding_threshold", 1.0))
    crowding_discount_scale = float(config.get("crowding_discount_scale", 0.08))
    discount_floor = float(config.get("discount_floor", 0.65))
    max_discount = float(config.get("max_discount", 0.35))

    discount = 0.0
    codes: list[str] = []
    if signal_direction > 0.0 and audited_edge < -edge_threshold:
        discount += min(max_discount, abs(audited_edge) * edge_discount_scale)
        codes.append("BINANCE_METRICS_LONG_RISK")
    elif signal_direction < 0.0 and audited_edge > edge_threshold:
        discount += min(max_discount, abs(audited_edge) * edge_discount_scale)
        codes.append("BINANCE_METRICS_SHORT_RISK")

    crowding_stress = max(0.0, abs(oi_value_growth) - crowding_threshold)
    if crowding_stress > 0.0:
        discount += min(max_discount, crowding_stress * crowding_discount_scale)
        codes.append("BINANCE_METRICS_CROWDING_DISCOUNT")

    if not codes:
        return SizerResult(factor=1.0, reason_codes=())

    factor = max(discount_floor, 1.0 - min(max_discount, discount))
    return SizerResult(factor=round(factor, 6), reason_codes=tuple(codes))
