"""Gate v3 evaluation based on closed position episodes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from zenquant.domain import PositionEpisode


class ProfitFactorStatus(StrEnum):
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    NO_LOSS_EPISODES = "NO_LOSS_EPISODES"
    DEFINED = "DEFINED"


class GateSeverity(StrEnum):
    NONE = "NONE"
    SOFT = "SOFT"
    HARD = "HARD"


@dataclass(frozen=True, slots=True)
class ProfitFactorResult:
    value: float | None
    status: ProfitFactorStatus
    closed_episode_count: int
    gross_profit: float
    gross_loss: float


@dataclass(frozen=True, slots=True)
class CadenceAssessment:
    switches_per_day: float
    severity: GateSeverity
    policy: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class GateV3Result:
    episode_profit_factor: ProfitFactorResult
    cadence: CadenceAssessment
    hard_failures: tuple[str, ...]
    soft_flags: tuple[str, ...]
    diagnostics: dict[str, Any]

    @property
    def hard_failed(self) -> bool:
        return bool(self.hard_failures)

    @property
    def passed(self) -> bool:
        return not self.hard_failed


def calculate_episode_profit_factor(
    episodes: list[PositionEpisode] | tuple[PositionEpisode, ...],
    *,
    min_closed_episodes: int = 1,
) -> ProfitFactorResult:
    """Return nullable profit factor from net PnL of closed episodes only."""
    if min_closed_episodes < 1:
        raise ValueError("min_closed_episodes must be at least one")
    closed = [episode for episode in episodes if episode.closed]
    gross_profit = sum(max(0.0, episode.net_pnl) for episode in closed)
    gross_loss = abs(sum(min(0.0, episode.net_pnl) for episode in closed))
    if len(closed) < min_closed_episodes:
        return ProfitFactorResult(
            value=None,
            status=ProfitFactorStatus.INSUFFICIENT_SAMPLE,
            closed_episode_count=len(closed),
            gross_profit=gross_profit,
            gross_loss=gross_loss,
        )
    if gross_loss == 0.0:
        return ProfitFactorResult(
            value=None,
            status=ProfitFactorStatus.NO_LOSS_EPISODES,
            closed_episode_count=len(closed),
            gross_profit=gross_profit,
            gross_loss=0.0,
        )
    return ProfitFactorResult(
        value=gross_profit / gross_loss,
        status=ProfitFactorStatus.DEFINED,
        closed_episode_count=len(closed),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
    )


episode_profit_factor = calculate_episode_profit_factor


def evaluate_cadence(switches_per_day: float, *, policy: str = "normal") -> CadenceAssessment:
    """Classify position-switch cadence at the exact Gate v3 boundaries."""
    if switches_per_day < 0.0:
        raise ValueError("switches_per_day cannot be negative")
    if switches_per_day <= 1.0:
        severity = GateSeverity.NONE
        reason = None
    elif switches_per_day <= 2.0:
        severity = GateSeverity.SOFT
        reason = "position_switch_cadence_above_preferred"
    else:
        severity = GateSeverity.HARD
        reason = (
            "position_switch_cadence_above_absolute_limit"
            if switches_per_day > 5.0
            else "position_switch_cadence_above_normal_limit"
        )
    return CadenceAssessment(
        switches_per_day=switches_per_day,
        severity=severity,
        policy=policy,
        reason=reason,
    )


assess_cadence = evaluate_cadence


def evaluate_gate_v3(
    episodes: list[PositionEpisode] | tuple[PositionEpisode, ...],
    *,
    switches_per_day: float,
    min_episode_profit_factor: float = 1.0,
    min_closed_episodes: int = 1,
    policy: str = "normal",
    fill_profit_factor: float | None = None,
    action_profit_factor: dict[str, float] | None = None,
) -> GateV3Result:
    """Evaluate hard episode PF and cadence while retaining legacy diagnostics."""
    if min_episode_profit_factor < 0.0:
        raise ValueError("min_episode_profit_factor cannot be negative")
    pf = calculate_episode_profit_factor(episodes, min_closed_episodes=min_closed_episodes)
    cadence = evaluate_cadence(switches_per_day, policy=policy)
    hard_failures: list[str] = []
    soft_flags: list[str] = []

    if pf.status is ProfitFactorStatus.INSUFFICIENT_SAMPLE:
        hard_failures.append("episode_profit_factor_insufficient_sample")
    elif pf.status is ProfitFactorStatus.NO_LOSS_EPISODES:
        if pf.gross_profit <= 0.0:
            hard_failures.append("episode_profit_factor_no_positive_episodes")
    elif pf.value is not None and pf.value < min_episode_profit_factor:
        hard_failures.append("episode_profit_factor_below_minimum")

    if cadence.severity is GateSeverity.HARD and cadence.reason is not None:
        hard_failures.append(cadence.reason)
    elif cadence.severity is GateSeverity.SOFT and cadence.reason is not None:
        soft_flags.append(cadence.reason)

    return GateV3Result(
        episode_profit_factor=pf,
        cadence=cadence,
        hard_failures=tuple(hard_failures),
        soft_flags=tuple(soft_flags),
        diagnostics={
            "fill_profit_factor": fill_profit_factor,
            "action_profit_factor": dict(sorted((action_profit_factor or {}).items())),
        },
    )
