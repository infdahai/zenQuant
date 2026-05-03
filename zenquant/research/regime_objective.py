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
    turnover_penalty = 0.0
    trade_count_penalty = 0.0
    fee_drag_penalty = 0.0
    net_pnl_per_trade_penalty = 0.0
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
    if baseline_net_pnl_per_trade > 0:
        net_pnl_per_trade_penalty = max(
            0.0,
            ((baseline_net_pnl_per_trade * MIN_NET_PNL_PER_TRADE_RELATIVE) - net_pnl_per_trade)
            / max(1.0, abs(baseline_net_pnl_per_trade)),
        )
    elif trade_count > 0 and net_pnl_per_trade <= 0:
        net_pnl_per_trade_penalty = 1.0

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

    regime_objective_score = (
        0.55 * sharpe
        + 0.20 * profit_factor
        + 0.10 * profit_factor_after_fee
        + 0.25 * bucket_score
        - 1.50 * drawdown
        - 0.80 * turnover_penalty
        - 0.80 * trade_count_penalty
        - 0.80 * fee_drag_penalty
        - 0.70 * net_pnl_per_trade_penalty
        - 0.75 * trade_count_per_day_penalty
        - 0.75 * profit_factor_after_fee_penalty
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
        "profit_factor_after_fee": round(profit_factor_after_fee, 6),
        "profit_factor_after_fee_penalty": round(profit_factor_after_fee_penalty, 6),
        "fee_efficiency_thresholds": {
            "max_fee_drag_pct_absolute": MAX_FEE_DRAG_PCT_ABSOLUTE,
            "max_trade_count_per_day": MAX_TRADE_COUNT_PER_DAY,
            "min_profit_factor_after_fee": MIN_PROFIT_FACTOR_AFTER_FEE,
            "min_net_pnl_per_trade_relative": MIN_NET_PNL_PER_TRADE_RELATIVE,
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
