"""Transfer Entropy estimation between two time series.

Measures directed information flow: TE(ETH→BTC) quantifies how much
ETH's past reduces uncertainty about BTC's future, beyond BTC's own past.

High TE(ETH→BTC) = ETH is "leading" BTC = regime transition signal.
Academic basis: Assaf et al. (2024), Keshari et al. (2024).

Mechanism: ETH has thinner orderbook → reacts faster to liquidity shocks
→ information propagates to BTC with 1-4H delay.

Usage:
    from zenquant.research.transfer_entropy import rolling_transfer_entropy
    te = rolling_transfer_entropy(btc_returns, eth_returns, window=120)
"""

from __future__ import annotations

import math


def _histogram_entropy(values: list[float], n_bins: int = 10) -> float:
    """Compute Shannon entropy of a discretized distribution."""
    if not values:
        return 0.0

    vmin, vmax = min(values), max(values)
    if vmax - vmin < 1e-12:
        return 0.0

    bin_width = (vmax - vmin) / n_bins
    counts = [0] * n_bins
    n = len(values)

    for v in values:
        idx = min(int((v - vmin) / bin_width), n_bins - 1)
        counts[idx] += 1

    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / n
            entropy -= p * math.log2(p)

    return entropy


def _joint_entropy(x: list[float], y: list[float], n_bins: int = 10) -> float:
    """Compute joint entropy H(X,Y) using 2D histogram."""
    if not x or not y or len(x) != len(y):
        return 0.0

    n = len(x)
    xmin, xmax = min(x), max(x)
    ymin, ymax = min(y), max(y)

    if xmax - xmin < 1e-12 or ymax - ymin < 1e-12:
        return 0.0

    xw = (xmax - xmin) / n_bins
    yw = (ymax - ymin) / n_bins

    counts: dict[tuple[int, int], int] = {}
    for i in range(n):
        xi = min(int((x[i] - xmin) / xw), n_bins - 1)
        yi = min(int((y[i] - ymin) / yw), n_bins - 1)
        counts[(xi, yi)] = counts.get((xi, yi), 0) + 1

    entropy = 0.0
    for c in counts.values():
        if c > 0:
            p = c / n
            entropy -= p * math.log2(p)

    return entropy


def transfer_entropy(
    source: list[float],
    target: list[float],
    lag: int = 1,
    n_bins: int = 8,
) -> float:
    """Compute Transfer Entropy TE(source → target).

    TE = H(target_future | target_past) - H(target_future | target_past, source_past)
       = H(target_past, target_future) - H(target_past)
         - H(target_past, source_past, target_future) + H(target_past, source_past)

    Simplified using: TE = I(source_past; target_future | target_past)
    """
    n = len(source)
    if n < lag + 10 or len(target) != n:
        return 0.0

    # Build lagged series
    target_past = target[:-lag]
    target_future = target[lag:]
    source_past = source[:-lag]

    # Trim to same length
    m = min(len(target_past), len(target_future), len(source_past))
    tp = target_past[:m]
    tf = target_future[:m]
    sp = source_past[:m]

    # TE = H(tp, tf) + H(tp, sp) - H(tp) - H(tp, sp, tf)
    h_tp = _histogram_entropy(tp, n_bins)
    h_tp_tf = _joint_entropy(tp, tf, n_bins)
    h_tp_sp = _joint_entropy(tp, sp, n_bins)

    # For 3-variable joint entropy, concatenate bins
    # Approximate: H(tp, sp, tf) ≈ H(concat(tp,sp), tf)
    tp_sp_combined = [tp[i] * 1000 + sp[i] for i in range(m)]
    h_tp_sp_tf = _joint_entropy(tp_sp_combined, tf, n_bins)

    te = h_tp_tf + h_tp_sp - h_tp - h_tp_sp_tf
    return max(0.0, te)  # TE is non-negative by definition


def rolling_transfer_entropy(
    source_returns: list[float],
    target_returns: list[float],
    window: int = 120,
    lag: int = 1,
) -> list[float]:
    """Compute rolling Transfer Entropy over a sliding window.

    Returns list of TE values, one per bar (0.0 for initial warmup period).
    """
    n = len(source_returns)
    result = [0.0] * n

    for i in range(window, n):
        src_window = source_returns[i - window : i]
        tgt_window = target_returns[i - window : i]
        result[i] = transfer_entropy(src_window, tgt_window, lag=lag)

    return result
