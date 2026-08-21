"""Contract tests for the fixed-notional A0/A1 attribution experiment."""

from __future__ import annotations

from dataclasses import replace

import pytest

from zenquant.domain import TargetPosition, Timeframe
from zenquant.historical.replay import HistoricalBacktest
from zenquant.research.architecture_experiment import (
    ArchitectureObservation,
    ArchitectureStage,
    build_architecture_attribution_report,
    stage_exposure,
)
from zenquant.strategy.decision_snapshot import build_decision_snapshot


def _observation(
    *,
    decision_id: str = "decision-1",
    cutoff_time_ns: int = 100,
    window: str = "dev",
    seed: int = 42,
    regime: str = "TREND",
    baseline_exposure: float = 0.25,
    raw_score: float = -0.4,
    frozen_confidence: float = 0.6,
    forward_return: float | None = 0.1,
) -> ArchitectureObservation:
    return ArchitectureObservation(
        decision_id=decision_id,
        cutoff_time_ns=cutoff_time_ns,
        window=window,
        seed=seed,
        regime=regime,
        baseline_exposure=baseline_exposure,
        raw_score=raw_score,
        frozen_confidence=frozen_confidence,
        forward_return=forward_return,
    )


def _snapshot(
    *,
    cutoff_time_ns: int,
    target_qty: float,
    raw_score: float,
    confidence: float,
    target_leverage: float = 0.5,
):
    target = TargetPosition(
        intent_id="ignored-intent-id",
        portfolio="arc",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=cutoff_time_ns,
        target_qty=target_qty,
        target_leverage=target_leverage,
        reduce_only=False,
        regime="TREND",
        regime_confidence=0.8,
        reason_codes=["TREND_LONG"],
        realized_vol=0.42,
        atr=250.0,
    )
    return build_decision_snapshot(
        target=target,
        formula_hash="formula-v1",
        config_hash="config-v1",
        indicator_version="pack.v1",
        data_identity=f"data-{cutoff_time_ns}",
        alpha_direction=1.0 if raw_score > 0 else -1.0 if raw_score < 0 else 0.0,
        alpha_raw_score=raw_score,
        alpha_confidence=confidence,
        directional_state="UPTREND",
        volatility_state="DISTORTED",
        positioning_state="CROWDED_LONG",
        quality_state="TRADABLE",
        regime_risk_multiplier=0.1,
        annualized_volatility=2.5,
        volatility_target=0.2,
        exposure_cap=6.0,
        quality_flags=["OPTIONS_STALE"],
        options_state_id="options-shadow-must-not-enter-a1",
    )


def test_stage_transforms_isolate_direction_and_frozen_confidence():
    """Catches A1 consuming baseline sizing, regime, or later-stage state."""
    observation = _observation()
    unrelated_state_changed = replace(
        observation,
        baseline_exposure=-0.9,
        regime="STRESS",
    )

    assert stage_exposure(observation, ArchitectureStage.A0) == pytest.approx(0.25)
    assert stage_exposure(observation, ArchitectureStage.A1_SIGN) == pytest.approx(-1.0)
    assert stage_exposure(observation, ArchitectureStage.A1_SCORE) == pytest.approx(-0.6)
    assert stage_exposure(unrelated_state_changed, ArchitectureStage.A1_SIGN) == pytest.approx(-1.0)
    assert stage_exposure(unrelated_state_changed, ArchitectureStage.A1_SCORE) == pytest.approx(-0.6)


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_stage_transforms_reject_confidence_outside_one_x_contract(confidence):
    """Catches malformed confidence silently creating leverage above 1x."""
    with pytest.raises(ValueError, match="confidence"):
        stage_exposure(_observation(frozen_confidence=confidence), ArchitectureStage.A1_SCORE)


def test_fixed_notional_accounting_does_not_compound():
    """Catches the second bar being sized from first-bar profit."""
    observations = [
        _observation(decision_id="d1", cutoff_time_ns=100, raw_score=0.4, forward_return=0.1),
        _observation(decision_id="d2", cutoff_time_ns=200, raw_score=0.4, forward_return=0.1),
    ]

    report = build_architecture_attribution_report(
        observations,
        fixed_notional=100.0,
        cost_stress_bps=(0.0,),
    )
    sign = report["runs"][0]["stages"]["A1_SIGN"]

    assert sign["gross_pnl"] == pytest.approx(20.0)
    assert sign["cost_stress"]["0"]["net_pnl"] == pytest.approx(20.0)
    assert sign["cost_stress"]["0"]["return_on_fixed_capital"] == pytest.approx(0.2)
    assert sign["turnover_notional"] == pytest.approx(100.0)


def test_coverage_and_direction_hit_rate_have_explicit_denominators():
    """Catches missing labels or flat signals being counted as directional wins."""
    observations = [
        _observation(decision_id="d1", cutoff_time_ns=100, raw_score=0.4, forward_return=0.1),
        _observation(decision_id="d2", cutoff_time_ns=200, raw_score=-0.4, forward_return=-0.1),
        _observation(decision_id="d3", cutoff_time_ns=300, raw_score=0.0, forward_return=0.2),
        _observation(decision_id="d4", cutoff_time_ns=400, raw_score=0.4, forward_return=None),
    ]

    report = build_architecture_attribution_report(observations, fixed_notional=100.0)
    sign = report["runs"][0]["stages"]["A1_SIGN"]

    assert sign["observation_count"] == 4
    assert sign["evaluable_count"] == 3
    assert sign["coverage"] == pytest.approx(0.75)
    assert sign["active_evaluable_count"] == 2
    assert sign["signal_coverage"] == pytest.approx(2 / 3)
    assert sign["direction_hit_count"] == 2
    assert sign["direction_opportunity_count"] == 2
    assert sign["direction_hit_rate"] == pytest.approx(1.0)


