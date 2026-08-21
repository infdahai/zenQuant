"""Pure fixed-notional attribution for the frozen A0/A1 architecture stages.

This module deliberately knows nothing about risk, volatility sizing, options,
no-trade bands, or execution behavior.  It receives a minimal causal record and
attributes one-bar returns to three frozen exposure transforms only.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION = "architecture_attribution.v1"
DEFAULT_COST_STRESS_BPS = (0.0, 5.0, 15.0)


class ArchitectureStage(StrEnum):
    """Task-2 stages, in causal experiment order."""

    A0 = "A0"
    A1_SIGN = "A1_SIGN"
    A1_SCORE = "A1_SCORE"


@dataclass(frozen=True, slots=True)
class ArchitectureObservation:
    """Minimal point-in-time input accepted by the attribution experiment."""

    decision_id: str
    cutoff_time_ns: int
    window: str
    seed: int
    regime: str
    baseline_exposure: float
    raw_score: float
    frozen_confidence: float
    forward_return: float | None


def stage_exposure(observation: ArchitectureObservation, stage: ArchitectureStage | str) -> float:
    """Return one pure stage exposure, bounded by the fixed 1x contract."""
    resolved_stage = ArchitectureStage(stage)
    if resolved_stage == ArchitectureStage.A0:
        exposure = _finite_float(observation.baseline_exposure, name="baseline_exposure")
        if abs(exposure) > 1.0:
            raise ValueError("baseline_exposure must remain within the fixed 1x contract")
        return exposure

    raw_score = _finite_float(observation.raw_score, name="raw_score")
    direction = 1.0 if raw_score > 0.0 else -1.0 if raw_score < 0.0 else 0.0
    if resolved_stage == ArchitectureStage.A1_SIGN:
        return direction

    confidence = _finite_float(observation.frozen_confidence, name="frozen_confidence")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("frozen_confidence must be between 0 and 1")
    return direction * confidence


def build_architecture_attribution_report(
    observations: list[ArchitectureObservation],
    *,
    fixed_notional: float,
    cost_stress_bps: tuple[float, ...] = DEFAULT_COST_STRESS_BPS,
) -> dict[str, Any]:
    """Aggregate deterministic per-window/per-seed A0/A1 attribution."""
    notional = _finite_float(fixed_notional, name="fixed_notional")
    if notional <= 0.0:
        raise ValueError("fixed_notional must be positive")
    costs = _normalize_cost_stress(cost_stress_bps)

    grouped: defaultdict[tuple[str, int], list[ArchitectureObservation]] = defaultdict(list)
    for observation in observations:
        grouped[(observation.window, observation.seed)].append(observation)

    runs: list[dict[str, Any]] = []
    for (window, seed), group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda item: (item.cutoff_time_ns, item.decision_id))
        _reject_duplicate_cutoffs(ordered, window=window, seed=seed)
        runs.append(
            {
                "window": window,
                "seed": seed,
                "stages": {
                    stage.value: _summarize_stage(
                        ordered,
                        stage=stage,
                        fixed_notional=notional,
                        cost_stress_bps=costs,
                    )
                    for stage in ArchitectureStage
                },
            }
        )

    return {
        "schema_version": ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION,
        "stages": [stage.value for stage in ArchitectureStage],
        "constraints": {
            "fixed_notional": notional,
            "max_abs_exposure": 1.0,
            "compounding": False,
            "parameter_search": False,
            "risk_overlay": False,
            "volatility_sizing": False,
            "no_trade_band": False,
            "options_state": False,
            "execution_market_logic": False,
        },
        "cost_stress_bps": list(costs),
        "runs": runs,
    }


def observations_from_rows(rows: list[dict[str, Any]]) -> list[ArchitectureObservation]:
    """Construct typed observations from the replay bridge's minimal rows."""
    return [ArchitectureObservation(**row) for row in rows]


