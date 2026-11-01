"""Shared quantity arithmetic for simulator position projections."""

import math
from decimal import Decimal


def add_filled_quantity(current: float, signed_fill: float) -> float:
    """Add canonical decimal quantities without accumulating binary64 dust.

    Quantities use their canonical decimal representation when added, avoiding
    accumulated binary64 drift through repeated decimal-sized fills. Only an
    opposing cancellation residue within four ULPs of its operands is treated
    as roundoff. No trade minimum, absolute BTC epsilon or lot-size cutoff is
    used. A genuine .00005 remainder survives; an offsetting .000165 cancels.
    """
    result = float(Decimal(str(current)) + Decimal(str(signed_fill)))
    opposing = (current > 0 > signed_fill) or (current < 0 < signed_fill)
    if opposing and abs(result) <= 4 * max(math.ulp(current), math.ulp(signed_fill)):
        return 0.0
    return result
