"""Robust z-score utilities shared by macro and options data stores.

Extracted to avoid duplicating the same median/MAD z-score logic across
multiple modules while keeping the data layer free of circular imports
with the indicator engine.
"""

from __future__ import annotations


def median(values: list[float]) -> float:
    """Compute median of a sorted list."""
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def mad(values: list[float], med: float) -> float:
    """Compute Median Absolute Deviation."""
    deviations = [abs(v - med) for v in values]
    return median(deviations)


def robust_z(values: list[float], current: float, min_samples: int) -> float:
    """Robust z-score based on median and MAD.

    Uses all values except the last as the reference window, then scores
    ``current`` against that reference.  Returns 0.0 when fewer than
    ``min_samples`` reference points are available, and clamps the result
    to [-6, 6].
    """
    reference = values[:-1]
    if len(reference) < min_samples:
        return 0.0
    med = median(reference)
    mad_val = mad(reference, med)
    if mad_val < 1e-9:
        return 0.0
    z = 0.6745 * (current - med) / mad_val
    return max(-6.0, min(6.0, z))
