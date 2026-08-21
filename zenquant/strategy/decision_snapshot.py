"""Frozen, replay-stable records of approved four-hour strategy decisions."""

from __future__ import annotations

import hashlib
import json

from zenquant.domain import DecisionSnapshot, TargetPosition, Timeframe


def build_decision_snapshot(
    *,
    target: TargetPosition,
    formula_hash: str,
    config_hash: str,
) -> DecisionSnapshot:
    """Freeze one approved four-hour target behind a content-addressed ID."""
    if target.timeframe != Timeframe.TF_4H:
        raise ValueError("DecisionSnapshot only supports 4h targets")

    reason_codes = tuple(target.reason_codes)
    canonical_inputs = {
        "config_hash": config_hash,
        "cutoff_time_ns": target.cutoff_time_ns,
        "formula_hash": formula_hash,
        "portfolio": target.portfolio,
        "reason_codes": reason_codes,
        "reduce_only": target.reduce_only,
        "regime": target.regime,
        "regime_confidence": target.regime_confidence,
        "symbol": target.symbol,
        "target_leverage": target.target_leverage,
        "target_qty": target.target_qty,
        "timeframe": target.timeframe.value,
    }
    decision_id = hashlib.sha256(
        json.dumps(canonical_inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return DecisionSnapshot(
        decision_id=decision_id,
        portfolio=target.portfolio,
        symbol=target.symbol,
        timeframe=target.timeframe,
        cutoff_time_ns=target.cutoff_time_ns,
        formula_hash=formula_hash,
        config_hash=config_hash,
        target_qty=target.target_qty,
        target_leverage=target.target_leverage,
        reduce_only=target.reduce_only,
        regime=target.regime,
        regime_confidence=target.regime_confidence,
        reason_codes=reason_codes,
    )
