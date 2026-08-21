"""Behavioral contract tests for frozen four-hour decision snapshots."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from zenquant.domain import TargetPosition, Timeframe
from zenquant.strategy.decision_snapshot import build_decision_snapshot


def _target(
    *,
    timeframe: Timeframe = Timeframe.TF_4H,
    target_qty: float = 1.25,
) -> TargetPosition:
    return TargetPosition(
        intent_id="non-deterministic-intent-id",
        portfolio="arc",
        symbol="BTCUSDT_PERP",
        timeframe=timeframe,
        cutoff_time_ns=1_725_667_200_000_000_000,
        target_qty=target_qty,
        target_leverage=1.0,
        reduce_only=False,
        regime="TREND",
        regime_confidence=0.8,
        reason_codes=["REGIME_TREND", "TREND_LONG"],
        realized_vol=0.42,
        atr=321.0,
    )


def _snapshot(
    *, target: TargetPosition | None = None, formula_hash: str = "formula-v1", config_hash: str = "config-v1"
):
    return build_decision_snapshot(
        target=target or _target(),
        formula_hash=formula_hash,
        config_hash=config_hash,
    )


def test_snapshot_is_frozen_after_construction():
    """Catches a mutable approved target after it has been snapshotted."""
    snapshot = _snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.target_qty = 0.0


def test_snapshot_rejects_non_4h_target():
    """Catches an intraday or daily target entering the BTC 4h V2 contract."""
    with pytest.raises(ValueError, match="4h"):
        _snapshot(target=_target(timeframe=Timeframe.TF_1H))


def test_snapshot_id_is_stable_across_replay_of_identical_inputs():
    """Catches replay identity depending on a generated intent or wall-clock value."""
    first = _snapshot()
    replayed = _snapshot()

    assert replayed.decision_id == first.decision_id


def test_snapshot_id_tracks_formula_and_config_identity():
    """Catches formula or configuration drift being hidden behind the same decision ID."""
    baseline = _snapshot()
    changed_formula = _snapshot(formula_hash="formula-v2")
    changed_config = _snapshot(config_hash="config-v2")

    assert changed_formula.decision_id != baseline.decision_id
    assert changed_config.decision_id != baseline.decision_id
    assert baseline.formula_hash == "formula-v1"
    assert baseline.config_hash == "config-v1"


def test_snapshot_records_complete_execution_and_audit_envelope():
    """Catches later stages needing to reconstruct source decision context elsewhere."""
    snapshot = build_decision_snapshot(
        target=_target(),
        formula_hash="formula-v1",
        config_hash="config-v1",
        indicator_version="pack.v1",
        data_identity="sha256:input-data-v1",
        alpha_direction=1.0,
        alpha_raw_score=0.37,
        alpha_confidence=0.72,
        directional_state="UPTREND",
        volatility_state="NORMAL",
        positioning_state="HEALTHY",
        quality_state="TRADABLE",
        regime_risk_multiplier=1.0,
        annualized_volatility=0.42,
        volatility_target=1.10,
        exposure_cap=2.5,
        quality_flags=["OK"],
        options_state_id=None,
    )

    assert snapshot.realized_vol == 0.42
    assert snapshot.atr == 321.0
    assert snapshot.indicator_version == "pack.v1"
    assert snapshot.data_identity == "sha256:input-data-v1"
    assert snapshot.alpha_direction == 1.0
    assert snapshot.alpha_raw_score == 0.37
    assert snapshot.alpha_confidence == 0.72
    assert snapshot.directional_state == "UPTREND"
    assert snapshot.volatility_state == "NORMAL"
    assert snapshot.positioning_state == "HEALTHY"
    assert snapshot.quality_state == "TRADABLE"
    assert snapshot.regime_risk_multiplier == 1.0
    assert snapshot.annualized_volatility == 0.42
    assert snapshot.volatility_target == 1.10
    assert snapshot.exposure_cap == 2.5
    assert snapshot.quality_flags == ("OK",)
    assert snapshot.options_state_id is None


@pytest.mark.parametrize(
    "target, changes",
    [
        (replace(_target(), portfolio="research"), {}),
        (replace(_target(), symbol="BTCUSDT"), {}),
        (replace(_target(), cutoff_time_ns=1_725_667_200_000_000_001), {}),
        (replace(_target(), target_qty=1.26), {}),
        (replace(_target(), target_leverage=1.1), {}),
        (replace(_target(), reduce_only=True), {}),
        (replace(_target(), regime="RANGE"), {}),
        (replace(_target(), regime_confidence=0.81), {}),
        (replace(_target(), reason_codes=["NO_DIRECTION"]), {}),
        (replace(_target(), realized_vol=0.43), {}),
        (replace(_target(), atr=322.0), {}),
        (_target(), {"indicator_version": "pack.v2"}),
        (_target(), {"data_identity": "sha256:input-data-v2"}),
        (_target(), {"alpha_direction": -1.0}),
        (_target(), {"alpha_raw_score": -0.37}),
        (_target(), {"alpha_confidence": 0.71}),
        (_target(), {"directional_state": "DOWNTREND"}),
        (_target(), {"volatility_state": "HIGH"}),
        (_target(), {"positioning_state": "CROWDED_LONG"}),
        (_target(), {"quality_state": "DEGRADED"}),
        (_target(), {"regime_risk_multiplier": 0.5}),
        (_target(), {"annualized_volatility": 0.43}),
        (_target(), {"volatility_target": 0.20}),
        (_target(), {"exposure_cap": 1.5}),
        (_target(), {"quality_flags": ["BOOK_STALE"]}),
        (_target(), {"options_state_id": "options-state-1"}),
    ],
)
def test_snapshot_id_includes_every_execution_and_audit_material_field(target, changes):
    """Catches content-addressed IDs omitting material target or audit fields."""
    snapshot_inputs = {
        "formula_hash": "formula-v1",
        "config_hash": "config-v1",
        "indicator_version": "pack.v1",
        "data_identity": "sha256:input-data-v1",
        "alpha_direction": 1.0,
        "alpha_raw_score": 0.37,
        "alpha_confidence": 0.72,
        "directional_state": "UPTREND",
        "volatility_state": "NORMAL",
        "positioning_state": "HEALTHY",
        "quality_state": "TRADABLE",
        "regime_risk_multiplier": 1.0,
        "annualized_volatility": 0.42,
        "volatility_target": 1.10,
        "exposure_cap": 2.5,
        "quality_flags": ["OK"],
        "options_state_id": None,
    }
    baseline = build_decision_snapshot(target=_target(), **snapshot_inputs)
    changed = build_decision_snapshot(target=target, **{**snapshot_inputs, **changes})

    assert changed.decision_id != baseline.decision_id
