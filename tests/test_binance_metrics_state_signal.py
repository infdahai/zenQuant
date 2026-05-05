from zenquant.plugins.signals import binance_metrics_state_signal


def test_binance_metrics_signal_is_neutral_without_audited_factors() -> None:
    result = binance_metrics_state_signal.compute({}, {}, {})

    assert result.value == 0.0
    assert result.confidence == 0.1


def test_binance_metrics_signal_uses_negative_oi_growth_ic_sign() -> None:
    result = binance_metrics_state_signal.compute(
        {
            "binance_oi_value_growth_24h_z": 2.0,
            "binance_top_trader_account_lsr_z": 0.0,
            "binance_crowding_unwind_pressure": 0.0,
        },
        {},
        {"max_magnitude": 0.4},
    )

    assert result.value < 0.0
    assert "BINANCE_OI_VALUE_GROWTH" in result.reason_codes


def test_binance_metrics_signal_combines_positive_state_factors() -> None:
    result = binance_metrics_state_signal.compute(
        {
            "binance_oi_value_growth_24h_z": 0.0,
            "binance_top_trader_account_lsr_z": 1.2,
            "binance_crowding_unwind_pressure": 0.8,
        },
        {},
        {"max_magnitude": 0.4},
    )

    assert result.value > 0.0
    assert "BINANCE_TOP_ACCOUNT_LS" in result.reason_codes
    assert "BINANCE_CROWDING_UNWIND" in result.reason_codes
