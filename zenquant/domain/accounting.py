"""Shared accounting validation contracts."""

from __future__ import annotations

import math


def validate_execution_fee(fee: float) -> None:
    """Reject fee values that cannot be safely applied to cash."""
    if not math.isfinite(fee) or fee < 0.0:
        raise ValueError("fee must be finite and non-negative")
