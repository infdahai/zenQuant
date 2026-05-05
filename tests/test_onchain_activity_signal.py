from zenquant.plugins.signals import onchain_activity_signal


def test_onchain_activity_neutral_without_features() -> None:
    result = onchain_activity_signal.compute({}, {}, {})

    assert result.value == 0.0
    assert result.confidence == 0.1


def test_onchain_activity_risk_on() -> None:
    result = onchain_activity_signal.compute(
        {
            "active_address_momentum_14d": 0.04,
            "supply_activity_regime": 0.03,
        },
        {},
        {},
    )

    assert result.value > 0.0
    assert "ONCHAIN_ACTIVITY_RISK_ON" in result.reason_codes


def test_onchain_activity_risk_off() -> None:
    result = onchain_activity_signal.compute(
        {
            "active_address_momentum_14d": -0.04,
            "supply_activity_regime": -0.03,
        },
        {},
        {},
    )

    assert result.value < 0.0
    assert "ONCHAIN_ACTIVITY_RISK_OFF" in result.reason_codes
