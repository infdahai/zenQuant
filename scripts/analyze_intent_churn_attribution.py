#!/usr/bin/env python3
"""Train-only churn attribution diagnostic for rejected framework candidates.

This is a diagnostic runner, not a trainer. By default it mirrors Stage 1
lightweight train-search semantics for the rejected candidate so trade_count
and fee behavior are comparable to framework campaign artifacts. Pass
--collect-ledger to keep the same lightweight execution and early-stop behavior
while collecting target/action/risk attribution ledgers for fee-source analysis.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.domain import ExecutionReport, Timeframe
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.research.attribution import build_decision_attribution_ledger
from zenquant.research.auto_research import _build_strategy_config
from zenquant.research.comparison import (
    WindowSpec,
    default_comparison_windows,
    paulwei_execution_baseline,
    paulwei_risk_baseline,
    split_window_spec,
)
from zenquant.research.framework_params import (
    build_execution_config,
    build_indicator_config,
    build_plugin_config_overrides,
    build_replay_toggles,
    build_risk_config,
    build_signal_weight_overrides,
)
from zenquant.research.splits import default_btc_split_spec

DEFAULT_V14B_REPORT = Path(
    "results/regime_objective_candidate_search_v14b_execution_inventory_rethink_train_only_timeout_adjusted"
    "/framework_optimization.json"
)


def _load_candidate_params(path: Path, *, source: str, index: int) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    diagnostics = _candidate_diagnostics(payload, source=source)
    if index >= len(diagnostics):
        raise ValueError(f"{source} candidate index {index} not found in {path}")
    return dict(diagnostics[index]["params"])


def _candidate_diagnostics(payload: dict[str, Any], *, source: str) -> list[dict[str, Any]]:
    if source == "rejected":
        return _rejected_candidate_diagnostics(payload)
    if source == "top-train":
        search_status_diagnostics = (
            (payload.get("search_status") or {})
            .get("train_candidate_diagnostics", {})
            .get("top_train_trial_diagnostics", [])
        )
        if search_status_diagnostics:
            return list(search_status_diagnostics)
        return list((payload.get("train_candidate_diagnostics") or {}).get("top_train_trial_diagnostics", []))
    raise ValueError(f"unsupported candidate source {source!r}")


def _rejected_candidate_diagnostics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    search_status_diagnostics = (
        (payload.get("search_status") or {})
        .get("train_candidate_diagnostics", {})
        .get("primary_search_diagnostics", {})
        .get("rejected_candidate_diagnostics", [])
    )
    if search_status_diagnostics:
        return list(search_status_diagnostics)
    return list(
        (payload.get("train_candidate_diagnostics") or {})
        .get("primary_search_diagnostics", {})
        .get("rejected_candidate_diagnostics", [])
    )


def _base_config(
    *,
    root: Path,
    symbol: str,
    liquidation_dir: Path,
    start_ns: int,
    end_ns: int,
    research_compute_mode: str,
    lightweight_stage1_semantics: bool,
    execution_timeframe: Timeframe,
    context_timeframe: Timeframe | None,
    diagnostic_collect_ledgers: bool,
    early_stop_fee_drag_buffer: float,
) -> HistoricalBacktestConfig:
    return HistoricalBacktestConfig(
        root_dir=root,
        symbol=symbol,
        starting_equity=100_000.0,
        execution_timeframe=execution_timeframe,
        bias_timeframe=Timeframe.TF_1D,
        direction_timeframe=Timeframe.TF_4H,
        context_timeframe=context_timeframe,
        start_ns=start_ns,
        end_ns=end_ns,
        liquidation_dir=liquidation_dir,
        liquidation_data_enabled=False,
        state_liquidation_overlay_enabled=False,
        research_compute_mode=research_compute_mode,
        diagnostic_collect_ledgers=diagnostic_collect_ledgers,
        profiling_enabled=True,
        early_stop_enabled=lightweight_stage1_semantics,
        early_stop_min_trade_count=20,
        early_stop_fee_drag_buffer=early_stop_fee_drag_buffer,
        trial_runtime_budget_s=60.0 if lightweight_stage1_semantics else None,
    )


def _candidate_config(base: HistoricalBacktestConfig, params: dict[str, Any]) -> HistoricalBacktestConfig:
    liquidation_data_enabled, state_liquidation_overlay_enabled = build_replay_toggles(params)
    return HistoricalBacktestConfig(
        **{
            **asdict(base),
            "strategy_config": _build_strategy_config(params, execution_timeframe=base.execution_timeframe),
            "risk_config": build_risk_config(params),
            "execution_config": build_execution_config(params),
            "indicator_config": build_indicator_config(params),
            "signal_weight_overrides": build_signal_weight_overrides(params),
            "plugin_config_overrides": build_plugin_config_overrides(params),
            "liquidation_data_enabled": liquidation_data_enabled,
            "state_liquidation_overlay_enabled": state_liquidation_overlay_enabled,
        }
    )


def _paulwei_config(base: HistoricalBacktestConfig) -> HistoricalBacktestConfig:
    return HistoricalBacktestConfig(
        **{
            **asdict(base),
            "risk_config": paulwei_risk_baseline(),
            "execution_config": paulwei_execution_baseline(),
            "liquidation_data_enabled": False,
            "state_liquidation_overlay_enabled": False,
        }
    )


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


def _execution_action_counts(reports: list[ExecutionReport]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    position_qty = 0.0
    for report in sorted(reports, key=lambda item: item.transaction_time_ns):
        if report.status != "FILLED" or report.filled_qty <= 0:
            continue
        next_position = position_qty + report.side * report.filled_qty
        counts[_execution_action(position_qty, next_position)] += 1
        position_qty = next_position
    return dict(counts.most_common())


def _run_variant(name: str, config: HistoricalBacktestConfig, *, window_label: str) -> dict[str, Any]:
    runner = HistoricalBacktest(config)
    result = runner.run()
    target_reason_counts: Counter[str] = Counter()
    for target in getattr(runner, "_targets", []):
        target_reason_counts.update(target.reason_codes)
    exit_reason_counts: Counter[str] = Counter()
    for exit_decision in getattr(runner, "_exits", []):
        if exit_decision.should_exit():
            exit_reason_counts.update(exit_decision.reason_codes or [exit_decision.reason_code])
    decision_attribution_ledger = {}
    if getattr(runner, "_targets", []):
        decision_attribution_ledger = build_decision_attribution_ledger(
            runner,
            result,
            variant=name,
            window_label=window_label,
        )
    return {
        "variant": name,
        "metrics": asdict(result.metrics),
        "terminated_early": result.terminated_early,
        "termination_reason": result.termination_reason,
        "processed_event_ratio": result.processed_event_ratio,
        "execution_action_counts": _execution_action_counts(getattr(runner, "_reports", [])),
        "target_reason_counts_available": bool(target_reason_counts),
        "target_reason_counts": dict(target_reason_counts.most_common(30)),
        "exit_reason_counts_available": bool(exit_reason_counts),
        "exit_reason_counts": dict(exit_reason_counts.most_common(30)),
        "decision_attribution_available": bool(decision_attribution_ledger),
        "action_attribution_summary": decision_attribution_ledger.get("action_attribution_summary", {}),
        "action_source_summary": decision_attribution_ledger.get("action_source_summary", {}),
        "decision_attribution_ledger": decision_attribution_ledger,
        "diagnostics": asdict(result.diagnostics),
    }


def _parse_param_override(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise ValueError(f"param override must use key=value: {raw}")
    key, value = raw.split("=", 1)
    if not key:
        raise ValueError(f"param override key is empty: {raw}")
    lowered = value.lower()
    if lowered == "true":
        return key, 1.0
    if lowered == "false":
        return key, 0.0
    try:
        return key, int(value)
    except ValueError:
        pass
    try:
        return key, float(value)
    except ValueError:
        return key, value


def _window_payload(
    *,
    label: str,
    root: Path,
    symbol: str,
    liquidation_dir: Path,
    start_ns: int,
    end_ns: int,
    params: dict[str, Any],
    research_compute_mode: str,
    lightweight_stage1_semantics: bool,
    execution_timeframe: Timeframe,
    context_timeframe: Timeframe | None,
    diagnostic_collect_ledgers: bool,
    early_stop_fee_drag_buffer: float,
    skip_baseline: bool,
) -> dict[str, Any]:
    base = _base_config(
        root=root,
        symbol=symbol,
        liquidation_dir=liquidation_dir,
        start_ns=start_ns,
        end_ns=end_ns,
        research_compute_mode=research_compute_mode,
        lightweight_stage1_semantics=lightweight_stage1_semantics,
        execution_timeframe=execution_timeframe,
        context_timeframe=context_timeframe,
        diagnostic_collect_ledgers=diagnostic_collect_ledgers,
        early_stop_fee_drag_buffer=early_stop_fee_drag_buffer,
    )
    variants = []
    if not skip_baseline:
        variants.append(_run_variant("paulwei_baseline", _paulwei_config(base), window_label=label))
    variants.append(_run_variant("rejected_candidate", _candidate_config(base, params), window_label=label))
    return {"window": label, "variants": variants}


def _selected_windows(*, split: Any, train_only: bool, window_labels: list[str]) -> list[WindowSpec]:
    if not window_labels:
        windows = [split_window_spec(split, "train")]
        if not train_only:
            windows.append(split_window_spec(split, "dev"))
        return windows

    available = {
        window.label: window
        for window in [split_window_spec(split, "train"), *default_comparison_windows(split, "dev")]
    }
    selected: list[WindowSpec] = []
    for label in window_labels:
        if label not in available:
            allowed = ", ".join(sorted(available))
            raise ValueError(f"unknown window label {label!r}; allowed labels: {allowed}")
        selected.append(available[label])
    return selected


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Intent Churn Attribution Diagnostic",
        "",
        f"- created_at: `{payload['created_at']}`",
        f"- source_report: `{payload['source_report']}`",
        f"- research_compute_mode: `{payload['research_compute_mode']}`",
        f"- lightweight_stage1_semantics: `{payload['lightweight_stage1_semantics']}`",
        f"- target_reason_attribution_note: `{payload['target_reason_attribution_note']}`",
        "",
    ]
    for window in payload["windows"]:
        lines.extend([f"## {window['window']}", ""])
        for variant in window["variants"]:
            metrics = variant["metrics"]
            lines.extend(
                [
                    f"### {variant['variant']}",
                    "",
                    f"- sharpe: `{metrics['sharpe']}`",
                    f"- net_pnl: `{metrics['net_pnl']}`",
                    f"- max_drawdown: `{metrics['max_drawdown']}`",
                    f"- trade_count: `{metrics['trade_count']}`",
                    f"- fee_drag_pct: `{metrics['fee_drag_pct']}`",
                    f"- turnover_per_day: `{metrics['turnover_per_day']}`",
                    f"- terminated_early: `{variant['terminated_early']}`",
                    f"- execution_action_counts: `{variant['execution_action_counts']}`",
                    f"- target_reason_counts_available: `{variant['target_reason_counts_available']}`",
                    f"- exit_reason_counts_available: `{variant['exit_reason_counts_available']}`",
                    f"- decision_attribution_available: `{variant['decision_attribution_available']}`",
                    "",
                ]
            )
            action_summary = variant.get("action_attribution_summary") or {}
            if action_summary:
                lines.extend(["#### action attribution", ""])
                for action, summary in action_summary.items():
                    lines.append(
                        f"- {action}: count=`{summary['count']}`, fees=`{summary['fees']}`, "
                        f"sources=`{summary['source_counts']}`"
                    )
                lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze train-only churn attribution for rejected params")
    parser.add_argument("--source-report", type=Path, default=DEFAULT_V14B_REPORT)
    parser.add_argument("--rejected-index", type=int, default=0)
    parser.add_argument(
        "--candidate-source",
        choices=("rejected", "top-train"),
        default="rejected",
        help="Candidate params source inside source-report.",
    )
    parser.add_argument(
        "--candidate-index",
        type=int,
        default=None,
        help="Candidate index for --candidate-source; defaults to --rejected-index for backward compatibility.",
    )
    parser.add_argument("--root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--liquidation-dir", type=Path, default=Path("data/raw/hyperliquid_liquidations"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/intent_churn_attribution_v15"))
    parser.add_argument("--execution-timeframe", choices=("5m", "1h", "4h", "1d"), default="1h")
    parser.add_argument("--context-timeframe", choices=("none", "5m", "1h", "4h", "1d"), default="1h")
    parser.add_argument(
        "--train-only",
        action="store_true",
        help="Only replay the train split so pre-candidate diagnostics do not touch dev.",
    )
    parser.add_argument(
        "--window-label",
        action="append",
        default=[],
        help=(
            "Replay only the named diagnostic window; may be passed multiple times. "
            "Supported labels include train, dev, 2023-08, and worst-period labels such as '2024-11 DD'."
        ),
    )
    parser.add_argument(
        "--full-window",
        action="store_true",
        help="Disable early-stop/runtime budget so diagnostics cover the full requested window.",
    )
    parser.add_argument(
        "--collect-ledger",
        action="store_true",
        help="Collect target/action/risk ledgers while preserving the selected replay and early-stop semantics.",
    )
    parser.add_argument(
        "--early-stop-fee-drag-buffer",
        type=float,
        default=2.0,
        help="Fee-drag early-stop buffer; use 1.25 to mirror strict train-fee Stage 1 runs.",
    )
    parser.add_argument(
        "--skip-baseline",
        action="store_true",
        help="Only replay the rejected candidate variant.",
    )
    parser.add_argument(
        "--param-override",
        action="append",
        default=[],
        help="Override one candidate param as key=value; may be passed multiple times.",
    )
    args = parser.parse_args()

    split = default_btc_split_spec()
    candidate_index = args.rejected_index if args.candidate_index is None else args.candidate_index
    params = _load_candidate_params(args.source_report, source=args.candidate_source, index=candidate_index)
    for raw_override in args.param_override:
        key, value = _parse_param_override(raw_override)
        params[key] = value
    research_compute_mode = "lightweight_train_search"
    lightweight_stage1_semantics = not args.full_window
    execution_timeframe = Timeframe(args.execution_timeframe)
    context_timeframe = None if args.context_timeframe == "none" else Timeframe(args.context_timeframe)
    windows = [
        _window_payload(
            label=window.label,
            root=args.root,
            symbol=args.symbol,
            liquidation_dir=args.liquidation_dir,
            start_ns=int(window.start_ns or 0),
            end_ns=int(window.end_ns or 0),
            params=params,
            research_compute_mode=research_compute_mode,
            lightweight_stage1_semantics=lightweight_stage1_semantics,
            execution_timeframe=execution_timeframe,
            context_timeframe=context_timeframe,
            diagnostic_collect_ledgers=args.collect_ledger,
            early_stop_fee_drag_buffer=args.early_stop_fee_drag_buffer,
            skip_baseline=args.skip_baseline,
        )
        for window in _selected_windows(split=split, train_only=args.train_only, window_labels=args.window_label)
    ]
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_report": str(args.source_report),
        "rejected_index": args.rejected_index,
        "candidate_source": args.candidate_source,
        "candidate_index": candidate_index,
        "symbol": args.symbol,
        "research_compute_mode": research_compute_mode,
        "execution_timeframe": execution_timeframe.value,
        "context_timeframe": context_timeframe.value if context_timeframe else None,
        "lightweight_stage1_semantics": lightweight_stage1_semantics,
        "diagnostic_collect_ledgers": args.collect_ledger,
        "early_stop_fee_drag_buffer": args.early_stop_fee_drag_buffer,
        "test_evaluated": False,
        "evaluation_role": "development",
        "target_reason_attribution_note": (
            "Stage 1 lightweight train search does not collect _targets/_indicators by default; "
            "--collect-ledger enables diagnostic-only collection without changing replay execution or early-stop semantics."
        ),
        "candidate_params": params,
        "windows": windows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "intent_churn_attribution.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (args.output_dir / "intent_churn_attribution.md").write_text(_markdown(payload), encoding="utf-8")
    print(f"Saved intent churn attribution diagnostic to {args.output_dir / 'intent_churn_attribution.json'}")


if __name__ == "__main__":
    main()
