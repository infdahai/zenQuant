#!/usr/bin/env python3
"""Run a resumable framework-optimization campaign with authoritative status output."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.framework_search import (
    FrameworkEvaluator,
    build_markdown_report,
    run_optuna_search,
    write_search_report,
)
from zenquant.research.rl_tuner import train_tuner
from zenquant.research.splits import default_btc_split_spec, split_summary_json


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _update_status(path: Path, **kwargs: Any) -> dict[str, Any]:
    payload = _read_json(path) or {}
    payload.update(kwargs)
    payload["updated_at"] = _ts()
    _write_json(path, payload)
    return payload


def _initialize_running_status(
    path: Path,
    *,
    budget: str,
    optuna_trials: int,
    rl_episodes: int,
    started_at: str,
) -> dict[str, Any]:
    payload = {
        "phase": "stage0_readiness",
        "status": "running",
        "budget": budget,
        "optuna_trials_requested": optuna_trials,
        "rl_episodes_requested": rl_episodes,
        "optuna_trials_completed": 0,
        "rl_episodes_completed": 0,
        "started_at": started_at,
        "updated_at": _ts(),
        "blocked_by": None,
        "liquidation_in_search": None,
        "liquidation_blocked_by": None,
        "search_space_profile": None,
        "resume_supported": True,
        "split_contract": split_summary_json(default_btc_split_spec()),
        "test_evaluated": False,
    }
    _write_json(path, payload)
    return payload


def _resolved_budget(budget: str, optuna_trials: int | None, rl_episodes: int | None) -> tuple[int, int]:
    defaults = {
        "smoke": (5, 5),
        "fast": (50, 50),
        "high": (150, 100),
    }
    default_optuna, default_rl = defaults[budget]
    return optuna_trials or default_optuna, rl_episodes or default_rl


def _archive_prior_outputs(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    archive_root = output_dir.parent / "archive" / "dev_runs" / f"{output_dir.name}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    archive_root.parent.mkdir(parents=True, exist_ok=True)
    for name in ("campaign_status.json", "framework_optimization.json", "framework_optimization.md", "campaign.log", "checkpoints"):
        source = output_dir / name
        if source.exists():
            target = archive_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run resumable framework optimization campaign")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", default="results/framework_optimization")
    parser.add_argument("--budget", choices=("smoke", "fast", "high"), default="high")
    parser.add_argument("--optuna-trials", type=int)
    parser.add_argument("--rl-episodes", type=int)
    parser.add_argument("--liquidation-dir", default="data/raw/merged_liquidations/aggregated")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not args.resume:
        _archive_prior_outputs(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "campaign_status.json"

    optuna_trials, rl_episodes = _resolved_budget(args.budget, args.optuna_trials, args.rl_episodes)
    started_at = (_read_json(status_path) or {}).get("started_at", _ts()) if args.resume else _ts()
    _initialize_running_status(
        status_path,
        budget=args.budget,
        optuna_trials=optuna_trials,
        rl_episodes=rl_episodes,
        started_at=started_at,
    )

    try:
        if importlib.util.find_spec("optuna") is None:
            _update_status(
                status_path,
                phase="stage1_search",
                status="blocked",
                blocked_by="optuna_unavailable",
            )
            print("blocked_by=optuna_unavailable")
            return

        base_config = HistoricalBacktestConfig(
            root_dir=Path(args.root),
            symbol=args.symbol,
            portfolio_symbol=f"{args.symbol}_PERP",
            liquidation_dir=Path(args.liquidation_dir),
        )
        split_spec = default_btc_split_spec()
        evaluator = FrameworkEvaluator(base_config, split_spec=split_spec)

        readiness_checkpoint = checkpoint_dir / "stage0_readiness.json"
        if not (args.resume and readiness_checkpoint.exists()):
            _write_json(
                readiness_checkpoint,
                {
                    "created_at": _ts(),
                    "readiness": evaluator.readiness,
                    "search_param_grid": evaluator.search_param_grid,
                    "split_contract": split_summary_json(split_spec),
                },
            )

        stage1_path = checkpoint_dir / "stage1_search.json"
        stage1_payload = _read_json(stage1_path) if args.resume else None
        if stage1_payload is None:
            _update_status(status_path, phase="stage1_search", status="running")
            optuna_params, optuna_trials_payload = run_optuna_search(evaluator, n_trials=optuna_trials)
            rl_result = train_tuner(
                total_timesteps=rl_episodes,
                verbose=0,
                param_grid=evaluator.search_param_grid,
                split_spec=split_spec,
            )
            rl_params = rl_result.get("best_params") if rl_result else None
            stage1_payload = {
                "created_at": _ts(),
                "optuna_params": optuna_params,
                "rl_params": rl_params,
                "search_status": {
                    "budget": args.budget,
                    "optuna_available": True,
                    "optuna_trials_requested": optuna_trials,
                    "rl_episodes_requested": rl_episodes,
                    "optuna_trials_completed": len(optuna_trials_payload),
                    "rl_episodes_completed": rl_episodes,
                    "optuna_candidate_found": optuna_params is not None,
                    "rl_candidate_found": rl_params is not None,
                    "liquidation_in_search": evaluator.readiness["promotion_ready"],
                    "liquidation_blocked_by": None if evaluator.readiness["promotion_ready"] else "not_promotion_ready",
                    "search_space_profile": evaluator.search_space_profile,
                    "split_contract_version": split_spec.version,
                    "candidate_sources": ["auto_research_train_only", "rl_train_dev_shortlist"],
                    "train_only_search": True,
                    "dev_only_ranking": True,
                },
                "top_optuna_trials": optuna_trials_payload[:5],
                "split_contract": split_summary_json(split_spec),
            }
            _write_json(stage1_path, stage1_payload)
        _update_status(
            status_path,
            phase="stage1_search",
            optuna_trials_completed=int(stage1_payload["search_status"]["optuna_trials_completed"]),
            rl_episodes_completed=int(stage1_payload["search_status"]["rl_episodes_completed"]),
            liquidation_in_search=stage1_payload["search_status"].get("liquidation_in_search"),
            liquidation_blocked_by=stage1_payload["search_status"].get("liquidation_blocked_by"),
            search_space_profile=stage1_payload["search_status"].get("search_space_profile"),
            candidate_sources=stage1_payload["search_status"].get("candidate_sources"),
            train_only_search=stage1_payload["search_status"].get("train_only_search"),
            dev_only_ranking=stage1_payload["search_status"].get("dev_only_ranking"),
            resume_supported=True,
        )

        stage2_path = checkpoint_dir / "stage2_full_report.json"
        final_report_payload = _read_json(stage2_path) if args.resume else None
        if final_report_payload is None:
            _update_status(status_path, phase="stage2_full_verdict", status="running")
            report = evaluator.final_report(
                optuna_params=stage1_payload.get("optuna_params"),
                rl_params=stage1_payload.get("rl_params"),
                search_status=stage1_payload.get("search_status"),
                full=True,
                evaluate_test=True,
            )
            final_json = output_dir / "framework_optimization.json"
            final_md = output_dir / "framework_optimization.md"
            write_search_report(final_json, report)
            final_md.write_text(build_markdown_report(report), encoding="utf-8")
            final_report_payload = {
                "created_at": _ts(),
                "best_variant": report.best_variant,
                "best_score": report.best_score,
                "report_mode": report.report_mode,
                "test_evaluated": bool(getattr(report, "test_evaluated", False)),
                "json_path": str(final_json),
                "md_path": str(final_md),
            }
            _write_json(stage2_path, final_report_payload)

        _update_status(
            status_path,
            phase="stage3_complete",
            status="completed",
            blocked_by=None,
            best_variant=final_report_payload.get("best_variant"),
            best_score=final_report_payload.get("best_score"),
            report_mode=final_report_payload.get("report_mode"),
            test_evaluated=bool(final_report_payload.get("test_evaluated")),
            json_path=final_report_payload.get("json_path"),
            md_path=final_report_payload.get("md_path"),
            resume_supported=True,
        )
        print(json.dumps(_read_json(status_path), indent=2))
    except Exception:
        _update_status(
            status_path,
            status="failed",
            blocked_by="campaign_exception",
            detail=traceback.format_exc(limit=10),
        )
        raise


if __name__ == "__main__":
    main()
