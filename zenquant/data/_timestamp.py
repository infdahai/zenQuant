"""Lossless integer timestamp parsing for point-in-time data contracts."""

from __future__ import annotations

import operator
import re
from typing import Any

_INT64_MAX = (1 << 63) - 1
_INTEGER_TEXT = re.compile(r"[+-]?\d+\Z")


def lossless_timestamp(value: Any) -> int | None:
    """Return a positive signed-int64 timestamp without numeric coercion.

    Python and NumPy integral scalars plus exact base-10 integer strings are
    accepted. Booleans, floats, fractional text, and out-of-range values are
    rejected so epoch nanoseconds never pass through binary floating point.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if _INTEGER_TEXT.fullmatch(value) is None:
            return None
        parsed = int(value, 10)
    else:
        try:
            parsed = operator.index(value)
        except TypeError:
            return None
    if parsed <= 0 or parsed > _INT64_MAX:
        return None
    return int(parsed)


__all__ = ["lossless_timestamp"]
