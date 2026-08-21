"""Behavioral contract tests for frozen four-hour decision snapshots."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

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
