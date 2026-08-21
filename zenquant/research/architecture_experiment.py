"""Pure transforms for the frozen A0-A5 architecture experiment.

A0/A1 remain isolated direction attribution. Later transforms add volatility,
regime, one target band, and observation-only cost instrumentation in the
approved causal order. Options state is deliberately outside this module.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from zenquant.domain import DecisionSnapshot, PositionEpisode

from .gate_v3 import evaluate_gate_v3

ARCHITECTURE_ATTRIBUTION_SCHEMA_VERSION = "architecture_attribution.v2"
DEFAULT_COST_STRESS_BPS = (0.0, 5.0, 15.0)
DEFAULT_ARCHITECTURE_MANIFEST_PATH = Path("config/research/architecture_experiment_v1.json")
ARCHITECTURE_EXPERIMENT_V1_MANIFEST_SHA256 = "c982d074b24ec21235dfefbd972d936dc4f5a4ab0360964013576c90729bfbe0"
A2_ANNUALIZED_VOLATILITY_TARGET = 0.20
A2_MAX_ABS_EXPOSURE = 1.50
A4_NO_TRADE_BAND_FRACTION = 0.04
A4_SAFETY_BYPASS_REASONS = frozenset({"KILL", "UNSAFE", "CAP_VIOLATION"})
A3_REGIME_MULTIPLIERS = MappingProxyType(
    {
        "NORMAL": 1.0,
        "TRADABLE": 1.0,
        "TRANSITION": 0.5,
        "NOISY": 0.5,
        "HOSTILE": 0.1,
        "STRESS": 0.1,
        "UNSAFE": 0.0,
        "UNKNOWN": 0.0,
    }
)


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
    A3 = "A3"
    A4 = "A4"
    A5_SHADOW = "A5_SHADOW"


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
    available_at_ns: int | None = None
    annualized_volatility: float | None = None
    risk_regime: str = "UNKNOWN"
    frozen_regime_multiplier: float | None = None
    exchange_min_quantity: float = 0.0
    reduce_only: bool = False
    safety_reasons: tuple[str, ...] = ()
    estimated_fee_bps: float = 0.0
    estimated_slippage_bps: float = 0.0
    reference_price: float | None = None
    actual_fill_price: float | None = None
    forward_return_3: float | None = None
    forward_return_6: float | None = None
    directional_state: str = "UNKNOWN"
    volatility_state: str = "UNKNOWN"
    positioning_state: str = "UNKNOWN"
    quality_state: str = "UNKNOWN"
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArchitectureExecutionMetrics:
    """Observed Task-3/4 metrics for one concrete stage replay."""

    target_tracking_error: float
    time_to_target_bars: float | None
    time_to_target_status: str
    immediate_target_reached_rate: float
    right_censored_target_rate: float
    implementation_shortfall: float
    slippage: float
    target_overshoot: int
    unexpected_reversal: int
    executor_created_exposure: int
    episode_profit_factor: float | None
    episode_profit_factor_status: str
    position_switches_per_day: float

    def __post_init__(self) -> None:
        for name in (
            "target_tracking_error",
            "immediate_target_reached_rate",
            "right_censored_target_rate",
            "implementation_shortfall",
            "slippage",
            "position_switches_per_day",
        ):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")
        if self.time_to_target_bars is not None and not math.isfinite(
            float(self.time_to_target_bars)
        ):
            raise ValueError("time_to_target_bars must be finite when observed")
        if not self.time_to_target_status:
            raise ValueError("time_to_target_status must be explicit")
        for name in ("immediate_target_reached_rate", "right_censored_target_rate"):
            if not 0.0 <= float(getattr(self, name)) <= 1.0:
                raise ValueError(f"{name} must be between zero and one")
        for name in ("target_overshoot", "unexpected_reversal", "executor_created_exposure"):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.episode_profit_factor is not None and not math.isfinite(float(self.episode_profit_factor)):
            raise ValueError("episode_profit_factor must be finite when observed")


@dataclass(frozen=True, slots=True)
class CostShadowObservation:
    """Non-authoritative A5 cost observation for one target transition."""

    target_delta: float
    estimated_fee: float
    estimated_slippage: float
    round_trip_cost: float
    breakeven_move: float
    actual_implementation_shortfall: float | None
    forward_return_1: float | None
    forward_return_3: float | None
    forward_return_6: float | None
    blocks_order: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class ArchitectureStageExecutionRow:
    """One actual target-tracking observation for a later architecture stage."""

    cutoff_time_ns: int
    decision_id: str
    approved_exposure: float
    approved_target_qty: float
    filled_exposure_before: float
    filled_exposure_after: float
    filled_position_before: float
    filled_position_after: float
    target_delta_exposure: float
    reference_price: float
    actual_fill_price: float | None
    filled_qty: float
    fee: float
    actual_slippage: float
    actual_implementation_shortfall: float
    funding_pnl: float
    target_tracking_error: float
    time_to_target_bars: int | None
    reduce_only: bool
    safety_reasons: tuple[str, ...]
    forward_return_1: float | None
    forward_return_3: float | None
    forward_return_6: float | None


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
    execution_metrics_by_stage: Mapping[str, ArchitectureExecutionMetrics] = field(default_factory=dict)
    stage_execution_rows_by_stage: Mapping[str, tuple[ArchitectureStageExecutionRow, ...]] = field(default_factory=dict)


def stage_exposure(
    observation: ArchitectureObservation,
    stage: ArchitectureStage | str,
    *,
    current_position: float = 0.0,
    fixed_notional: float = 1.0,
) -> float:
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
    a2_exposure = max(-A2_MAX_ABS_EXPOSURE, min(A2_MAX_ABS_EXPOSURE, a1_score * volatility_scalar))
    if resolved_stage == ArchitectureStage.A2:
        return a2_exposure
    mapped_multiplier = frozen_regime_multiplier(observation.risk_regime)
    regime_multiplier = observation.frozen_regime_multiplier
    if regime_multiplier is None:
        regime_multiplier = mapped_multiplier
    else:
        regime_multiplier = _finite_float(regime_multiplier, name="frozen_regime_multiplier")
        if not math.isclose(regime_multiplier, mapped_multiplier, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("frozen regime multiplier does not match A3 mapping")
    a3_exposure = a2_exposure * regime_multiplier
    if resolved_stage == ArchitectureStage.A3:
        return a3_exposure

    normalized_regime = str(observation.risk_regime).strip().upper()
    safety_reasons = observation.safety_reasons
    if normalized_regime not in A3_REGIME_MULTIPLIERS or normalized_regime in {"UNSAFE", "UNKNOWN"}:
        safety_reasons = (*safety_reasons, "UNSAFE")
    return apply_target_no_trade_band(
        a3_exposure,
        current_position=current_position,
        exchange_min_quantity=_exchange_minimum_exposure(
            observation.exchange_min_quantity,
            reference_price=observation.reference_price,
            fixed_notional=fixed_notional,
        ),
        reduce_only=observation.reduce_only,
        safety_reasons=safety_reasons,
    )


def frozen_regime_multiplier(risk_regime: str) -> float:
    """Resolve the immutable A3 mapping and fail closed for new labels."""
    return A3_REGIME_MULTIPLIERS.get(str(risk_regime).strip().upper(), 0.0)


def bind_stage_inputs_from_snapshot(
    observation: ArchitectureObservation,
    *,
    snapshot: DecisionSnapshot,
    risk_regime: str,
    exchange_min_quantity: float,
    reference_price: float,
) -> ArchitectureObservation:
    """Bind A2-A4 inputs to the matching immutable 4h decision snapshot."""
    if snapshot.cutoff_time_ns != observation.cutoff_time_ns:
        raise ValueError("DecisionSnapshot cutoff does not match architecture observation")
    if snapshot.formula_hash != observation.formula_hash:
        raise ValueError("DecisionSnapshot formula hash does not match architecture observation")
    if not math.isclose(snapshot.alpha_raw_score, observation.raw_score, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("DecisionSnapshot raw alpha does not match architecture observation")
    if not math.isclose(
        snapshot.alpha_confidence,
        observation.frozen_confidence,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("DecisionSnapshot confidence does not match architecture observation")
    if not math.isclose(
        snapshot.volatility_target,
        A2_ANNUALIZED_VOLATILITY_TARGET,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("DecisionSnapshot does not use the frozen A2 volatility target")
    if not math.isclose(snapshot.exposure_cap, A2_MAX_ABS_EXPOSURE, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("DecisionSnapshot does not use the frozen A2 exposure cap")
    expected_multiplier = frozen_regime_multiplier(risk_regime)
    if not math.isclose(
        snapshot.regime_risk_multiplier,
        expected_multiplier,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("DecisionSnapshot regime multiplier does not match the frozen A3 mapping")
    _exchange_minimum_exposure(
        exchange_min_quantity,
        reference_price=reference_price,
        fixed_notional=1.0,
    )
    return replace(
        observation,
        annualized_volatility=snapshot.annualized_volatility,
        risk_regime=str(risk_regime).strip().upper(),
        frozen_regime_multiplier=snapshot.regime_risk_multiplier,
        exchange_min_quantity=exchange_min_quantity,
        reduce_only=snapshot.reduce_only,
        reference_price=reference_price,
    )


def apply_target_no_trade_band(
    target_position: float,
    *,
    current_position: float,
    exchange_min_quantity: float,
    reduce_only: bool = False,
    safety_reasons: tuple[str, ...] = (),
) -> float:
    """Apply A4's sole target-position deadband as a deterministic transform."""
    target = _finite_float(target_position, name="target_position")
    current = _finite_float(current_position, name="current_position")
    minimum_quantity = _finite_float(exchange_min_quantity, name="exchange_min_quantity")
    if minimum_quantity < 0.0:
        raise ValueError("exchange_min_quantity must be non-negative")
    normalized_safety_reasons = {str(reason).strip().upper() for reason in safety_reasons}
    if reduce_only or normalized_safety_reasons & A4_SAFETY_BYPASS_REASONS:
        return target
    threshold = max(
        A4_NO_TRADE_BAND_FRACTION * max(abs(current), abs(target)),
        minimum_quantity,
    )
    return current if abs(target - current) < threshold else target


