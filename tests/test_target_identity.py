from __future__ import annotations

from dataclasses import replace

import pytest

from zenquant.domain import TargetPosition, Timeframe, target_position_hash


def _target() -> TargetPosition:
    return TargetPosition(
        intent_id="intent-1",
        portfolio="arc",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        target_qty=0.75,
        target_leverage=0.8,
        reduce_only=False,
        regime="TREND",
        regime_confidence=0.9,
        reason_codes=["B", "A"],
        realized_vol=0.4,
        atr=500.0,
    )


def test_target_position_hash_is_deterministic_with_canonical_reason_list_semantics() -> None:
    target = _target()
    semantically_equal = replace(target, reason_codes=["A", "B", "A"])

    assert target_position_hash(target) == target_position_hash(target)
    assert target_position_hash(target) == target_position_hash(semantically_equal)
    assert target_position_hash(target).startswith("sha256:")


@pytest.mark.parametrize(
    ("field_name", "new_value"),
    [
        ("intent_id", "intent-2"),
        ("portfolio", "research"),
        ("symbol", "ETHUSDT_PERP"),
        ("timeframe", Timeframe.TF_1H),
        ("cutoff_time_ns", 2),
        ("target_qty", 0.5),
        ("target_leverage", 0.7),
        ("reduce_only", True),
        ("regime", "RANGE"),
        ("regime_confidence", 0.4),
        ("reason_codes", ["A", "C"]),
        ("realized_vol", 0.5),
        ("atr", 501.0),
    ],
)
def test_target_position_hash_changes_for_every_material_field(field_name: str, new_value: object) -> None:
    target = _target()

    mutated = replace(target, **{field_name: new_value})

    assert target_position_hash(mutated) != target_position_hash(target)
