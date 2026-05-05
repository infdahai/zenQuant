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
    parser.add_argument(
        "--paulwei-behavior-profile", type=Path, default=Path("results/paulwei_research/behavior_profile.json")
    )
    parser.add_argument(
        "--paulwei-episode-catalog", type=Path, default=Path("results/paulwei_research/trade_episode_catalog.csv")
    )
    parser.add_argument("--paulwei-symbol", default="XBTUSD")
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
    attribution = build_baseline_gap_attribution(
        comparison_payload,
        profile_path=args.profile_path,
        paulwei_behavior_profile=_read_json_if_exists(args.paulwei_behavior_profile),
        paulwei_episode_stats=build_paulwei_episode_stats(
            args.paulwei_episode_catalog,
            symbol=args.paulwei_symbol,
        ),
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "comparison.json").write_text(json.dumps(comparison_payload, indent=2), encoding="utf-8")
    (args.output_dir / "comparison.md").write_text(build_markdown_summary(comparison_payload), encoding="utf-8")
    (args.output_dir / "baseline_gap_attribution.json").write_text(
        json.dumps(attribution, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "baseline_gap_attribution.md").write_text(_markdown(attribution), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "verdict": attribution["verdict"]}, indent=2))


def build_baseline_gap_attribution(
    payload: dict[str, Any],
    profile_path: Path | None = None,
    paulwei_behavior_profile: dict[str, Any] | None = None,
    paulwei_episode_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    strength_profile = _build_strength_profile(
        aggregate,
        comparisons,
        paulwei_behavior_profile=paulwei_behavior_profile,
        paulwei_episode_stats=paulwei_episode_stats,
    )
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
        "strength_profile": strength_profile,
        "verdict": {
            "status": "needs_research_fix" if blockers else "baseline_gap_mapped",
            "blocked_by": blockers[0] if blockers else None,
            "reason_codes": blockers,
            "next_action": _next_action(blockers),
        },
    }


