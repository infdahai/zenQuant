#!/usr/bin/env python3
"""Dry-run the train-only regime objective candidate-generation scorer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.regime_objective import (
    compare_regime_objective_candidate_rankings,
    score_regime_objective_candidate_generation,
)


def _variant_summary(verdict: dict[str, Any], variant: str) -> dict[str, Any]:
    return dict((verdict.get("variant_window_summary") or {}).get(variant) or {})


def _candidate_metrics_from_verdict(verdict: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate_name = str(verdict.get("candidate_variant") or "")
    candidate = _variant_summary(verdict, candidate_name)
    current = _variant_summary(verdict, "current")
    candidate_full = dict(candidate.get("full_train") or {})
    current_full = dict(current.get("full_train") or {})
    candidate_full["train_bucket_metrics"] = dict(candidate.get("bucket_metrics") or {})
    current_full["train_bucket_metrics"] = dict(current.get("bucket_metrics") or {})
    return candidate_full, current_full


def _candidate_from_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    candidate_metrics, baseline_metrics = _candidate_metrics_from_verdict(verdict)
    return {
        "candidate_id": str(verdict.get("candidate_variant") or "candidate"),
        "params": {},
        "metrics": candidate_metrics,
        "baseline_train_metrics": baseline_metrics,
    }


def _load_candidates(path: Path | None, verdict: dict[str, Any]) -> list[dict[str, Any]]:
    if path is None:
        return [_candidate_from_verdict(verdict)]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    return [dict(item) for item in payload.get("candidates", [])]


def _markdown(payload: dict[str, Any]) -> str:
    score = payload["candidate_generation_score"]
    ranking = payload["ranking_comparison"]
    return "\n".join(
        [
            "# Regime Objective Candidate Generation Dry Run",
            "",
            f"- source_regime_objective_verdict: `{payload['source_regime_objective_verdict']}`",
            f"- candidate_variant: `{payload['candidate_variant']}`",
            f"- allow_dev_diagnostic_review: `{score['allow_dev_diagnostic_review']}`",
            f"- regime_objective_score: `{score['regime_objective_score']}`",
            f"- bucket_blockers: `{score['bucket_blockers']}`",
            f"- turnover_penalty: `{score['turnover_penalty']}`",
            f"- fee_drag_penalty: `{score['fee_drag_penalty']}`",
            f"- candidate_count: `{ranking['candidate_count']}`",
            f"- allowed_count: `{ranking['allowed_count']}`",
            f"- rejected_count: `{ranking['rejected_count']}`",
            f"- original_top_candidate: `{ranking['original_top_candidate']}`",
            f"- regime_objective_top_candidate: `{ranking['regime_objective_top_candidate']}`",
            f"- top_candidate_changed: `{ranking['top_candidate_changed']}`",
            f"- test_evaluated: `{payload['test_evaluated']}`",
            f"- promotion_allowed: `{payload['promotion_allowed']}`",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run train-only regime objective candidate generation scoring")
    parser.add_argument(
        "--source",
        default="results/regime_objective_redesign/regime_objective_check.json",
        help="Regime objective verdict JSON used only as train-bucket input.",
    )
    parser.add_argument(
        "--candidates",
        default=None,
        help="Optional JSON list/payload of train candidates to compare with original Stage 1 ranking.",
    )
    parser.add_argument("--output-dir", default="results/regime_objective_candidate_generation")
    args = parser.parse_args()

    source = Path(args.source)
    verdict = json.loads(source.read_text(encoding="utf-8"))
    candidate_metrics, baseline_metrics = _candidate_metrics_from_verdict(verdict)
    score = score_regime_objective_candidate_generation(candidate_metrics, baseline_metrics=baseline_metrics)
    candidates = _load_candidates(Path(args.candidates) if args.candidates else None, verdict)
    ranking = compare_regime_objective_candidate_rankings(candidates)
    payload = {
        "source_regime_objective_verdict": str(source),
        "source_candidates": args.candidates,
        "candidate_variant": verdict.get("candidate_variant"),
        "selection_scope": "train_only",
        "candidate_generation_score": score,
        "ranking_comparison": ranking,
        "test_evaluated": False,
        "promotion_allowed": False,
        "paper_live_eligible": False,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "candidate_generation_check.json"
    md_path = output_dir / "candidate_generation_check.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")

    print(f"Saved candidate generation JSON to {json_path}")
    print(f"Saved candidate generation Markdown to {md_path}")


if __name__ == "__main__":
    main()
