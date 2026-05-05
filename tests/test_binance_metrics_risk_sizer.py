from zenquant.plugins.sizers import binance_metrics_risk_sizer


def test_binance_metrics_risk_sizer_is_neutral_without_direction_or_factors() -> None:
    result = binance_metrics_risk_sizer.compute({}, {}, {})

    assert result.factor == 1.0
    assert result.reason_codes == ()


def test_binance_metrics_risk_sizer_discounts_long_when_audited_edge_is_bearish() -> None:
    result = binance_metrics_risk_sizer.compute(
        {
            "binance_oi_value_growth_24h_z": 2.0,
            "binance_top_trader_account_lsr_z": 0.0,
            "binance_crowding_unwind_pressure": 0.0,
        },
        {"signal_direction": 1.0},
        {},
    )

    assert result.factor < 1.0
    assert "BINANCE_METRICS_LONG_RISK" in result.reason_codes
    assert "BINANCE_METRICS_CROWDING_DISCOUNT" in result.reason_codes


def test_binance_metrics_risk_sizer_discounts_short_when_audited_edge_is_bullish() -> None:
    result = binance_metrics_risk_sizer.compute(
        {
            "binance_oi_value_growth_24h_z": 0.0,
            "binance_top_trader_account_lsr_z": 1.2,
            "binance_crowding_unwind_pressure": 0.8,
        },
        {"signal_direction": -1.0},
        {"discount_floor": 0.7},
    )

    assert 0.7 <= result.factor < 1.0
    assert "BINANCE_METRICS_SHORT_RISK" in result.reason_codes


def test_binance_metrics_risk_sizer_does_not_boost_supportive_direction() -> None:
    result = binance_metrics_risk_sizer.compute(
        {
            "binance_oi_value_growth_24h_z": 0.0,
            "binance_top_trader_account_lsr_z": 1.2,
            "binance_crowding_unwind_pressure": 0.8,
        },
        {"signal_direction": 1.0},
        {},
    )

    assert result.factor == 1.0
    assert result.reason_codes == ()


def test_binance_metrics_risk_sizer_uses_phase_interactions() -> None:
    result = binance_metrics_risk_sizer.compute(
        {
            "binance_oi_value_growth_24h_z": 0.0,
            "binance_top_trader_account_lsr_z": 0.0,
            "binance_crowding_unwind_pressure": 0.0,
            "binance_top_account_global_divergence": 1.0,
            "binance_oi_expansion_price_continuation": 1.0,
            "binance_taker_oi_exhaustion": 1.0,
            "binance_crowded_unwind_vol_adjusted": 1.0,
        },
        {"signal_direction": -1.0},
        {},
    )

    assert result.factor < 1.0
    assert "BINANCE_METRICS_SHORT_RISK" in result.reason_codes
