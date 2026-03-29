"""Promotion gate for AutoResearch campaigns.

Implements a multi-stage validation pipeline that determines whether a
candidate parameter set should be promoted to production use:

    1. Walk-forward validation  — OOS Sharpe must beat baseline
    2. Domain randomization     — must survive on 4/5 random seeds
    3. Drawdown regression      — must not worsen max drawdown beyond threshold

Usage:
    gate = PromotionGate(baseline=baseline_metrics)
    verdict = gate.evaluate(candidate_metrics, robustness_results)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PromotionCriteria:
    """Thresholds for promotion decisions."""

    # Walk-forward gate
    min_val_sharpe: float = 0.5
    min_sharpe_vs_baseline: float = -0.5  # allow up to 0.5 worse than baseline
    max_val_drawdown: float = 0.20  # 20% absolute cap
    min_trade_count: int = 5

    # Robustness gate
    min_seeds_passing: int = 4  # out of 5
    robustness_sharpe_floor: float = 0.0  # perturbed must stay positive
    max_sharpe_degradation: float = 0.50  # max drop from clean to perturbed

    # Drawdown regression gate
    max_drawdown_regression: float = 0.05  # 5pp worse than baseline allowed


@dataclass(frozen=True, slots=True)
class PromotionVerdict:
    """Result of the promotion evaluation."""

    decision: str  # PROMOTE | REVIEW | REJECT
    passed_gates: list[str]
    failed_gates: list[str]
    reasons: list[str]
    metrics_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BaselineMetrics:
    """Baseline performance to compare against."""

    sharpe: float = 0.0
    max_drawdown: float = 0.0
    net_pnl: float = 0.0
    trade_count: int = 0


@dataclass(frozen=True, slots=True)
class CandidateMetrics:
    """Candidate experiment metrics from walk-forward validation."""

    avg_val_sharpe: float
    avg_val_drawdown: float
    avg_val_pnl: float
    total_trade_count: int
    split_sharpes: list[float] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RobustnessResult:
    """Result from one domain-randomization seed."""

    seed: int
    sharpe: float
    max_drawdown: float
    net_pnl: float
    trade_count: int


class PromotionGate:
    """Multi-stage promotion evaluation."""

    def __init__(
        self,
        baseline: BaselineMetrics | None = None,
        criteria: PromotionCriteria | None = None,
    ) -> None:
        self._baseline = baseline or BaselineMetrics()
        self._criteria = criteria or PromotionCriteria()

    def evaluate(
        self,
        candidate: CandidateMetrics,
        robustness: list[RobustnessResult] | None = None,
    ) -> PromotionVerdict:
        """Run all promotion gates and return a verdict."""
        passed: list[str] = []
        failed: list[str] = []
        reasons: list[str] = []

        # Gate 1: Walk-forward validation
        wf_pass = self._check_walk_forward(candidate, reasons)
        (passed if wf_pass else failed).append("walk_forward")

        # Gate 2: Robustness (if results provided)
        if robustness is not None:
            rob_pass = self._check_robustness(candidate, robustness, reasons)
            (passed if rob_pass else failed).append("robustness")

        # Gate 3: Drawdown regression vs baseline
        dd_pass = self._check_drawdown_regression(candidate, reasons)
        (passed if dd_pass else failed).append("drawdown_regression")

        # Decision logic
        if not failed:
            decision = "PROMOTE"
        elif "walk_forward" in failed:
            decision = "REJECT"
        else:
            decision = "REVIEW"

        return PromotionVerdict(
            decision=decision,
            passed_gates=passed,
            failed_gates=failed,
            reasons=reasons,
            metrics_summary={
                "avg_val_sharpe": candidate.avg_val_sharpe,
                "avg_val_drawdown": candidate.avg_val_drawdown,
                "baseline_sharpe": self._baseline.sharpe,
                "baseline_drawdown": self._baseline.max_drawdown,
                "robustness_seeds": len(robustness) if robustness else 0,
            },
        )

    def _check_walk_forward(
        self,
        candidate: CandidateMetrics,
        reasons: list[str],
    ) -> bool:
        c = self._criteria
        ok = True

        if candidate.avg_val_sharpe < c.min_val_sharpe:
            reasons.append(
                f"walk_forward: avg Sharpe {candidate.avg_val_sharpe:.2f} < min {c.min_val_sharpe:.2f}"
            )
            ok = False

        sharpe_delta = candidate.avg_val_sharpe - self._baseline.sharpe
        if sharpe_delta < c.min_sharpe_vs_baseline:
            reasons.append(
                f"walk_forward: Sharpe delta {sharpe_delta:+.2f} < allowed {c.min_sharpe_vs_baseline:+.2f}"
            )
            ok = False

        if candidate.avg_val_drawdown > c.max_val_drawdown:
            reasons.append(
                f"walk_forward: drawdown {candidate.avg_val_drawdown:.2%} > cap {c.max_val_drawdown:.2%}"
            )
            ok = False

        if candidate.total_trade_count < c.min_trade_count:
            reasons.append(
                f"walk_forward: {candidate.total_trade_count} trades < min {c.min_trade_count}"
            )
            ok = False

        if ok:
            reasons.append(
                f"walk_forward: PASS (Sharpe {candidate.avg_val_sharpe:.2f}, "
                f"DD {candidate.avg_val_drawdown:.2%}, "
                f"{candidate.total_trade_count} trades)"
            )
        return ok

    def _check_robustness(
        self,
        candidate: CandidateMetrics,
        robustness: list[RobustnessResult],
        reasons: list[str],
    ) -> bool:
        c = self._criteria
        if not robustness:
            reasons.append("robustness: SKIP (no results)")
            return True

        seeds_passing = 0
        for r in robustness:
            degradation = candidate.avg_val_sharpe - r.sharpe
            if r.sharpe >= c.robustness_sharpe_floor and degradation <= c.max_sharpe_degradation:
                seeds_passing += 1

        ok = seeds_passing >= c.min_seeds_passing
        if ok:
            reasons.append(f"robustness: PASS ({seeds_passing}/{len(robustness)} seeds)")
        else:
            reasons.append(
                f"robustness: FAIL ({seeds_passing}/{len(robustness)} seeds, "
                f"need {c.min_seeds_passing})"
            )
        return ok

    def _check_drawdown_regression(
        self,
        candidate: CandidateMetrics,
        reasons: list[str],
    ) -> bool:
        c = self._criteria
        regression = candidate.avg_val_drawdown - self._baseline.max_drawdown
        ok = regression <= c.max_drawdown_regression

        if ok:
            reasons.append(
                f"drawdown_regression: PASS (regression {regression:+.2%} "
                f"<= allowed {c.max_drawdown_regression:+.2%})"
            )
        else:
            reasons.append(
                f"drawdown_regression: FAIL (regression {regression:+.2%} "
                f"> allowed {c.max_drawdown_regression:+.2%})"
            )
        return ok
