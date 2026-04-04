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

    Two modes:
    1. Domain randomization: noise, dropout, delay (for robustness testing)
    2. Adversarial: regime_whipsaw, liquidity_vacuum, crowding_trap (for GAN-style stress testing)
    """

    seed: int = 42

    # ── Domain Randomization (passive robustness) ──

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

    # ── Adversarial Modes (active stress testing) ──

    # Regime whipsaw: inject trend reversals every N bars
    regime_whipsaw_bars: int = 0  # e.g. 20 = reverse direction every 20 bars
    regime_whipsaw_magnitude: float = 0.0  # e.g. 50 = ±50 USD per bar

    # Liquidity vacuum: inject K random price gaps of -N%
    liquidity_vacuum_count: int = 0  # e.g. 5 = 5 gaps
    liquidity_vacuum_pct: tuple[float, float] = (0.0, 0.0)  # e.g. (-0.10, -0.05) = -10% to -5%

    # Crowding trap: multiply funding rates by N (but keep price trend)
    crowding_trap_funding_mult: float = 0.0  # e.g. 10.0 = 10x funding rates


def perturb_events(
    events: list[dict[str, Any]],
    config: PerturbationConfig,
) -> list[dict[str, Any]]:
    """Apply deterministic perturbations to a list of bar-close events.

    Returns a new list; the input is not modified.

    Supports two modes:
    1. Domain randomization: add noise/dropout/delay to test robustness
    2. Adversarial: inject worst-case patterns (whipsaw, vacuum, crowding)
    """
    if _is_noop(config):
        return events

    rng = random.Random(config.seed)
    result: list[dict[str, Any]] = []

    # ── Adversarial Mode 1: Regime Whipsaw ──
    whipsaw_state = {"direction": 1.0, "base_price": None, "bar_count": 0}
    if config.regime_whipsaw_bars > 0 and config.regime_whipsaw_magnitude > 0:
        result = _apply_regime_whipsaw(events, config, rng, whipsaw_state)
        events = result  # Chain: whipsaw first, then other perturbations

    # ── Adversarial Mode 2: Liquidity Vacuum ──
    if config.liquidity_vacuum_count > 0 and config.liquidity_vacuum_pct != (0.0, 0.0):
        result = _apply_liquidity_vacuum(events, config, rng)
        events = result

    # ── Standard Perturbations (can combine with adversarial) ──
    result = []
    for event in events:
        # Bar dropout
        if config.bar_dropout_pct > 0 and rng.random() < config.bar_dropout_pct:
            continue

        row = deepcopy(event)

        # Price noise: independent factor per field to test wick/body structure
        if config.price_noise_pct > 0:
            for price_field in ("open", "high", "low", "close"):
                if price_field in row:
                    noise_factor = 1.0 + rng.gauss(0, config.price_noise_pct)
                    row[price_field] = float(row[price_field]) * noise_factor
            # Maintain OHLC consistency: high >= max(open, close), low <= min(open, close)
            if "high" in row and "open" in row and "close" in row:
                row["high"] = max(float(row["high"]), float(row["open"]), float(row["close"]))
            if "low" in row and "open" in row and "close" in row:
                row["low"] = min(float(row["low"]), float(row["open"]), float(row["close"]))

        # Volume noise
        if config.volume_noise_pct > 0 and "volume" in row:
            noise_factor = 1.0 + rng.gauss(0, config.volume_noise_pct)
            row["volume"] = max(0.0, float(row["volume"]) * noise_factor)

        # Spread noise: widen the high-low range to simulate worse fills
        if config.spread_noise_bps > 0 and "high" in row and "low" in row:
            mid = (float(row["high"]) + float(row["low"])) / 2.0
            spread_pct = config.spread_noise_bps / 10_000.0
            row["high"] = float(row["high"]) + mid * spread_pct / 2.0
            row["low"] = float(row["low"]) - mid * spread_pct / 2.0

        result.append(row)

    # Execution delay: shift close prices forward to simulate latency
    # (the strategy decides on bar N but executes at bar N+delay's close)
    if config.execution_delay_bars > 0 and len(result) > config.execution_delay_bars:
        delayed: list[dict[str, Any]] = []
        delay = config.execution_delay_bars
        for i, row in enumerate(result):
            row = dict(row)  # shallow copy — only "close" is overwritten (float)
            if i + delay < len(result):
                row["close"] = result[i + delay]["close"]
            delayed.append(row)
        result = delayed

    return result


def _is_noop(config: PerturbationConfig) -> bool:
    """Check if all perturbations are disabled."""
    return (
        config.price_noise_pct == 0.0
        and config.volume_noise_pct == 0.0
        and config.bar_dropout_pct == 0.0
        and config.execution_delay_bars == 0
        and config.spread_noise_bps == 0.0
        and config.regime_whipsaw_bars == 0
        and config.liquidity_vacuum_count == 0
        and config.crowding_trap_funding_mult == 0.0
    )


# ── Adversarial Generators ──


def _apply_regime_whipsaw(
    events: list[dict[str, Any]],
    config: PerturbationConfig,
    rng: random.Random,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Inject rapid trend reversals every N bars (sawtooth pattern).

    Tests strategy's ability to handle regime whipsaw.
    Creates cumulative sawtooth: +50, +50, -50, -50, +50, +50, ...
    """
    result: list[dict[str, Any]] = []
    direction = 1.0
    cumulative_offset = 0.0

    for i, event in enumerate(events):
        row = deepcopy(event)

        # Reverse direction every whipsaw_bars
        if i > 0 and i % config.regime_whipsaw_bars == 0:
            direction *= -1.0

        # Apply cumulative move to price (sawtooth pattern)
        move = direction * config.regime_whipsaw_magnitude
        cumulative_offset += move

        for field in ("open", "high", "low", "close"):
            if field in row:
                row[field] = float(row[field]) + cumulative_offset

        result.append(row)

    return result


def _apply_liquidity_vacuum(
    events: list[dict[str, Any]],
    config: PerturbationConfig,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Inject K random price gaps (simulate flash crashes).

    Tests strategy's crash_reversal and risk controls.
    """
    if len(events) < config.liquidity_vacuum_count * 2:
        return events  # Not enough bars

    # Select random gap positions (avoid first/last 10% of data)
    margin = len(events) // 10
    gap_positions = sorted(rng.sample(
        range(margin, len(events) - margin),
        config.liquidity_vacuum_count
    ))

    result: list[dict[str, Any]] = []
    for i, event in enumerate(events):
        row = deepcopy(event)

        # Inject gap at selected positions
        if i in gap_positions and "close" in row:
            gap_pct = rng.uniform(*config.liquidity_vacuum_pct)
            gap_mult = 1.0 + gap_pct
            for field in ("open", "high", "low", "close"):
                if field in row:
                    row[field] = float(row[field]) * gap_mult

        result.append(row)

    return result


def apply_crowding_trap_funding(
    funding_events: list[dict[str, Any]],
    config: PerturbationConfig,
) -> list[dict[str, Any]]:
    """Multiply funding rates by N (for crowding trap scenario).

    Separate function because funding is not in kline events.
    Tests strategy's crowding detection (should not be fooled by extreme funding alone).
    """
    if config.crowding_trap_funding_mult == 0.0:
        return funding_events

    result: list[dict[str, Any]] = []
    for event in funding_events:
        row = deepcopy(event)
        if "funding_rate" in row:
            row["funding_rate"] = float(row["funding_rate"]) * config.crowding_trap_funding_mult
        result.append(row)

    return result