def _summarize_stage(
    observations: list[ArchitectureObservation],
    *,
    stage: ArchitectureStage,
    fixed_notional: float,
    cost_stress_bps: tuple[float, ...],
) -> dict[str, Any]:
    attribution_rows: list[dict[str, Any]] = []
    previous_exposure = 0.0
    evaluable_count = 0
    active_evaluable_count = 0
    direction_hit_count = 0
    direction_opportunity_count = 0
    gross_pnl = 0.0
    turnover_notional = 0.0
    regime_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    for observation in observations:
        exposure = stage_exposure(observation, stage)
        forward_return = observation.forward_return
        row: dict[str, Any] = {
            "decision_id": observation.decision_id,
            "cutoff_time_ns": observation.cutoff_time_ns,
            "regime": observation.regime,
            "exposure": exposure,
            "forward_return": forward_return,
            "gross_pnl": None,
            "turnover_notional": 0.0,
        }
        if forward_return is not None:
            realized_return = _finite_float(forward_return, name="forward_return")
            row_turnover = abs(exposure - previous_exposure) * fixed_notional
            row_gross_pnl = exposure * realized_return * fixed_notional
            row["gross_pnl"] = row_gross_pnl
            row["turnover_notional"] = row_turnover
            evaluable_count += 1
            gross_pnl += row_gross_pnl
            turnover_notional += row_turnover
            previous_exposure = exposure
            if exposure != 0.0:
                active_evaluable_count += 1
                if realized_return != 0.0:
                    direction_opportunity_count += 1
                    if exposure * realized_return > 0.0:
                        direction_hit_count += 1
            regime_rows[observation.regime].append(row)
        attribution_rows.append(row)

    return {
        "stage": stage.value,
        "observation_count": len(observations),
        "evaluable_count": evaluable_count,
        "coverage": _ratio(evaluable_count, len(observations)),
        "active_evaluable_count": active_evaluable_count,
        "signal_coverage": _ratio(active_evaluable_count, evaluable_count),
        "direction_hit_count": direction_hit_count,
        "direction_opportunity_count": direction_opportunity_count,
        "direction_hit_rate": _ratio(direction_hit_count, direction_opportunity_count),
        "gross_pnl": gross_pnl,
        "turnover_notional": turnover_notional,
        "cost_stress": {
            _number_key(cost_bps): _cost_summary(
                attribution_rows,
                fixed_notional=fixed_notional,
                cost_bps=cost_bps,
            )
            for cost_bps in cost_stress_bps
        },
        "regime_splits": {
            regime: _regime_summary(rows, fixed_notional=fixed_notional) for regime, rows in sorted(regime_rows.items())
        },
        "attribution_rows": attribution_rows,
    }


def _cost_summary(
    rows: list[dict[str, Any]],
    *,
    fixed_notional: float,
    cost_bps: float,
) -> dict[str, float]:
    transaction_cost = sum(float(row["turnover_notional"]) for row in rows) * cost_bps / 10_000.0
    gross_pnl = sum(float(row["gross_pnl"]) for row in rows if row["gross_pnl"] is not None)
    net_pnl = gross_pnl - transaction_cost
    equity = fixed_notional
    peak = fixed_notional
    max_drawdown = 0.0
    for row in rows:
        if row["gross_pnl"] is None:
            continue
        row_cost = float(row["turnover_notional"]) * cost_bps / 10_000.0
        equity += float(row["gross_pnl"]) - row_cost
        peak = max(peak, equity)
        if peak > 0.0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return {
        "cost_bps": cost_bps,
        "transaction_cost": transaction_cost,
        "net_pnl": net_pnl,
        "return_on_fixed_capital": net_pnl / fixed_notional,
        "max_drawdown": max_drawdown,
    }


def _regime_summary(rows: list[dict[str, Any]], *, fixed_notional: float) -> dict[str, Any]:
    active_rows = [row for row in rows if float(row["exposure"]) != 0.0]
    opportunity_rows = [row for row in active_rows if float(row["forward_return"]) != 0.0]
    direction_hits = sum(1 for row in opportunity_rows if float(row["exposure"]) * float(row["forward_return"]) > 0.0)
    gross_pnl = sum(float(row["gross_pnl"]) for row in rows)
    return {
        "evaluable_count": len(rows),
        "active_evaluable_count": len(active_rows),
        "direction_hit_count": direction_hits,
        "direction_opportunity_count": len(opportunity_rows),
        "direction_hit_rate": _ratio(direction_hits, len(opportunity_rows)),
        "gross_pnl": gross_pnl,
        "return_on_fixed_capital": gross_pnl / fixed_notional,
    }


def _normalize_cost_stress(cost_stress_bps: tuple[float, ...]) -> tuple[float, ...]:
    normalized = tuple(sorted({_finite_float(value, name="cost_stress_bps") for value in cost_stress_bps}))
    if not normalized:
        raise ValueError("cost_stress_bps must not be empty")
    if normalized[0] < 0.0:
        raise ValueError("cost_stress_bps must be non-negative")
    return normalized


def _reject_duplicate_cutoffs(
    observations: list[ArchitectureObservation],
    *,
    window: str,
    seed: int,
) -> None:
    seen: set[int] = set()
    for observation in observations:
        if observation.cutoff_time_ns in seen:
            raise ValueError(f"duplicate cutoff_time_ns in window={window!r} seed={seed}")
        seen.add(observation.cutoff_time_ns)


def _finite_float(value: float, *, name: str) -> float:
    resolved = float(value)
    if not math.isfinite(resolved):
        raise ValueError(f"{name} must be finite")
    return resolved


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _number_key(value: float) -> str:
    return str(int(value)) if value.is_integer() else format(value, "g")
