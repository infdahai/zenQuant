"""Plugin protocol definitions and result types.

This module defines the contract between the strategy engine and plugins.
Plugins import ONLY from this module — never from zenquant internals.

Four plugin types map to the four stages of the decision chain:
  Signal  → weighted sum   → direction [-1, 1]
  Filter  → AND chain      → pass/block
  Sizer   → multiplicative → final leverage factor
  Exit    → priority order → first match wins
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PluginMeta:
    """Self-describing metadata every plugin must expose as module-level ``meta``."""

    name: str
    version: str
    plugin_type: str  # "signal" | "filter" | "sizer" | "exit"
    description: str
    author: str = "zenquant"
    config_schema: type | None = None


# ---------------------------------------------------------------------------
# Result types (frozen — plugins return these, never mutate them)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalResult:
    """Output of a signal plugin."""

    value: float  # -1.0 to +1.0 (directional strength)
    confidence: float  # 0.0 to 1.0
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Output of a filter plugin."""

    passed: bool
    reason: str = ""
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SizerResult:
    """Output of a sizer plugin."""

    factor: float  # multiplicative scaling (0.0 to N)
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExitResult:
    """Output of an exit plugin."""

    should_exit: bool
    exit_type: str = "NONE"  # INVALIDATION, TRAILING, TIME_DECAY, PROFIT_LOCK, ...
    urgency: str = "NONE"  # NONE, GRADUAL, IMMEDIATE
    reduction_pct: float = 0.0  # 0.0 to 1.0
    stop_price: float | None = None
    reason: str = ""
    reason_codes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Plugin protocols — structural subtyping via Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SignalPlugin(Protocol):
    """Produces a directional signal from indicators and state."""

    meta: PluginMeta

    def compute(
        self,
        indicators: dict[str, Any],
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> SignalResult: ...


@runtime_checkable
class FilterPlugin(Protocol):
    """Gates trades — pass or block with reason."""

    meta: PluginMeta

    def compute(
        self,
        indicators: dict[str, Any],
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> FilterResult: ...


@runtime_checkable
class SizerPlugin(Protocol):
    """Produces a multiplicative sizing factor."""

    meta: PluginMeta

    def compute(
        self,
        indicators: dict[str, Any],
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> SizerResult: ...


@runtime_checkable
class ExitPlugin(Protocol):
    """Decides whether to exit a position."""

    meta: PluginMeta

    def compute(
        self,
        indicators: dict[str, Any],
        state: dict[str, Any],
        position: dict[str, Any],
        config: dict[str, Any],
    ) -> ExitResult: ...
