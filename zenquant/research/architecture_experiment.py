"""Pure fixed-notional attribution for the frozen A0/A1 architecture stages.

This module deliberately knows nothing about risk, volatility sizing, options,
no-trade bands, or execution behavior.  It receives a minimal causal record and
attributes one-bar returns to three frozen exposure transforms only.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from zenquant.domain import PositionEpisode

from .gate_v3 import evaluate_gate_v3

ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION = "architecture_attribution.v1"
DEFAULT_COST_STRESS_BPS = (0.0, 5.0, 15.0)
DEFAULT_ARCHITECTURE_MANIFEST_PATH = Path("config/research/architecture_experiment_v1.json")
ARCHITECTURE_EXPERIMENT_V1_MANIFEST_SHA256 = "c982d074b24ec21235dfefbd972d936dc4f5a4ab0360964013576c90729bfbe0"
A2_ANNUALIZED_VOLATILITY_TARGET = 0.20
A2_MAX_ABS_EXPOSURE = 1.50


@dataclass(frozen=True, slots=True)
class ArchitectureWindow:
    label: str
    start_ns: int
    end_ns: int


@dataclass(frozen=True, slots=True)
class ArchitectureExperimentManifest:
    version: str
    sha256: str
    manifest_path: Path
    candidate_id: str
    candidate_source_path: str
    candidate_source_json_path: str
    candidate_source_sha256: str
    candidate_params_sha256: str
    candidate_params: Mapping[str, Any]
    profile_path: Path
    profile_sha256: str
    symbol: str
    portfolio_symbol: str
    alpha_timeframe: str
    alpha_horizon_bars: int
    alpha_label: str
    alpha_signal_roles: tuple[str, ...]
    a0_execution_timeframe: str
    a0_context_timeframe: str
    a0_direction_timeframe: str
    a0_bias_timeframe: str
    windows: tuple[ArchitectureWindow, ...]
    seeds: tuple[int, ...]
    perturbation: Mapping[str, float]
    cost_stress_bps: tuple[float, ...]

    @property
    def expected_run_keys(self) -> tuple[tuple[str, int], ...]:
        return tuple((window.label, seed) for window in self.windows for seed in self.seeds)


def load_architecture_experiment_manifest(
    path: Path = DEFAULT_ARCHITECTURE_MANIFEST_PATH,
) -> ArchitectureExperimentManifest:
    """Load the immutable V1 experiment manifest and reject any drift."""
    resolved_path = path
    if path == DEFAULT_ARCHITECTURE_MANIFEST_PATH and not path.exists():
        resolved_path = Path(__file__).parents[2] / path
    raw = resolved_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ARCHITECTURE_EXPERIMENT_V1_MANIFEST_SHA256:
        raise ValueError(
            "architecture experiment manifest SHA-256 mismatch: "
            f"expected {ARCHITECTURE_EXPERIMENT_V1_MANIFEST_SHA256}, got {digest}"
        )
    payload = json.loads(raw)
    candidate = payload["candidate"]
    params = dict(candidate["params"])
    params_digest = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if params_digest != candidate["params_sha256"]:
        raise ValueError("architecture experiment candidate params SHA-256 mismatch")
    profile = payload["profile"]
    repository_root = Path(__file__).parents[2]
    profile_path = (repository_root / str(profile["path"])).resolve()
    profile_digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    if profile_digest != profile["sha256"]:
        raise ValueError(
            f"architecture experiment profile SHA-256 mismatch: expected {profile['sha256']}, got {profile_digest}"
        )
    alpha = payload["alpha"]
    execution = payload["a0_execution_contract"]
    return ArchitectureExperimentManifest(
        version=str(payload["version"]),
        sha256=digest,
        manifest_path=resolved_path.resolve(),
        candidate_id=str(candidate["id"]),
        candidate_source_path=str(candidate["source_path"]),
        candidate_source_json_path=str(candidate["source_json_path"]),
        candidate_source_sha256=str(candidate["source_sha256"]),
        candidate_params_sha256=str(candidate["params_sha256"]),
        candidate_params=_deep_freeze(params),
        profile_path=profile_path,
        profile_sha256=str(profile["sha256"]),
        symbol=str(payload["symbol"]),
        portfolio_symbol=str(payload["portfolio_symbol"]),
        alpha_timeframe=str(alpha["timeframe"]),
        alpha_horizon_bars=int(alpha["horizon_bars"]),
        alpha_label=str(alpha["label"]),
        alpha_signal_roles=tuple(str(role) for role in alpha["signal_roles"]),
        a0_execution_timeframe=str(execution["execution_timeframe"]),
        a0_context_timeframe=str(execution["context_timeframe"]),
        a0_direction_timeframe=str(execution["direction_timeframe"]),
        a0_bias_timeframe=str(execution["bias_timeframe"]),
        windows=tuple(
            ArchitectureWindow(
                label=str(window["label"]),
                start_ns=int(window["start_ns"]),
                end_ns=int(window["end_ns"]),
            )
            for window in payload["windows"]
        ),
        seeds=tuple(int(seed) for seed in payload["seeds"]),
        perturbation=_deep_freeze({key: float(value) for key, value in payload["perturbation"].items()}),
        cost_stress_bps=tuple(float(value) for value in payload["cost_stress_bps"]),
    )


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_deep_freeze(item) for item in value)
    return value


class ArchitectureStage(StrEnum):
    """Frozen stages in causal experiment order."""

    A0 = "A0"
    A1_SIGN = "A1_SIGN"
    A1_SCORE = "A1_SCORE"
    A2 = "A2"


@dataclass(frozen=True, slots=True)
class ArchitectureObservation:
    """One evaluable point-in-time 4h alpha and its next retained 4h return."""

    alpha_id: str
    cutoff_time_ns: int
    regime: str
    raw_score: float
    frozen_confidence: float
    forward_return: float
    formula_hash: str
    alpha_config_hash: str
    annualized_volatility: float | None = None


@dataclass(frozen=True, slots=True)
class ArchitectureRunEvidence:
    """Complete A0 replay result and A1 causal rows for one frozen run."""

    window: str
    start_ns: int
    end_ns: int
    seed: int
    a0_metrics: dict[str, Any]
    a0_final_equity: float
    a0_config_hash: str
    observations: tuple[ArchitectureObservation, ...]
    excluded_non_evaluable_count: int
    confirmed_4h_row_count: int
    alpha_snapshot_count: int
    missing_alpha_count: int
    terminal_excluded_count: int
    position_episodes: tuple[PositionEpisode, ...] = ()


def stage_exposure(observation: ArchitectureObservation, stage: ArchitectureStage | str) -> float:
    """Return one pure cumulative stage exposure."""
    resolved_stage = ArchitectureStage(stage)
    if resolved_stage == ArchitectureStage.A0:
        raise ValueError("A0 is represented by actual frozen replay metrics, not an exposure transform")

    raw_score = _finite_float(observation.raw_score, name="raw_score")
    direction = 1.0 if raw_score > 0.0 else -1.0 if raw_score < 0.0 else 0.0
    if resolved_stage == ArchitectureStage.A1_SIGN:
        return direction

    confidence = _finite_float(observation.frozen_confidence, name="frozen_confidence")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("frozen_confidence must be between 0 and 1")
    a1_score = direction * confidence
    if resolved_stage == ArchitectureStage.A1_SCORE:
        return a1_score

    annualized_volatility = observation.annualized_volatility
    if annualized_volatility is None:
        return 0.0
    resolved_volatility = float(annualized_volatility)
    if not math.isfinite(resolved_volatility) or resolved_volatility <= 0.0:
        return 0.0
    volatility_scalar = A2_ANNUALIZED_VOLATILITY_TARGET / resolved_volatility
    return max(-A2_MAX_ABS_EXPOSURE, min(A2_MAX_ABS_EXPOSURE, a1_score * volatility_scalar))


def build_architecture_attribution_report(
    run_evidence: list[ArchitectureRunEvidence],
    *,
    manifest: ArchitectureExperimentManifest,
    fixed_notional: float,
) -> dict[str, Any]:
    """Validate and aggregate the complete immutable window-by-seed matrix."""
    notional = _finite_float(fixed_notional, name="fixed_notional")
    if notional <= 0.0:
        raise ValueError("fixed_notional must be positive")
    costs = _normalize_cost_stress(manifest.cost_stress_bps)
    indexed = _validate_run_matrix(run_evidence, manifest=manifest)

    runs: list[dict[str, Any]] = []
    for window in manifest.windows:
        for seed in manifest.seeds:
            evidence = indexed[(window.label, seed)]
            ordered = sorted(evidence.observations, key=lambda item: (item.cutoff_time_ns, item.alpha_id))
            _reject_duplicate_cutoffs(ordered, window=window.label, seed=seed)
            formula_hashes = sorted({item.formula_hash for item in ordered})
            alpha_config_hashes = sorted({item.alpha_config_hash for item in ordered})
            if not all(formula_hashes) or not all(alpha_config_hashes):
                raise ValueError(f"missing A1 identity in window={window.label!r} seed={seed}")
            a0_stage = {
                "stage": ArchitectureStage.A0.value,
                "metrics": dict(evidence.a0_metrics),
                "final_equity": _finite_float(evidence.a0_final_equity, name="a0_final_equity"),
                "config_hash": evidence.a0_config_hash,
            }
            if not evidence.a0_metrics or not evidence.a0_config_hash:
                raise ValueError(f"missing A0 identity in window={window.label!r} seed={seed}")
            elapsed_days = max(1.0, (evidence.end_ns - evidence.start_ns) / 1_000_000_000 / 86_400)
            gate_v3 = evaluate_gate_v3(
                evidence.position_episodes,
                elapsed_days=elapsed_days,
                fill_profit_factor=evidence.a0_metrics.get("profit_factor"),
                action_profit_factor=evidence.a0_metrics.get("action_profit_factor"),
            )
            a0_stage["gate_v3"] = gate_v3.to_dict()
            a0_stage["position_episodes"] = [
                asdict(episode)
                for _, episode in sorted(
                    enumerate(evidence.position_episodes),
                    key=lambda item: (item[1].opened_at_ns, item[1].symbol, item[0]),
                )
            ]
            stages = {
                ArchitectureStage.A0.value: a0_stage,
                ArchitectureStage.A1_SIGN.value: _summarize_stage(
                    ordered,
                    stage=ArchitectureStage.A1_SIGN,
                    fixed_notional=notional,
                    cost_stress_bps=costs,
                ),
                ArchitectureStage.A1_SCORE.value: _summarize_stage(
                    ordered,
                    stage=ArchitectureStage.A1_SCORE,
                    fixed_notional=notional,
                    cost_stress_bps=costs,
                ),
                ArchitectureStage.A2.value: _summarize_stage(
                    ordered,
                    stage=ArchitectureStage.A2,
                    fixed_notional=notional,
                    cost_stress_bps=costs,
                ),
            }
            runs.append(
                {
                    "window": window.label,
                    "start_ns": window.start_ns,
                    "end_ns": window.end_ns,
                    "seed": seed,
                    "confirmed_4h_row_count": evidence.confirmed_4h_row_count,
                    "alpha_snapshot_count": evidence.alpha_snapshot_count,
                    "missing_alpha_count": evidence.missing_alpha_count,
                    "terminal_excluded_count": evidence.terminal_excluded_count,
                    "excluded_non_evaluable_count": evidence.excluded_non_evaluable_count,
                    "identities": {
                        "a0_config_hash": evidence.a0_config_hash,
                        "formula_hashes": formula_hashes,
                        "alpha_config_hashes": alpha_config_hashes,
                    },
                    "stages": stages,
                }
            )

    return {
        "schema_version": ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION,
        "stages": [stage.value for stage in ArchitectureStage],
        "manifest": {
            "version": manifest.version,
            "sha256": manifest.sha256,
            "candidate_id": manifest.candidate_id,
            "candidate_source_sha256": manifest.candidate_source_sha256,
            "candidate_params_sha256": manifest.candidate_params_sha256,
            "profile_path": str(manifest.profile_path),
            "profile_sha256": manifest.profile_sha256,
            "windows": [
                {"label": window.label, "start_ns": window.start_ns, "end_ns": window.end_ns}
                for window in manifest.windows
            ],
            "seeds": list(manifest.seeds),
            "perturbation": dict(manifest.perturbation),
            "cost_stress_bps": list(manifest.cost_stress_bps),
            "alpha": {
                "symbol": manifest.portfolio_symbol,
                "timeframe": manifest.alpha_timeframe,
                "horizon_bars": manifest.alpha_horizon_bars,
                "label": manifest.alpha_label,
                "signal_roles": list(manifest.alpha_signal_roles),
            },
            "a0_execution_contract": {
                "execution_timeframe": manifest.a0_execution_timeframe,
                "context_timeframe": manifest.a0_context_timeframe,
                "direction_timeframe": manifest.a0_direction_timeframe,
                "bias_timeframe": manifest.a0_bias_timeframe,
            },
        },
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
        "stage_constraints": {
            ArchitectureStage.A2.value: {
                "annualized_volatility_target": A2_ANNUALIZED_VOLATILITY_TARGET,
                "max_abs_exposure": A2_MAX_ABS_EXPOSURE,
                "invalid_volatility_target": 0.0,
            }
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
        forward_return = _finite_float(observation.forward_return, name="forward_return")
        row: dict[str, Any] = {
            "alpha_id": observation.alpha_id,
            "cutoff_time_ns": observation.cutoff_time_ns,
            "regime": observation.regime,
            "exposure": exposure,
            "forward_return": forward_return,
            "gross_pnl": 0.0,
            "turnover_notional": 0.0,
        }
        realized_return = forward_return
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


def _validate_run_matrix(
    runs: list[ArchitectureRunEvidence],
    *,
    manifest: ArchitectureExperimentManifest,
) -> dict[tuple[str, int], ArchitectureRunEvidence]:
    expected = set(manifest.expected_run_keys)
    indexed: dict[tuple[str, int], ArchitectureRunEvidence] = {}
    duplicate_keys: set[tuple[str, int]] = set()
    window_by_label = {window.label: window for window in manifest.windows}
    for run in runs:
        key = (run.window, run.seed)
        if key in indexed:
            duplicate_keys.add(key)
        indexed[key] = run
    actual = set(indexed)
    if len(runs) != len(expected) or actual != expected or duplicate_keys:
        raise ValueError(
            "architecture run matrix mismatch: "
            f"missing={sorted(expected - actual)!r} extra={sorted(actual - expected)!r} "
            f"duplicates={sorted(duplicate_keys)!r}"
        )
    for key, run in indexed.items():
        window = window_by_label[run.window]
        if (run.start_ns, run.end_ns) != (window.start_ns, window.end_ns):
            raise ValueError(f"architecture run matrix boundary mismatch for {key!r}")
        if not run.observations:
            raise ValueError(f"architecture run observations must not be empty for {key!r}")
        if run.excluded_non_evaluable_count < 0 or run.confirmed_4h_row_count <= 0:
            raise ValueError(f"invalid architecture row counts for {key!r}")
        if (
            run.missing_alpha_count != 0
            or run.alpha_snapshot_count != run.confirmed_4h_row_count
            or run.terminal_excluded_count != run.excluded_non_evaluable_count
            or len(run.observations) + run.terminal_excluded_count != run.alpha_snapshot_count
        ):
            raise ValueError(
                f"architecture alpha coverage mismatch for {key!r}: "
                f"confirmed={run.confirmed_4h_row_count} alpha={run.alpha_snapshot_count} "
                f"missing={run.missing_alpha_count} terminal_excluded={run.terminal_excluded_count}"
            )
    return indexed


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
