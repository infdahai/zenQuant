#!/usr/bin/env python3
"""Run frozen-candidate acceptance on the test split only."""

from __future__ import annotations

import argparse
import json
from dataclasses import MISSING, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.framework_search import (
    CandidateSummary,
    FrameworkEvaluator,
    SearchReport,
    build_markdown_report,
    write_search_report,
)
from zenquant.research.splits import default_btc_split_spec, split_summary_json
from zenquant.strategy import ARCStrategyConfig


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"development_report_not_found:{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_handoff_candidate(dev_report: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    required_fields = (
        "evaluation_role",
        "candidate_ready_for_handoff",
        "candidate_frozen_id",
        "frozen_params",
        "test_evaluated",
    )
    missing = [field for field in required_fields if field not in dev_report]
    if missing:
        raise SystemExit(f"development_report_not_handoff_capable:missing={','.join(missing)}")
    if dev_report.get("evaluation_role") != "development":
        raise SystemExit("development_report_must_be_development_role")
    if bool(dev_report.get("test_evaluated")):
        raise SystemExit("development_report_already_consumed_by_test")
    if not dev_report.get("candidate_ready_for_handoff"):
        raise SystemExit("development_report_not_ready_for_handoff")
    frozen_id = dev_report.get("candidate_frozen_id")
    frozen_params = dev_report.get("frozen_params")
    if not frozen_id:
        raise SystemExit("development_report_missing_candidate_frozen_id")
    if not isinstance(frozen_params, dict) or not frozen_params:
        raise SystemExit("development_report_missing_frozen_params")
    return str(frozen_id), dict(frozen_params)


def _load_handoff_candidate_from_development_report(path: Path) -> tuple[str, dict[str, Any]]:
    return _extract_handoff_candidate(_read_json(path))


def _candidate_summary_from_payload(payload: Any, *, field_name: str) -> CandidateSummary:
    if not isinstance(payload, dict):
        raise SystemExit(f"development_report_missing_{field_name}")
    field_defs = fields(CandidateSummary)
    kwargs = {field.name: payload[field.name] for field in field_defs if field.name in payload}
    missing = [
        field.name
        for field in field_defs
        if field.default is MISSING and field.default_factory is MISSING and field.name not in kwargs
    ]
    if missing:
        raise SystemExit(f"development_report_incomplete_{field_name}:missing={','.join(missing)}")
    return CandidateSummary(**kwargs)


def _best_development_summary(dev_report: dict[str, Any]) -> tuple[str, CandidateSummary]:
    best_variant = str(dev_report.get("best_variant") or "optuna_tuned")
    candidate_payload = dev_report.get(best_variant) or dev_report.get("optuna_tuned")
    return best_variant, _candidate_summary_from_payload(candidate_payload, field_name=best_variant)


