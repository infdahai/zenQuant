"""Train-only regime objective checks for research candidate falsification."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from zenquant.research.comparison import WindowSpec, fixed_2023_08_window, windows_from_worst_periods
from zenquant.research.splits import DatasetSplitSpec, default_btc_split_spec, period_within_split

MAX_BUCKET_SHARPE_REGRESSION = 0.10
MAX_FEE_DRAG_RELATIVE_INCREASE = 1.15
MAX_FEE_DRAG_ABSOLUTE_INCREASE = 0.002
MAX_TURNOVER_RELATIVE_INCREASE = 1.20
MAX_TURNOVER_ABSOLUTE_INCREASE = 100.0
TRAIN_TRUTH_DAY_FALLBACK = 1461.0
MAX_FEE_DRAG_PCT_ABSOLUTE = 0.025
MAX_TRADE_COUNT_PER_DAY = 1.50
MIN_PROFIT_FACTOR_AFTER_FEE = 1.05
MIN_NET_PNL_PER_TRADE_RELATIVE = 0.80
PAULWEI_MATURE_POSITION_SWITCHES_PER_30D = 1.6807
MAX_POSITION_SWITCHES_PER_30D = 3.0
MAX_POSITION_SWITCH_RELATIVE_INCREASE = 1.15
MAX_FLIP_REDUCE_FEE_DRAG_PCT_ABSOLUTE = 0.016
MAX_FLIP_REDUCE_FEE_DRAG_RELATIVE_INCREASE = 1.15
MAX_EXIT_FEE_DRAG_PCT_ABSOLUTE = 0.018
MAX_FLIP_ACTION_FEE_DRAG_PCT_ABSOLUTE = 0.018
MIN_REDUCE_ACTION_PROFIT_FACTOR = 1.0
MIN_STAGED_BUILD_UNWIND_BALANCE_RATIO = 0.25
MIN_TRANCHE_ACTIONS_PER_POSITION_SWITCH = 3.0
MIN_NET_PNL_PER_POSITION_SWITCH_RELATIVE = 0.75
MIN_PROTECTIVE_REDUCE_SHARE = 0.75
TRAIN_DRAWDOWN_PREFERRED_BAND = 0.06
_FORBIDDEN_SELECTION_PREFIXES = ("dev", "test")


@dataclass(slots=True, frozen=True)
class RegimeBucketSpec:
    """One deterministic train-only bucket used by the regime objective."""

    label: str
    regime_role: str
    source: str
    window: WindowSpec


def _safe_float(value: Any) -> float:
    return float(value or 0.0)


def _trade_count_per_day(metrics: dict[str, Any], trade_count: float) -> float:
    explicit = _safe_float(metrics.get("trade_count_per_day"))
    if explicit > 0:
        return explicit
    for key in ("duration_days", "elapsed_days", "window_days", "train_days"):
        days = _safe_float(metrics.get(key))
        if days > 0:
            return trade_count / days
    return trade_count / TRAIN_TRUTH_DAY_FALLBACK if trade_count > 0 else 0.0


def _net_pnl_per_trade(metrics: dict[str, Any], net_pnl: float, trade_count: float) -> float:
    explicit = metrics.get("net_pnl_per_trade")
    if explicit is not None:
        return _safe_float(explicit)
    return net_pnl / trade_count if trade_count > 0 else 0.0


def _profit_factor_after_fee(metrics: dict[str, Any], profit_factor: float, fee_drag_pct: float) -> float:
    explicit = metrics.get("profit_factor_after_fee")
    if explicit is not None:
        return _safe_float(explicit)
    return profit_factor * max(0.0, 1.0 - fee_drag_pct)


def _action_value(metrics: dict[str, Any], key: str, action: str) -> float:
    values = metrics.get(key) or {}
    if not isinstance(values, dict):
        return 0.0
    return _safe_float(values.get(action))


def _action_profit_factor(metrics: dict[str, Any], action: str) -> float:
    return _action_value(metrics, "action_profit_factor", action)


def _action_counts_present(metrics: dict[str, Any], actions: set[str]) -> bool:
    values = metrics.get("action_trade_counts")
    return isinstance(values, dict) and bool(actions & set(values))


def _effective_elapsed_days(metrics: dict[str, Any]) -> float:
    explicit = _safe_float(metrics.get("effective_elapsed_days"))
    if explicit > 0:
        return explicit
    elapsed = _safe_float(metrics.get("elapsed_days"))
    processed_ratio = _safe_float(metrics.get("processed_event_ratio"))
    if elapsed > 0 and 0.0 < processed_ratio <= 1.0:
        return max(1.0, elapsed * processed_ratio)
    if elapsed > 0:
        return elapsed
    for key in ("duration_days", "window_days", "train_days"):
        days = _safe_float(metrics.get(key))
        if days > 0:
            return days
    return TRAIN_TRUTH_DAY_FALLBACK


def _position_switches_per_30d(metrics: dict[str, Any], flip_count: float) -> float:
    explicit = _safe_float(metrics.get("position_switches_per_30d"))
    if explicit > 0:
        return explicit
    elapsed_days = _effective_elapsed_days(metrics)
    return (flip_count / elapsed_days) * 30.0 if elapsed_days > 0 and flip_count > 0 else 0.0


def _flip_reduce_fee_drag_pct(metrics: dict[str, Any]) -> float:
    explicit = metrics.get("flip_reduce_fee_drag_pct")
    if explicit is not None:
        return _safe_float(explicit)
    return _action_value(metrics, "action_fee_drag_pct", "flip") + _action_value(
        metrics,
        "action_fee_drag_pct",
        "reduce",
    )


def _net_pnl_per_position_switch(metrics: dict[str, Any], net_pnl: float, flip_count: float) -> float:
    explicit = metrics.get("net_pnl_per_position_switch")
    if explicit is not None:
        return _safe_float(explicit)
    return net_pnl / max(1.0, flip_count)


def _protective_reduce_share(metrics: dict[str, Any], flip_count: float, reduce_count: float) -> tuple[float, float]:
    explicit = metrics.get("protective_reduce_share")
    if explicit is not None:
        return _safe_float(explicit), max(1.0, flip_count + reduce_count)
    action_count = flip_count + reduce_count
    if action_count <= 0:
        return 0.0, 0.0
    return reduce_count / action_count, action_count


def _assert_train_only_metric_keys(metrics: dict[str, Any]) -> None:
    forbidden = sorted(
        key for key in metrics if any(str(key).lower().startswith(prefix) for prefix in _FORBIDDEN_SELECTION_PREFIXES)
    )
    if forbidden:
        raise ValueError(f"Regime objective candidate generation must not use dev/test metrics: {forbidden}")


def _relabel(window: WindowSpec, label: str) -> WindowSpec:
    return WindowSpec(label=label, start_ns=window.start_ns, end_ns=window.end_ns)


def _worst_period_by_label(label: str) -> WindowSpec:
    for window in windows_from_worst_periods():
        if window.label == label:
            return window
    raise ValueError(f"Missing adversarial train bucket source: {label}")


def train_regime_bucket_specs(split_spec: DatasetSplitSpec | None = None) -> list[RegimeBucketSpec]:
    """Return deterministic BTC train buckets for regime-objective scoring."""

    spec = split_spec or default_btc_split_spec()
    buckets = [
        RegimeBucketSpec(
            label="train_full",
            regime_role="full_train_baseline",
            source="dataset_split_contract",
            window=WindowSpec("train_full", spec.train.start_ns, spec.train.end_ns),
        ),
        RegimeBucketSpec(
            label="train_hostile_grind_2022_10",
            regime_role="hostile_grind",
            source="tests/adversarial/worst_periods.json:2022-10 DD",
            window=_relabel(_worst_period_by_label("2022-10 DD"), "train_hostile_grind_2022_10"),
        ),
        RegimeBucketSpec(
            label="train_transition_drawdown_2023_05",
            regime_role="crash_or_transition_drawdown",
            source="tests/adversarial/worst_periods.json:2023-05 DD",
            window=_relabel(_worst_period_by_label("2023-05 DD"), "train_transition_drawdown_2023_05"),
        ),
        RegimeBucketSpec(
            label="train_neutral_chop_2023_08",
            regime_role="neutral_chop_weak_window",
            source="fixed_2023_08_window",
            window=_relabel(fixed_2023_08_window(), "train_neutral_chop_2023_08"),
        ),
        RegimeBucketSpec(
            label="train_late_cycle_recovery_2023_11",
            regime_role="bull_late_cycle_recovery",
            source="tests/adversarial/worst_periods.json:2023-11 DD",
            window=_relabel(_worst_period_by_label("2023-11 DD"), "train_late_cycle_recovery_2023_11"),
        ),
    ]
    validate_train_regime_buckets(buckets, spec)
    return buckets


def train_regime_windows(split_spec: DatasetSplitSpec | None = None) -> list[WindowSpec]:
    """Return WindowSpec values for comparison replay."""

    return [bucket.window for bucket in train_regime_bucket_specs(split_spec)]


def validate_train_regime_buckets(
    buckets: list[RegimeBucketSpec],
    split_spec: DatasetSplitSpec | None = None,
) -> None:
    """Fail closed if any bucket escapes the train split."""

    spec = split_spec or default_btc_split_spec()
    for bucket in buckets:
        window = bucket.window
        if window.start_ns is None or window.end_ns is None:
            raise ValueError(f"Regime objective bucket must be bounded: {bucket.label}")
        if not period_within_split(window.start_ns, window.end_ns, spec.train):
            raise ValueError(f"Regime objective bucket is outside train split: {bucket.label}")


def _runs(payload: dict[str, Any], variant: str) -> list[dict[str, Any]]:
    return [item for item in payload.get("window_reports", []) if item.get("variant") == variant]


def _run_by_label(payload: dict[str, Any], *, variant: str, label: str) -> dict[str, Any]:
    return next((item for item in _runs(payload, variant) if item.get("window_label") == label), {})


def _window_metrics(run: dict[str, Any]) -> dict[str, float]:
    metrics = run.get("metrics") or {}
    return {
        "sharpe": round(_safe_float(metrics.get("sharpe")), 4),
        "net_pnl": round(_safe_float(metrics.get("net_pnl")), 6),
        "max_drawdown": round(_safe_float(metrics.get("max_drawdown")), 6),
        "profit_factor": round(_safe_float(metrics.get("profit_factor")), 6),
        "turnover_per_day": round(_safe_float(metrics.get("turnover_per_day")), 4),
        "fee_drag_pct": round(_safe_float(metrics.get("fee_drag_pct")), 6),
    }


def _variant_summary(payload: dict[str, Any], *, variant: str, bucket_labels: list[str]) -> dict[str, Any]:
    bucket_metrics = {
        label: _window_metrics(_run_by_label(payload, variant=variant, label=label)) for label in bucket_labels
    }
    bucket_sharpes = [item["sharpe"] for item in bucket_metrics.values()]
    return {
        "variant": variant,
        "full_train": bucket_metrics.get("train_full", {}),
        "bucket_metrics": bucket_metrics,
        "worst_bucket_sharpe": round(min(bucket_sharpes), 4) if bucket_sharpes else 0.0,
        "negative_bucket_count": sum(1 for value in bucket_sharpes if value < 0.0),
    }


def _metric_gap(candidate: dict[str, float], anchor: dict[str, float], key: str) -> float:
    return round(_safe_float(candidate.get(key)) - _safe_float(anchor.get(key)), 6)


def _sharpe_anchor(current: dict[str, float], paulwei: dict[str, float]) -> float:
    return max(_safe_float(current.get("sharpe")), _safe_float(paulwei.get("sharpe")))


def _max_anchor(current: dict[str, float], paulwei: dict[str, float], key: str) -> float:
    return max(_safe_float(current.get(key)), _safe_float(paulwei.get(key)))


def _bucket_deltas(
    *,
    candidate: dict[str, Any],
    current: dict[str, Any],
    paulwei: dict[str, Any],
    bucket_labels: list[str],
) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    for label in bucket_labels:
        candidate_metrics = candidate["bucket_metrics"][label]
        current_metrics = current["bucket_metrics"][label]
        paulwei_metrics = paulwei["bucket_metrics"][label]
        sharpe_anchor = _sharpe_anchor(current_metrics, paulwei_metrics)
        deltas[label] = {
            "candidate_vs_current": {
                "sharpe": _metric_gap(candidate_metrics, current_metrics, "sharpe"),
                "net_pnl": _metric_gap(candidate_metrics, current_metrics, "net_pnl"),
                "max_drawdown": _metric_gap(candidate_metrics, current_metrics, "max_drawdown"),
                "turnover_per_day": _metric_gap(candidate_metrics, current_metrics, "turnover_per_day"),
                "fee_drag_pct": _metric_gap(candidate_metrics, current_metrics, "fee_drag_pct"),
            },
            "candidate_vs_paulwei_baseline": {
                "sharpe": _metric_gap(candidate_metrics, paulwei_metrics, "sharpe"),
                "net_pnl": _metric_gap(candidate_metrics, paulwei_metrics, "net_pnl"),
                "max_drawdown": _metric_gap(candidate_metrics, paulwei_metrics, "max_drawdown"),
                "turnover_per_day": _metric_gap(candidate_metrics, paulwei_metrics, "turnover_per_day"),
                "fee_drag_pct": _metric_gap(candidate_metrics, paulwei_metrics, "fee_drag_pct"),
            },
            "best_baseline_sharpe": round(sharpe_anchor, 4),
            "candidate_sharpe_gap_vs_best_baseline": round(candidate_metrics["sharpe"] - sharpe_anchor, 4),
        }
    return deltas


def _regime_objective_failures(
    *,
    candidate: dict[str, Any],
    current: dict[str, Any],
    paulwei: dict[str, Any],
    bucket_labels: list[str],
) -> list[str]:
    failures: list[str] = []
    full_train_candidate = candidate["bucket_metrics"]["train_full"]
    full_train_current = current["bucket_metrics"]["train_full"]
    aggregate_improved = full_train_candidate["sharpe"] > full_train_current["sharpe"]
    hostile_regressed = False

    for label in bucket_labels:
        if label == "train_full":
            continue
        candidate_metrics = candidate["bucket_metrics"][label]
        current_metrics = current["bucket_metrics"][label]
        paulwei_metrics = paulwei["bucket_metrics"][label]
        sharpe_floor = _sharpe_anchor(current_metrics, paulwei_metrics) - MAX_BUCKET_SHARPE_REGRESSION
        if candidate_metrics["sharpe"] < sharpe_floor:
            hostile_regressed = True
            failures.append(f"{label}_sharpe_regressed")

    if aggregate_improved and hostile_regressed:
        failures.append("aggregate_sharpe_only_improvement")

    candidate_full = candidate["bucket_metrics"]["train_full"]
    current_full = current["bucket_metrics"]["train_full"]
    paulwei_full = paulwei["bucket_metrics"]["train_full"]
    fee_anchor = _max_anchor(current_full, paulwei_full, "fee_drag_pct")
    fee_limit = max(fee_anchor * MAX_FEE_DRAG_RELATIVE_INCREASE, fee_anchor + MAX_FEE_DRAG_ABSOLUTE_INCREASE)
    if candidate_full["fee_drag_pct"] > fee_limit:
        failures.append("train_fee_drag_structurally_worse")

    turnover_anchor = _max_anchor(current_full, paulwei_full, "turnover_per_day")
    turnover_limit = max(
        turnover_anchor * MAX_TURNOVER_RELATIVE_INCREASE,
        turnover_anchor + MAX_TURNOVER_ABSOLUTE_INCREASE,
    )
    if candidate_full["turnover_per_day"] > turnover_limit:
        failures.append("train_turnover_structurally_worse")

    if candidate["negative_bucket_count"] > current["negative_bucket_count"]:
        failures.append("negative_train_bucket_count_increased")

    return failures


def build_regime_objective_verdict(
    comparison_payload: dict[str, Any],
    *,
    candidate_variant: str,
    split_spec: DatasetSplitSpec | None = None,
) -> dict[str, Any]:
    """Build the machine-readable train-only regime objective verdict."""

    spec = split_spec or default_btc_split_spec()
    buckets = train_regime_bucket_specs(spec)
    bucket_labels = [bucket.label for bucket in buckets]
    current = _variant_summary(comparison_payload, variant="current", bucket_labels=bucket_labels)
    paulwei = _variant_summary(comparison_payload, variant="paulwei_baseline", bucket_labels=bucket_labels)
    candidate = _variant_summary(comparison_payload, variant=candidate_variant, bucket_labels=bucket_labels)
    failures = _regime_objective_failures(
        candidate=candidate,
        current=current,
        paulwei=paulwei,
        bucket_labels=bucket_labels,
    )
    verdict = "allow_dev_diagnostic_review" if not failures else "reject_regime_objective"
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "research_line_id": "train_only_regime_objective_redesign",
        "predecessor_research_line_id": "execution_inventory_architecture_rethink_v1",
        "candidate_variant": candidate_variant,
        "asset": spec.asset,
        "split_version": spec.version,
        "selection_policy": "train_only_regime_objective_dev_diagnostics_test_acceptance",
        "train_search_executed": False,
        "optuna_search_executed": False,
        "rl_search_executed": False,
        "full_training_executed": False,
        "test_evaluated": False,
        "promotion_allowed": False,
        "paper_live_eligible": False,
        "dev_diagnostic_review_allowed": verdict == "allow_dev_diagnostic_review",
        "terminal_recommendation": verdict,
        "rejection_reasons": failures,
        "thresholds": {
            "max_bucket_sharpe_regression": MAX_BUCKET_SHARPE_REGRESSION,
            "max_fee_drag_relative_increase": MAX_FEE_DRAG_RELATIVE_INCREASE,
            "max_fee_drag_absolute_increase": MAX_FEE_DRAG_ABSOLUTE_INCREASE,
            "max_turnover_relative_increase": MAX_TURNOVER_RELATIVE_INCREASE,
            "max_turnover_absolute_increase": MAX_TURNOVER_ABSOLUTE_INCREASE,
        },
        "regime_buckets": [
            {
                "label": bucket.label,
                "regime_role": bucket.regime_role,
                "source": bucket.source,
                "window": asdict(bucket.window),
            }
            for bucket in buckets
        ],
        "variant_window_summary": {
            "current": current,
            "paulwei_baseline": paulwei,
            candidate_variant: candidate,
        },
        "bucket_deltas": _bucket_deltas(
            candidate=candidate,
            current=current,
            paulwei=paulwei,
            bucket_labels=bucket_labels,
        ),
    }


def build_regime_objective_markdown(verdict: dict[str, Any]) -> str:
    """Render a compact markdown summary for human review."""

    lines = [
        "# Train-Only Regime Objective Check",
        "",
        f"- research_line_id: `{verdict['research_line_id']}`",
        f"- predecessor_research_line_id: `{verdict['predecessor_research_line_id']}`",
        f"- candidate_variant: `{verdict['candidate_variant']}`",
        f"- terminal_recommendation: `{verdict['terminal_recommendation']}`",
        f"- dev_diagnostic_review_allowed: `{verdict['dev_diagnostic_review_allowed']}`",
        f"- test_evaluated: `{verdict['test_evaluated']}`",
        f"- promotion_allowed: `{verdict['promotion_allowed']}`",
        f"- rejection_reasons: `{verdict['rejection_reasons']}`",
        "",
        "## Buckets",
        "",
    ]
    for bucket in verdict["regime_buckets"]:
        lines.append(f"- `{bucket['label']}`: {bucket['regime_role']}")
    lines.extend(["", "## Candidate Bucket Gaps", ""])
    for label, delta in verdict["bucket_deltas"].items():
        lines.extend(
            [
                f"### {label}",
                "",
                f"- candidate_sharpe_gap_vs_best_baseline: `{delta['candidate_sharpe_gap_vs_best_baseline']}`",
                f"- candidate_vs_current: `{delta['candidate_vs_current']}`",
                f"- candidate_vs_paulwei_baseline: `{delta['candidate_vs_paulwei_baseline']}`",
                "",
            ]
        )
    return "\n".join(lines)


def score_regime_objective_candidate_generation(
    metrics: dict[str, Any],
    *,
    baseline_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a train-only candidate before dev diagnostics.

    The input is intentionally dict-shaped because stage1 Optuna/RL candidates
    already flow through JSON-ready train metrics. Any dev/test metric key fails
    closed so this helper cannot become a hidden validation feedback path.
    """

    train_metrics = dict(metrics or {})
    _assert_train_only_metric_keys(train_metrics)
    baseline = dict(baseline_metrics or {})
    _assert_train_only_metric_keys(baseline)

    sharpe = _safe_float(train_metrics.get("sharpe"))
    profit_factor = _safe_float(train_metrics.get("profit_factor"))
    drawdown = _safe_float(train_metrics.get("max_drawdown"))
    net_pnl = _safe_float(train_metrics.get("net_pnl", train_metrics.get("pnl")))
    turnover = _safe_float(train_metrics.get("turnover_per_day"))
    trade_count = _safe_float(train_metrics.get("trade_count"))
    fee_drag = _safe_float(train_metrics.get("fee_drag_pct"))
    net_pnl_per_trade = _net_pnl_per_trade(train_metrics, net_pnl, trade_count)
    trade_count_per_day = _trade_count_per_day(train_metrics, trade_count)
    profit_factor_after_fee = _profit_factor_after_fee(train_metrics, profit_factor, fee_drag)
    profit_factor_after_fee_present = "profit_factor" in train_metrics or "profit_factor_after_fee" in train_metrics
    add_count = _action_value(train_metrics, "action_trade_counts", "add")
    entry_count = _action_value(train_metrics, "action_trade_counts", "entry")
    exit_count = _action_value(train_metrics, "action_trade_counts", "exit")
    flip_count = _action_value(train_metrics, "action_trade_counts", "flip")
    reduce_count = _action_value(train_metrics, "action_trade_counts", "reduce")
    position_switches_per_30d = _position_switches_per_30d(train_metrics, flip_count)
    flip_reduce_fee_drag_pct = _flip_reduce_fee_drag_pct(train_metrics)
    exit_fee_drag_pct = _action_value(train_metrics, "action_fee_drag_pct", "exit")
    flip_action_fee_drag_pct = _action_value(train_metrics, "action_fee_drag_pct", "flip")
    reduce_action_profit_factor = _action_profit_factor(train_metrics, "reduce")
    flip_reduce_trade_share = (flip_count + reduce_count) / trade_count if trade_count > 0 else 0.0
    net_pnl_per_position_switch = _net_pnl_per_position_switch(train_metrics, net_pnl, flip_count)
    protective_reduce_share, protective_reduce_action_count = _protective_reduce_share(
        train_metrics,
        flip_count,
        reduce_count,
    )
    staged_build_tranche_count = entry_count + add_count
    staged_unwind_tranche_count = exit_count + reduce_count
    staged_tranche_count = staged_build_tranche_count + staged_unwind_tranche_count
    staged_inventory_balance_ratio = (
        min(staged_build_tranche_count, staged_unwind_tranche_count)
        / max(staged_build_tranche_count, staged_unwind_tranche_count)
        if max(staged_build_tranche_count, staged_unwind_tranche_count) > 0
        else 0.0
    )
    tranche_actions_per_position_switch = staged_tranche_count / max(1.0, flip_count)
    time_leverage_efficiency = max(0.0, net_pnl_per_position_switch) / max(1.0, abs(net_pnl_per_trade))
    time_leverage_bonus = min(0.35, 0.05 * time_leverage_efficiency)
    tranche_alignment_bonus = 0.0
    baseline_turnover = _safe_float(baseline.get("turnover_per_day"))
    baseline_trade_count = _safe_float(baseline.get("trade_count"))
    baseline_fee_drag = _safe_float(baseline.get("fee_drag_pct"))
    baseline_net_pnl = _safe_float(baseline.get("net_pnl", baseline.get("pnl")))
    baseline_net_pnl_per_trade = _net_pnl_per_trade(baseline, baseline_net_pnl, baseline_trade_count)
    baseline_trade_count_per_day = _trade_count_per_day(baseline, baseline_trade_count)
    baseline_profit_factor = _safe_float(baseline.get("profit_factor"))
    baseline_profit_factor_after_fee = _profit_factor_after_fee(
        baseline,
        baseline_profit_factor,
        baseline_fee_drag,
    )
    baseline_flip_count = _action_value(baseline, "action_trade_counts", "flip")
    baseline_position_switches_per_30d = _position_switches_per_30d(baseline, baseline_flip_count)
    baseline_flip_reduce_fee_drag_pct = _flip_reduce_fee_drag_pct(baseline)
    baseline_net_pnl_per_position_switch = _net_pnl_per_position_switch(
        baseline,
        baseline_net_pnl,
        baseline_flip_count,
    )
    turnover_penalty = 0.0
    trade_count_penalty = 0.0
    fee_drag_penalty = 0.0
    net_pnl_per_trade_penalty = 0.0
    position_switch_penalty = 0.0
    mature_position_switch_penalty = 0.0
    flip_reduce_fee_drag_penalty = 0.0
    exit_fee_drag_penalty = 0.0
    flip_action_fee_drag_penalty = 0.0
    reduce_profit_factor_penalty = 0.0
    staged_inventory_balance_penalty = 0.0
    position_switch_efficiency_penalty = 0.0
    protective_reduce_bias_penalty = (
        max(0.0, (MIN_PROTECTIVE_REDUCE_SHARE - protective_reduce_share) / MIN_PROTECTIVE_REDUCE_SHARE)
        if protective_reduce_action_count > 0
        else 0.0
    )
    drawdown_preferred_band_penalty = max(
        0.0,
        (drawdown - TRAIN_DRAWDOWN_PREFERRED_BAND) / max(0.01, TRAIN_DRAWDOWN_PREFERRED_BAND),
    )
    trade_count_per_day_penalty = max(0.0, (trade_count_per_day - MAX_TRADE_COUNT_PER_DAY) / MAX_TRADE_COUNT_PER_DAY)
    profit_factor_after_fee_floor = (
        max(MIN_PROFIT_FACTOR_AFTER_FEE, baseline_profit_factor_after_fee * 0.95)
        if baseline_profit_factor_after_fee > 0
        else MIN_PROFIT_FACTOR_AFTER_FEE
    )
    profit_factor_after_fee_penalty = (
        max(
            0.0,
            (profit_factor_after_fee_floor - profit_factor_after_fee) / max(0.1, profit_factor_after_fee_floor),
        )
        if profit_factor_after_fee_present
        else 0.0
    )
    if baseline_turnover > 0:
        turnover_penalty = max(0.0, (turnover - baseline_turnover) / max(1.0, baseline_turnover))
    elif baseline_trade_count > 0:
        trade_count_penalty = max(0.0, (trade_count - baseline_trade_count) / max(1.0, baseline_trade_count))
    else:
        trade_count_penalty = max(0.0, (trade_count - 4200.0) / 500.0)
    if baseline_trade_count_per_day > 0:
        trade_count_per_day_penalty = max(
            trade_count_per_day_penalty,
            (trade_count_per_day - baseline_trade_count_per_day * 1.10) / max(0.01, baseline_trade_count_per_day),
        )
    if baseline_fee_drag > 0:
        fee_drag_penalty = max(0.0, (fee_drag - baseline_fee_drag) / max(0.001, baseline_fee_drag))
    else:
        fee_drag_penalty = max(0.0, (fee_drag - 0.02) / 0.01)
    position_switch_limit = MAX_POSITION_SWITCHES_PER_30D
    if baseline_position_switches_per_30d > 0:
        position_switch_limit = max(
            position_switch_limit,
            baseline_position_switches_per_30d * MAX_POSITION_SWITCH_RELATIVE_INCREASE,
        )
    position_switch_penalty = max(
        0.0,
        (position_switches_per_30d - position_switch_limit) / max(0.10, position_switch_limit),
    )
    mature_position_switch_penalty = max(
        0.0,
        (position_switches_per_30d - PAULWEI_MATURE_POSITION_SWITCHES_PER_30D)
        / PAULWEI_MATURE_POSITION_SWITCHES_PER_30D,
    )
    flip_reduce_fee_drag_limit = MAX_FLIP_REDUCE_FEE_DRAG_PCT_ABSOLUTE
    if baseline_flip_reduce_fee_drag_pct > 0:
        flip_reduce_fee_drag_limit = max(
            flip_reduce_fee_drag_limit,
            baseline_flip_reduce_fee_drag_pct * MAX_FLIP_REDUCE_FEE_DRAG_RELATIVE_INCREASE,
            baseline_flip_reduce_fee_drag_pct + 0.001,
        )
    flip_reduce_fee_drag_penalty = max(
        0.0,
        (flip_reduce_fee_drag_pct - flip_reduce_fee_drag_limit) / max(0.001, flip_reduce_fee_drag_limit),
    )
    exit_fee_drag_penalty = max(
        0.0,
        (exit_fee_drag_pct - MAX_EXIT_FEE_DRAG_PCT_ABSOLUTE) / MAX_EXIT_FEE_DRAG_PCT_ABSOLUTE,
    )
    flip_action_fee_drag_penalty = max(
        0.0,
        (flip_action_fee_drag_pct - MAX_FLIP_ACTION_FEE_DRAG_PCT_ABSOLUTE) / MAX_FLIP_ACTION_FEE_DRAG_PCT_ABSOLUTE,
    )
    reduce_profit_factor_penalty = (
        max(
            0.0,
            (MIN_REDUCE_ACTION_PROFIT_FACTOR - reduce_action_profit_factor) / MIN_REDUCE_ACTION_PROFIT_FACTOR,
        )
        if reduce_count > 0 and reduce_action_profit_factor > 0.0
        else 0.0
    )
    staged_inventory_balance_penalty = (
        max(
            0.0,
            (MIN_STAGED_BUILD_UNWIND_BALANCE_RATIO - staged_inventory_balance_ratio)
            / MIN_STAGED_BUILD_UNWIND_BALANCE_RATIO,
        )
        if staged_tranche_count >= 10
        and _action_counts_present(train_metrics, {"add", "entry", "exit"})
        and max(staged_build_tranche_count, staged_unwind_tranche_count) > 0
        else 0.0
    )
    tranche_alignment_bonus = (
        min(0.20, 0.04 * min(5.0, tranche_actions_per_position_switch))
        if tranche_actions_per_position_switch >= MIN_TRANCHE_ACTIONS_PER_POSITION_SWITCH
        and staged_inventory_balance_ratio >= MIN_STAGED_BUILD_UNWIND_BALANCE_RATIO
        and position_switches_per_30d <= PAULWEI_MATURE_POSITION_SWITCHES_PER_30D
        and reduce_profit_factor_penalty == 0.0
        and flip_reduce_fee_drag_penalty == 0.0
        else 0.0
    )
    if baseline_net_pnl_per_trade > 0:
        net_pnl_per_trade_penalty = max(
            0.0,
            ((baseline_net_pnl_per_trade * MIN_NET_PNL_PER_TRADE_RELATIVE) - net_pnl_per_trade)
            / max(1.0, abs(baseline_net_pnl_per_trade)),
        )
    elif trade_count > 0 and net_pnl_per_trade <= 0:
        net_pnl_per_trade_penalty = 1.0
    if baseline_net_pnl_per_position_switch > 0:
        position_switch_efficiency_penalty = max(
            0.0,
            (
                (baseline_net_pnl_per_position_switch * MIN_NET_PNL_PER_POSITION_SWITCH_RELATIVE)
                - net_pnl_per_position_switch
            )
            / max(1.0, abs(baseline_net_pnl_per_position_switch)),
        )

    bucket_blockers: list[str] = []
    bucket_metrics = dict(train_metrics.get("train_bucket_metrics") or {})
    baseline_buckets = dict(baseline.get("train_bucket_metrics") or {})
    bucket_score = 0.0
    bucket_count = 0
    for label, raw_bucket in sorted(bucket_metrics.items()):
        if any(str(label).lower().startswith(prefix) for prefix in _FORBIDDEN_SELECTION_PREFIXES):
            raise ValueError(f"Regime objective bucket must be train-only: {label}")
        bucket = dict(raw_bucket or {})
        _assert_train_only_metric_keys(bucket)
        baseline_bucket = dict(baseline_buckets.get(label) or {})
        _assert_train_only_metric_keys(baseline_bucket)
        bucket_sharpe = _safe_float(bucket.get("sharpe"))
        baseline_sharpe = _safe_float(baseline_bucket.get("sharpe"))
        bucket_gap = bucket_sharpe - baseline_sharpe
        bucket_score += bucket_gap
        bucket_count += 1
        if baseline_bucket and bucket_gap < -MAX_BUCKET_SHARPE_REGRESSION:
            bucket_blockers.append(f"{label}_sharpe_regressed")

    if bucket_count:
        bucket_score /= bucket_count
    if turnover_penalty > 0.20:
        bucket_blockers.append("train_turnover_structurally_worse")
    if trade_count_penalty > 0.20:
        bucket_blockers.append("train_trade_count_churn_worse")
    if trade_count_per_day_penalty > 0.0:
        bucket_blockers.append("train_trade_count_per_day_cap_exceeded")
    if fee_drag_penalty > 0.15 or fee_drag > MAX_FEE_DRAG_PCT_ABSOLUTE:
        bucket_blockers.append("train_fee_drag_structurally_worse")
    if fee_drag > MAX_FEE_DRAG_PCT_ABSOLUTE:
        bucket_blockers.append("train_fee_drag_cap_exceeded")
    if net_pnl_per_trade_penalty > 0.0:
        bucket_blockers.append("train_net_pnl_per_trade_inefficient")
    if profit_factor_after_fee_penalty > 0.0:
        bucket_blockers.append("train_profit_factor_after_fee_weak")
    if position_switch_penalty > 0.15:
        bucket_blockers.append("train_position_switch_cadence_worse")
    if flip_reduce_fee_drag_penalty > 0.15:
        bucket_blockers.append("train_flip_reduce_fee_drag_worse")
    if exit_fee_drag_penalty > 0.15:
        bucket_blockers.append("train_exit_fee_drag_worse")
    if flip_action_fee_drag_penalty > 0.15:
        bucket_blockers.append("train_flip_action_fee_drag_worse")
    if reduce_profit_factor_penalty > 0.0:
        bucket_blockers.append("train_reduce_profit_factor_weak")
    if staged_inventory_balance_penalty > 0.25:
        bucket_blockers.append("train_staged_inventory_balance_weak")
    if protective_reduce_bias_penalty > 0.0:
        bucket_blockers.append("train_protective_reduce_bias_weak")
    if position_switch_efficiency_penalty > 0.20:
        bucket_blockers.append("train_time_leverage_efficiency_weak")

    regime_objective_score = (
        0.55 * sharpe
        + 0.20 * profit_factor
        + 0.10 * profit_factor_after_fee
        + 0.25 * bucket_score
        + time_leverage_bonus
        + tranche_alignment_bonus
        - 1.50 * drawdown
        - 0.60 * drawdown_preferred_band_penalty
        - 0.80 * turnover_penalty
        - 0.80 * trade_count_penalty
        - 0.80 * fee_drag_penalty
        - 0.70 * net_pnl_per_trade_penalty
        - 0.75 * trade_count_per_day_penalty
        - 0.75 * profit_factor_after_fee_penalty
        - 0.65 * position_switch_penalty
        - 0.35 * mature_position_switch_penalty
        - 0.85 * flip_reduce_fee_drag_penalty
        - 0.55 * exit_fee_drag_penalty
        - 0.55 * flip_action_fee_drag_penalty
        - 0.45 * reduce_profit_factor_penalty
        - 0.40 * staged_inventory_balance_penalty
        - 0.70 * protective_reduce_bias_penalty
        - 0.45 * position_switch_efficiency_penalty
    )
    return {
        "regime_objective_score": round(regime_objective_score, 6),
        "bucket_blockers": bucket_blockers,
        "turnover_penalty": round(turnover_penalty, 6),
        "trade_count_penalty": round(trade_count_penalty, 6),
        "fee_drag_penalty": round(fee_drag_penalty, 6),
        "net_pnl_per_trade": round(net_pnl_per_trade, 6),
        "net_pnl_per_trade_penalty": round(net_pnl_per_trade_penalty, 6),
        "trade_count_per_day": round(trade_count_per_day, 6),
        "trade_count_per_day_penalty": round(trade_count_per_day_penalty, 6),
        "drawdown_preferred_band": TRAIN_DRAWDOWN_PREFERRED_BAND,
        "drawdown_preferred_band_penalty": round(drawdown_preferred_band_penalty, 6),
        "profit_factor_after_fee": round(profit_factor_after_fee, 6),
        "profit_factor_after_fee_penalty": round(profit_factor_after_fee_penalty, 6),
        "position_switches_per_30d": round(position_switches_per_30d, 6),
        "target_position_switches_per_30d": PAULWEI_MATURE_POSITION_SWITCHES_PER_30D,
        "position_switch_penalty": round(position_switch_penalty, 6),
        "mature_position_switch_penalty": round(mature_position_switch_penalty, 6),
        "flip_reduce_fee_drag_pct": round(flip_reduce_fee_drag_pct, 6),
        "flip_reduce_fee_drag_penalty": round(flip_reduce_fee_drag_penalty, 6),
        "exit_fee_drag_pct": round(exit_fee_drag_pct, 6),
        "exit_fee_drag_penalty": round(exit_fee_drag_penalty, 6),
        "flip_action_fee_drag_pct": round(flip_action_fee_drag_pct, 6),
        "flip_action_fee_drag_penalty": round(flip_action_fee_drag_penalty, 6),
        "reduce_action_profit_factor": round(reduce_action_profit_factor, 6),
        "reduce_profit_factor_penalty": round(reduce_profit_factor_penalty, 6),
        "flip_reduce_trade_share": round(flip_reduce_trade_share, 6),
        "staged_build_tranche_count": round(staged_build_tranche_count, 6),
        "staged_unwind_tranche_count": round(staged_unwind_tranche_count, 6),
        "staged_inventory_balance_ratio": round(staged_inventory_balance_ratio, 6),
        "staged_inventory_balance_penalty": round(staged_inventory_balance_penalty, 6),
        "tranche_actions_per_position_switch": round(tranche_actions_per_position_switch, 6),
        "tranche_alignment_bonus": round(tranche_alignment_bonus, 6),
        "protective_reduce_share": round(protective_reduce_share, 6),
        "protective_reduce_action_count": round(protective_reduce_action_count, 6),
        "protective_reduce_bias_penalty": round(protective_reduce_bias_penalty, 6),
        "net_pnl_per_position_switch": round(net_pnl_per_position_switch, 6),
        "position_switch_efficiency_penalty": round(position_switch_efficiency_penalty, 6),
        "time_leverage_efficiency": round(time_leverage_efficiency, 6),
        "time_leverage_bonus": round(time_leverage_bonus, 6),
        "fee_efficiency_thresholds": {
            "max_fee_drag_pct_absolute": MAX_FEE_DRAG_PCT_ABSOLUTE,
            "max_trade_count_per_day": MAX_TRADE_COUNT_PER_DAY,
            "min_profit_factor_after_fee": MIN_PROFIT_FACTOR_AFTER_FEE,
            "min_net_pnl_per_trade_relative": MIN_NET_PNL_PER_TRADE_RELATIVE,
            "target_position_switches_per_30d": PAULWEI_MATURE_POSITION_SWITCHES_PER_30D,
            "max_position_switches_per_30d": MAX_POSITION_SWITCHES_PER_30D,
            "max_flip_reduce_fee_drag_pct_absolute": MAX_FLIP_REDUCE_FEE_DRAG_PCT_ABSOLUTE,
            "max_exit_fee_drag_pct_absolute": MAX_EXIT_FEE_DRAG_PCT_ABSOLUTE,
            "max_flip_action_fee_drag_pct_absolute": MAX_FLIP_ACTION_FEE_DRAG_PCT_ABSOLUTE,
            "min_reduce_action_profit_factor": MIN_REDUCE_ACTION_PROFIT_FACTOR,
            "min_staged_build_unwind_balance_ratio": MIN_STAGED_BUILD_UNWIND_BALANCE_RATIO,
            "min_tranche_actions_per_position_switch": MIN_TRANCHE_ACTIONS_PER_POSITION_SWITCH,
            "min_net_pnl_per_position_switch_relative": MIN_NET_PNL_PER_POSITION_SWITCH_RELATIVE,
            "min_protective_reduce_share": MIN_PROTECTIVE_REDUCE_SHARE,
        },
        "allow_dev_diagnostic_review": not bucket_blockers,
        "bucket_metrics_available": bool(bucket_metrics),
        "selection_scope": "train_only",
        "test_evaluated": False,
        "promotion_allowed": False,
    }


