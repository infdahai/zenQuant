"""Frozen, replay-stable records of approved four-hour strategy decisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, replace
from enum import Enum
from typing import Any

from zenquant.domain import DecisionSnapshot, TargetPosition, Timeframe, target_position_hash


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _decision_id(snapshot: DecisionSnapshot) -> str:
    payload = asdict(snapshot)
    payload.pop("decision_id")
    payload.pop("approved_target_hash")
    return hashlib.sha256(
        json.dumps(
            _json_value(payload),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def attach_options_state_id(snapshot: DecisionSnapshot, options_state_id: str) -> DecisionSnapshot:
    """Return an observation-only copy linked to one OptionsState payload."""
    if not options_state_id:
        raise ValueError("options_state_id must be non-empty")
    observed = replace(snapshot, options_state_id=options_state_id, decision_id="")
    return replace(observed, decision_id=_decision_id(observed))


def build_decision_snapshot(
    *,
    target: TargetPosition,
    formula_hash: str,
    config_hash: str,
    indicator_version: str,
    data_identity: str,
    alpha_direction: float,
    alpha_raw_score: float,
    alpha_confidence: float,
    directional_state: str,
    volatility_state: str,
    positioning_state: str,
    quality_state: str,
    regime_risk_multiplier: float,
    annualized_volatility: float,
    volatility_target: float,
    exposure_cap: float,
    quality_flags: Sequence[str],
    options_state_id: str | None,
) -> DecisionSnapshot:
    """Freeze one approved four-hour target behind a content-addressed ID."""
    if target.timeframe != Timeframe.TF_4H:
        raise ValueError("DecisionSnapshot only supports 4h targets")
    if not target.symbol.upper().startswith("BTC"):
        raise ValueError("DecisionSnapshot only supports BTC symbols")

    reason_codes = tuple(target.reason_codes)
    frozen_quality_flags = tuple(quality_flags)
    canonical_inputs = {
        "alpha_confidence": alpha_confidence,
        "alpha_direction": alpha_direction,
        "alpha_raw_score": alpha_raw_score,
        "annualized_volatility": annualized_volatility,
        "atr": target.atr,
        "config_hash": config_hash,
        "cutoff_time_ns": target.cutoff_time_ns,
        "data_identity": data_identity,
        "directional_state": directional_state,
        "exposure_cap": exposure_cap,
        "formula_hash": formula_hash,
        "indicator_version": indicator_version,
        "options_state_id": options_state_id,
        "portfolio": target.portfolio,
        "positioning_state": positioning_state,
        "quality_flags": frozen_quality_flags,
        "quality_state": quality_state,
        "reason_codes": reason_codes,
        "reduce_only": target.reduce_only,
        "regime": target.regime,
        "regime_confidence": target.regime_confidence,
        "regime_risk_multiplier": regime_risk_multiplier,
        "realized_vol": target.realized_vol,
        "symbol": target.symbol,
        "target_leverage": target.target_leverage,
        "target_qty": target.target_qty,
        "timeframe": target.timeframe.value,
        "volatility_state": volatility_state,
        "volatility_target": volatility_target,
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
        realized_vol=target.realized_vol,
        atr=target.atr,
        indicator_version=indicator_version,
        data_identity=data_identity,
        alpha_direction=alpha_direction,
        alpha_raw_score=alpha_raw_score,
        alpha_confidence=alpha_confidence,
        directional_state=directional_state,
        volatility_state=volatility_state,
        positioning_state=positioning_state,
        quality_state=quality_state,
        regime_risk_multiplier=regime_risk_multiplier,
        annualized_volatility=annualized_volatility,
        volatility_target=volatility_target,
        exposure_cap=exposure_cap,
        quality_flags=frozen_quality_flags,
        options_state_id=options_state_id,
        approved_target_hash=target_position_hash(target),
    )
