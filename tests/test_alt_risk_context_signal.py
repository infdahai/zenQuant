"""Tests for research-only alt risk context signal."""

from zenquant.plugins.signals import alt_risk_context_signal


def test_alt_risk_context_neutral_without_context() -> None:
    result = alt_risk_context_signal.compute({}, {}, {})

    assert result.value == 0.0
    assert result.confidence == 0.1


def test_alt_risk_context_flags_risk_on() -> None:
    result = alt_risk_context_signal.compute(
        {
            "alt_btc_spread_z": 2.0,
            "alt_momentum_7d": 0.08,
            "eth_momentum_7d": 0.03,
        },
        {},
        {"spread_z_threshold": 1.25, "momentum_threshold": 0.04},
    )

    assert result.value > 0.0
    assert result.confidence > 0.0
    assert "ALT_RISK_ON_CONTEXT" in result.reason_codes


def test_alt_risk_context_flags_risk_off() -> None:
    result = alt_risk_context_signal.compute(
        {
            "alt_btc_spread_z": -2.0,
            "alt_momentum_7d": -0.08,
            "eth_momentum_7d": -0.03,
        },
        {},
        {"spread_z_threshold": 1.25, "momentum_threshold": 0.04},
    )

    assert result.value < 0.0
    assert result.confidence > 0.0
    assert "ALT_RISK_OFF_CONTEXT" in result.reason_codes
