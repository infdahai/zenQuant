from zenquant.plugins.sizers import bear_defense_churn_sizer


def test_bear_defense_churn_sizer_is_neutral_outside_bear_defense() -> None:
    result = bear_defense_churn_sizer.compute(
        {},
        {
            "playbook_id": "RANGE_LIGHT",
            "signal_direction": -1.0,
            "directional_confidence": 0.4,
            "transition_risk": 0.8,
        },
        {},
    )

    assert result.factor == 1.0
    assert result.reason_codes == ()


def test_bear_defense_churn_sizer_is_neutral_without_short_direction() -> None:
    result = bear_defense_churn_sizer.compute(
        {},
        {
            "playbook_id": "BEAR_DEFENSE",
            "signal_direction": 0.0,
            "directional_confidence": 0.4,
            "transition_risk": 0.8,
        },
        {},
    )

    assert result.factor == 1.0
    assert result.reason_codes == ()


def test_bear_defense_churn_sizer_discounts_only_weak_transition_short() -> None:
    result = bear_defense_churn_sizer.compute(
        {},
        {
            "playbook_id": "BEAR_DEFENSE",
            "signal_direction": -1.0,
            "directional_confidence": 0.51,
            "transition_risk": 0.48,
            "volatility_state": "HIGH",
            "quality_state": "NOISY",
        },
        {},
    )

    assert 0.55 <= result.factor < 1.0
    assert result.reason_codes == ("BEAR_DEFENSE_WEAK_TRANSITION_CHURN_HAIRCUT",)


def test_bear_defense_churn_sizer_keeps_strong_transition_short() -> None:
    result = bear_defense_churn_sizer.compute(
        {},
        {
            "playbook_id": "BEAR_DEFENSE",
            "signal_direction": -1.0,
            "directional_confidence": 0.65,
            "transition_risk": 0.55,
            "volatility_state": "HIGH",
            "quality_state": "NOISY",
        },
        {},
    )

    assert result.factor == 1.0
    assert result.reason_codes == ()


def test_bear_defense_churn_sizer_respects_discount_floor() -> None:
    result = bear_defense_churn_sizer.compute(
        {},
        {
            "playbook_id": "BEAR_DEFENSE",
            "signal_direction": -1.0,
            "directional_confidence": 0.1,
            "transition_risk": 0.9,
            "volatility_state": "HIGH",
            "quality_state": "NOISY",
        },
        {
            "weak_transition_discount": 0.4,
            "discount_floor": 0.6,
        },
    )

    assert result.factor == 0.6
