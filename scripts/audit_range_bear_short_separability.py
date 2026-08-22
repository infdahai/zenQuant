#!/usr/bin/env python3
"""Train-only separability audit for range-bear short candidates."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.research.auto_research import _build_strategy_config
from zenquant.research.comparison import split_window_spec
from zenquant.research.framework_params import (
    build_execution_config,
    build_indicator_config,
    build_plugin_config_overrides,
    build_replay_toggles,
    build_risk_config,
    build_signal_weight_overrides,
)
from zenquant.research.splits import default_btc_split_spec

NUMERIC_FEATURES = (
    "momentum_roc_z",
    "multi_momentum_z",
    "cycle_position",
    "direction_signal",
    "directional_confidence",
    "transition_risk",
    "realized_vol",
    "funding_oi_z",
    "ob_liq_z",
    "zen_pulse",
    "atr",
    "playbook_confidence",
    "final_leverage",
)

CATEGORICAL_FEATURES = (
    "playbook_id",
    "directional_state",
    "volatility_state",
    "positioning_state",
    "quality_state",
    "structure_state",
)


def _load_top_train_params(path: Path, *, index: int) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    diagnostics = list((payload.get("train_candidate_diagnostics") or {}).get("top_train_trial_diagnostics") or [])
    if not diagnostics:
        diagnostics = list(
            ((payload.get("search_status") or {}).get("train_candidate_diagnostics") or {}).get(
                "top_train_trial_diagnostics"
            )
            or []
        )
    if index >= len(diagnostics):
        raise ValueError(f"top train index {index} not found in {path}")
    return dict(diagnostics[index]["params"])


def _base_config(args: argparse.Namespace, *, start_ns: int, end_ns: int) -> HistoricalBacktestConfig:
    return HistoricalBacktestConfig(
        root_dir=args.root,
        symbol=args.symbol,
        starting_equity=100_000.0,
        execution_timeframe=Timeframe(args.execution_timeframe),
        bias_timeframe=Timeframe.TF_1D,
        direction_timeframe=Timeframe.TF_4H,
        context_timeframe=Timeframe(args.context_timeframe),
        start_ns=start_ns,
        end_ns=end_ns,
        liquidation_dir=args.liquidation_dir,
        liquidation_data_enabled=False,
        state_liquidation_overlay_enabled=False,
        research_compute_mode="lightweight_train_search",
        diagnostic_collect_ledgers=True,
        profiling_enabled=True,
        early_stop_enabled=False,
        trial_runtime_budget_s=None,
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


def _safe_float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key, default)
    try:
        if value is None:
            return default
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def _pipe_codes(value: Any) -> set[str]:
    return {item for item in str(value or "").split("|") if item}


def _is_range_bear_candidate(row: dict[str, Any]) -> bool:
    regime = str(row.get("regime") or "")
    directional_state = str(row.get("directional_state") or "")
    cycle_position = _safe_float(row, "cycle_position", 1.0)
    momentum_roc_z = _safe_float(row, "momentum_roc_z")
    multi_momentum_z = _safe_float(row, "multi_momentum_z")
    return (
        regime == "RANGE"
        and (directional_state == "DOWNTREND" or cycle_position <= 0.95)
        and (momentum_roc_z < -0.20 or multi_momentum_z < -0.20)
    )


def _has_confirmed_short_promotion(row: dict[str, Any]) -> bool:
    reasons = _pipe_codes(row.get("intent_promotion_reasons")) or _pipe_codes(row.get("intent_promotion_latest_reason"))
    pending_direction = _safe_float(row, "intent_promotion_pending_direction") or _safe_float(
        row, "intent_promotion_latest_pending_direction"
    )
    return pending_direction < 0.0 and ("1H_CONTEXT_CONFIRMED" in reasons or "5M_TIMING_CONFIRMED" in reasons)


def _rank_auc(values: list[float], labels: list[int]) -> float | None:
    pairs = sorted(zip(values, labels, strict=True), key=lambda item: item[0])
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    rank_sum = 0.0
    idx = 0
    while idx < len(pairs):
        end = idx + 1
        while end < len(pairs) and pairs[end][0] == pairs[idx][0]:
            end += 1
        avg_rank = (idx + 1 + end) / 2.0
        rank_sum += avg_rank * sum(label for _, label in pairs[idx:end])
        idx = end
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "mean": round(mean(values), 8),
        "median": round(median(values), 8),
        "p10": round(ordered[max(0, int(len(ordered) * 0.10) - 1)], 8),
        "p90": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.90))], 8),
        "hit_rate_gt_0": round(sum(value > 0 for value in values) / len(values), 6),
    }


def _bucket_numeric(rows: list[dict[str, Any]], feature: str, edge_key: str) -> list[dict[str, Any]]:
    values = sorted(_safe_float(row, feature) for row in rows)
    if not values:
        return []
    cuts = [values[int((len(values) - 1) * pct)] for pct in (0.25, 0.50, 0.75)]
    buckets: list[list[float]] = [[], [], [], []]
    for row in rows:
        value = _safe_float(row, feature)
        if value <= cuts[0]:
            bucket = 0
        elif value <= cuts[1]:
            bucket = 1
        elif value <= cuts[2]:
            bucket = 2
        else:
            bucket = 3
        buckets[bucket].append(_safe_float(row, edge_key))
    labels = [
        f"<= {cuts[0]:.4g}",
        f"{cuts[0]:.4g}..{cuts[1]:.4g}",
        f"{cuts[1]:.4g}..{cuts[2]:.4g}",
        f"> {cuts[2]:.4g}",
    ]
    return [{"bucket": label, **_summary(bucket_values)} for label, bucket_values in zip(labels, buckets, strict=True)]


def _bucket_categorical(rows: list[dict[str, Any]], feature: str, edge_key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(feature) or "UNKNOWN"), []).append(_safe_float(row, edge_key))
    return [
        {"bucket": key, **_summary(values)}
        for key, values in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    ]


def _top_feature_scores(rows: list[dict[str, Any]], edge_key: str) -> list[dict[str, Any]]:
    labels = [1 if _safe_float(row, edge_key) > 0.0 else 0 for row in rows]
    scored = []
    for feature in NUMERIC_FEATURES:
        values = [_safe_float(row, feature) for row in rows]
        auc = _rank_auc(values, labels)
        if auc is None:
            continue
        scored.append(
            {
                "feature": feature,
                "auc": round(auc, 6),
                "directional_auc": round(max(auc, 1.0 - auc), 6),
                "better_when": "high" if auc >= 0.5 else "low",
            }
        )
    return sorted(scored, key=lambda item: item["directional_auc"], reverse=True)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Range Bear Short Separability Audit",
        "",
        f"- source_report: `{payload['source_report']}`",
        f"- split: `{payload['split']}`",
        f"- test_evaluated: `{payload['test_evaluated']}`",
        f"- total_rows: `{payload['row_count']}`",
        f"- candidate_rows: `{payload['candidate_count']}`",
        f"- confirmed_short_rows: `{payload['confirmed_short_count']}`",
        "",
        "## Edge Summary",
        "",
    ]
    for key, summary in payload["edge_summary"].items():
        lines.append(f"- {key}: `{summary}`")
    lines.extend(["", "## Top Single Feature Separability", ""])
    for item in payload["top_feature_scores"][:10]:
        lines.append(
            f"- {item['feature']}: directional_auc=`{item['directional_auc']}`, better_when=`{item['better_when']}`"
        )
    lines.extend(["", "## Best Buckets", ""])
    for item in payload["best_buckets"][:12]:
        lines.append(
            f"- {item['feature']} {item['bucket']}: count=`{item['count']}`, "
            f"mean=`{item.get('mean')}`, hit_rate=`{item.get('hit_rate_gt_0')}`"
        )
    lines.extend(["", "## Diagnosis", "", payload["diagnosis"]])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--candidate-index", type=int, default=0)
    parser.add_argument("--root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--liquidation-dir", type=Path, default=Path("data/raw/hyperliquid_liquidations"))
    parser.add_argument("--execution-timeframe", choices=("5m", "1h", "4h", "1d"), default="5m")
    parser.add_argument("--context-timeframe", choices=("5m", "1h", "4h", "1d"), default="1h")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    split = default_btc_split_spec()
    train_window = split_window_spec(split, "train")
    params = _load_top_train_params(args.source_report, index=args.candidate_index)
    base = _base_config(args, start_ns=int(train_window.start_ns or 0), end_ns=int(train_window.end_ns or 0))
    runner = HistoricalBacktest(_candidate_config(base, params))
    result = runner.run()
    rows = runner.build_analysis_rows(horizons=(1, 3, 6, 12, 24, 72))
    candidates = [row for row in rows if _is_range_bear_candidate(row)]

    for row in candidates:
        row["short_edge_1"] = -_safe_float(row, "future_return_1")
        row["short_edge_3"] = -_safe_float(row, "future_return_3")
        row["short_edge_6"] = -_safe_float(row, "future_return_6")
        row["short_edge_12"] = -_safe_float(row, "future_return_12")
        row["short_edge_24"] = -_safe_float(row, "future_return_24")
        row["short_edge_72"] = -_safe_float(row, "future_return_72")
        row["confirmed_short_promotion"] = _has_confirmed_short_promotion(row)

    edge_key = "short_edge_12"
    feature_scores = _top_feature_scores(candidates, edge_key)
    bucket_payload: list[dict[str, Any]] = []
    for feature in NUMERIC_FEATURES:
        for bucket in _bucket_numeric(candidates, feature, edge_key):
            bucket_payload.append({"feature": feature, **bucket})
    for feature in CATEGORICAL_FEATURES:
        for bucket in _bucket_categorical(candidates, feature, edge_key):
            bucket_payload.append({"feature": feature, **bucket})
    best_buckets = sorted(
        [item for item in bucket_payload if item.get("count", 0) >= 100],
        key=lambda item: (float(item.get("mean", 0.0)), float(item.get("hit_rate_gt_0", 0.0))),
        reverse=True,
    )
    confirmed = [row for row in candidates if row.get("confirmed_short_promotion")]
    unconfirmed = [row for row in candidates if not row.get("confirmed_short_promotion")]

    edge_summary = {
        horizon: _summary([_safe_float(row, f"short_edge_{horizon}") for row in candidates])
        for horizon in (1, 3, 6, 12, 24, 72)
    }
    selected_auc = feature_scores[0]["directional_auc"] if feature_scores else 0.0
    diagnosis = (
        "Weak separability: current features do not isolate profitable range-bear shorts strongly enough."
        if selected_auc < 0.58
        else "Some separability exists: use top buckets as a deterministic quality gate before further training."
    )
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_report": str(args.source_report),
        "candidate_index": args.candidate_index,
        "split": "train",
        "test_evaluated": False,
        "metrics": asdict(result.metrics),
        "terminated_early": result.terminated_early,
        "termination_reason": result.termination_reason,
        "row_count": len(rows),
        "candidate_count": len(candidates),
        "confirmed_short_count": len(confirmed),
        "confirmed_short_edge_summary": _summary([_safe_float(row, edge_key) for row in confirmed]),
        "unconfirmed_edge_summary": _summary([_safe_float(row, edge_key) for row in unconfirmed]),
        "edge_summary": edge_summary,
        "top_feature_scores": feature_scores,
        "best_buckets": best_buckets[:30],
        "bucket_summary": bucket_payload,
        "target_reason_counts": dict(
            Counter(code for row in candidates for code in _pipe_codes(row.get("target_reason_codes"))).most_common(20)
        ),
        "promotion_reason_counts": dict(
            Counter(
                code
                for row in candidates
                for code in (
                    _pipe_codes(row.get("intent_promotion_reasons"))
                    or _pipe_codes(row.get("intent_promotion_latest_reason"))
                )
            ).most_common(20)
        ),
        "diagnosis": diagnosis,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "range_bear_short_separability.json"
    md_path = args.output_dir / "range_bear_short_separability.md"
    rows_path = args.output_dir / "range_bear_candidate_rows.jsonl"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    _write_jsonl(rows_path, candidates)
    print(f"json_path={json_path}")
    print(f"md_path={md_path}")
    print(f"rows_path={rows_path}")


if __name__ == "__main__":
    main()