def test_cost_stress_uses_changed_fixed_notional():
    """Catches costs being applied to returns or equity instead of turnover."""
    observations = [
        _observation(decision_id="d1", cutoff_time_ns=100, raw_score=0.4, forward_return=0.0),
        _observation(decision_id="d2", cutoff_time_ns=200, raw_score=-0.4, forward_return=0.0),
    ]

    report = build_architecture_attribution_report(
        observations,
        fixed_notional=100.0,
        cost_stress_bps=(0.0, 10.0),
    )
    sign = report["runs"][0]["stages"]["A1_SIGN"]

    assert sign["turnover_notional"] == pytest.approx(300.0)
    assert sign["cost_stress"]["10"]["transaction_cost"] == pytest.approx(0.3)
    assert sign["cost_stress"]["10"]["net_pnl"] == pytest.approx(-0.3)


def test_report_includes_regime_splits_without_regime_sizing():
    """Catches regime labels changing exposure instead of only grouping results."""
    observations = [
        _observation(
            decision_id="trend",
            cutoff_time_ns=100,
            regime="TREND",
            raw_score=0.4,
            forward_return=0.1,
        ),
        _observation(
            decision_id="stress",
            cutoff_time_ns=200,
            regime="STRESS",
            raw_score=0.4,
            forward_return=-0.05,
        ),
    ]

    report = build_architecture_attribution_report(observations, fixed_notional=100.0)
    sign = report["runs"][0]["stages"]["A1_SIGN"]

    assert set(sign["regime_splits"]) == {"STRESS", "TREND"}
    assert sign["regime_splits"]["TREND"]["gross_pnl"] == pytest.approx(10.0)
    assert sign["regime_splits"]["STRESS"]["gross_pnl"] == pytest.approx(-5.0)
    assert [row["exposure"] for row in sign["attribution_rows"]] == [1.0, 1.0]


def test_report_is_per_window_per_seed_and_has_only_approved_stages():
    """Catches accidental search or later architecture stages entering Task 2 output."""
    observations = [
        _observation(decision_id="a", window="dev", seed=42),
        _observation(decision_id="b", window="dev", seed=123),
        _observation(decision_id="c", window="hostile", seed=42),
    ]

    report = build_architecture_attribution_report(observations, fixed_notional=100.0)

    assert [(run["window"], run["seed"]) for run in report["runs"]] == [
        ("dev", 42),
        ("dev", 123),
        ("hostile", 42),
    ]
    assert all(list(run["stages"]) == ["A0", "A1_SIGN", "A1_SCORE"] for run in report["runs"])
    assert report["constraints"] == {
        "fixed_notional": 100.0,
        "max_abs_exposure": 1.0,
        "compounding": False,
        "parameter_search": False,
        "risk_overlay": False,
        "volatility_sizing": False,
        "no_trade_band": False,
        "options_state": False,
        "execution_market_logic": False,
    }


def test_replay_bridge_emits_minimal_causal_rows_and_reproduces_a0():
    """Catches Options/risk/execution fields leaking from replay into A1 inputs."""
    runner = object.__new__(HistoricalBacktest)
    runner._execution_rows = [
        {"close_time_ns": 100, "close": 50.0},
        {"close_time_ns": 200, "close": 55.0},
    ]
    runner._decision_snapshots = [
        _snapshot(cutoff_time_ns=100, target_qty=0.5, raw_score=-0.4, confidence=0.6),
        _snapshot(
            cutoff_time_ns=200,
            target_qty=-0.25,
            raw_score=0.2,
            confidence=0.7,
            target_leverage=2.0,
        ),
    ]

    rows = runner.build_architecture_attribution_rows(
        window="dev",
        seed=42,
        fixed_notional=100.0,
    )

    assert rows[0] == {
        "decision_id": runner._decision_snapshots[0].decision_id,
        "cutoff_time_ns": 100,
        "window": "dev",
        "seed": 42,
        "regime": "TREND",
        "baseline_exposure": 0.5,
        "raw_score": -0.4,
        "frozen_confidence": 0.6,
        "forward_return": 0.1,
    }
    assert rows[1]["forward_return"] is None
    assert rows[1]["baseline_exposure"] == pytest.approx(-1.0)
    prohibited_fields = {"options_state_id", "regime_risk_multiplier", "annualized_volatility", "risk_flags"}
    assert not (prohibited_fields & set(rows[0]))

    observations = [ArchitectureObservation(**row) for row in rows]
    report = build_architecture_attribution_report(observations, fixed_notional=100.0)
    first = report["runs"][0]["stages"]
    assert first["A0"]["attribution_rows"][0]["exposure"] == pytest.approx(0.5)
    assert first["A1_SIGN"]["attribution_rows"][0]["exposure"] == pytest.approx(-1.0)
    assert first["A1_SCORE"]["attribution_rows"][0]["exposure"] == pytest.approx(-0.6)
