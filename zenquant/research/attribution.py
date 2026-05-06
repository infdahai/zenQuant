"""Decision attribution ledger for candidate existence checks."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from zenquant.domain import ExecutionReport

CHOP_PNL_DAMAGE_TOLERANCE = 0.0
PROFITABLE_CHOP_CHANGE_LIMIT = 0


def classify_hostile_micro_state(row: dict[str, Any], *, previous_target_qty: float = 0.0) -> str:
    """Classify a bar using point-in-time state and target fields only."""

    target_qty = _safe_float(row.get("target_qty"))
    target_direction = 1 if target_qty > 0 else -1 if target_qty < 0 else 0
    realized_vol = _safe_float(row.get("realized_vol"))
    regime = str(row.get("regime") or row.get("target_regime") or "RANGE")
    regime_confidence = _safe_float(row.get("regime_confidence") or row.get("target_regime_confidence"))
    directional_state = str(row.get("directional_state") or "NEUTRAL")
    volatility_state = str(row.get("volatility_state") or "NORMAL")
    transition_risk = _safe_float(row.get("transition_risk"))
    momentum_roc_z = _safe_float(row.get("momentum_roc_z"))
    funding_oi_z = _safe_float(row.get("funding_oi_z"))

    if (
        previous_target_qty < 0
        and target_direction >= 0
        and (realized_vol >= 0.8 or directional_state == "UPTREND" or momentum_roc_z >= 0.5)
    ):
        return "squeeze_continuation"
    if (
        target_direction < 0
        and realized_vol >= 0.9
        and (volatility_state in {"HIGH", "DISTORTED"} or regime == "STRESS" or transition_risk >= 0.45)
    ):
        return "panic_reversal"
    if target_direction < 0 and directional_state == "DOWNTREND" and regime == "TREND" and regime_confidence >= 0.55:
        return "bearish_grind"
    if target_direction < 0 and funding_oi_z >= 1.5 and transition_risk >= 0.40:
        return "squeeze_continuation"
    if regime == "RANGE" or directional_state in {"NEUTRAL", "TRANSITION"} or regime_confidence < 0.50:
        return "chop_neutral"
    return "default"


def build_decision_attribution_ledger(
    runner: Any,
    result: Any,
    *,
    variant: str,
    window_label: str,
) -> dict[str, Any]:
    """Build a compact, JSON-ready bar/execution attribution ledger."""

    rows = runner.build_analysis_rows()
    reports_by_ts = _execution_reports_by_ts(getattr(runner, "_reports", []))
    state_summary: dict[str, dict[str, Any]] = {}
    playbook_summary: dict[str, dict[str, Any]] = {}
    reason_counts: dict[str, Counter[str]] = defaultdict(Counter)
    action_attribution: dict[str, dict[str, Any]] = {}
    source_attribution: dict[str, dict[str, Any]] = {}
    decision_rows: list[dict[str, Any]] = []
    previous_target_qty = 0.0
    previous_equity: float | None = None

    for row in rows:
        cutoff_ns = int(row.get("cutoff_time_ns", 0) or 0)
        target_qty = _safe_float(row.get("target_qty"))
        equity = _safe_float(row.get("equity"))
        equity_pnl = 0.0 if previous_equity is None or equity <= 0 else equity - previous_equity
        if equity > 0:
            previous_equity = equity

        micro_state = classify_hostile_micro_state(row, previous_target_qty=previous_target_qty)
        playbook_id = str(row.get("playbook_id") or "UNKNOWN")
        target_action = _target_action(previous_target_qty, target_qty)
        execution_reports = reports_by_ts.get(cutoff_ns, [])
        report_fees = sum(_safe_float(item.get("fee")) for item in execution_reports)
        report_turnover = sum(_safe_float(item.get("turnover_notional")) for item in execution_reports)
        execution_actions = [str(item.get("action") or "none") for item in execution_reports]
        execution_action_sources: list[str] = []

        summary = state_summary.setdefault(
            micro_state,
            {
                "bars": 0,
                "estimated_equity_pnl": 0.0,
                "fees": 0.0,
                "turnover_notional": 0.0,
                "target_actions": Counter(),
                "execution_actions": Counter(),
            },
        )
        summary["bars"] += 1
        summary["estimated_equity_pnl"] += equity_pnl
        summary["fees"] += report_fees
        summary["turnover_notional"] += report_turnover
        summary["target_actions"][target_action] += 1
        for action in execution_actions:
            summary["execution_actions"][action] += 1
        row_reason_codes = _reason_codes(row)
        for code in row_reason_codes:
            reason_counts[micro_state][code] += 1

        playbook_payload = playbook_summary.setdefault(
            playbook_id,
            {
                "bars": 0,
                "estimated_equity_pnl": 0.0,
                "fees": 0.0,
                "turnover_notional": 0.0,
                "target_actions": Counter(),
                "execution_actions": Counter(),
            },
        )
        playbook_payload["bars"] += 1
        playbook_payload["estimated_equity_pnl"] += equity_pnl
        playbook_payload["fees"] += report_fees
        playbook_payload["turnover_notional"] += report_turnover
        playbook_payload["target_actions"][target_action] += 1
        for action, report in zip(execution_actions, execution_reports, strict=True):
            source = classify_execution_action_source(action, row, target_action=target_action)
            execution_action_sources.append(source)
            _update_action_attribution(
                action_attribution,
                source_attribution,
                action=action,
                source=source,
                row=row,
                report=report,
                target_action=target_action,
                micro_state=micro_state,
                playbook_id=playbook_id,
            )
            playbook_payload["execution_actions"][action] += 1

        decision_rows.append(
            {
                "cutoff_time_ns": cutoff_ns,
                "micro_state": micro_state,
                "playbook_id": playbook_id,
                "target_action": target_action,
                "target_qty": round(target_qty, 6),
                "target_direction": 1 if target_qty > 0 else -1 if target_qty < 0 else 0,
                "regime": row.get("regime") or row.get("target_regime"),
                "regime_confidence": round(_safe_float(row.get("regime_confidence")), 6),
                "directional_state": row.get("directional_state"),
                "directional_confidence": round(_safe_float(row.get("directional_confidence")), 6),
                "volatility_state": row.get("volatility_state"),
                "positioning_state": row.get("positioning_state"),
                "quality_state": row.get("quality_state"),
                "transition_risk": round(_safe_float(row.get("transition_risk")), 6),
                "funding_oi_z": round(_safe_float(row.get("funding_oi_z")), 6),
                "momentum_roc_z": round(_safe_float(row.get("momentum_roc_z")), 6),
                "realized_vol": round(_safe_float(row.get("realized_vol")), 6),
                "drawdown": round(_safe_float(row.get("drawdown")), 6),
                "estimated_equity_pnl": round(equity_pnl, 6),
                "fees": round(report_fees, 6),
                "turnover_notional": round(report_turnover, 6),
                "execution_actions": execution_actions,
                "execution_action_sources": execution_action_sources,
                "risk_flags": str(row.get("risk_flags") or ""),
                "risk_decision_flags": str(row.get("risk_decision_flags") or ""),
                "target_reason_codes": str(row.get("target_reason_codes") or ""),
                "exit_reason_codes": str(row.get("exit_reason_codes") or ""),
                "reason_codes": row_reason_codes,
            }
        )
        previous_target_qty = target_qty

    return {
        "variant": variant,
        "window_label": window_label,
        "metrics": asdict(result.metrics),
        "summary": {
            "bar_count": len(rows),
            "execution_report_count": len(getattr(runner, "_reports", [])),
            "total_fees": round(_safe_float(getattr(result.metrics, "total_fees", 0.0)), 6),
            "total_funding": round(_safe_float(getattr(result.metrics, "total_funding", 0.0)), 6),
            "net_pnl": round(_safe_float(getattr(result.metrics, "net_pnl", 0.0)), 6),
        },
        "micro_state_summary": _serialize_state_summary(state_summary),
        "playbook_summary": _serialize_state_summary(playbook_summary),
        "action_attribution_summary": _serialize_action_attribution(action_attribution),
        "action_source_summary": _serialize_action_attribution(source_attribution),
        "top_reason_codes_by_micro_state": {
            state: dict(counter.most_common(12)) for state, counter in sorted(reason_counts.items())
        },
        "decision_rows": decision_rows,
    }


def build_bull_window_direction_exposure_attribution(
    runner: Any,
    result: Any,
    *,
    variant: str,
    window_label: str,
    source: str,
    role: str,
    formal_train_truth: bool,
    execution_truth: bool,
    starting_equity: float,
    formal_auxiliary_train_leg: bool = False,
) -> dict[str, Any]:
    """Explain why a bull/profit-run window did or did not translate into exposure."""

    rows = runner.build_analysis_rows()
    reports_by_ts = _execution_reports_by_ts(getattr(runner, "_reports", []))
    bar_count = len(rows)
    if not rows:
        return {
            "variant": variant,
            "window_label": window_label,
            "source": source,
            "role": role,
            "scope": "train_dev_only_bull_window_direction_exposure_attribution",
            "formal_train_truth": formal_train_truth,
            "formal_auxiliary_train_leg": formal_auxiliary_train_leg,
            "execution_truth": execution_truth,
            "promotion_allowed": False,
            "test_evaluated": False,
            "summary": {"bar_count": 0},
            "counts": {},
            "diagnosis": {
                "primary_failure_mode": "analysis_rows_missing",
                "failure_flags": ["analysis_rows_missing"],
            },
        }

    first_price = _safe_float(rows[0].get("close_price"))
    last_price = _safe_float(rows[-1].get("close_price"))
    price_return = (last_price / first_price - 1.0) if first_price > 0.0 else 0.0
    positive_beta_pnl = max(0.0, price_return) * max(0.0, starting_equity)

    target_actions: Counter[str] = Counter()
    execution_actions: Counter[str] = Counter()
    playbooks: Counter[str] = Counter()
    regimes: Counter[str] = Counter()
    directional_states: Counter[str] = Counter()
    exit_types: Counter[str] = Counter()
    risk_flags: Counter[str] = Counter()
    target_reason_codes: Counter[str] = Counter()
    exit_reason_codes: Counter[str] = Counter()

    previous_target_qty = 0.0
    target_abs_leverage_sum = 0.0
    long_target_leverage_sum = 0.0
    long_target_bars = 0
    short_target_bars = 0
    flat_target_bars = 0
    uptrend_bars = 0
    uptrend_long_bars = 0
    uptrend_short_bars = 0
    uptrend_flat_bars = 0
    cycle_bull_bars = 0
    cycle_bull_long_bars = 0
    cycle_bull_flat_bars = 0
    positive_momentum_bars = 0
    range_cycle_profit_candidate_bars = 0
    range_cycle_profit_candidate_long_bars = 0
    trend_regime_bars = 0
    bull_playbook_bars = 0
    exit_active_bars = 0
    target_reduce_bars = 0
    risk_reduced_bars = 0
    reduce_only_bars = 0

    for row in rows:
        target_qty = _safe_float(row.get("target_qty"))
        close_price = _safe_float(row.get("close_price"))
        equity = _safe_float(row.get("equity")) or max(0.0, starting_equity)
        target_leverage = abs(_safe_float(row.get("target_leverage")))
        if target_leverage <= 0.0 and close_price > 0.0 and equity > 0.0:
            target_leverage = abs(target_qty) * close_price / equity
        target_abs_leverage_sum += target_leverage
        if target_qty > 0.0:
            long_target_bars += 1
            long_target_leverage_sum += target_leverage
        elif target_qty < 0.0:
            short_target_bars += 1
        else:
            flat_target_bars += 1

        directional_state = str(row.get("directional_state") or "UNKNOWN")
        regime = str(row.get("regime") or row.get("target_regime") or "UNKNOWN")
        playbook_id = str(row.get("playbook_id") or "UNKNOWN")
        exit_type = str(row.get("exit_type") or "NONE")
        cycle_position = _safe_float(row.get("cycle_position")) if row.get("cycle_position") is not None else 1.0
        momentum_roc_z = _safe_float(row.get("momentum_roc_z"))
        multi_momentum_z = _safe_float(row.get("multi_momentum_z"))
        target_action = _target_action(previous_target_qty, target_qty)
        target_actions[target_action] += 1
        playbooks[playbook_id] += 1
        regimes[regime] += 1
        directional_states[directional_state] += 1

        is_uptrend = directional_state == "UPTREND"
        if is_uptrend:
            uptrend_bars += 1
            if target_qty > 0.0:
                uptrend_long_bars += 1
            elif target_qty < 0.0:
                uptrend_short_bars += 1
            else:
                uptrend_flat_bars += 1
        is_cycle_bull = cycle_position >= 1.0
        if is_cycle_bull:
            cycle_bull_bars += 1
            if target_qty > 0.0:
                cycle_bull_long_bars += 1
            elif target_qty == 0.0:
                cycle_bull_flat_bars += 1
        if momentum_roc_z > 0.20 or multi_momentum_z > 0.20:
            positive_momentum_bars += 1
        is_range_cycle_profit_candidate = (
            regime == "RANGE"
            and is_cycle_bull
            and directional_state != "DOWNTREND"
            and (momentum_roc_z > 0.20 or multi_momentum_z > 0.20)
        )
        if is_range_cycle_profit_candidate:
            range_cycle_profit_candidate_bars += 1
            if target_qty > 0.0:
                range_cycle_profit_candidate_long_bars += 1
        if regime == "TREND":
            trend_regime_bars += 1
        if "BULL" in playbook_id:
            bull_playbook_bars += 1
        if exit_type and exit_type != "NONE":
            exit_active_bars += 1
            exit_types[exit_type] += 1
        if target_action in {"reduce", "exit", "flip"}:
            target_reduce_bars += 1
        if _risk_reduced_target(row):
            risk_reduced_bars += 1
        if bool(row.get("risk_reduce_only")) or bool(row.get("reduce_only")):
            reduce_only_bars += 1

        for flag in _pipe_codes(row.get("risk_decision_flags") or row.get("risk_flags")):
            risk_flags[flag] += 1
        target_reason_codes.update(_pipe_codes(row.get("target_reason_codes")))
        exit_reason_codes.update(_pipe_codes(row.get("exit_reason_codes")))
        for report in reports_by_ts.get(int(row.get("cutoff_time_ns", 0) or 0), []):
            execution_actions[str(report.get("action") or "none")] += 1
        previous_target_qty = target_qty

    long_target_bar_pct = _pct(long_target_bars, bar_count)
    short_target_bar_pct = _pct(short_target_bars, bar_count)
    flat_target_bar_pct = _pct(flat_target_bars, bar_count)
    uptrend_detected_pct = _pct(uptrend_bars, bar_count)
    trend_regime_bar_pct = _pct(trend_regime_bars, bar_count)
    long_when_uptrend_pct = _pct(uptrend_long_bars, uptrend_bars)
    short_when_uptrend_pct = _pct(uptrend_short_bars, uptrend_bars)
    flat_when_uptrend_pct = _pct(uptrend_flat_bars, uptrend_bars)
    cycle_bull_bar_pct = _pct(cycle_bull_bars, bar_count)
    long_when_cycle_bull_pct = _pct(cycle_bull_long_bars, cycle_bull_bars)
    flat_when_cycle_bull_pct = _pct(cycle_bull_flat_bars, cycle_bull_bars)
    positive_momentum_bar_pct = _pct(positive_momentum_bars, bar_count)
    range_cycle_profit_candidate_bar_pct = _pct(range_cycle_profit_candidate_bars, bar_count)
    long_when_range_cycle_profit_candidate_pct = _pct(
        range_cycle_profit_candidate_long_bars,
        range_cycle_profit_candidate_bars,
    )
    avg_abs_target_leverage = target_abs_leverage_sum / bar_count if bar_count else 0.0
    avg_long_target_leverage = long_target_leverage_sum / long_target_bars if long_target_bars else 0.0
    exit_active_bar_pct = _pct(exit_active_bars, bar_count)
    risk_reduced_bar_pct = _pct(risk_reduced_bars, bar_count)
    target_reduce_action_pct = _pct(target_reduce_bars, bar_count)
    profit_run_capture_ratio = (
        _safe_float(getattr(result.metrics, "net_pnl", 0.0)) / positive_beta_pnl if positive_beta_pnl > 0.0 else 0.0
    )

    failure_flags = _bull_window_failure_flags(
        price_return=price_return,
        net_pnl=_safe_float(getattr(result.metrics, "net_pnl", 0.0)),
        uptrend_detected_pct=uptrend_detected_pct,
        trend_regime_bar_pct=trend_regime_bar_pct,
        long_target_bar_pct=long_target_bar_pct,
        long_when_uptrend_pct=long_when_uptrend_pct,
        short_target_bar_pct=short_target_bar_pct,
        short_when_uptrend_pct=short_when_uptrend_pct,
        cycle_bull_bar_pct=cycle_bull_bar_pct,
        long_when_cycle_bull_pct=long_when_cycle_bull_pct,
        range_cycle_profit_candidate_bar_pct=range_cycle_profit_candidate_bar_pct,
        long_when_range_cycle_profit_candidate_pct=long_when_range_cycle_profit_candidate_pct,
        avg_long_target_leverage=avg_long_target_leverage,
        exit_active_bar_pct=exit_active_bar_pct,
        risk_reduced_bar_pct=risk_reduced_bar_pct,
    )

    return {
        "variant": variant,
        "window_label": window_label,
        "source": source,
        "role": role,
        "scope": "train_dev_only_bull_window_direction_exposure_attribution",
        "formal_train_truth": formal_train_truth,
        "formal_auxiliary_train_leg": formal_auxiliary_train_leg,
        "execution_truth": execution_truth,
        "promotion_allowed": False,
        "test_evaluated": False,
        "metrics": asdict(result.metrics),
        "summary": {
            "bar_count": bar_count,
            "price_return": round(price_return, 6),
            "equity_multiple": round(
                (starting_equity + _safe_float(getattr(result.metrics, "net_pnl", 0.0))) / starting_equity,
                6,
            )
            if starting_equity > 0.0
            else 0.0,
            "net_pnl": round(_safe_float(getattr(result.metrics, "net_pnl", 0.0)), 6),
            "sharpe": round(_safe_float(getattr(result.metrics, "sharpe", 0.0)), 6),
            "max_drawdown": round(_safe_float(getattr(result.metrics, "max_drawdown", 0.0)), 6),
            "profit_run_capture_ratio": round(profit_run_capture_ratio, 6),
            "long_target_bar_pct": round(long_target_bar_pct, 6),
            "short_target_bar_pct": round(short_target_bar_pct, 6),
            "flat_target_bar_pct": round(flat_target_bar_pct, 6),
            "uptrend_detected_pct": round(uptrend_detected_pct, 6),
            "trend_regime_bar_pct": round(trend_regime_bar_pct, 6),
            "bull_playbook_bar_pct": round(_pct(bull_playbook_bars, bar_count), 6),
            "long_when_uptrend_pct": round(long_when_uptrend_pct, 6),
            "short_when_uptrend_pct": round(short_when_uptrend_pct, 6),
            "flat_when_uptrend_pct": round(flat_when_uptrend_pct, 6),
            "cycle_bull_bar_pct": round(cycle_bull_bar_pct, 6),
            "long_when_cycle_bull_pct": round(long_when_cycle_bull_pct, 6),
            "flat_when_cycle_bull_pct": round(flat_when_cycle_bull_pct, 6),
            "positive_momentum_bar_pct": round(positive_momentum_bar_pct, 6),
            "range_cycle_profit_candidate_bar_pct": round(range_cycle_profit_candidate_bar_pct, 6),
            "long_when_range_cycle_profit_candidate_pct": round(long_when_range_cycle_profit_candidate_pct, 6),
            "avg_abs_target_leverage": round(avg_abs_target_leverage, 6),
            "avg_long_target_leverage": round(avg_long_target_leverage, 6),
            "exit_active_bar_pct": round(exit_active_bar_pct, 6),
            "target_reduce_action_pct": round(target_reduce_action_pct, 6),
            "risk_reduced_bar_pct": round(risk_reduced_bar_pct, 6),
            "reduce_only_bar_pct": round(_pct(reduce_only_bars, bar_count), 6),
            "execution_report_count": len(getattr(runner, "_reports", [])),
        },
        "counts": {
            "target_actions": dict(target_actions),
            "execution_actions": dict(execution_actions),
            "top_playbooks": dict(playbooks.most_common(12)),
            "regimes": dict(regimes),
            "directional_states": dict(directional_states),
            "exit_types": dict(exit_types),
            "top_risk_flags": dict(risk_flags.most_common(12)),
            "top_target_reason_codes": dict(target_reason_codes.most_common(12)),
            "top_exit_reason_codes": dict(exit_reason_codes.most_common(12)),
        },
        "diagnosis": {
            "primary_failure_mode": failure_flags[0] if failure_flags else None,
            "failure_flags": failure_flags,
        },
    }


def build_bear_window_direction_exposure_attribution(
    runner: Any,
    result: Any,
    *,
    variant: str,
    window_label: str,
    source: str,
    role: str,
    formal_train_truth: bool,
    execution_truth: bool,
    starting_equity: float,
) -> dict[str, Any]:
    """Explain why a bear/survival window lost money or missed downside exposure."""

    rows = runner.build_analysis_rows()
    reports_by_ts = _execution_reports_by_ts(getattr(runner, "_reports", []))
    bar_count = len(rows)
    if not rows:
        return {
            "variant": variant,
            "window_label": window_label,
            "source": source,
            "role": role,
            "scope": "train_dev_only_bear_window_direction_exposure_attribution",
            "formal_train_truth": formal_train_truth,
            "execution_truth": execution_truth,
            "promotion_allowed": False,
            "test_evaluated": False,
            "summary": {"bar_count": 0},
            "counts": {},
            "diagnosis": {
                "primary_failure_mode": "analysis_rows_missing",
                "failure_flags": ["analysis_rows_missing"],
            },
        }

    first_price = _safe_float(rows[0].get("close_price"))
    last_price = _safe_float(rows[-1].get("close_price"))
    price_return = (last_price / first_price - 1.0) if first_price > 0.0 else 0.0
    negative_beta_pnl = abs(min(0.0, price_return)) * max(0.0, starting_equity)

    target_actions: Counter[str] = Counter()
    execution_actions: Counter[str] = Counter()
    playbooks: Counter[str] = Counter()
    regimes: Counter[str] = Counter()
    directional_states: Counter[str] = Counter()
    exit_types: Counter[str] = Counter()
    risk_flags: Counter[str] = Counter()
    target_reason_codes: Counter[str] = Counter()
    exit_reason_codes: Counter[str] = Counter()

    previous_target_qty = 0.0
    previous_equity: float | None = None
    long_equity_pnl = 0.0
    short_equity_pnl = 0.0
    flat_equity_pnl = 0.0
    target_abs_leverage_sum = 0.0
    short_target_leverage_sum = 0.0
    long_target_bars = 0
    short_target_bars = 0
    flat_target_bars = 0
    downtrend_bars = 0
    downtrend_long_bars = 0
    downtrend_short_bars = 0
    downtrend_flat_bars = 0
    bearish_state_bars = 0
    bearish_state_long_bars = 0
    bearish_state_short_bars = 0
    cycle_bull_bars = 0
    cycle_bull_long_bars = 0
    range_cycle_profit_candidate_bars = 0
    range_cycle_profit_candidate_long_bars = 0
    trend_regime_bars = 0
    bear_playbook_bars = 0
    exit_active_bars = 0
    target_reduce_bars = 0
    risk_reduced_bars = 0
    reduce_only_bars = 0

    for row in rows:
        target_qty = _safe_float(row.get("target_qty"))
        close_price = _safe_float(row.get("close_price"))
        equity = _safe_float(row.get("equity")) or max(0.0, starting_equity)
        equity_pnl = 0.0 if previous_equity is None or equity <= 0.0 else equity - previous_equity
        if equity > 0.0:
            previous_equity = equity
        if target_qty > 0.0:
            long_equity_pnl += equity_pnl
        elif target_qty < 0.0:
            short_equity_pnl += equity_pnl
        else:
            flat_equity_pnl += equity_pnl

        target_leverage = abs(_safe_float(row.get("target_leverage")))
        if target_leverage <= 0.0 and close_price > 0.0 and equity > 0.0:
            target_leverage = abs(target_qty) * close_price / equity
        target_abs_leverage_sum += target_leverage
        if target_qty > 0.0:
            long_target_bars += 1
        elif target_qty < 0.0:
            short_target_bars += 1
            short_target_leverage_sum += target_leverage
        else:
            flat_target_bars += 1

        directional_state = str(row.get("directional_state") or "UNKNOWN")
        regime = str(row.get("regime") or row.get("target_regime") or "UNKNOWN")
        playbook_id = str(row.get("playbook_id") or "UNKNOWN")
        exit_type = str(row.get("exit_type") or "NONE")
        cycle_position = _safe_float(row.get("cycle_position")) if row.get("cycle_position") is not None else 1.0
        momentum_roc_z = _safe_float(row.get("momentum_roc_z"))
        multi_momentum_z = _safe_float(row.get("multi_momentum_z"))
        direction_signal = _safe_float(row.get("direction_signal"))
        target_action = _target_action(previous_target_qty, target_qty)
        target_actions[target_action] += 1
        playbooks[playbook_id] += 1
        regimes[regime] += 1
        directional_states[directional_state] += 1

        is_downtrend = directional_state == "DOWNTREND"
        if is_downtrend:
            downtrend_bars += 1
            if target_qty > 0.0:
                downtrend_long_bars += 1
            elif target_qty < 0.0:
                downtrend_short_bars += 1
            else:
                downtrend_flat_bars += 1
        is_bearish_state = is_downtrend or direction_signal <= -0.25
        if is_bearish_state:
            bearish_state_bars += 1
            if target_qty > 0.0:
                bearish_state_long_bars += 1
            elif target_qty < 0.0:
                bearish_state_short_bars += 1
        is_cycle_bull = cycle_position >= 1.0
        if is_cycle_bull:
            cycle_bull_bars += 1
            if target_qty > 0.0:
                cycle_bull_long_bars += 1
        is_range_cycle_profit_candidate = (
            regime == "RANGE"
            and is_cycle_bull
            and directional_state != "DOWNTREND"
            and (momentum_roc_z > 0.20 or multi_momentum_z > 0.20)
        )
        if is_range_cycle_profit_candidate:
            range_cycle_profit_candidate_bars += 1
            if target_qty > 0.0:
                range_cycle_profit_candidate_long_bars += 1
        if regime == "TREND":
            trend_regime_bars += 1
        if "BEAR" in playbook_id:
            bear_playbook_bars += 1
        if exit_type and exit_type != "NONE":
            exit_active_bars += 1
            exit_types[exit_type] += 1
        if target_action in {"reduce", "exit", "flip"}:
            target_reduce_bars += 1
        if _risk_reduced_target(row):
            risk_reduced_bars += 1
        if bool(row.get("risk_reduce_only")) or bool(row.get("reduce_only")):
            reduce_only_bars += 1

        for flag in _pipe_codes(row.get("risk_decision_flags") or row.get("risk_flags")):
            risk_flags[flag] += 1
        target_reason_codes.update(_pipe_codes(row.get("target_reason_codes")))
        exit_reason_codes.update(_pipe_codes(row.get("exit_reason_codes")))
        for report in reports_by_ts.get(int(row.get("cutoff_time_ns", 0) or 0), []):
            execution_actions[str(report.get("action") or "none")] += 1
        previous_target_qty = target_qty

    long_target_bar_pct = _pct(long_target_bars, bar_count)
    short_target_bar_pct = _pct(short_target_bars, bar_count)
    flat_target_bar_pct = _pct(flat_target_bars, bar_count)
    downtrend_detected_pct = _pct(downtrend_bars, bar_count)
    bearish_state_bar_pct = _pct(bearish_state_bars, bar_count)
    long_when_downtrend_pct = _pct(downtrend_long_bars, downtrend_bars)
    short_when_downtrend_pct = _pct(downtrend_short_bars, downtrend_bars)
    flat_when_downtrend_pct = _pct(downtrend_flat_bars, downtrend_bars)
    long_when_bearish_state_pct = _pct(bearish_state_long_bars, bearish_state_bars)
    short_when_bearish_state_pct = _pct(bearish_state_short_bars, bearish_state_bars)
    cycle_bull_bar_pct = _pct(cycle_bull_bars, bar_count)
    long_when_cycle_bull_pct = _pct(cycle_bull_long_bars, cycle_bull_bars)
    range_cycle_profit_candidate_bar_pct = _pct(range_cycle_profit_candidate_bars, bar_count)
    long_when_range_cycle_profit_candidate_pct = _pct(
        range_cycle_profit_candidate_long_bars,
        range_cycle_profit_candidate_bars,
    )
    avg_abs_target_leverage = target_abs_leverage_sum / bar_count if bar_count else 0.0
    avg_short_target_leverage = short_target_leverage_sum / short_target_bars if short_target_bars else 0.0
    exit_active_bar_pct = _pct(exit_active_bars, bar_count)
    risk_reduced_bar_pct = _pct(risk_reduced_bars, bar_count)
    target_reduce_action_pct = _pct(target_reduce_bars, bar_count)
    bear_capture_ratio = (
        _safe_float(getattr(result.metrics, "net_pnl", 0.0)) / negative_beta_pnl if negative_beta_pnl > 0.0 else 0.0
    )
    failure_flags = _bear_window_failure_flags(
        price_return=price_return,
        net_pnl=_safe_float(getattr(result.metrics, "net_pnl", 0.0)),
        long_equity_pnl=long_equity_pnl,
        downtrend_detected_pct=downtrend_detected_pct,
        bearish_state_bar_pct=bearish_state_bar_pct,
        long_target_bar_pct=long_target_bar_pct,
        short_target_bar_pct=short_target_bar_pct,
        long_when_downtrend_pct=long_when_downtrend_pct,
        short_when_downtrend_pct=short_when_downtrend_pct,
        long_when_bearish_state_pct=long_when_bearish_state_pct,
        short_when_bearish_state_pct=short_when_bearish_state_pct,
        long_when_range_cycle_profit_candidate_pct=long_when_range_cycle_profit_candidate_pct,
        exit_active_bar_pct=exit_active_bar_pct,
        target_reduce_action_pct=target_reduce_action_pct,
        risk_reduced_bar_pct=risk_reduced_bar_pct,
    )

    return {
        "variant": variant,
        "window_label": window_label,
        "source": source,
        "role": role,
        "scope": "train_dev_only_bear_window_direction_exposure_attribution",
        "formal_train_truth": formal_train_truth,
        "execution_truth": execution_truth,
        "promotion_allowed": False,
        "test_evaluated": False,
        "metrics": asdict(result.metrics),
        "summary": {
            "bar_count": bar_count,
            "price_return": round(price_return, 6),
            "equity_multiple": round(
                (starting_equity + _safe_float(getattr(result.metrics, "net_pnl", 0.0))) / starting_equity,
                6,
            )
            if starting_equity > 0.0
            else 0.0,
            "net_pnl": round(_safe_float(getattr(result.metrics, "net_pnl", 0.0)), 6),
            "sharpe": round(_safe_float(getattr(result.metrics, "sharpe", 0.0)), 6),
            "max_drawdown": round(_safe_float(getattr(result.metrics, "max_drawdown", 0.0)), 6),
            "bear_capture_ratio": round(bear_capture_ratio, 6),
            "long_equity_pnl": round(long_equity_pnl, 6),
            "short_equity_pnl": round(short_equity_pnl, 6),
            "flat_equity_pnl": round(flat_equity_pnl, 6),
            "long_target_bar_pct": round(long_target_bar_pct, 6),
            "short_target_bar_pct": round(short_target_bar_pct, 6),
            "flat_target_bar_pct": round(flat_target_bar_pct, 6),
            "downtrend_detected_pct": round(downtrend_detected_pct, 6),
            "bearish_state_bar_pct": round(bearish_state_bar_pct, 6),
            "trend_regime_bar_pct": round(_pct(trend_regime_bars, bar_count), 6),
            "bear_playbook_bar_pct": round(_pct(bear_playbook_bars, bar_count), 6),
            "long_when_downtrend_pct": round(long_when_downtrend_pct, 6),
            "short_when_downtrend_pct": round(short_when_downtrend_pct, 6),
            "flat_when_downtrend_pct": round(flat_when_downtrend_pct, 6),
            "long_when_bearish_state_pct": round(long_when_bearish_state_pct, 6),
            "short_when_bearish_state_pct": round(short_when_bearish_state_pct, 6),
            "cycle_bull_bar_pct": round(cycle_bull_bar_pct, 6),
            "long_when_cycle_bull_pct": round(long_when_cycle_bull_pct, 6),
            "range_cycle_profit_candidate_bar_pct": round(range_cycle_profit_candidate_bar_pct, 6),
            "long_when_range_cycle_profit_candidate_pct": round(long_when_range_cycle_profit_candidate_pct, 6),
            "avg_abs_target_leverage": round(avg_abs_target_leverage, 6),
            "avg_short_target_leverage": round(avg_short_target_leverage, 6),
            "exit_active_bar_pct": round(exit_active_bar_pct, 6),
            "target_reduce_action_pct": round(target_reduce_action_pct, 6),
            "risk_reduced_bar_pct": round(risk_reduced_bar_pct, 6),
            "reduce_only_bar_pct": round(_pct(reduce_only_bars, bar_count), 6),
            "execution_report_count": len(getattr(runner, "_reports", [])),
        },
        "counts": {
            "target_actions": dict(target_actions),
            "execution_actions": dict(execution_actions),
            "top_playbooks": dict(playbooks.most_common(12)),
            "regimes": dict(regimes),
            "directional_states": dict(directional_states),
            "exit_types": dict(exit_types),
            "top_risk_flags": dict(risk_flags.most_common(12)),
            "top_target_reason_codes": dict(target_reason_codes.most_common(12)),
            "top_exit_reason_codes": dict(exit_reason_codes.most_common(12)),
        },
        "diagnosis": {
            "primary_failure_mode": failure_flags[0] if failure_flags else None,
            "failure_flags": failure_flags,
        },
    }


def classify_execution_action_source(action: str, row: dict[str, Any], *, target_action: str) -> str:
    """Classify why a filled action happened from the same-bar audit fields."""

    exit_type = str(row.get("exit_type") or "NONE")
    exit_codes = _pipe_codes(row.get("exit_reason_codes"))
    target_codes = _pipe_codes(row.get("target_reason_codes"))
    risk_flags = _pipe_codes(row.get("risk_decision_flags") or row.get("risk_flags"))
    is_reduce_action = action in {"reduce", "exit", "flip"}
    is_add_action = action in {"add", "entry"}

    if is_reduce_action and (exit_type != "NONE" or _has_exit_code(exit_codes)):
        return "exit_plugin"
    if is_reduce_action and _risk_reduced_target(row) and _has_active_risk_flag(risk_flags):
        return "risk_overlay"
    if target_action in {"reduce", "exit", "flip"} or bool(row.get("reduce_only")):
        return "strategy_target"
    if _has_exit_code(target_codes):
        return "strategy_target"
    if action == "flip" and target_action == "entry":
        return "strategy_target"
    if is_add_action and target_action in {"add", "entry", "flip"}:
        return "strategy_target"
    if action in {"add", "reduce", "entry", "exit"} and target_action == "hold":
        return "execution_inventory"
    return "unattributed"


def build_chop_neutral_edge_ledger(
    payload: dict[str, Any],
    *,
    candidate_name: str,
    baseline_name: str = "current",
) -> dict[str, Any]:
    """Compare current vs candidate inside chop_neutral decision rows."""

    comparisons: list[dict[str, Any]] = []
    for candidate_run in _window_runs(payload, candidate_name):
        window_label = str(candidate_run.get("window_label") or "")
        baseline_run = _find_window_run(payload, baseline_name, window_label)
        if not baseline_run:
            continue
        comparisons.append(
            _chop_window_comparison(
                baseline_run,
                candidate_run,
                baseline_name=baseline_name,
                candidate_name=candidate_name,
                window_label=window_label,
            )
        )
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "baseline_variant": baseline_name,
        "candidate_variant": candidate_name,
        "window_comparisons": comparisons,
        "aggregate": _aggregate_chop_comparisons(comparisons),
    }


def build_attribution_precheck_verdict(
    payload: dict[str, Any],
    *,
    candidate_name: str,
    baseline_name: str = "current",
) -> dict[str, Any]:
    """Fail closed before candidate existence if attribution damages chop edge."""

    ledger = build_chop_neutral_edge_ledger(payload, candidate_name=candidate_name, baseline_name=baseline_name)
    failures: list[str] = []
    attribution_available = bool(ledger["window_comparisons"]) and all(
        item["attribution_available"] for item in ledger["window_comparisons"]
    )
    if not attribution_available:
        failures.append("attribution_ledger_missing")

    aggregate = ledger["aggregate"]
    if aggregate["profitable_chop_changed_bars"] > PROFITABLE_CHOP_CHANGE_LIMIT:
        failures.append("profitable_chop_neutral_path_changed")
    if aggregate["chop_pnl_delta"] < CHOP_PNL_DAMAGE_TOLERANCE:
        failures.append("chop_neutral_pnl_damaged")
    if aggregate["turnover_not_below_current_windows"] > 0:
        failures.append("turnover_not_below_current")
    if aggregate["squeeze_pnl_delta"] > 0 and aggregate["chop_pnl_delta"] < 0:
        failures.append("squeeze_improved_but_chop_damaged")

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_variant": candidate_name,
        "baseline_variant": baseline_name,
        "train_search_executed": False,
        "optuna_search_executed": False,
        "full_training_allowed": False,
        "candidate_existence_allowed": not failures,
        "attribution_ledger_required": True,
        "attribution_ledger_available": attribution_available,
        "gate": {
            "profitable_chop_change_limit": PROFITABLE_CHOP_CHANGE_LIMIT,
            "chop_pnl_damage_tolerance": CHOP_PNL_DAMAGE_TOLERANCE,
        },
        "chop_neutral_edge_ledger": ledger,
        "terminal_recommendation": "open_candidate_existence_gate"
        if not failures
        else "reject_before_candidate_existence",
        "rejection_reasons": failures,
    }


def _execution_reports_by_ts(reports: list[ExecutionReport]) -> dict[int, list[dict[str, Any]]]:
    by_ts: dict[int, list[dict[str, Any]]] = defaultdict(list)
    position_qty = 0.0
    for report in sorted(reports, key=lambda item: item.transaction_time_ns):
        if report.status != "FILLED" or report.filled_qty <= 0:
            continue
        signed_qty = report.side * report.filled_qty
        next_position = position_qty + signed_qty
        action = _execution_action(position_qty, next_position)
        by_ts[int(report.transaction_time_ns)].append(
            {
                "action": action,
                "side": report.side,
                "filled_qty": round(report.filled_qty, 6),
                "avg_price": round(report.avg_price, 6),
                "fee": round(report.fee, 6),
                "turnover_notional": round(abs(signed_qty) * report.avg_price, 6),
                "position_after": round(next_position, 6),
            }
        )
        position_qty = next_position
    return dict(by_ts)


def _update_action_attribution(
    action_attribution: dict[str, dict[str, Any]],
    source_attribution: dict[str, dict[str, Any]],
    *,
    action: str,
    source: str,
    row: dict[str, Any],
    report: dict[str, Any],
    target_action: str,
    micro_state: str,
    playbook_id: str,
) -> None:
    payload = _action_bucket(action_attribution, action)
    source_payload = _action_bucket(source_attribution, source)
    fee = _safe_float(report.get("fee"))
    turnover = _safe_float(report.get("turnover_notional"))
    for bucket in (payload, source_payload):
        bucket["count"] += 1
        bucket["fees"] += fee
        bucket["turnover_notional"] += turnover
        bucket["source_counts"][source] += 1
        bucket["source_fees"][source] += fee
        bucket["action_counts"][action] += 1
        bucket["action_fees"][action] += fee
        bucket["target_actions"][target_action] += 1
        bucket["micro_states"][micro_state] += 1
        bucket["playbooks"][playbook_id] += 1
        bucket["target_reason_codes"].update(_pipe_codes(row.get("target_reason_codes")))
        bucket["exit_reason_codes"].update(_pipe_codes(row.get("exit_reason_codes")))
        bucket["risk_flags"].update(_pipe_codes(row.get("risk_decision_flags") or row.get("risk_flags")))


def _action_bucket(container: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    return container.setdefault(
        key,
        {
            "count": 0,
            "fees": 0.0,
            "turnover_notional": 0.0,
            "source_counts": Counter(),
            "source_fees": Counter(),
            "action_counts": Counter(),
            "action_fees": Counter(),
            "target_actions": Counter(),
            "target_reason_codes": Counter(),
            "exit_reason_codes": Counter(),
            "risk_flags": Counter(),
            "micro_states": Counter(),
            "playbooks": Counter(),
        },
    )


def _window_runs(payload: dict[str, Any], variant: str) -> list[dict[str, Any]]:
    return [item for item in payload.get("window_reports", []) if item.get("variant") == variant]


def _find_window_run(payload: dict[str, Any], variant: str, window_label: str) -> dict[str, Any]:
    return next(
        (
            item
            for item in payload.get("window_reports", [])
            if item.get("variant") == variant and item.get("window_label") == window_label
        ),
        {},
    )


def _chop_window_comparison(
    baseline_run: dict[str, Any],
    candidate_run: dict[str, Any],
    *,
    baseline_name: str,
    candidate_name: str,
    window_label: str,
) -> dict[str, Any]:
    baseline_ledger = baseline_run.get("decision_attribution_ledger") or {}
    candidate_ledger = candidate_run.get("decision_attribution_ledger") or {}
    baseline_chop = _micro_state_payload(baseline_ledger, "chop_neutral")
    candidate_chop = _micro_state_payload(candidate_ledger, "chop_neutral")
    baseline_squeeze = _micro_state_payload(baseline_ledger, "squeeze_continuation")
    candidate_squeeze = _micro_state_payload(candidate_ledger, "squeeze_continuation")
    baseline_rows = _rows_by_ts(baseline_ledger, "chop_neutral")
    candidate_rows = _rows_by_ts(candidate_ledger, "chop_neutral")
    changed_profitable_rows = _changed_profitable_chop_rows(baseline_rows, candidate_rows)
    return {
        "window_label": window_label,
        "baseline_variant": baseline_name,
        "candidate_variant": candidate_name,
        "attribution_available": bool(baseline_ledger) and bool(candidate_ledger),
        "baseline_metrics": _metrics_summary(baseline_run),
        "candidate_metrics": _metrics_summary(candidate_run),
        "chop_neutral": {
            "baseline": baseline_chop,
            "candidate": candidate_chop,
            "pnl_delta": round(
                _safe_float(candidate_chop.get("estimated_equity_pnl"))
                - _safe_float(baseline_chop.get("estimated_equity_pnl")),
                6,
            ),
            "turnover_delta": round(
                _safe_float(candidate_chop.get("turnover_notional"))
                - _safe_float(baseline_chop.get("turnover_notional")),
                6,
            ),
            "fee_delta": round(_safe_float(candidate_chop.get("fees")) - _safe_float(baseline_chop.get("fees")), 6),
            "changed_profitable_bars": changed_profitable_rows,
        },
        "squeeze_continuation": {
            "baseline": baseline_squeeze,
            "candidate": candidate_squeeze,
            "pnl_delta": round(
                _safe_float(candidate_squeeze.get("estimated_equity_pnl"))
                - _safe_float(baseline_squeeze.get("estimated_equity_pnl")),
                6,
            ),
        },
        "top_reason_codes_by_micro_state": {
            "baseline": (baseline_ledger.get("top_reason_codes_by_micro_state") or {}).get("chop_neutral", {}),
            "candidate": (candidate_ledger.get("top_reason_codes_by_micro_state") or {}).get("chop_neutral", {}),
        },
    }


def _aggregate_chop_comparisons(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    chop_pnl_delta = sum(_safe_float((item.get("chop_neutral") or {}).get("pnl_delta")) for item in comparisons)
    squeeze_pnl_delta = sum(
        _safe_float((item.get("squeeze_continuation") or {}).get("pnl_delta")) for item in comparisons
    )
    changed_bars = sum(
        len((item.get("chop_neutral") or {}).get("changed_profitable_bars") or []) for item in comparisons
    )
    turnover_not_below = sum(
        1
        for item in comparisons
        if _safe_float((item.get("candidate_metrics") or {}).get("turnover_per_day"))
        > _safe_float((item.get("baseline_metrics") or {}).get("turnover_per_day"))
    )
    return {
        "window_count": len(comparisons),
        "chop_pnl_delta": round(chop_pnl_delta, 6),
        "squeeze_pnl_delta": round(squeeze_pnl_delta, 6),
        "profitable_chop_changed_bars": changed_bars,
        "turnover_not_below_current_windows": turnover_not_below,
    }


def _micro_state_payload(ledger: dict[str, Any], micro_state: str) -> dict[str, Any]:
    return dict((ledger.get("micro_state_summary") or {}).get(micro_state) or {})


def _rows_by_ts(ledger: dict[str, Any], micro_state: str) -> dict[int, dict[str, Any]]:
    return {
        int(row.get("cutoff_time_ns", 0) or 0): row
        for row in ledger.get("decision_rows", [])
        if row.get("micro_state") == micro_state
    }


def _changed_profitable_chop_rows(
    baseline_rows: dict[int, dict[str, Any]],
    candidate_rows: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for ts, baseline in sorted(baseline_rows.items()):
        if _safe_float(baseline.get("estimated_equity_pnl")) <= 0:
            continue
        candidate = candidate_rows.get(ts)
        if candidate is None or _row_path_changed(baseline, candidate):
            changed.append(
                {
                    "cutoff_time_ns": ts,
                    "baseline_pnl": round(_safe_float(baseline.get("estimated_equity_pnl")), 6),
                    "candidate_pnl": round(_safe_float(candidate.get("estimated_equity_pnl")) if candidate else 0.0, 6),
                    "baseline_target_action": baseline.get("target_action"),
                    "candidate_target_action": candidate.get("target_action") if candidate else None,
                    "baseline_execution_actions": baseline.get("execution_actions") or [],
                    "candidate_execution_actions": (candidate.get("execution_actions") if candidate else []) or [],
                    "baseline_reason_codes": baseline.get("target_reason_codes") or "",
                    "candidate_reason_codes": (candidate.get("target_reason_codes") if candidate else "") or "",
                }
            )
    return changed


def _row_path_changed(baseline: dict[str, Any], candidate: dict[str, Any]) -> bool:
    return (
        baseline.get("target_action") != candidate.get("target_action")
        or baseline.get("execution_actions") != candidate.get("execution_actions")
        or abs(_safe_float(baseline.get("target_qty")) - _safe_float(candidate.get("target_qty"))) > 1e-9
    )


def _metrics_summary(run: dict[str, Any]) -> dict[str, float]:
    metrics = run.get("metrics") or {}
    return {
        "sharpe": round(_safe_float(metrics.get("sharpe")), 4),
        "max_drawdown": round(_safe_float(metrics.get("max_drawdown")), 6),
        "net_pnl": round(_safe_float(metrics.get("net_pnl")), 6),
        "turnover_per_day": round(_safe_float(metrics.get("turnover_per_day")), 4),
        "fee_drag_pct": round(_safe_float(metrics.get("fee_drag_pct")), 6),
        "profit_factor": round(_safe_float(metrics.get("profit_factor")), 6),
    }


def _execution_action(previous_qty: float, next_qty: float) -> str:
    if abs(previous_qty) <= 0.0 and abs(next_qty) > 0.0:
        return "entry"
    if abs(next_qty) <= 0.0:
        return "exit"
    if previous_qty * next_qty < 0:
        return "flip"
    if abs(next_qty) > abs(previous_qty):
        return "add"
    if abs(next_qty) < abs(previous_qty):
        return "reduce"
    return "unchanged"


def _target_action(previous_qty: float, target_qty: float) -> str:
    if abs(previous_qty) <= 0.0 and abs(target_qty) > 0.0:
        return "entry"
    if abs(target_qty) <= 0.0 and abs(previous_qty) > 0.0:
        return "exit"
    if previous_qty * target_qty < 0:
        return "flip"
    if abs(target_qty) > abs(previous_qty):
        return "add"
    if abs(target_qty) < abs(previous_qty):
        return "reduce"
    return "hold"


def _reason_codes(row: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in (
        "state_reason_codes",
        "playbook_reason_codes",
        "sizing_reason_codes",
        "exit_reason_codes",
        "target_reason_codes",
        "risk_flags",
    ):
        value = row.get(key)
        if not value:
            continue
        codes.extend(part for part in str(value).split("|") if part)
    return codes


def _serialize_state_summary(summary: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    serializable: dict[str, dict[str, Any]] = {}
    for state, payload in sorted(summary.items()):
        serializable[state] = {
            "bars": int(payload["bars"]),
            "estimated_equity_pnl": round(float(payload["estimated_equity_pnl"]), 6),
            "fees": round(float(payload["fees"]), 6),
            "turnover_notional": round(float(payload["turnover_notional"]), 6),
            "target_actions": dict(payload["target_actions"]),
            "execution_actions": dict(payload["execution_actions"]),
        }
    return serializable


def _serialize_action_attribution(summary: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    serializable: dict[str, dict[str, Any]] = {}
    for key, payload in sorted(summary.items()):
        serializable[key] = {
            "count": int(payload["count"]),
            "fees": round(float(payload["fees"]), 6),
            "turnover_notional": round(float(payload["turnover_notional"]), 6),
            "source_counts": dict(payload["source_counts"]),
            "source_fees": _rounded_counter(payload["source_fees"]),
            "action_counts": dict(payload["action_counts"]),
            "action_fees": _rounded_counter(payload["action_fees"]),
            "target_actions": dict(payload["target_actions"]),
            "top_target_reason_codes": dict(payload["target_reason_codes"].most_common(12)),
            "top_exit_reason_codes": dict(payload["exit_reason_codes"].most_common(12)),
            "top_risk_flags": dict(payload["risk_flags"].most_common(12)),
            "top_micro_states": dict(payload["micro_states"].most_common(12)),
            "top_playbooks": dict(payload["playbooks"].most_common(12)),
        }
    return serializable


def _rounded_counter(counter: Counter[str]) -> dict[str, float]:
    return {key: round(float(value), 6) for key, value in counter.items()}


def _pct(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _bull_window_failure_flags(
    *,
    price_return: float,
    net_pnl: float,
    uptrend_detected_pct: float,
    trend_regime_bar_pct: float,
    long_target_bar_pct: float,
    long_when_uptrend_pct: float,
    short_target_bar_pct: float,
    short_when_uptrend_pct: float,
    cycle_bull_bar_pct: float,
    long_when_cycle_bull_pct: float,
    range_cycle_profit_candidate_bar_pct: float,
    long_when_range_cycle_profit_candidate_pct: float,
    avg_long_target_leverage: float,
    exit_active_bar_pct: float,
    risk_reduced_bar_pct: float,
) -> list[str]:
    flags: list[str] = []
    if price_return > 0.20:
        if uptrend_detected_pct < 0.35 and trend_regime_bar_pct < 0.35:
            flags.append("bull_regime_not_recognized")
        if uptrend_detected_pct >= 0.20 and long_when_uptrend_pct < 0.35:
            flags.append("uptrend_not_converted_to_long_intent")
        if cycle_bull_bar_pct >= 0.35 and long_when_cycle_bull_pct < 0.35:
            flags.append("cycle_bull_context_not_converted_to_long_intent")
        if range_cycle_profit_candidate_bar_pct >= 0.10 and long_when_range_cycle_profit_candidate_pct < 0.35:
            flags.append("range_cycle_profit_context_not_converted_to_long_intent")
        if long_target_bar_pct < 0.35:
            flags.append("insufficient_long_time_in_market")
        if long_target_bar_pct >= 0.35 and avg_long_target_leverage < 0.35:
            flags.append("long_position_size_too_small")
        if short_target_bar_pct > 0.15 or short_when_uptrend_pct > 0.10:
            flags.append("short_bias_during_bull_window")
        if exit_active_bar_pct > 0.12:
            flags.append("exit_layer_cutting_profit_run")
        if risk_reduced_bar_pct > 0.12:
            flags.append("risk_overlay_reducing_bull_exposure")
    if net_pnl < 0.0 and not flags:
        flags.append("unattributed_bull_window_loss")
    return flags


def _bear_window_failure_flags(
    *,
    price_return: float,
    net_pnl: float,
    long_equity_pnl: float,
    downtrend_detected_pct: float,
    bearish_state_bar_pct: float,
    long_target_bar_pct: float,
    short_target_bar_pct: float,
    long_when_downtrend_pct: float,
    short_when_downtrend_pct: float,
    long_when_bearish_state_pct: float,
    short_when_bearish_state_pct: float,
    long_when_range_cycle_profit_candidate_pct: float,
    exit_active_bar_pct: float,
    target_reduce_action_pct: float,
    risk_reduced_bar_pct: float,
) -> list[str]:
    flags: list[str] = []
    if price_return < -0.10 or net_pnl < 0.0:
        if long_target_bar_pct > 0.15 or long_when_bearish_state_pct > 0.10:
            flags.append("cycle_profit_run_overlong_in_bear_window")
        if long_when_range_cycle_profit_candidate_pct > 0.15:
            flags.append("range_cycle_profit_fallback_overactive_in_bear_window")
        if (downtrend_detected_pct >= 0.10 or bearish_state_bar_pct >= 0.15) and short_when_bearish_state_pct < 0.20:
            flags.append("bearish_state_not_converted_to_short_intent")
        if downtrend_detected_pct >= 0.10 and short_when_downtrend_pct < 0.20:
            flags.append("downtrend_not_converted_to_short_intent")
        if short_target_bar_pct < 0.10 and (downtrend_detected_pct >= 0.10 or bearish_state_bar_pct >= 0.15):
            flags.append("insufficient_short_time_in_market")
        if long_equity_pnl < 0.0 and exit_active_bar_pct < 0.05 and target_reduce_action_pct < 0.05:
            flags.append("exit_layer_not_reducing_bear_loss")
        if long_when_bearish_state_pct > 0.05 and risk_reduced_bar_pct < 0.05:
            flags.append("risk_overlay_not_reducing_bear_exposure")
    if net_pnl < 0.0 and not flags:
        flags.append("unattributed_bear_window_loss")
    return flags


def _pipe_codes(value: Any) -> list[str]:
    if not value:
        return []
    return [part for part in str(value).split("|") if part]


def _has_exit_code(codes: list[str]) -> bool:
    return any(code.startswith("EXIT") or "_EXIT" in code for code in codes)


def _has_active_risk_flag(flags: list[str]) -> bool:
    return any(flag and flag != "OK" for flag in flags)


def _risk_reduced_target(row: dict[str, Any]) -> bool:
    original = _safe_float(row.get("risk_original_target_qty"))
    revised = _safe_float(row.get("risk_revised_target_qty"))
    if abs(original) <= 0.0:
        return False
    if abs(revised) < abs(original) - 1e-9:
        return True
    return bool(row.get("risk_reduce_only")) and abs(revised) <= abs(original)


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
