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

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-safe acceptance report."""
        return {
            "episode_profit_factor": {
                "value": self.episode_profit_factor.value,
                "status": self.episode_profit_factor.status.value,
                "closed_episode_count": self.episode_profit_factor.closed_episode_count,
                "gross_profit": self.episode_profit_factor.gross_profit,
                "gross_loss": self.episode_profit_factor.gross_loss,
            },
            "cadence": {
                "switches_per_day": self.cadence.switches_per_day,
                "severity": self.cadence.severity.value,
                "policy": self.cadence.policy,
                "reason": self.cadence.reason,
            },
            "hard_failed": self.hard_failed,
            "passed": self.passed,
            "hard_failures": list(self.hard_failures),
            "soft_flags": list(self.soft_flags),
            "diagnostics": self.diagnostics,
        }


def calculate_episode_profit_factor(
    episodes: list[PositionEpisode] | tuple[PositionEpisode, ...],
) -> ProfitFactorResult:
    """Return nullable profit factor from net PnL of closed episodes only."""
    closed = [episode for episode in episodes if episode.closed]
    gross_profit = sum(max(0.0, episode.net_pnl) for episode in closed)
    gross_loss = abs(sum(min(0.0, episode.net_pnl) for episode in closed))
    if not closed:
        return ProfitFactorResult(
            value=None,
            status=ProfitFactorStatus.INSUFFICIENT_SAMPLE,
            closed_episode_count=0,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
        )
    if gross_profit == 0.0:
        return ProfitFactorResult(
            value=0.0,
            status=ProfitFactorStatus.DEFINED,
            closed_episode_count=len(closed),
            gross_profit=0.0,
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


def position_switch_count(episodes: list[PositionEpisode] | tuple[PositionEpisode, ...]) -> int:
    """Count per-symbol side transitions across direct flips and flat gaps.

    The first episode for a symbol is not a switch. Reopening the same side
    after flat is not a switch; reopening the opposite side is one switch.
    """
    previous_side: dict[str, int] = {}
    count = 0
    ordered = sorted(
        enumerate(episodes),
        key=lambda item: (item[1].opened_at_ns, item[1].symbol, item[0]),
    )
    for _, episode in ordered:
        if episode.side not in {-1, 1}:
            raise ValueError("episode side must be -1 or 1")
        prior = previous_side.get(episode.symbol)
        if prior is not None and prior != episode.side:
            count += 1
        previous_side[episode.symbol] = episode.side
    return count


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
    switches_per_day: float | None = None,
    elapsed_days: float | None = None,
    min_episode_profit_factor: float = 1.0,
    policy: str = "normal",
    fill_profit_factor: float | None = None,
    action_profit_factor: dict[str, float] | None = None,
) -> GateV3Result:
    """Evaluate hard episode PF and cadence while retaining legacy diagnostics."""
    if min_episode_profit_factor < 0.0:
        raise ValueError("min_episode_profit_factor cannot be negative")
    actual_switch_count = position_switch_count(episodes)
    if switches_per_day is None:
        if elapsed_days is None or elapsed_days <= 0.0:
            raise ValueError("positive elapsed_days is required when switches_per_day is omitted")
        switches_per_day = actual_switch_count / elapsed_days
    pf = calculate_episode_profit_factor(episodes)
    cadence = evaluate_cadence(switches_per_day, policy=policy)
    hard_failures: list[str] = []
    soft_flags: list[str] = []

    if pf.status is ProfitFactorStatus.INSUFFICIENT_SAMPLE:
        hard_failures.append("episode_profit_factor_insufficient_sample")
    elif pf.status is ProfitFactorStatus.NO_LOSS_EPISODES:
        if pf.closed_episode_count < 2:
            hard_failures.append("episode_profit_factor_no_loss_sample_too_small")
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
            "position_switch_count": actual_switch_count,
        },
    )
