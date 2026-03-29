"""Domain randomization for robustness testing.

Perturbs historical data to test whether strategies are robust to
distribution shifts, or merely fit to the exact historical path.

Inspired by RL sim-to-real transfer techniques (domain randomization).
Each perturbation is deterministic given a seed for reproducibility.

Usage:
    from zenquant.historical.perturbation import PerturbationConfig, perturb_events

    config = PerturbationConfig(seed=42, price_noise_pct=0.001)
    perturbed = perturb_events(events, config)
"""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PerturbationConfig:
    """Configuration for domain randomization perturbations.

    All perturbations are optional and default to disabled (0.0).
    """

    seed: int = 42

    # Price noise: add ±N% Gaussian noise to OHLC prices
    price_noise_pct: float = 0.0  # e.g. 0.001 = ±0.1%

    # Volume noise: add ±N% Gaussian noise to volume
    volume_noise_pct: float = 0.0  # e.g. 0.05 = ±5%

    # Bar dropout: randomly skip N% of bars (simulate data gaps)
    bar_dropout_pct: float = 0.0  # e.g. 0.02 = 2% bars dropped

    # Execution delay: shift execution by N bars (simulate latency)
    execution_delay_bars: int = 0  # e.g. 1 = one bar delay

    # Spread perturbation: add N bps to simulated spread
    spread_noise_bps: float = 0.0  # e.g. 5.0 = 5 bps additional spread


def perturb_events(
    events: list[dict[str, Any]],
    config: PerturbationConfig,
) -> list[dict[str, Any]]:
    """Apply deterministic perturbations to a list of bar-close events.

    Returns a new list; the input is not modified.
    """
    if _is_noop(config):
        return events

    rng = random.Random(config.seed)
    result: list[dict[str, Any]] = []

    for event in events:
        # Bar dropout
        if config.bar_dropout_pct > 0 and rng.random() < config.bar_dropout_pct:
            continue

        row = deepcopy(event)

        # Price noise
        if config.price_noise_pct > 0:
            noise_factor = 1.0 + rng.gauss(0, config.price_noise_pct)
            for field in ("open", "high", "low", "close"):
                if field in row:
                    row[field] = float(row[field]) * noise_factor
            # Maintain OHLC consistency: high >= max(open, close), low <= min(open, close)
            if "high" in row and "open" in row and "close" in row:
                row["high"] = max(float(row["high"]), float(row["open"]), float(row["close"]))
            if "low" in row and "open" in row and "close" in row:
                row["low"] = min(float(row["low"]), float(row["open"]), float(row["close"]))

        # Volume noise
        if config.volume_noise_pct > 0 and "volume" in row:
            noise_factor = 1.0 + rng.gauss(0, config.volume_noise_pct)
            row["volume"] = max(0.0, float(row["volume"]) * noise_factor)

        result.append(row)

    return result


def _is_noop(config: PerturbationConfig) -> bool:
    """Check if all perturbations are disabled."""
    return (
        config.price_noise_pct == 0.0
        and config.volume_noise_pct == 0.0
        and config.bar_dropout_pct == 0.0
        and config.execution_delay_bars == 0
        and config.spread_noise_bps == 0.0
    )