def build_paulwei_episode_stats(path: Path, *, symbol: str = "XBTUSD") -> dict[str, Any]:
    """Summarize public PaulWei episode cadence for research-only behavior priors."""
    if not path.exists():
        return {"available": False, "reason": "episode_catalog_missing", "path": str(path)}

    import pandas as pd

    frame = pd.read_csv(path)
    required = {
        "symbol",
        "side",
        "closed",
        "estimated_return_bps",
        "duration_hours",
        "add_events",
        "reduce_events",
        "entry_notional",
        "flip_exit",
        "flip_entry",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        return {"available": False, "reason": "episode_catalog_missing_columns", "missing_columns": missing}

    closed = frame[(frame["symbol"] == symbol) & (frame["closed"].astype(bool))].copy()
    if closed.empty:
        return {"available": False, "reason": "episode_catalog_empty_for_symbol", "symbol": symbol}

    return {
        "available": True,
        "symbol": symbol,
        "closed_episodes": len(closed),
        "win_rate": _round_float((closed["estimated_return_bps"] > 0).mean()),
        "median_return_bps": _round_float(closed["estimated_return_bps"].median()),
        "p10_return_bps": _round_float(closed["estimated_return_bps"].quantile(0.10)),
        "p90_return_bps": _round_float(closed["estimated_return_bps"].quantile(0.90)),
        "median_duration_hours": _round_float(closed["duration_hours"].median()),
        "p25_duration_hours": _round_float(closed["duration_hours"].quantile(0.25)),
        "p75_duration_hours": _round_float(closed["duration_hours"].quantile(0.75)),
        "median_adds_per_episode": _round_float(closed["add_events"].median()),
        "median_reduces_per_episode": _round_float(closed["reduce_events"].median()),
        "median_entry_notional": _round_float(closed["entry_notional"].median()),
        "flip_exit_rate": _round_float(closed["flip_exit"].mean()),
        "flip_entry_rate": _round_float(closed["flip_entry"].mean()),
        "side_profile": {
            str(side): {
                "episodes": len(group),
                "win_rate": _round_float((group["estimated_return_bps"] > 0).mean()),
                "median_return_bps": _round_float(group["estimated_return_bps"].median()),
                "p10_return_bps": _round_float(group["estimated_return_bps"].quantile(0.10)),
                "p90_return_bps": _round_float(group["estimated_return_bps"].quantile(0.90)),
            }
            for side, group in sorted(closed.groupby("side"), key=lambda item: str(item[0]))
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


def _build_strength_profile(
    aggregate: dict[str, Any],
    comparisons: list[dict[str, Any]],
    *,
    paulwei_behavior_profile: dict[str, Any] | None,
    paulwei_episode_stats: dict[str, Any] | None,
) -> dict[str, Any]:
    metric_delta = aggregate["metric_delta_current_minus_baseline"]
    playbook_delta = aggregate["playbook_delta_current_minus_baseline"]
    micro_state_delta = aggregate["micro_state_delta_current_minus_baseline"]
    current_strengths: list[dict[str, Any]] = []
    paulwei_strengths: list[dict[str, Any]] = []
    research_implications: list[str] = []

    if metric_delta.get("net_pnl", 0.0) > 0:
        current_strengths.append(
            {
                "code": "higher_net_pnl",
                "metric_delta": metric_delta["net_pnl"],
                "interpretation": "current monetizes more gross opportunity on the compared dev windows",
            }
        )
    if metric_delta.get("sharpe", 0.0) > 0:
        current_strengths.append(
            {
                "code": "higher_sharpe",
                "metric_delta": metric_delta["sharpe"],
                "interpretation": "current has stronger risk-adjusted return in the compared windows",
            }
        )
    if metric_delta.get("max_drawdown", 0.0) < 0:
        current_strengths.append(
            {
                "code": "lower_drawdown",
                "metric_delta": metric_delta["max_drawdown"],
                "interpretation": "current carries less peak-to-trough loss in the compared windows",
            }
        )
    range_light_pnl = (playbook_delta.get("RANGE_LIGHT") or {}).get("pnl_delta", 0.0)
    if _safe_float(range_light_pnl) > 0:
        current_strengths.append(
            {
                "code": "range_light_pnl_engine",
                "pnl_delta": range_light_pnl,
                "interpretation": "current's RANGE_LIGHT behavior is a material positive PnL source",
            }
        )
    chop_pnl = (micro_state_delta.get("chop_neutral") or {}).get("pnl_delta", 0.0)
    if _safe_float(chop_pnl) > 0:
        current_strengths.append(
            {
                "code": "chop_neutral_edge",
                "pnl_delta": chop_pnl,
                "interpretation": "current extracts more from chop/neutral states, so broad chop gating is risky",
            }
        )

    if metric_delta.get("trade_count", 0.0) > 0:
        paulwei_strengths.append(
            {
                "code": "lower_trade_count",
                "metric_delta_current_minus_baseline": metric_delta["trade_count"],
                "interpretation": "PaulWei baseline reaches similar behavior with fewer completed trades",
            }
        )
    if metric_delta.get("turnover_per_day", 0.0) > 0:
        paulwei_strengths.append(
            {
                "code": "lower_turnover",
                "metric_delta_current_minus_baseline": metric_delta["turnover_per_day"],
                "interpretation": "PaulWei baseline is more turnover efficient",
            }
        )
    if metric_delta.get("profit_factor", 0.0) < 0:
        paulwei_strengths.append(
            {
                "code": "higher_profit_factor",
                "metric_delta_current_minus_baseline": metric_delta["profit_factor"],
                "interpretation": "PaulWei baseline keeps better gross win/loss quality",
            }
        )

    behavior = paulwei_behavior_profile or {}
    if behavior:
        paulwei_strengths.extend(_public_behavior_strengths(behavior))
    if paulwei_episode_stats and paulwei_episode_stats.get("available"):
        paulwei_strengths.extend(_episode_strengths(paulwei_episode_stats))

    if any(item["code"] in {"range_light_pnl_engine", "chop_neutral_edge"} for item in current_strengths):
        research_implications.append("do_not_broadly_gate_range_light_or_chop_neutral_intent")
    if any(
        item["code"] in {"lower_trade_count", "lower_turnover", "higher_profit_factor"} for item in paulwei_strengths
    ):
        research_implications.append("convert_paulwei_cadence_into_fee_efficiency_targets")
    if paulwei_episode_stats and paulwei_episode_stats.get("available"):
        research_implications.append("use_public_episode_stats_as_behavior_priors_not_primary_truth")
    research_implications.append("keep_test_split_closed_until_train_candidate_exists")

    return {
        "current_strengths": current_strengths,
        "paulwei_baseline_strengths": paulwei_strengths,
        "paulwei_public_episode_stats": paulwei_episode_stats or {"available": False, "reason": "not_loaded"},
        "window_labels": [comparison["window_label"] for comparison in comparisons],
        "research_implications": research_implications,
    }


def _public_behavior_strengths(profile: dict[str, Any]) -> list[dict[str, Any]]:
    strengths: list[dict[str, Any]] = []
    if _safe_float(profile.get("btc_notional_share")) >= 0.75:
        strengths.append(
            {
                "code": "btc_concentrated_behavior",
                "value": profile.get("btc_notional_share"),
                "interpretation": "public archive behavior is mostly BTC, making it relevant as a BTC behavior prior",
            }
        )
    if _safe_float(profile.get("position_switches_per_30d")) <= 3.0:
        strengths.append(
            {
                "code": "low_position_switch_rate",
                "value": profile.get("position_switches_per_30d"),
                "interpretation": "PaulWei changes net direction slowly enough to be useful as an anti-churn prior",
            }
        )
    protective = profile.get("protective_exit_profile") or {}
    if _safe_float(protective.get("protective_reduce_share")) >= 0.75:
        strengths.append(
            {
                "code": "protective_reduce_bias",
                "value": protective.get("protective_reduce_share"),
                "interpretation": "public behavior favors protective reduction instead of constant flipping",
            }
        )
    recovery = profile.get("recovery_profile") or {}
    if recovery.get("kill_recovery_style") == "gradual_ramp_after_cooldown":
        strengths.append(
            {
                "code": "gradual_recovery_after_drawdown",
                "value": recovery.get("median_recovery_days"),
                "interpretation": "drawdown recovery appears staged rather than immediate re-risking",
            }
        )
    return strengths


def _episode_strengths(stats: dict[str, Any]) -> list[dict[str, Any]]:
    strengths: list[dict[str, Any]] = []
    if _safe_float(stats.get("win_rate")) >= 0.55:
        strengths.append(
            {
                "code": "positive_episode_hit_rate",
                "value": stats.get("win_rate"),
                "closed_episodes": stats.get("closed_episodes"),
                "interpretation": "public BTC episodes have a positive hit-rate profile",
            }
        )
    if _safe_float(stats.get("median_duration_hours")) >= 6.0:
        strengths.append(
            {
                "code": "multi_hour_holding_cadence",
                "value": stats.get("median_duration_hours"),
                "interpretation": "public BTC episodes are not dominated by very fast flips",
            }
        )
    if (
        _safe_float(stats.get("median_adds_per_episode")) >= 5.0
        and _safe_float(stats.get("median_reduces_per_episode")) >= 5.0
    ):
        strengths.append(
            {
                "code": "staged_build_and_unwind",
                "median_adds": stats.get("median_adds_per_episode"),
                "median_reduces": stats.get("median_reduces_per_episode"),
                "interpretation": "public BTC behavior uses staged inventory management",
            }
        )
    side_profile = stats.get("side_profile") or {}
    long_profile = side_profile.get("long") or {}
    short_profile = side_profile.get("short") or {}
    if _safe_float(long_profile.get("win_rate")) > _safe_float(short_profile.get("win_rate")):
        strengths.append(
            {
                "code": "stronger_long_side_profile",
                "long_win_rate": long_profile.get("win_rate"),
                "short_win_rate": short_profile.get("win_rate"),
                "interpretation": "public BTC archive shows stronger long-side quality than short-side quality",
            }
        )
    return strengths


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
        "## Strength Profile",
        "",
        f"- current_strengths: `{payload['strength_profile']['current_strengths']}`",
        f"- paulwei_baseline_strengths: `{payload['strength_profile']['paulwei_baseline_strengths']}`",
        f"- paulwei_public_episode_stats: `{payload['strength_profile']['paulwei_public_episode_stats']}`",
        f"- research_implications: `{payload['strength_profile']['research_implications']}`",
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


def _round_float(value: Any, digits: int = 4) -> float:
    return round(_safe_float(value), digits)


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
