#!/usr/bin/env python3
"""Run the bounded Gate v2 rescore without training or frozen-test access."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktestConfig
from zenquant.historical.perturbation import PerturbationConfig
from zenquant.research.comparison import VariantSpec, WindowSpec, compare_variants, default_variant_specs
from zenquant.research.framework_search import FrameworkEvaluator
from zenquant.research.gate_v2 import DEFAULT_COST_BPS, build_campaign_verdict
from zenquant.research.splits import default_btc_split_spec
from zenquant.strategy import ARCStrategyConfig

FIXED_SEEDS = (42, 123, 456, 789, 1024)


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """One builtin baseline or ranked train/dev parameter record."""

    name: str
    kind: str
    params: dict[str, Any] | None
    source: str | None = None
    rank: int | None = None


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def _reject_frozen_test_source(payload: dict[str, Any], *, source: Path) -> None:
    if (
        bool(payload.get("test_evaluated"))
        or str(payload.get("evaluation_role") or "").lower() == "acceptance"
        or bool(payload.get("test_comparison_payload"))
    ):
        raise ValueError(f"frozen_test_source_rejected:{source}")


def _select_path(payload: dict[str, Any], selector: str, *, source: Path) -> Any:
    value: Any = payload
    for part in selector.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"candidate_selector_missing:{source}:{selector}")
        value = value[part]
    return value


def load_candidate_manifest(path: Path) -> list[CandidateRecord]:
    """Load ranked train/dev candidates while failing closed on test evidence."""
    manifest = _read_json(path)
    entries = manifest.get("candidates")
    if not isinstance(entries, list) or not entries:
        raise ValueError("candidate_manifest_requires_nonempty_candidates")

    records: list[CandidateRecord] = []
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("candidate_manifest_entry_must_be_object")
        name = str(entry.get("name") or "")
        if not name:
            raise ValueError("candidate_name_required")
        if name in names:
            raise ValueError(f"duplicate_candidate_name:{name}")
        names.add(name)

        kind = str(entry.get("kind") or "")
        if kind == "builtin":
            records.append(CandidateRecord(name=name, kind=kind, params=None))
            continue
        if kind != "ranked_params":
            raise ValueError(f"unsupported_candidate_kind:{kind}")

        source = Path(str(entry.get("source") or ""))
        selector = str(entry.get("selector") or "")
        rank = int(entry.get("rank") or 0)
        if not source.is_file():
            raise ValueError(f"candidate_source_missing:{source}")
        if not selector or rank < 1:
            raise ValueError(f"candidate_selector_and_positive_rank_required:{name}")
        source_payload = _read_json(source)
        _reject_frozen_test_source(source_payload, source=source)
        ranked = _select_path(source_payload, selector, source=source)
        if not isinstance(ranked, list) or rank > len(ranked):
            raise ValueError(f"candidate_rank_unavailable:{name}:{rank}")
        selected = ranked[rank - 1]
        params = selected.get("params") if isinstance(selected, dict) else None
        if not isinstance(params, dict) or not params:
            raise ValueError(f"candidate_params_missing:{name}:{rank}")
        records.append(
            CandidateRecord(
                name=name,
                kind=kind,
                params=dict(params),
                source=str(source),
                rank=rank,
            )
        )
    return records


def _utc_ns(year: int, month: int = 1, day: int = 1) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1_000_000_000)


def gate_v2_windows() -> list[WindowSpec]:
    """Return the explicit train/dev-only temporal scorecard windows."""
    split_spec = default_btc_split_spec()
    return [
        WindowSpec(label="train_full", start_ns=split_spec.train.start_ns, end_ns=split_spec.train.end_ns - 1),
        *[
            WindowSpec(label=str(year), start_ns=_utc_ns(year), end_ns=_utc_ns(year + 1) - 1)
            for year in (2020, 2021, 2022, 2023)
        ],
        WindowSpec(label="late_dev_2025", start_ns=_utc_ns(2025), end_ns=split_spec.dev.end_ns - 1),
    ]


def gate_v2_perturbations() -> list[PerturbationConfig]:
    """Return the five deterministic domain-randomization scenarios."""
    return [
        PerturbationConfig(seed=seed, price_noise_pct=0.001, volume_noise_pct=0.05, bar_dropout_pct=0.02)
        for seed in FIXED_SEEDS
    ]


def build_base_config(*, root_dir: Path, symbol: str) -> HistoricalBacktestConfig:
    """Reconstruct the authoritative execution contract used by the source campaigns."""
    return HistoricalBacktestConfig(
        root_dir=root_dir,
        symbol=symbol,
        portfolio_symbol=f"{symbol}_PERP",
        execution_timeframe=Timeframe.TF_5M,
        context_timeframe=Timeframe.TF_1H,
        strategy_config=ARCStrategyConfig(execution_timeframe=Timeframe.TF_5M),
    )


def _variant_specs(records: Sequence[CandidateRecord], base_config: HistoricalBacktestConfig) -> list[VariantSpec]:
    split_spec = default_btc_split_spec()
    evaluator = FrameworkEvaluator(base_config, split_spec=split_spec)
    builtin = {variant.name: variant for variant in default_variant_specs()}
    variants: list[VariantSpec] = []
    for record in records:
        if record.kind == "builtin":
            variant = builtin.get(record.name)
            if variant is None:
                raise ValueError(f"unsupported_builtin_candidate:{record.name}")
            variants.append(variant)
            continue
        assert record.params is not None
        variants.append(evaluator.candidate_variant(record.params, name=record.name, label=record.name))
    return variants


def finalize_report(verdict: dict[str, Any], *, candidate_names: Sequence[str]) -> dict[str, Any]:
    """Attach the immutable no-training/no-test experiment contract."""
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "experiment": "gate_v2_candidate_rescore",
        "candidate_names": list(candidate_names),
        "cost_stress_bps": list(DEFAULT_COST_BPS),
        "window_labels": [window.label for window in gate_v2_windows()],
        "train_search_executed": False,
        "optuna_search_executed": False,
        "rl_search_executed": False,
        "full_training_executed": False,
        "test_evaluated": False,
        "test_access_allowed": False,
        **verdict,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render the compact candidate decision matrix."""
    lines = [
        "# Gate v2 Candidate Rescore",
        "",
        f"- terminal_recommendation: `{report['terminal_recommendation']}`",
        f"- seeds: `{report['seed_count']}`",
        f"- test_evaluated: `{str(report['test_evaluated']).lower()}`",
        f"- full_training_executed: `{str(report['full_training_executed']).lower()}`",
        "",
        "| Candidate | Passing seeds | Gate passed | Hard blockers |",
        "| --- | ---: | --- | --- |",
    ]
    for name in report["candidate_names"]:
        candidate = report["candidates"].get(name) or {
            "passing_seeds": 0,
            "seed_count": report["seed_count"],
            "gate_passed": False,
            "hard_blocker_counts": {"candidate_result_missing": report["seed_count"]},
        }
        blockers = ", ".join(
            f"{reason} ({count})" for reason, count in candidate.get("hard_blocker_counts", {}).items()
        )
        lines.append(
            f"| {name} | {candidate['passing_seeds']}/{candidate['seed_count']} | "
            f"{str(candidate['gate_passed']).lower()} | {blockers or 'none'} |"
        )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def run_rescore(
    *,
    manifest_path: Path,
    output_dir: Path,
    root_dir: Path,
    symbol: str,
    window_workers: int,
) -> dict[str, Any]:
    """Run all fixed candidates through the bounded Gate v2 experiment."""
    records = load_candidate_manifest(manifest_path)
    candidate_names = [record.name for record in records]
    windows = gate_v2_windows()
    split_spec = default_btc_split_spec()
    output_dir.mkdir(parents=True, exist_ok=True)
    base_config = build_base_config(root_dir=root_dir, symbol=symbol)
    variants = _variant_specs(records, base_config)
    seed_payloads: list[dict[str, Any]] = []
    for perturbation in gate_v2_perturbations():
        comparison = compare_variants(
            replace(base_config, perturbation=perturbation),
            variants=variants,
            windows=windows,
            walk_forward_windows=[],
            split_spec=split_spec,
            comparison_split="dev",
            comparison_compute_mode="smoke_minimal",
            window_workers=window_workers,
            include_attribution_ledger=False,
        )
        seed_payload = {
            "seed": perturbation.seed,
            "perturbation": asdict(perturbation),
            "comparison_payload": comparison,
        }
        seed_payloads.append(seed_payload)
        _write_json(output_dir / f"comparison_seed_{perturbation.seed}.json", seed_payload)

    verdict = build_campaign_verdict(seed_payloads, candidate_names=candidate_names)
    report = finalize_report(verdict, candidate_names=candidate_names)
    _write_json(output_dir / "gate_v2_rescore.json", report)
    (output_dir / "gate_v2_rescore.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--window-workers", type=int, default=1)
    args = parser.parse_args()
    report = run_rescore(
        manifest_path=args.manifest,
        output_dir=args.output_dir,
        root_dir=args.root,
        symbol=args.symbol,
        window_workers=max(1, args.window_workers),
    )
    print(f"Saved Gate v2 report to {args.output_dir / 'gate_v2_rescore.json'}")
    print(f"Terminal recommendation: {report['terminal_recommendation']}")


if __name__ == "__main__":
    main()
