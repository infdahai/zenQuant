"""Deterministic cross-round policy for framework research loops."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from zenquant.research.framework_params import (
    baseline_anchor_seed_trials,
    param_grid_for_mode,
)

HOSTILE_WINDOW_BLOCKERS = {
    "worst_dev_sharpe_worse_than_paulwei",
    "negative_worst_windows_increased",
    "drawdown_worse_than_baseline_relative_gate",
}
PROGRESSION_BLOCKERS = {
    "no_tuned_candidate_from_smoke",
    "stage_runtime_or_search_issue",
}

INCREASE_KEYS = {
    "pyramid_spacing_atr",
    "dca_spacing_atr",
    "exit_unwind_pct",
    "flip_min_confidence",
}
DECREASE_KEYS = {
    "dca_size_decay",
    "pyramid_add_pct",
    "pyramid_short_add_pct",
}


@dataclass(slots=True, frozen=True)
class LoopIterationPlan:
    stop: bool
    stop_reason: str | None
    next_budget: str
    adaptation_policy: str
    exploit_seed_trials: list[dict[str, Any]]
    explore_seed_trials: list[dict[str, Any]]
    anchor_seed_trials: list[dict[str, Any]]
    should_open_fast_or_full_training: bool
    loop_progression_blocked: bool

    @property
    def all_seed_trials(self) -> list[dict[str, Any]]:
        seeds = [
            *self.anchor_seed_trials,
            *self.exploit_seed_trials,
            *self.explore_seed_trials,
        ]
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for seed in seeds:
            key = json.dumps(seed, sort_keys=True, separators=(",", ":"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(dict(seed))
        return deduped


def _round_to_grid(value: Any, choices: list[float]) -> float:
    if not choices:
        return float(value)
    target = float(value)
    return min(choices, key=lambda item: abs(item - target))


def _project_seed_to_mode(seed: dict[str, Any], *, mode: str) -> dict[str, Any]:
    projected = dict(seed)
    grid = param_grid_for_mode(mode=mode)
    for key, value in tuple(projected.items()):
        choices = grid.get(key)
        if not choices:
            continue
        projected[key] = _round_to_grid(value, [float(item) for item in choices])
    return projected


def project_seed_trials_to_mode(seed_trials: list[dict[str, Any]], *, mode: str) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for seed in seed_trials:
        item = _project_seed_to_mode(seed, mode=mode)
        key = json.dumps(item, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        projected.append(item)
    return projected


def _neighbor_value(name: str, value: Any, *, mode: str) -> float:
    choices = sorted(float(item) for item in param_grid_for_mode(mode=mode).get(name, []))
    if not choices:
        return float(value)
    current = _round_to_grid(value, choices)
    idx = choices.index(current)
    if name in INCREASE_KEYS:
        idx = min(len(choices) - 1, idx + 1)
    elif name in DECREASE_KEYS:
        idx = max(0, idx - 1)
    return choices[idx]


def _top_trials(stage1_payload: dict[str, Any]) -> list[dict[str, Any]]:
    trials = stage1_payload.get("top_optuna_trials") or []
    return [dict(item) for item in trials if isinstance(item, dict)]


def _exploit_seeds(stage1_payload: dict[str, Any], *, mode: str) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    best_params = stage1_payload.get("optuna_params") or stage1_payload.get("rl_params")
    if isinstance(best_params, dict):
        seeds.append(dict(best_params))
    for item in _top_trials(stage1_payload)[:3]:
        params = item.get("params")
        if isinstance(params, dict):
            seeds.append(dict(params))
    mutated: list[dict[str, Any]] = []
    for params in seeds[1:3]:
        mutated_seed = dict(params)
        for key in ("pyramid_spacing_atr", "dca_spacing_atr", "flip_min_confidence"):
            if key in mutated_seed:
                mutated_seed[key] = _neighbor_value(key, mutated_seed[key], mode=mode)
        mutated.append(mutated_seed)
    return [*seeds, *mutated]


def _explore_seeds(
    final_report: dict[str, Any],
    stage1_payload: dict[str, Any],
    *,
    mode: str,
) -> tuple[list[dict[str, Any]], str]:
    scenario = ((final_report.get("post_smoke_scenario") or {}).get("scenario")) or ""
    blockers = set((final_report.get("full_training_assessment") or {}).get("blockers") or [])
    base = stage1_payload.get("optuna_params") or stage1_payload.get("rl_params") or {}
    if not isinstance(base, dict):
        base = {}

    if scenario == "scenario_b_hostile_window_failure" or blockers & HOSTILE_WINDOW_BLOCKERS:
        seeds: list[dict[str, Any]] = []
        if base:
            defensive = dict(base)
            for key in INCREASE_KEYS | DECREASE_KEYS:
                if key in defensive:
                    defensive[key] = _neighbor_value(key, defensive[key], mode=mode)
            seeds.append(defensive)

            faster_exit = dict(defensive)
            for key in ("exit_unwind_pct", "flip_min_confidence", "dca_spacing_atr"):
                if key in faster_exit:
                    faster_exit[key] = _neighbor_value(key, faster_exit[key], mode=mode)
            seeds.append(faster_exit)

            lighter_adds = dict(defensive)
            for key in ("pyramid_add_pct", "pyramid_short_add_pct", "dca_size_decay"):
                if key in lighter_adds:
                    lighter_adds[key] = _neighbor_value(key, lighter_adds[key], mode=mode)
            seeds.append(lighter_adds)
        return seeds, "hostile_window_defensive"

    if scenario == "scenario_b_no_tuned_candidate" or "no_tuned_candidate_from_smoke" in blockers:
        seeds = _exploit_seeds(stage1_payload, mode=mode)
        return seeds, "candidate_existence_recovery"

    if scenario in {"scenario_c_state_data_gap", "scenario_d_direction_gate_failure"}:
        return [], "blocked_progression"

    return [], "steady_state"


def plan_next_iteration(
    *,
    round_index: int,
    max_rounds: int,
    current_budget: str,
    final_report: dict[str, Any],
    stage1_payload: dict[str, Any],
    anchor_ratio: float = 0.2,
    exploit_ratio: float = 0.4,
    explore_ratio: float = 0.4,
    allow_fast_progression: bool = True,
) -> LoopIterationPlan:
    del anchor_ratio, exploit_ratio, explore_ratio

    scenario = ((final_report.get("post_smoke_scenario") or {}).get("scenario")) or ""
    blockers = set(final_report.get("handoff_blockers") or (final_report.get("full_training_assessment") or {}).get("blockers") or [])
    candidate_ready_for_handoff = bool(final_report.get("candidate_ready_for_handoff", False))

    if candidate_ready_for_handoff:
        return LoopIterationPlan(
            stop=True,
            stop_reason="candidate_ready_for_handoff",
            next_budget=current_budget,
            adaptation_policy="candidate_ready_for_handoff",
            anchor_seed_trials=[],
            exploit_seed_trials=[],
            explore_seed_trials=[],
            should_open_fast_or_full_training=False,
            loop_progression_blocked=False,
        )

    if round_index >= max_rounds:
        return LoopIterationPlan(
            stop=True,
            stop_reason="max_rounds_reached",
            next_budget=current_budget,
            adaptation_policy="max_rounds_reached",
            anchor_seed_trials=[],
            exploit_seed_trials=[],
            explore_seed_trials=[],
            should_open_fast_or_full_training=False,
            loop_progression_blocked=False,
        )

    next_budget = current_budget
    if (
        allow_fast_progression
        and round_index >= 4
        and current_budget == "smoke"
        and not (blockers & PROGRESSION_BLOCKERS)
        and not {"scenario_c_state_data_gap", "scenario_d_direction_gate_failure"}.__contains__(scenario)
    ):
        next_budget = "fast"

    mode = "baseline_anchored_train_only" if next_budget == "smoke" else "execution_first_v2_correlation_constrained"
    exploit = _exploit_seeds(stage1_payload, mode=mode)
    explore, adaptation_policy = _explore_seeds(final_report, stage1_payload, mode=mode)
    anchor = baseline_anchor_seed_trials()
    anchor = project_seed_trials_to_mode(anchor, mode=mode)
    exploit = project_seed_trials_to_mode(exploit, mode=mode)
    explore = project_seed_trials_to_mode(explore, mode=mode)
    loop_progression_blocked = scenario in {"scenario_c_state_data_gap", "scenario_d_direction_gate_failure"}

    return LoopIterationPlan(
        stop=False,
        stop_reason=None,
        next_budget=next_budget,
        adaptation_policy=adaptation_policy,
        anchor_seed_trials=anchor,
        exploit_seed_trials=exploit,
        explore_seed_trials=explore,
        should_open_fast_or_full_training=False,
        loop_progression_blocked=loop_progression_blocked,
    )
