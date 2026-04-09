"""Compose plugin results into strategy decisions.

Four composition rules, one per plugin type:

  Signals → weighted sum     → ComposedSignal (direction + confidence)
  Filters → AND chain        → FilterVerdict  (passed + blockers)
  Sizers  → multiplicative   → ComposedSize   (factor + breakdown)
  Exits   → priority-ordered → first ExitResult that fires wins
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from zenquant.plugins.protocols import ExitResult, FilterResult, SignalResult, SizerResult
from zenquant.plugins.registry import LoadedPlugin

# ---------------------------------------------------------------------------
# Composed result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComposedSignal:
    """Weighted signal combination with full audit trail."""

    direction: float  # -1.0 to +1.0
    confidence: float  # 0.0 to 1.0
    contributions: dict[str, float] = field(default_factory=dict)
    role_contributions: dict[str, dict[str, float]] = field(default_factory=dict)
    state_confidence_haircut: float = 1.0
    execution_bias: float = 0.0
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FilterVerdict:
    """AND-chain result: blocked if any filter blocks."""

    passed: bool
    blockers: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ComposedSize:
    """Multiplicative chain of sizer factors."""

    factor: float
    breakdown: dict[str, float] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Composition functions
# ---------------------------------------------------------------------------


def compose_signals(
    plugins: list[LoadedPlugin],
    weights: dict[str, float],
    indicators: dict[str, Any],
    state: dict[str, Any],
) -> ComposedSignal:
    """Weighted combination of signal plugins into a direction signal."""
    if not plugins:
        return ComposedSignal(direction=0.0, confidence=0.0)

    direction_plugins = [plugin for plugin in plugins if getattr(plugin, "role", "direction") == "direction"]
    state_plugins = [plugin for plugin in plugins if getattr(plugin, "role", "direction") == "state"]
    execution_plugins = [
        plugin for plugin in plugins if getattr(plugin, "role", "direction") == "execution_modifier"
    ]

    total_weight = sum(weights.get(p.meta.name, 1.0) for p in direction_plugins)
    if total_weight <= 0:
        return ComposedSignal(direction=0.0, confidence=0.0)

    weighted_dir = 0.0
    weighted_conf = 0.0
    contributions: dict[str, float] = {}
    role_contributions: dict[str, dict[str, float]] = {
        "direction": {},
        "state": {},
        "execution_modifier": {},
    }
    reason_codes: list[str] = []

    for plugin in direction_plugins:
        w = weights.get(plugin.meta.name, 1.0)
        norm_w = w / total_weight
        result: SignalResult = plugin.compute(indicators, state, plugin.config)
        contribution = result.value * norm_w * result.confidence
        weighted_dir += contribution
        weighted_conf += result.confidence * norm_w
        contributions[plugin.meta.name] = round(contribution, 6)
        role_contributions["direction"][plugin.meta.name] = round(contribution, 6)
        reason_codes.extend(f"{plugin.meta.name}:{c}" for c in result.reason_codes)

    state_haircut = 1.0
    if state_plugins:
        state_values: list[float] = []
        for plugin in state_plugins:
            result = plugin.compute(indicators, state, plugin.config)
            contribution = result.value * result.confidence
            role_contributions["state"][plugin.meta.name] = round(contribution, 6)
            state_values.append(contribution)
            reason_codes.extend(f"{plugin.meta.name}:{c}" for c in result.reason_codes)
        if state_values:
            bearish_stress = max(0.0, -sum(state_values) / max(1, len(state_values)))
            state_haircut = max(0.55, min(1.0, 1.0 - 0.35 * bearish_stress))

    execution_bias = 0.0
    if execution_plugins:
        execution_values: list[float] = []
        for plugin in execution_plugins:
            result = plugin.compute(indicators, state, plugin.config)
            contribution = result.value * result.confidence
            role_contributions["execution_modifier"][plugin.meta.name] = round(contribution, 6)
            execution_values.append(contribution)
            reason_codes.extend(f"{plugin.meta.name}:{c}" for c in result.reason_codes)
        if execution_values:
            execution_bias = max(-0.35, min(0.35, sum(execution_values) / len(execution_values)))

    direction = max(-1.0, min(1.0, weighted_dir))
    return ComposedSignal(
        direction=round(direction, 6),
        confidence=round(weighted_conf, 4),
        contributions=contributions,
        role_contributions=role_contributions,
        state_confidence_haircut=round(state_haircut, 4),
        execution_bias=round(execution_bias, 4),
        reason_codes=reason_codes,
    )


def compose_filters(
    plugins: list[LoadedPlugin],
    indicators: dict[str, Any],
    state: dict[str, Any],
) -> FilterVerdict:
    """AND chain: all filters must pass for the trade to proceed."""
    blockers: list[str] = []
    reason_codes: list[str] = []

    for plugin in plugins:
        result: FilterResult = plugin.compute(indicators, state, plugin.config)
        if not result.passed:
            blockers.append(plugin.meta.name)
        reason_codes.extend(f"{plugin.meta.name}:{c}" for c in result.reason_codes)

    return FilterVerdict(
        passed=len(blockers) == 0,
        blockers=blockers,
        reason_codes=reason_codes,
    )


def compose_sizers(
    plugins: list[LoadedPlugin],
    indicators: dict[str, Any],
    state: dict[str, Any],
) -> ComposedSize:
    """Multiplicative chain of sizer factors."""
    factor = 1.0
    breakdown: dict[str, float] = {}
    reason_codes: list[str] = []

    for plugin in plugins:
        result: SizerResult = plugin.compute(indicators, state, plugin.config)
        factor *= result.factor
        breakdown[plugin.meta.name] = round(result.factor, 6)
        reason_codes.extend(f"{plugin.meta.name}:{c}" for c in result.reason_codes)

    return ComposedSize(
        factor=round(max(0.0, factor), 6),
        breakdown=breakdown,
        reason_codes=reason_codes,
    )


def compose_exits(
    plugins: list[LoadedPlugin],
    indicators: dict[str, Any],
    state: dict[str, Any],
    position: dict[str, Any],
) -> ExitResult | None:
    """Priority-ordered: first plugin that says exit wins.

    Plugins must be pre-sorted by priority (registry handles this).
    """
    for plugin in plugins:
        result: ExitResult = plugin.compute(indicators, state, position, plugin.config)
        if result.should_exit:
            return result
    return None
