#!/usr/bin/env python3
"""Run frozen-candidate acceptance on the test split only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.framework_search import (
    FrameworkEvaluator,
    build_markdown_report,
    write_search_report,
)
from zenquant.research.splits import default_btc_split_spec
from zenquant.strategy import ARCStrategyConfig


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"development_report_not_found:{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_handoff_candidate_from_development_report(path: Path) -> tuple[str, dict[str, Any]]:
    dev_report = _read_json(path)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen framework candidate acceptance")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--liquidation-dir", default="data/raw/merged_liquidations/aggregated")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--development-report", default=None)
    parser.add_argument("--candidate-frozen-id", default=None)
    parser.add_argument("--params-file", default=None)
    args = parser.parse_args()

    frozen_id = args.candidate_frozen_id
    frozen_params: dict[str, Any] | None = None
    if args.development_report:
        frozen_id, frozen_params = _load_handoff_candidate_from_development_report(Path(args.development_report))
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
    print(
        json.dumps(
            {
                "accepted_for_paper_live": bool(payload.get("full_training_allowed", False)),
                "acceptance_failure_reason": (
                    None
                    if payload.get("full_training_allowed", False)
                    else (payload.get("full_training_blockers") or [None])[0]
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
