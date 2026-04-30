from zenquant.plugins.filters import bear_defense_override_filter


def test_bear_defense_override_filter_blocks_noisy_range_transition_short() -> None:
    result = bear_defense_override_filter.compute(
        {},
        {
            "playbook_id": "BEAR_DEFENSE",
            "signal_direction": -1.0,
            "regime": "RANGE",
            "directional_state": "TRANSITION",
            "quality_state": "NOISY",
            "directional_confidence": 0.52,
            "transition_risk": 0.48,
        },
        {},
    )

    assert result.passed is False
    assert result.reason_codes == ("BEAR_DEFENSE_NOISY_TRANSITION_OVERRIDE_BLOCK",)


def test_bear_defense_override_filter_allows_true_downtrend_short() -> None:
    result = bear_defense_override_filter.compute(
        {},
        {
            "playbook_id": "BEAR_DEFENSE",
            "signal_direction": -1.0,
            "regime": "TREND",
            "directional_state": "DOWNTREND",
            "quality_state": "NOISY",
            "directional_confidence": 0.52,
            "transition_risk": 0.48,
        },
        {},
    )

    assert result.passed is True
    assert result.reason_codes == ("BEAR_DEFENSE_OVERRIDE_FILTER_PASS",)


def test_bear_defense_override_filter_allows_other_playbooks() -> None:
    result = bear_defense_override_filter.compute(
        {},
        {
            "playbook_id": "RANGE_LIGHT",
            "signal_direction": -1.0,
            "regime": "RANGE",
            "directional_state": "TRANSITION",
            "quality_state": "NOISY",
            "directional_confidence": 0.52,
            "transition_risk": 0.48,
        },
        {},
    )

    assert result.passed is True
    assert result.reason_codes == ("BEAR_DEFENSE_OVERRIDE_FILTER_OTHER_PLAYBOOK",)