def _core_acceptance_report(
    *,
    evaluator: FrameworkEvaluator,
    dev_report: dict[str, Any],
    frozen_id: str,
    frozen_params: dict[str, Any],
    symbol: str,
) -> SearchReport:
    best_variant, dev_summary = _best_development_summary(dev_report)
    variant = evaluator.candidate_variant(
        frozen_params,
        name=best_variant,
        label=best_variant.replace("_", " ").title(),
    )
    accepted_summary = evaluator._evaluate_test_acceptance(variant, dev_summary)
    current = _candidate_summary_from_payload(dev_report.get("current"), field_name="current")
    paulwei = _candidate_summary_from_payload(dev_report.get("paulwei_baseline"), field_name="paulwei_baseline")
    search_status = dict(dev_report.get("search_status") or {})
    search_status.update(
        {
            "evaluation_role": "acceptance",
            "candidate_frozen_id": frozen_id,
            "acceptance_mode": "core_test_only",
        }
    )
    optuna_best = accepted_summary if best_variant == "optuna_tuned" else None
    rl_best = accepted_summary if best_variant == "rl_tuned" else None
    if optuna_best is None and rl_best is None:
        optuna_best = accepted_summary
    return SearchReport(
        created_at=datetime.now(UTC).isoformat(),
        symbol=symbol,
        readiness=dict(dev_report.get("readiness") or {}),
        deribit_status=dict(dev_report.get("deribit_status") or {}),
        split_contract=dict(dev_report.get("split_contract") or split_summary_json(default_btc_split_spec())),
        training_window_coverage=dict(dev_report.get("training_window_coverage") or {}),
        training_input_contract=dict(dev_report.get("training_input_contract") or {}),
        search_status=search_status,
        current=current,
        paulwei_baseline=paulwei,
        optuna_best=optuna_best,
        rl_best=rl_best,
        best_variant=best_variant,
        best_score=accepted_summary.robust_score,
        best_params=dict(frozen_params),
        comparison_payload={},
        test_comparison_payload=None,
        test_evaluated=True,
        report_mode="core_acceptance",
        report_degraded_due_to_memory=False,
        smoke_completed=True,
        signal_audit_summary=dev_report.get("signal_combination_audit"),
        evaluation_role="acceptance",
        test_access_allowed=True,
        candidate_ready_for_handoff=True,
        candidate_frozen_id=frozen_id,
        frozen_params=dict(frozen_params),
        handoff_blockers=[],
        dev_gate_summary=dev_report.get("dev_gate_summary"),
        dev_gate_used_this_round=True,
        dev_gate_last_round=dev_report.get("dev_gate_last_round"),
        dev_gate_interval=dev_report.get("dev_gate_interval"),
        full_stage2_executed_this_round=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen framework candidate acceptance")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--liquidation-dir", default="data/raw/merged_liquidations/aggregated")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--development-report", default=None)
    parser.add_argument("--candidate-frozen-id", default=None)
    parser.add_argument("--params-file", default=None)
    parser.add_argument(
        "--core-only",
        action="store_true",
        help="Run only frozen-candidate test acceptance instead of rebuilding the full dev comparison report.",
    )
    args = parser.parse_args()

    frozen_id = args.candidate_frozen_id
    frozen_params: dict[str, Any] | None = None
    dev_report: dict[str, Any] | None = None
    if args.development_report:
        dev_report = _read_json(Path(args.development_report))
        frozen_id, frozen_params = _extract_handoff_candidate(dev_report)
    elif args.params_file:
        frozen_params = _read_json(Path(args.params_file))

    if not frozen_id:
        raise SystemExit("candidate_frozen_id_required")
    if not isinstance(frozen_params, dict) or not frozen_params:
        raise SystemExit("frozen_params_required")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_config = HistoricalBacktestConfig(
        root_dir=Path(args.root),
        symbol=args.symbol,
        portfolio_symbol=f"{args.symbol}_PERP",
        liquidation_dir=Path(args.liquidation_dir),
        execution_timeframe=Timeframe.TF_5M,
        context_timeframe=Timeframe.TF_1H,
        strategy_config=ARCStrategyConfig(execution_timeframe=Timeframe.TF_5M),
    )
    evaluator = FrameworkEvaluator(
        base_config,
        split_spec=default_btc_split_spec(),
        search_space_mode="execution_first_v2_correlation_constrained",
    )
    if args.core_only:
        if dev_report is None:
            raise SystemExit("core_acceptance_requires_development_report")
        report = _core_acceptance_report(
            evaluator=evaluator,
            dev_report=dev_report,
            frozen_id=frozen_id,
            frozen_params=frozen_params,
            symbol=args.symbol,
        )
    else:
        report = evaluator.final_report(
            optuna_params=frozen_params,
            rl_params=None,
            search_status={
                "evaluation_role": "acceptance",
                "candidate_frozen_id": frozen_id,
            },
            full=True,
            evaluate_test=True,
            include_comparison_payload=True,
            evaluation_role="acceptance",
            test_access_allowed=True,
            dev_gate_used_this_round=True,
            full_stage2_executed_this_round=True,
        )
    report.candidate_frozen_id = frozen_id
    report.frozen_params = dict(frozen_params)
    final_json = output_dir / "acceptance_report.json"
    final_md = output_dir / "acceptance_report.md"
    write_search_report(final_json, report)
    final_md.write_text(build_markdown_report(report), encoding="utf-8")
    payload = _read_json(final_json)
    final_verdict = payload.get("final_verdict") or {}
    accepted = bool(payload.get("full_training_allowed", False)) and bool(
        final_verdict.get("promote_to_paper_live_candidate", False)
    )
    print(
        json.dumps(
            {
                "accepted_for_paper_live": accepted,
                "acceptance_failure_reason": (
                    None
                    if accepted
                    else final_verdict.get("promotion_failure_reason")
                    or (payload.get("full_training_blockers") or [None])[0]
                ),
                "candidate_frozen_id": frozen_id,
                "json_path": str(final_json),
                "md_path": str(final_md),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