def observe_cost_shadow(
    *,
    target_delta: float,
    fixed_notional: float,
    estimated_fee_bps: float,
    estimated_slippage_bps: float,
    reference_price: float | None,
    actual_fill_price: float | None,
    forward_return_1: float | None,
    forward_return_3: float | None,
    forward_return_6: float | None,
) -> CostShadowObservation:
    """Measure A5 costs without returning an execution decision."""
    delta = _finite_float(target_delta, name="target_delta")
    notional = _finite_float(fixed_notional, name="fixed_notional")
    fee_bps = _finite_float(estimated_fee_bps, name="estimated_fee_bps")
    slippage_bps = _finite_float(estimated_slippage_bps, name="estimated_slippage_bps")
    if notional <= 0.0:
        raise ValueError("fixed_notional must be positive")
    if fee_bps < 0.0 or slippage_bps < 0.0:
        raise ValueError("estimated cost bps must be non-negative")

    turnover_notional = abs(delta) * notional
    estimated_fee = turnover_notional * fee_bps / 10_000.0
    estimated_slippage = turnover_notional * slippage_bps / 10_000.0
    round_trip_cost = 2.0 * (estimated_fee + estimated_slippage)
    breakeven_move = round_trip_cost / turnover_notional if turnover_notional else 0.0
    actual_shortfall: float | None = None
    if reference_price is not None and actual_fill_price is not None:
        reference = _finite_float(reference_price, name="reference_price")
        fill_price = _finite_float(actual_fill_price, name="actual_fill_price")
        if reference <= 0.0 or fill_price <= 0.0:
            raise ValueError("observed prices must be positive")
        direction = 1.0 if delta > 0.0 else -1.0 if delta < 0.0 else 0.0
        actual_shortfall = direction * ((fill_price / reference) - 1.0) * turnover_notional

    return CostShadowObservation(
        target_delta=delta,
        estimated_fee=estimated_fee,
        estimated_slippage=estimated_slippage,
        round_trip_cost=round_trip_cost,
        breakeven_move=breakeven_move,
        actual_implementation_shortfall=actual_shortfall,
        forward_return_1=_optional_finite_float(forward_return_1, name="forward_return_1"),
        forward_return_3=_optional_finite_float(forward_return_3, name="forward_return_3"),
        forward_return_6=_optional_finite_float(forward_return_6, name="forward_return_6"),
    )


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
                    execution_rows=evidence.stage_execution_rows_by_stage.get(ArchitectureStage.A2.value),
                ),
                ArchitectureStage.A3.value: _summarize_stage(
                    ordered,
                    stage=ArchitectureStage.A3,
                    fixed_notional=notional,
                    cost_stress_bps=costs,
                    execution_rows=evidence.stage_execution_rows_by_stage.get(ArchitectureStage.A3.value),
                ),
                ArchitectureStage.A4.value: _summarize_stage(
                    ordered,
                    stage=ArchitectureStage.A4,
                    fixed_notional=notional,
                    cost_stress_bps=costs,
                    execution_rows=evidence.stage_execution_rows_by_stage.get(ArchitectureStage.A4.value),
                ),
                ArchitectureStage.A5_SHADOW.value: _summarize_stage(
                    ordered,
                    stage=ArchitectureStage.A5_SHADOW,
                    fixed_notional=notional,
                    cost_stress_bps=costs,
                    execution_rows=evidence.stage_execution_rows_by_stage.get(ArchitectureStage.A5_SHADOW.value),
                ),
            }
            _attach_execution_metrics(stages, evidence.execution_metrics_by_stage)
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
            "scope": "A0_A1_DIRECTION_ATTRIBUTION_ONLY",
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
            },
            ArchitectureStage.A3.value: {
                "regime_multipliers": dict(A3_REGIME_MULTIPLIERS),
                "unrecognized_multiplier": 0.0,
            },
            ArchitectureStage.A4.value: {
                "no_trade_band_fraction": A4_NO_TRADE_BAND_FRACTION,
                "exchange_minimum_floor": True,
                "safety_bypass_reasons": sorted(A4_SAFETY_BYPASS_REASONS),
            },
            ArchitectureStage.A5_SHADOW.value: {
                "observation_only": True,
                "blocks_orders": False,
                "forward_horizons_4h": [1, 3, 6],
            },
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
    execution_rows: tuple[ArchitectureStageExecutionRow, ...] | None = None,
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
    cost_shadow_rows: list[dict[str, Any]] = []
    execution_by_cutoff = _validated_stage_execution_rows(observations, execution_rows)

    for observation in observations:
        execution_row = execution_by_cutoff.get(observation.cutoff_time_ns)
        approved_exposure = (
            execution_row.approved_exposure
            if execution_row is not None
            else stage_exposure(
                observation,
                stage,
                current_position=previous_exposure,
                fixed_notional=fixed_notional,
            )
        )
        exposure = (
            execution_row.filled_exposure_after if execution_row is not None else approved_exposure
        )
        forward_return = _finite_float(observation.forward_return, name="forward_return")
        row: dict[str, Any] = {
            "alpha_id": observation.alpha_id,
            "cutoff_time_ns": observation.cutoff_time_ns,
            "regime": observation.regime,
            "exposure": exposure,
            "approved_exposure": approved_exposure,
            "forward_return": forward_return,
            "gross_pnl": 0.0,
            "turnover_notional": 0.0,
            "baseline_execution_cost": 0.0,
            "funding_pnl": 0.0,
        }
        realized_return = forward_return
        row_turnover = (
            execution_row.filled_qty
            * (execution_row.actual_fill_price or execution_row.reference_price)
            if execution_row is not None
            else abs(approved_exposure - previous_exposure) * fixed_notional
        )
        row_gross_pnl = exposure * realized_return * fixed_notional
        row["gross_pnl"] = row_gross_pnl
        row["turnover_notional"] = row_turnover
        if execution_row is not None:
            row["execution"] = asdict(execution_row)
            row["approved_target_gross_pnl"] = (
                approved_exposure * realized_return * fixed_notional
            )
            row["baseline_execution_cost"] = execution_row.fee + execution_row.actual_slippage
            row["funding_pnl"] = execution_row.funding_pnl
            row["execution_net_pnl"] = (
                row_gross_pnl
                + execution_row.funding_pnl
                - float(row["baseline_execution_cost"])
            )
        evaluable_count += 1
        gross_pnl += row_gross_pnl
        turnover_notional += row_turnover
        if stage == ArchitectureStage.A5_SHADOW:
            shadow = observe_cost_shadow(
                target_delta=(
                    execution_row.target_delta_exposure if execution_row is not None else exposure - previous_exposure
                ),
                fixed_notional=fixed_notional,
                estimated_fee_bps=observation.estimated_fee_bps,
                estimated_slippage_bps=observation.estimated_slippage_bps,
                reference_price=observation.reference_price,
                actual_fill_price=(
                    execution_row.actual_fill_price if execution_row is not None else observation.actual_fill_price
                ),
                forward_return_1=observation.forward_return,
                forward_return_3=observation.forward_return_3,
                forward_return_6=observation.forward_return_6,
            )
            if execution_row is not None:
                shadow = replace(
                    shadow,
                    actual_implementation_shortfall=execution_row.actual_implementation_shortfall,
                )
            shadow_payload = asdict(shadow)
            row["cost_shadow"] = shadow_payload
            cost_shadow_rows.append(shadow_payload)
        previous_exposure = approved_exposure
        if exposure != 0.0:
            active_evaluable_count += 1
            if realized_return != 0.0:
                direction_opportunity_count += 1
                if exposure * realized_return > 0.0:
                    direction_hit_count += 1
        regime_rows[observation.regime].append(row)
        attribution_rows.append(row)

    summary = {
        "stage": stage.value,
        "pnl_basis": (
            "ACTUAL_FILLED_EXPOSURE" if execution_by_cutoff else "APPROVED_TARGET_EXPOSURE"
        ),
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
    if stage == ArchitectureStage.A5_SHADOW:
        observed_shortfall = [
            float(row["actual_implementation_shortfall"])
            for row in cost_shadow_rows
            if row["actual_implementation_shortfall"] is not None
        ]
        summary["cost_shadow"] = {
            "blocks_order": False,
            "observation_count": len(cost_shadow_rows),
            "estimated_fee": sum(float(row["estimated_fee"]) for row in cost_shadow_rows),
            "estimated_slippage": sum(float(row["estimated_slippage"]) for row in cost_shadow_rows),
            "round_trip_cost": sum(float(row["round_trip_cost"]) for row in cost_shadow_rows),
            "actual_implementation_shortfall": sum(observed_shortfall) if observed_shortfall else None,
            "actual_shortfall_observation_count": len(observed_shortfall),
            "forward_label_counts": {
                str(horizon): sum(1 for row in cost_shadow_rows if row[f"forward_return_{horizon}"] is not None)
                for horizon in (1, 3, 6)
            },
        }
    return summary


def _validated_stage_execution_rows(
    observations: list[ArchitectureObservation],
    execution_rows: tuple[ArchitectureStageExecutionRow, ...] | None,
) -> dict[int, ArchitectureStageExecutionRow]:
    if execution_rows is None:
        return {}
    expected = {item.cutoff_time_ns for item in observations}
    indexed = {item.cutoff_time_ns: item for item in execution_rows}
    if len(indexed) != len(execution_rows) or set(indexed) != expected:
        raise ValueError(
            "architecture stage execution rows do not match evaluable observations: "
            f"missing={sorted(expected - set(indexed))!r} extra={sorted(set(indexed) - expected)!r}"
        )
    return indexed


def _attach_execution_metrics(
    stages: dict[str, dict[str, Any]],
    observed_metrics: Mapping[str, ArchitectureExecutionMetrics],
) -> None:
    tracked_stages = tuple(
        stage.value
        for stage in (
            ArchitectureStage.A2,
            ArchitectureStage.A3,
            ArchitectureStage.A4,
            ArchitectureStage.A5_SHADOW,
        )
    )
    extra = sorted(set(observed_metrics) - set(tracked_stages))
    if extra:
        raise ValueError(f"execution metrics contain unapproved architecture stages: {extra!r}")
    metric_names = tuple(ArchitectureExecutionMetrics.__dataclass_fields__)
    for stage in tracked_stages:
        metrics = observed_metrics.get(stage)
        stages[stage]["execution_metrics"] = (
            {"status": "OBSERVED", **asdict(metrics)}
            if metrics is not None
            else {"status": "NOT_OBSERVED", **dict.fromkeys(metric_names)}
        )


def _cost_summary(
    rows: list[dict[str, Any]],
    *,
    fixed_notional: float,
    cost_bps: float,
) -> dict[str, float]:
    baseline_execution_cost = sum(float(row.get("baseline_execution_cost", 0.0)) for row in rows)
    additional_stress_cost = (
        sum(float(row["turnover_notional"]) for row in rows) * cost_bps / 10_000.0
    )
    transaction_cost = baseline_execution_cost + additional_stress_cost
    funding_pnl = sum(float(row.get("funding_pnl", 0.0)) for row in rows)
    gross_pnl = sum(float(row["gross_pnl"]) for row in rows if row["gross_pnl"] is not None)
    net_pnl = gross_pnl + funding_pnl - transaction_cost
    equity = fixed_notional
    peak = fixed_notional
    max_drawdown = 0.0
    for row in rows:
        if row["gross_pnl"] is None:
            continue
        row_cost = float(row.get("baseline_execution_cost", 0.0)) + (
            float(row["turnover_notional"]) * cost_bps / 10_000.0
        )
        equity += float(row["gross_pnl"]) + float(row.get("funding_pnl", 0.0)) - row_cost
        peak = max(peak, equity)
        if peak > 0.0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return {
        "cost_bps": cost_bps,
        "baseline_execution_cost": baseline_execution_cost,
        "additional_stress_cost": additional_stress_cost,
        "transaction_cost": transaction_cost,
        "funding_pnl": funding_pnl,
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
            or run.alpha_snapshot_count > run.confirmed_4h_row_count
            or run.terminal_excluded_count != run.excluded_non_evaluable_count
            or len(run.observations) + run.terminal_excluded_count != run.confirmed_4h_row_count
            or run.terminal_excluded_count < run.confirmed_4h_row_count - run.alpha_snapshot_count
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


def _optional_finite_float(value: float | None, *, name: str) -> float | None:
    return None if value is None else _finite_float(value, name=name)


def _exchange_minimum_exposure(
    exchange_minimum_quantity: float,
    *,
    reference_price: float | None,
    fixed_notional: float,
) -> float:
    minimum_quantity = _finite_float(exchange_minimum_quantity, name="exchange_minimum_quantity")
    if minimum_quantity < 0.0:
        raise ValueError("exchange_minimum_quantity must be non-negative")
    if minimum_quantity == 0.0:
        return 0.0
    notional = _finite_float(fixed_notional, name="fixed_notional")
    if notional <= 0.0:
        raise ValueError("fixed_notional must be positive")
    if reference_price is None:
        raise ValueError("reference_price is required when exchange_minimum_quantity is positive")
    price = _finite_float(reference_price, name="reference_price")
    if price <= 0.0:
        raise ValueError("reference_price must be positive")
    return minimum_quantity * price / notional


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _number_key(value: float) -> str:
    return str(int(value)) if value.is_integer() else format(value, "g")