def compare_regime_objective_candidate_rankings(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare existing Stage 1 ranking against regime-objective ranking."""

    scored: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        payload = dict(candidate or {})
        metrics = dict(payload.get("metrics") or {})
        baseline_metrics = dict(payload.get("baseline_train_metrics") or {})
        original_score = _safe_float(metrics.get("stage1_score", metrics.get("score", metrics.get("sharpe"))))
        regime_score = score_regime_objective_candidate_generation(metrics, baseline_metrics=baseline_metrics)
        scored.append(
            {
                "candidate_id": str(payload.get("candidate_id") or payload.get("name") or f"candidate_{index + 1}"),
                "params": dict(payload.get("params") or {}),
                "original_stage1_score": round(original_score, 6),
                "regime_objective_score": regime_score["regime_objective_score"],
                "allow_dev_diagnostic_review": regime_score["allow_dev_diagnostic_review"],
                "bucket_blockers": list(regime_score["bucket_blockers"]),
                "turnover_penalty": regime_score["turnover_penalty"],
                "fee_drag_penalty": regime_score["fee_drag_penalty"],
                "net_pnl_per_trade": regime_score["net_pnl_per_trade"],
                "trade_count_per_day": regime_score["trade_count_per_day"],
                "profit_factor_after_fee": regime_score["profit_factor_after_fee"],
                "position_switches_per_30d": regime_score["position_switches_per_30d"],
                "flip_reduce_fee_drag_pct": regime_score["flip_reduce_fee_drag_pct"],
                "net_pnl_per_position_switch": regime_score["net_pnl_per_position_switch"],
                "time_leverage_bonus": regime_score["time_leverage_bonus"],
            }
        )

    original_ranking = sorted(scored, key=lambda item: item["original_stage1_score"], reverse=True)
    regime_ranking = sorted(
        scored,
        key=lambda item: (
            bool(item["allow_dev_diagnostic_review"]),
            float(item["regime_objective_score"]),
        ),
        reverse=True,
    )
    allowed = [item for item in scored if item["allow_dev_diagnostic_review"]]
    rejected = [item for item in scored if not item["allow_dev_diagnostic_review"]]
    original_top = original_ranking[0]["candidate_id"] if original_ranking else None
    regime_top = regime_ranking[0]["candidate_id"] if regime_ranking else None
    return {
        "selection_scope": "train_only",
        "candidate_count": len(scored),
        "allowed_count": len(allowed),
        "rejected_count": len(rejected),
        "original_top_candidate": original_top,
        "regime_objective_top_candidate": regime_top,
        "top_candidate_changed": original_top != regime_top,
        "original_stage1_ranking": original_ranking,
        "regime_objective_ranking": regime_ranking,
        "rejected_candidates": rejected,
        "test_evaluated": False,
        "promotion_allowed": False,
    }
