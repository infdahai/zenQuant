"""Replay A2-A5 targets through the audited target-tracking V2 kernel."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace

from zenquant.domain import (
    DecisionSnapshot,
    EventMeta,
    ExecutionOrderAudit,
    ExecutionReport,
    MarkPriceUpdate,
    TargetPosition,
    Timeframe,
)
from zenquant.execution import ExecutionKernel, ExecutionKernelConfig, SimulatedGateway, SimulatedGatewayConfig
from zenquant.infra import InMemoryJetStream

from .architecture_experiment import (
    A2_ANNUALIZED_VOLATILITY_TARGET,
    A2_MAX_ABS_EXPOSURE,
    ArchitectureExecutionMetrics,
    ArchitectureObservation,
    ArchitectureStage,
    ArchitectureStageExecutionRow,
    frozen_regime_multiplier,
    stage_exposure,
)
from .episode_ledger import PositionEpisodeLedger, PriceObservation
from .gate_v3 import calculate_episode_profit_factor


@dataclass(frozen=True, slots=True)
class ArchitectureStageReplayResult:
    stage: ArchitectureStage
    rows: tuple[ArchitectureStageExecutionRow, ...]
    snapshots: tuple[DecisionSnapshot, ...]
    metrics: ArchitectureExecutionMetrics


class _FeeAccumulator:
    def __init__(self) -> None:
        self.total = 0.0

    def apply_fee(self, fee: float) -> None:
        self.total += fee


def run_architecture_stage_replay(
    observations: tuple[ArchitectureObservation, ...],
    *,
    stage: ArchitectureStage | str,
    fixed_notional: float,
    symbol: str,
    portfolio: str,
    min_trade_qty: float = 0.0001,
    max_trade_qty_per_bar: float = 1.0,
    quantity_precision: int = 6,
    taker_fee_bps: float = 4.0,
    fallback_slippage_bps: float = 1.5,
) -> ArchitectureStageReplayResult:
    """Execute one frozen later stage without invoking strategy parameter search."""
    resolved_stage = ArchitectureStage(stage)
    if resolved_stage not in {
        ArchitectureStage.A2,
        ArchitectureStage.A3,
        ArchitectureStage.A4,
        ArchitectureStage.A5_SHADOW,
    }:
        raise ValueError("stage replay only supports A2-A5_SHADOW")
    if not observations:
        raise ValueError("stage replay observations must not be empty")
    if fixed_notional <= 0.0 or not math.isfinite(fixed_notional):
        raise ValueError("fixed_notional must be positive and finite")

    ordered = tuple(sorted(observations, key=lambda item: (item.cutoff_time_ns, item.alpha_id)))
    if len({item.cutoff_time_ns for item in ordered}) != len(ordered):
        raise ValueError("stage replay observations contain duplicate cutoffs")

    bus = InMemoryJetStream()
    fee_accumulator = _FeeAccumulator()
    gateway = SimulatedGateway(
        SimulatedGatewayConfig(
            taker_fee_rate=taker_fee_bps / 10_000.0,
            fallback_slippage_bps=fallback_slippage_bps,
        )
    )
    kernel = ExecutionKernel(
        bus=bus,
        gateway=gateway,
        risk_overlay=fee_accumulator,
        config=ExecutionKernelConfig(
            execution_authority_mode="target_tracking_v2",
            min_trade_qty=min_trade_qty,
            max_trade_qty_per_bar=max_trade_qty_per_bar,
            quantity_precision=quantity_precision,
        ),
    )
    reports: list[ExecutionReport] = []
    audits: list[ExecutionOrderAudit] = []
    bus.subscribe("exec.order.report", lambda _subject, payload: reports.append(payload))
    bus.subscribe("exec.order.audit", lambda _subject, payload: audits.append(payload))
    episode_ledger = PositionEpisodeLedger()
    rows: list[ArchitectureStageExecutionRow] = []
    snapshots: list[DecisionSnapshot] = []
    tracking_errors: list[float] = []
    time_to_target: list[float] = []
    total_shortfall = 0.0
    total_slippage = 0.0

    for observation in ordered:
        price = _positive(observation.reference_price, name="reference_price")
        cutoff = observation.cutoff_time_ns
        episode_ledger.observe_price(
            PriceObservation(symbol=symbol, timestamp_ns=cutoff, high=price, low=price, close=price)
        )
        current_qty = kernel.positions.get(symbol, 0.0)
        current_exposure = current_qty * price / fixed_notional
        safety_reasons = set(observation.safety_reasons)
        if _uncapped_a2_exposure(observation) > A2_MAX_ABS_EXPOSURE:
            safety_reasons.add("CAP_VIOLATION")
        normalized_regime = observation.risk_regime.strip().upper()
        if normalized_regime in {"UNSAFE", "UNKNOWN"}:
            safety_reasons.add("UNSAFE")
        stage_observation = replace(observation, safety_reasons=tuple(sorted(safety_reasons)))
        approved_exposure = stage_exposure(
            stage_observation,
            resolved_stage,
            current_position=current_exposure,
            fixed_notional=fixed_notional,
        )
        target_qty = approved_exposure * fixed_notional / price
        is_reduction = abs(current_qty) > min_trade_qty and (
            abs(target_qty) < abs(current_qty) and (abs(target_qty) <= min_trade_qty or target_qty * current_qty > 0.0)
        )
        reduce_only = is_reduction and (observation.reduce_only or bool(safety_reasons))
        reasons = tuple(sorted(safety_reasons))
        target = TargetPosition(
            intent_id=f"architecture-{resolved_stage.value}-{observation.alpha_id}",
            portfolio=portfolio,
            symbol=symbol,
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=cutoff,
            target_qty=target_qty,
            target_leverage=abs(approved_exposure),
            reduce_only=reduce_only,
            regime=observation.regime,
            regime_confidence=observation.frozen_confidence,
            reason_codes=list(reasons),
            realized_vol=float(observation.annualized_volatility or 0.0),
            atr=0.0,
        )
        multiplier = frozen_regime_multiplier(observation.risk_regime)
        snapshot = _build_stage_snapshot(
            target=target,
            observation=observation,
            stage=resolved_stage,
            multiplier=multiplier,
            reference_price=price,
        )
        snapshots.append(snapshot)
        report_start = len(reports)
        bus.publish(
            f"md.mark.ARCHITECTURE.{symbol}",
            MarkPriceUpdate(
                meta=EventMeta(
                    event_time_ns=cutoff,
                    ingest_time_ns=cutoff,
                    venue="ARCHITECTURE",
                    symbol=symbol,
                    stream_seq=cutoff,
                ),
                mark_price=price,
                index_price=price,
            ),
        )
        bus.publish(f"strategy.decision_snapshot.4h.{symbol}", snapshot)
        bus.publish(f"intent.target_position.final.{symbol}", target)
        new_reports = [report for report in reports[report_start:] if report.filled_qty > 0.0]
        for report in new_reports:
            episode_ledger.on_fill(report)
        position_after = kernel.positions.get(symbol, 0.0)
        exposure_after = position_after * price / fixed_notional
        filled_qty = sum(report.filled_qty for report in new_reports)
        fill_notional = sum(report.filled_qty * report.avg_price for report in new_reports)
        fill_price = fill_notional / filled_qty if filled_qty > 0.0 else None
        fee = sum(report.fee for report in new_reports)
        signed_delta = target_qty - current_qty
        fill_side = 1.0 if signed_delta > 0.0 else -1.0 if signed_delta < 0.0 else 0.0
        actual_shortfall = (
            sum(fill_side * (report.avg_price - price) * report.filled_qty for report in new_reports)
            if new_reports
            else 0.0
        )
        error = abs(target_qty - position_after) * price / fixed_notional
        reached = error <= max(min_trade_qty * price / fixed_notional, kernel.quantity_epsilon)
        row = ArchitectureStageExecutionRow(
            cutoff_time_ns=cutoff,
            decision_id=snapshot.decision_id,
            approved_exposure=approved_exposure,
            approved_target_qty=target_qty,
            filled_exposure_before=current_exposure,
            filled_exposure_after=exposure_after,
            filled_position_before=current_qty,
            filled_position_after=position_after,
            target_delta_exposure=(target_qty - current_qty) * price / fixed_notional,
            reference_price=price,
            actual_fill_price=fill_price,
            filled_qty=filled_qty,
            fee=fee,
            actual_slippage=actual_shortfall,
            actual_implementation_shortfall=actual_shortfall + fee,
            target_tracking_error=error,
            time_to_target_bars=0 if reached else None,
            reduce_only=reduce_only,
            safety_reasons=reasons,
            forward_return_1=observation.forward_return,
            forward_return_3=observation.forward_return_3,
            forward_return_6=observation.forward_return_6,
        )
        rows.append(row)
        tracking_errors.append(error)
        # A new 4h decision supersedes the prior authority. Unreached targets
        # are therefore right-censored at one decision bar, not silently
        # dropped from the aggregate.
        time_to_target.append(0.0 if reached else 1.0)
        total_slippage += actual_shortfall
        total_shortfall += actual_shortfall + fee

    elapsed_days = max(1.0, (ordered[-1].cutoff_time_ns - ordered[0].cutoff_time_ns) / 1e9 / 86_400.0)
    pf = calculate_episode_profit_factor(episode_ledger.closed_episodes)
    metrics = ArchitectureExecutionMetrics(
        target_tracking_error=sum(tracking_errors) / len(tracking_errors),
        time_to_target_bars=sum(time_to_target) / len(time_to_target),
        implementation_shortfall=total_shortfall,
        slippage=total_slippage,
        target_overshoot=kernel.purity_counters["target_overshoot"],
        unexpected_reversal=kernel.purity_counters["unexpected_reversal"],
        executor_created_exposure=kernel.purity_counters["executor_created_exposure"],
        episode_profit_factor=pf.value,
        episode_profit_factor_status=pf.status.value,
        position_switches_per_day=episode_ledger.position_switch_count / elapsed_days,
    )
    if len(audits) != sum(1 for row in rows if row.filled_qty > 0.0):
        raise ValueError("target-tracking audit count does not match filled stage rows")
    return ArchitectureStageReplayResult(
        stage=resolved_stage,
        rows=tuple(rows),
        snapshots=tuple(snapshots),
        metrics=metrics,
    )


def _build_stage_snapshot(
    *,
    target: TargetPosition,
    observation: ArchitectureObservation,
    stage: ArchitectureStage,
    multiplier: float,
    reference_price: float,
) -> DecisionSnapshot:
    from zenquant.strategy.decision_snapshot import build_decision_snapshot

    config_payload = {
        "architecture_stage": stage.value,
        "execution_authority_mode": "target_tracking_v2",
        "exposure_cap": A2_MAX_ABS_EXPOSURE,
        "volatility_target": A2_ANNUALIZED_VOLATILITY_TARGET,
    }
    config_hash = hashlib.sha256(json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    data_payload = {
        "alpha_id": observation.alpha_id,
        "cutoff_time_ns": observation.cutoff_time_ns,
        "reference_price": reference_price,
        "annualized_volatility": observation.annualized_volatility,
        "risk_regime": observation.risk_regime,
        "regime_risk_multiplier": multiplier,
        "directional_state": observation.directional_state,
        "volatility_state": observation.volatility_state,
        "positioning_state": observation.positioning_state,
        "quality_state": observation.quality_state,
        "quality_flags": observation.quality_flags,
        "reduce_only": observation.reduce_only,
        "safety_reasons": observation.safety_reasons,
    }
    data_identity = hashlib.sha256(json.dumps(data_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return build_decision_snapshot(
        target=target,
        formula_hash=observation.formula_hash,
        config_hash=f"sha256:{config_hash}",
        indicator_version="architecture-stage.v2",
        data_identity=f"sha256:{data_identity}",
        alpha_direction=1.0 if observation.raw_score > 0.0 else -1.0 if observation.raw_score < 0.0 else 0.0,
        alpha_raw_score=observation.raw_score,
        alpha_confidence=observation.frozen_confidence,
        directional_state=observation.directional_state,
        volatility_state=observation.volatility_state,
        positioning_state=observation.positioning_state,
        quality_state=observation.quality_state,
        regime_risk_multiplier=multiplier,
        annualized_volatility=float(observation.annualized_volatility or 0.0),
        volatility_target=A2_ANNUALIZED_VOLATILITY_TARGET,
        exposure_cap=A2_MAX_ABS_EXPOSURE,
        quality_flags=observation.quality_flags,
        options_state_id=None,
    )


def _positive(value: float | None, *, name: str) -> float:
    resolved = float(value) if value is not None else 0.0
    if not math.isfinite(resolved) or resolved <= 0.0:
        raise ValueError(f"{name} must be positive and finite")
    return resolved


def _uncapped_a2_exposure(observation: ArchitectureObservation) -> float:
    volatility = float(observation.annualized_volatility or 0.0)
    if not math.isfinite(volatility) or volatility <= 0.0:
        return 0.0
    confidence = float(observation.frozen_confidence)
    return abs(confidence * A2_ANNUALIZED_VOLATILITY_TARGET / volatility)
