#!/usr/bin/env python3
"""Research-only baseline gap attribution for current vs PaulWei baseline."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.comparison import (
    build_markdown_summary,
    compare_variants,
    default_comparison_windows,
    filtered_worst_period_windows,
)
from zenquant.research.splits import default_btc_split_spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze baseline gap with decision attribution ledgers")
    parser.add_argument("--root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", type=Path, default=Path("results/research_swarm/baseline_gap_attribution_v1"))
    parser.add_argument("--profile-path", type=Path, default=Path("config/profiles/default.toml"))
    parser.add_argument("--include-worst-periods", action="store_true")
    parser.add_argument("--skip-walk-forward", action="store_true", default=True)
    args = parser.parse_args()

    split_spec = default_btc_split_spec()
    windows = default_comparison_windows(split_spec, "dev")
    if args.include_worst_periods:
        seen = {window.label for window in windows}
        for window in filtered_worst_period_windows(split_spec, "dev"):
            if window.label not in seen:
                windows.append(window)
                seen.add(window.label)

    comparison_payload = compare_variants(
        HistoricalBacktestConfig(
            root_dir=args.root,
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
            profile_path=args.profile_path,
        ),
        windows=windows,
        walk_forward_windows=[] if args.skip_walk_forward else None,
        split_spec=split_spec,
        comparison_split="dev",
        include_attribution_ledger=True,
    )
    attribution = build_baseline_gap_attribution(comparison_payload, profile_path=args.profile_path)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "comparison.json").write_text(json.dumps(comparison_payload, indent=2), encoding="utf-8")
    (args.output_dir / "comparison.md").write_text(build_markdown_summary(comparison_payload), encoding="utf-8")
    (args.output_dir / "baseline_gap_attribution.json").write_text(
        json.dumps(attribution, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "baseline_gap_attribution.md").write_text(_markdown(attribution), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "verdict": attribution["verdict"]}, indent=2))


def build_baseline_gap_attribution(payload: dict[str, Any], profile_path: Path | None = None) -> dict[str, Any]:
    windows = sorted(
        {
            str(run.get("window_label"))
            for run in payload.get("window_reports", [])
            if run.get("variant") in {"current", "paulwei_baseline"}
        }
    )
    comparisons = [
        _compare_window(payload, window_label=window)
        for window in windows
        if _find_run(payload, "current", window) and _find_run(payload, "paulwei_baseline", window)
    ]
    aggregate = _aggregate(comparisons)
    blockers = _blockers(aggregate)
    return {
        "schema_version": "baseline_gap_attribution.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "split_contract": {
            "version": "btc_temporal_v2_train_selection_only",
            "comparison_split": "dev",
            "test_evaluated": False,
        },
        "baseline_variant": "paulwei_baseline",
        "current_variant": "current",
        "profile_path": str(profile_path) if profile_path else None,
        "window_count": len(comparisons),
        "window_comparisons": comparisons,
        "aggregate": aggregate,
        "verdict": {
            "status": "needs_research_fix" if blockers else "baseline_gap_mapped",
            "blocked_by": blockers[0] if blockers else None,
            "reason_codes": blockers,
            "next_action": _next_action(blockers),
        },
    }


def _compare_window(payload: dict[str, Any], *, window_label: str) -> dict[str, Any]:
    current = _find_run(payload, "current", window_label)
    baseline = _find_run(payload, "paulwei_baseline", window_label)
    current_metrics = _metrics(current)
    baseline_metrics = _metrics(baseline)
    current_ledger = current.get("decision_attribution_ledger") or {}
    baseline_ledger = baseline.get("decision_attribution_ledger") or {}
    micro_states = sorted(
        set((current_ledger.get("micro_state_summary") or {}).keys())
        | set((baseline_ledger.get("micro_state_summary") or {}).keys())
    )
    micro_state_deltas = {
        state: _micro_state_delta(
            (current_ledger.get("micro_state_summary") or {}).get(state) or {},
            (baseline_ledger.get("micro_state_summary") or {}).get(state) or {},
        )
        for state in micro_states
    }
    playbooks = sorted(
        set((current_ledger.get("playbook_summary") or {}).keys())
        | set((baseline_ledger.get("playbook_summary") or {}).keys())
    )
    playbook_deltas = {
        playbook: _micro_state_delta(
            (current_ledger.get("playbook_summary") or {}).get(playbook) or {},
            (baseline_ledger.get("playbook_summary") or {}).get(playbook) or {},
        )
        for playbook in playbooks
    }
    return {
        "window_label": window_label,
        "metrics": {
            "current": current_metrics,
            "paulwei_baseline": baseline_metrics,
            "delta_current_minus_baseline": _metric_delta(current_metrics, baseline_metrics),
        },
        "attribution_available": bool(current_ledger) and bool(baseline_ledger),
        "micro_state_deltas": micro_state_deltas,
        "playbook_deltas": playbook_deltas,
        "top_reason_code_delta": _reason_delta(current_ledger, baseline_ledger),
        "largest_turnover_regression_state": _largest_state_by(micro_state_deltas, "turnover_delta"),
        "largest_fee_regression_state": _largest_state_by(micro_state_deltas, "fee_delta"),
        "largest_pnl_improvement_state": _largest_state_by(micro_state_deltas, "pnl_delta"),
        "largest_turnover_regression_playbook": _largest_state_by(playbook_deltas, "turnover_delta"),
        "largest_pnl_drag_playbook": _smallest_state_by(playbook_deltas, "pnl_delta"),
    }


def _find_run(payload: dict[str, Any], variant: str, window_label: str) -> dict[str, Any]:
    return next(
        (
            run
            for run in payload.get("window_reports", [])
            if run.get("variant") == variant and run.get("window_label") == window_label
        ),
        {},
    )


def _metrics(run: dict[str, Any]) -> dict[str, float]:
    metrics = run.get("metrics") or {}
    return {
        "sharpe": round(_safe_float(metrics.get("sharpe")), 4),
        "net_pnl": round(_safe_float(metrics.get("net_pnl")), 6),
        "max_drawdown": round(_safe_float(metrics.get("max_drawdown")), 6),
        "profit_factor": round(_safe_float(metrics.get("profit_factor")), 6),
        "trade_count": round(_safe_float(metrics.get("trade_count")), 6),
        "turnover_per_day": round(_safe_float(metrics.get("turnover_per_day")), 6),
        "fee_drag_pct": round(_safe_float(metrics.get("fee_drag_pct")), 6),
    }


def _metric_delta(current: dict[str, float], baseline: dict[str, float]) -> dict[str, float]:
    return {key: round(current.get(key, 0.0) - baseline.get(key, 0.0), 6) for key in sorted(current)}


def _micro_state_delta(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    current_actions = Counter(current.get("target_actions") or {})
    baseline_actions = Counter(baseline.get("target_actions") or {})
    current_exec = Counter(current.get("execution_actions") or {})
    baseline_exec = Counter(baseline.get("execution_actions") or {})
    return {
        "bars_delta": int(_safe_float(current.get("bars")) - _safe_float(baseline.get("bars"))),
        "pnl_delta": round(
            _safe_float(current.get("estimated_equity_pnl")) - _safe_float(baseline.get("estimated_equity_pnl")),
            6,
        ),
        "fee_delta": round(_safe_float(current.get("fees")) - _safe_float(baseline.get("fees")), 6),
        "turnover_delta": round(
            _safe_float(current.get("turnover_notional")) - _safe_float(baseline.get("turnover_notional")),
            6,
        ),
        "target_action_delta": dict((current_actions - baseline_actions) + Counter()),
        "execution_action_delta": dict((current_exec - baseline_exec) + Counter()),
        "current": current,
        "paulwei_baseline": baseline,
    }


def _reason_delta(current_ledger: dict[str, Any], baseline_ledger: dict[str, Any]) -> dict[str, int]:
    current = _flatten_reason_counts(current_ledger)
    baseline = _flatten_reason_counts(baseline_ledger)
    delta = {key: current.get(key, 0) - baseline.get(key, 0) for key in sorted(set(current) | set(baseline))}
    return dict(sorted(delta.items(), key=lambda item: abs(item[1]), reverse=True)[:25])


def _flatten_reason_counts(ledger: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for state_counts in (ledger.get("top_reason_codes_by_micro_state") or {}).values():
        counts.update({str(key): int(value) for key, value in state_counts.items()})
    return counts


def _largest_state_by(states: dict[str, dict[str, Any]], key: str) -> dict[str, Any] | None:
    if not states:
        return None
    name, payload = max(states.items(), key=lambda item: _safe_float(item[1].get(key)))
    return {"micro_state": name, key: payload.get(key)}


def _smallest_state_by(states: dict[str, dict[str, Any]], key: str) -> dict[str, Any] | None:
    if not states:
        return None
    name, payload = min(states.items(), key=lambda item: _safe_float(item[1].get(key)))
    return {"name": name, key: payload.get(key)}


def _aggregate(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    metric_deltas: Counter[str] = Counter()
    state_deltas: dict[str, Counter[str]] = {}
    playbook_deltas: dict[str, Counter[str]] = {}
    attribution_missing = 0
    for comparison in comparisons:
        if not comparison["attribution_available"]:
            attribution_missing += 1
        metric_deltas.update(comparison["metrics"]["delta_current_minus_baseline"])
        for state, delta in comparison["micro_state_deltas"].items():
            bucket = state_deltas.setdefault(state, Counter())
            for key in ("pnl_delta", "fee_delta", "turnover_delta", "bars_delta"):
                bucket[key] += _safe_float(delta.get(key))
        for playbook, delta in comparison["playbook_deltas"].items():
            bucket = playbook_deltas.setdefault(playbook, Counter())
            for key in ("pnl_delta", "fee_delta", "turnover_delta", "bars_delta"):
                bucket[key] += _safe_float(delta.get(key))
    return {
        "window_count": len(comparisons),
        "attribution_missing_windows": attribution_missing,
        "metric_delta_current_minus_baseline": {key: round(value, 6) for key, value in sorted(metric_deltas.items())},
        "micro_state_delta_current_minus_baseline": {
            state: {key: round(value, 6) for key, value in sorted(counter.items())}
            for state, counter in sorted(state_deltas.items())
        },
        "playbook_delta_current_minus_baseline": {
            playbook: {key: round(value, 6) for key, value in sorted(counter.items())}
            for playbook, counter in sorted(playbook_deltas.items())
        },
    }


def _blockers(aggregate: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if aggregate["attribution_missing_windows"] > 0:
        blockers.append("attribution_ledger_missing")
    metric_delta = aggregate["metric_delta_current_minus_baseline"]
    if metric_delta.get("trade_count", 0.0) > 0:
        blockers.append("current_trade_count_above_baseline")
    if metric_delta.get("fee_drag_pct", 0.0) > 0:
        blockers.append("current_fee_drag_above_baseline")
    if metric_delta.get("net_pnl", 0.0) < 0:
        blockers.append("current_net_pnl_below_baseline")
    if metric_delta.get("profit_factor", 0.0) < 0:
        blockers.append("current_profit_factor_below_baseline")
    if metric_delta.get("max_drawdown", 0.0) > 0:
        blockers.append("current_drawdown_above_baseline")
    return blockers


def _next_action(blockers: list[str]) -> str:
    if "attribution_ledger_missing" in blockers:
        return "rerun_with_attribution_ledger"
    if "current_trade_count_above_baseline" in blockers or "current_fee_drag_above_baseline" in blockers:
        return "design_churn_reduction_gate_before_training"
    if "current_net_pnl_below_baseline" in blockers or "current_profit_factor_below_baseline" in blockers:
        return "repair_entry_exit_quality_before_expanding_search"
    return "use_gap_map_to_prioritize_next_factor_family"


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Baseline Gap Attribution",
        "",
        f"- created_at: `{payload['created_at']}`",
        f"- research_only_non_promotable: `{payload['research_only_non_promotable']}`",
        f"- profile_path: `{payload['profile_path']}`",
        f"- test_evaluated: `{payload['split_contract']['test_evaluated']}`",
        f"- status: `{payload['verdict']['status']}`",
        f"- blocked_by: `{payload['verdict']['blocked_by']}`",
        f"- next_action: `{payload['verdict']['next_action']}`",
        "",
        "## Aggregate",
        "",
        f"- metric_delta_current_minus_baseline: `{payload['aggregate']['metric_delta_current_minus_baseline']}`",
        f"- attribution_missing_windows: `{payload['aggregate']['attribution_missing_windows']}`",
        "",
        "## Windows",
        "",
    ]
    for comparison in payload["window_comparisons"]:
        lines.extend(
            [
                f"### {comparison['window_label']}",
                "",
                f"- metrics_delta: `{comparison['metrics']['delta_current_minus_baseline']}`",
                f"- largest_turnover_regression_state: `{comparison['largest_turnover_regression_state']}`",
                f"- largest_fee_regression_state: `{comparison['largest_fee_regression_state']}`",
                f"- largest_pnl_improvement_state: `{comparison['largest_pnl_improvement_state']}`",
                f"- largest_turnover_regression_playbook: `{comparison['largest_turnover_regression_playbook']}`",
                f"- largest_pnl_drag_playbook: `{comparison['largest_pnl_drag_playbook']}`",
                "",
            ]
        )
    return "\n".join(lines)


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    main()
