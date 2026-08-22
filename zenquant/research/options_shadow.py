"""Observation-only OptionsState attachment and OOS attribution.

This module is deliberately downstream of the risk-final decision snapshot.
It may add an auditable sidecar reference, but it never republishes on a
strategy or execution subject and never returns a target or risk decision.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from zenquant.data.options_state import build_options_state, validate_options_state_identity
from zenquant.domain import DecisionSnapshot, OptionsState, Timeframe
from zenquant.infra import InMemoryJetStream
from zenquant.strategy.decision_snapshot import attach_options_state_id

OptionsStateResolver = Callable[[DecisionSnapshot], OptionsState]


@dataclass(frozen=True, slots=True)
class OptionsShadowConfig:
    """Explicit default-off switch for the research sidecar."""

    enabled: bool = False


@dataclass(frozen=True, slots=True)
class OptionsStateDecisionLink:
    """Auditable link from the risk-final decision to its sidecar copy."""

    risk_final_decision_id: str
    attached_decision_id: str
    options_state_id: str
    cutoff_time_ns: int


@dataclass(frozen=True, slots=True)
class OptionsStateSidecarFailure:
    risk_final_decision_id: str
    cutoff_time_ns: int
    stage: str
    error_type: str


class OptionsStateSidecarLedger:
    """Collision-safe OptionsState payload and decision-link ledger."""

    def __init__(self) -> None:
        self._states: dict[str, OptionsState] = {}
        self._links: list[OptionsStateDecisionLink] = []
        self._failures: list[OptionsStateSidecarFailure] = []

    @property
    def states(self) -> tuple[OptionsState, ...]:
        return tuple(self._states[key] for key in sorted(self._states))

    @property
    def links(self) -> tuple[OptionsStateDecisionLink, ...]:
        return tuple(self._links)

    @property
    def failures(self) -> tuple[OptionsStateSidecarFailure, ...]:
        return tuple(self._failures)

    def record_state(self, state: OptionsState) -> None:
        existing = self._states.get(state.options_state_id)
        if existing is not None and existing != state:
            raise ValueError(f"OptionsState identity collision: {state.options_state_id}")
        self._states[state.options_state_id] = state

    def record_link(self, link: OptionsStateDecisionLink) -> None:
        self._links.append(link)

    def record_failure(self, failure: OptionsStateSidecarFailure) -> None:
        self._failures.append(failure)


class OptionsShadowObserver:
    """Attach a PIT OptionsState ID on an observation-only bus subject."""

    def __init__(
        self,
        *,
        bus: InMemoryJetStream,
        config: OptionsShadowConfig | None = None,
        state_resolver: OptionsStateResolver | None = None,
        ledger: OptionsStateSidecarLedger | None = None,
    ) -> None:
        self.config = config or OptionsShadowConfig()
        self.ledger = ledger or OptionsStateSidecarLedger()
        self._bus = bus
        self._state_resolver = state_resolver
        if self.config.enabled:
            self._bus.subscribe("strategy.decision_snapshot.4h.>", self._on_snapshot)

    def _on_snapshot(self, _: str, payload: object) -> None:
        if not isinstance(payload, DecisionSnapshot):
            return
        if payload.timeframe != Timeframe.TF_4H or not payload.symbol.upper().startswith("BTC"):
            return
        stage = "STATE_RESOLUTION"
        try:
            state = self._resolve_or_unknown(payload)
            stage = "STATE_LEDGER"
            self.ledger.record_state(state)
            attached = attach_options_state_id(payload, state.options_state_id)
            stage = "ATTACHED_SNAPSHOT_PUBLICATION"
            self._bus.publish(f"research.options_shadow.snapshot.4h.{payload.symbol}", attached)
            stage = "LINK_LEDGER"
            self.ledger.record_link(
                OptionsStateDecisionLink(
                    risk_final_decision_id=payload.decision_id,
                    attached_decision_id=attached.decision_id,
                    options_state_id=state.options_state_id,
                    cutoff_time_ns=payload.cutoff_time_ns,
                )
            )
        except Exception as exc:
            self.ledger.record_failure(
                OptionsStateSidecarFailure(
                    risk_final_decision_id=payload.decision_id,
                    cutoff_time_ns=payload.cutoff_time_ns,
                    stage=stage,
                    error_type=type(exc).__name__,
                )
            )
            # Research sidecars are fail-open by contract: execution remains
            # subscribed to the original risk-final snapshot.
            return

    def _resolve_or_unknown(self, snapshot: DecisionSnapshot) -> OptionsState:
        state: OptionsState | None = None
        if self._state_resolver is not None:
            try:
                resolved = self._state_resolver(snapshot)
                state = resolved if isinstance(resolved, OptionsState) else None
            except Exception:
                state = None
        if not _is_visible_state(state, snapshot):
            return _unknown_state(snapshot)
        if state.is_stale and (state.trend_quality != "UNKNOWN" or state.shadow_risk_multiplier != 1.0):
            return _unknown_state(snapshot)
        if state.trend_quality == "UNKNOWN" and state.shadow_risk_multiplier != 1.0:
            return _unknown_state(snapshot)
        return state


def _is_visible_state(state: OptionsState | None, snapshot: DecisionSnapshot) -> bool:
    if state is None:
        return False
    return (
        bool(state.options_state_id)
        and validate_options_state_identity(state)
        and state.symbol.upper().startswith("BTC")
        and state.cutoff_time_ns == snapshot.cutoff_time_ns
        and 0 <= state.source_timestamp_ns <= snapshot.cutoff_time_ns
    )


def _unknown_state(snapshot: DecisionSnapshot) -> OptionsState:
    return build_options_state(
        (),
        cutoff_time_ns=snapshot.cutoff_time_ns,
        realized_vol_30d=(snapshot.annualized_volatility if snapshot.annualized_volatility > 0.0 else None),
        realized_vol_source_timestamp_ns=snapshot.cutoff_time_ns,
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def execution_material_snapshot_bytes(snapshot: DecisionSnapshot) -> bytes:
    """Canonical snapshot bytes excluding only observation identity metadata."""
    payload = asdict(snapshot)
    payload.pop("decision_id")
    payload.pop("options_state_id")
    return json.dumps(
        _json_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class OptionsShadowObservation:
    """One labeled OOS 4h return joined to an observed OptionsState."""

    cutoff_time_ns: int
    options_state_id: str
    trend_quality: str
    market_regime: str
    return_value: float


@dataclass(frozen=True, slots=True)
class OptionsShadowBucketMetrics:
    status: str
    sample_count: int
    options_state_count: int
    hostile_sample_count: int
    hostile_max_drawdown: float
    cvar_95: float | None
    tail_threshold: float | None
    tail_loss_count: int
    tail_loss_mean: float | None
    trend_sample_count: int
    trend_return: float
    trend_return_retention: float | None


@dataclass(frozen=True, slots=True)
class OptionsShadowCounterfactualReport:
    oos_label: str
    observation_only: bool
    activation_recommendation: None
    status: str
    minimum_total_samples: int
    minimum_known_samples: int
    minimum_hostile_samples: int
    minimum_trend_samples: int
    off: OptionsShadowBucketMetrics
    by_trend_quality: dict[str, OptionsShadowBucketMetrics]


def build_options_shadow_counterfactual(
    observations: Iterable[OptionsShadowObservation],
    *,
    oos_label: str,
    minimum_total_samples: int = 30,
    minimum_known_samples: int = 10,
    minimum_hostile_samples: int = 5,
    minimum_trend_samples: int = 5,
) -> OptionsShadowCounterfactualReport:
    """Aggregate OFF baseline and shadow quality associations without trading."""
    ordered = tuple(sorted(observations, key=lambda item: item.cutoff_time_ns))
    if len({item.cutoff_time_ns for item in ordered}) != len(ordered):
        raise ValueError("Options shadow observations require unique 4h cutoffs")
    for item in ordered:
        if item.cutoff_time_ns % (4 * 60 * 60 * 1_000_000_000) != 0:
            raise ValueError("Options shadow observations require confirmed 4h cutoffs")
        if not math.isfinite(item.return_value) or item.return_value <= -1.0:
            raise ValueError("Options shadow returns must be finite and greater than -1")
    off = _bucket_metrics(ordered, baseline_trend_return=None, minimum_samples=minimum_total_samples)
    baseline_trend_return = off.trend_return
    grouped: dict[str, list[OptionsShadowObservation]] = {"UNKNOWN": []}
    for item in ordered:
        quality = item.trend_quality.strip().upper() or "UNKNOWN"
        grouped.setdefault(quality, []).append(item)
    by_quality = {
        quality: _bucket_metrics(tuple(rows), baseline_trend_return=baseline_trend_return, minimum_samples=10)
        for quality, rows in sorted(grouped.items())
    }
    known_count = sum(1 for item in ordered if item.trend_quality.strip().upper() != "UNKNOWN")
    sufficient = (
        len(ordered) >= minimum_total_samples
        and known_count >= minimum_known_samples
        and off.hostile_sample_count >= minimum_hostile_samples
        and off.trend_sample_count >= minimum_trend_samples
    )
    return OptionsShadowCounterfactualReport(
        oos_label=oos_label,
        observation_only=True,
        activation_recommendation=None,
        status="SUFFICIENT" if sufficient else "INSUFFICIENT_SAMPLE",
        minimum_total_samples=minimum_total_samples,
        minimum_known_samples=minimum_known_samples,
        minimum_hostile_samples=minimum_hostile_samples,
        minimum_trend_samples=minimum_trend_samples,
        off=off,
        by_trend_quality=by_quality,
    )


def _bucket_metrics(
    observations: tuple[OptionsShadowObservation, ...],
    *,
    baseline_trend_return: float | None,
    minimum_samples: int,
) -> OptionsShadowBucketMetrics:
    hostile_mask = tuple(item.market_regime.strip().upper() in {"HOSTILE", "STRESS", "UNSAFE"} for item in observations)
    hostile = tuple(item.return_value for item, selected in zip(observations, hostile_mask, strict=True) if selected)
    hostile_timeline = tuple(
        item.return_value if selected else 0.0 for item, selected in zip(observations, hostile_mask, strict=True)
    )
    all_returns = sorted(item.return_value for item in observations)
    tail_count = max(1, math.ceil(len(all_returns) * 0.05)) if all_returns else 0
    tail = tuple(all_returns[:tail_count])
    trend = tuple(
        item.return_value
        for item in observations
        if item.market_regime.strip().upper() in {"BULL", "EXPANSION", "TREND"}
    )
    trend_return = sum(trend)
    retention = None
    if baseline_trend_return is not None and not math.isclose(baseline_trend_return, 0.0, abs_tol=1e-15):
        retention = trend_return / baseline_trend_return
    return OptionsShadowBucketMetrics(
        status="SUFFICIENT" if len(observations) >= minimum_samples else "INSUFFICIENT_SAMPLE",
        sample_count=len(observations),
        options_state_count=len({item.options_state_id for item in observations}),
        hostile_sample_count=len(hostile),
        hostile_max_drawdown=_max_drawdown(hostile_timeline),
        cvar_95=(sum(tail) / len(tail) if tail else None),
        tail_threshold=(max(tail) if tail else None),
        tail_loss_count=sum(1 for value in tail if value < 0.0),
        tail_loss_mean=(sum(value for value in tail if value < 0.0) / sum(1 for value in tail if value < 0.0))
        if any(value < 0.0 for value in tail)
        else None,
        trend_sample_count=len(trend),
        trend_return=trend_return,
        trend_return_retention=retention,
    )


def build_options_shadow_oos_artifact(
    evidence: Any,
    *,
    options_store: Any,
    manifest_identity: str,
) -> dict[str, Any]:
    """Join frozen A5 OOS rows to PIT OptionsState and return JSON evidence."""
    window = str(evidence.window)
    base = {
        "schema_version": "options-shadow-counterfactual-v1",
        "window": window,
        "seed": int(evidence.seed),
        "stage": "A5_SHADOW",
        "observation_only": True,
        "activation_recommendation": None,
        "label_contract": "forward_return_1_available_at_cutoff_plus_1_confirmed_4h_bar",
        "metric_definitions": {
            "hostile_mdd": "full_chronological_timeline_non_hostile_returns_zeroed",
            "cvar_95": "mean_of_worst_ceil_5pct_observed_4h_returns",
            "tail_threshold": "largest_return_in_worst_ceil_5pct_tail",
            "trend_return_retention": "quality_bucket_bull_expansion_trend_return_sum/off_trend_return_sum",
            "unknown": "reported_as_separate_quality_bucket",
        },
        "identities": {
            "manifest": manifest_identity,
            "donor_config": str(evidence.donor_config_hash),
            "options_data": "sha256:" + hashlib.sha256(b"[]").hexdigest(),
        },
        "rows": [],
    }
    if window.lower().startswith("train"):
        return {**base, "status": "NOT_OOS", "sample_count": 0, "known_sample_count": 0, "unknown_sample_count": 0}

    rows_by_stage = evidence.stage_execution_rows_by_stage
    a4_rows = tuple(rows_by_stage.get("A4", ()))
    a5_rows = tuple(rows_by_stage.get("A5_SHADOW", ()))
    if len(a4_rows) != len(a5_rows):
        raise ValueError("A5_SHADOW row count differs from A4")
    a4_by_cutoff = {row.cutoff_time_ns: row for row in a4_rows}
    a5_by_cutoff = {row.cutoff_time_ns: row for row in a5_rows}
    if len(a4_by_cutoff) != len(a4_rows) or len(a5_by_cutoff) != len(a5_rows):
        raise ValueError("A4/A5_SHADOW rows require unique cutoffs")
    if set(a4_by_cutoff) != set(a5_by_cutoff):
        raise ValueError("A5_SHADOW cutoffs differ from A4")
    for cutoff, a5 in a5_by_cutoff.items():
        a4 = a4_by_cutoff[cutoff]
        if (a5.approved_exposure, a5.approved_target_qty, a5.reduce_only) != (
            a4.approved_exposure,
            a4.approved_target_qty,
            a4.reduce_only,
        ):
            raise ValueError("A5_SHADOW approved target differs from A4")

    observations = {item.cutoff_time_ns: item for item in evidence.observations}
    if len(observations) != len(evidence.observations):
        raise ValueError("OOS observations require unique cutoffs")
    joined: list[OptionsShadowObservation] = []
    artifact_rows: list[dict[str, Any]] = []
    states: list[OptionsState] = []
    for cutoff, stage_row in sorted(a5_by_cutoff.items()):
        observation = observations.get(cutoff)
        if observation is None or not (evidence.start_ns <= cutoff < evidence.end_ns):
            continue
        label = stage_row.forward_return_1
        if label is None:
            continue
        if not math.isclose(float(label), float(observation.forward_return), rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("A5_SHADOW forward label differs from frozen observation")
        state = options_store.lookup_options_state(
            cutoff,
            realized_vol_30d=observation.annualized_volatility,
            realized_vol_source_timestamp_ns=cutoff,
        )
        if not validate_options_state_identity(state) or state.source_timestamp_ns > cutoff:
            state = build_options_state(
                (),
                cutoff_time_ns=cutoff,
                realized_vol_30d=observation.annualized_volatility,
                realized_vol_source_timestamp_ns=cutoff,
            )
        states.append(state)
        joined.append(
            OptionsShadowObservation(
                cutoff_time_ns=cutoff,
                options_state_id=state.options_state_id,
                trend_quality=state.trend_quality,
                market_regime=observation.risk_regime,
                return_value=float(label),
            )
        )
        artifact_rows.append(
            {
                "cutoff_time_ns": cutoff,
                "label_available_at_ns": cutoff + 4 * 60 * 60 * 1_000_000_000,
                "options_state_id": state.options_state_id,
                "options_source_timestamp_ns": state.source_timestamp_ns,
                "trend_quality": state.trend_quality,
                "market_regime": observation.risk_regime,
                "forward_return_1": float(label),
            }
        )
    report = build_options_shadow_counterfactual(joined, oos_label=window)
    data_payload = [
        {
            "cutoff_time_ns": state.cutoff_time_ns,
            "options_state_id": state.options_state_id,
            "data_identity": state.data_identity,
            "calculation_version": state.calculation_version,
        }
        for state in states
    ]
    data_digest = hashlib.sha256(
        json.dumps(data_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    known_count = sum(1 for state in states if state.trend_quality != "UNKNOWN")
    return {
        **base,
        "status": report.status,
        "sample_count": len(joined),
        "known_sample_count": known_count,
        "unknown_sample_count": len(joined) - known_count,
        "identities": {**base["identities"], "options_data": f"sha256:{data_digest}"},
        "rows": artifact_rows,
        "report": asdict(report),
    }


def _max_drawdown(returns: tuple[float, ...]) -> float:
    wealth = 1.0
    peak = 1.0
    maximum = 0.0
    for value in returns:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        maximum = max(maximum, (peak - wealth) / peak)
    return maximum


__all__ = [
    "OptionsShadowBucketMetrics",
    "OptionsShadowConfig",
    "OptionsShadowCounterfactualReport",
    "OptionsShadowObservation",
    "OptionsShadowObserver",
    "OptionsStateDecisionLink",
    "OptionsStateResolver",
    "OptionsStateSidecarFailure",
    "OptionsStateSidecarLedger",
    "build_options_shadow_counterfactual",
    "build_options_shadow_oos_artifact",
    "execution_material_snapshot_bytes",
]
