"""Canonical content identities for strategy target authority."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import TargetPosition


def canonical_target_position(target: TargetPosition) -> dict[str, Any]:
    """Return the complete deterministic execution-material target payload."""
    return {
        "atr": target.atr,
        "cutoff_time_ns": target.cutoff_time_ns,
        "intent_id": target.intent_id,
        "portfolio": target.portfolio,
        "realized_vol": target.realized_vol,
        "reason_codes": sorted(set(target.reason_codes)),
        "reduce_only": target.reduce_only,
        "regime": target.regime,
        "regime_confidence": target.regime_confidence,
        "symbol": target.symbol,
        "target_leverage": target.target_leverage,
        "target_qty": target.target_qty,
        "timeframe": target.timeframe.value,
    }


def target_position_hash(target: TargetPosition) -> str:
    """Hash every field that can affect execution or its decision audit."""
    canonical = json.dumps(
        canonical_target_position(target),
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
