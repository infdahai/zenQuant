#!/usr/bin/env python3
"""Explain reduce/flip cadence with frozen pressure-context factors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.pressure_context import (
    build_pressure_context_attribution,
    build_pressure_context_markdown,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run research-only pressure context attribution")
    parser.add_argument(
        "--attribution-report",
        type=Path,
        required=True,
        help="Intent churn attribution JSON with decision_attribution_ledger rows.",
    )
    parser.add_argument(
        "--admission-report",
        type=Path,
        default=Path("results/data_source_review/deribit_large_option_flow_admission_review.json"),
    )
    parser.add_argument(
        "--independence-report",
        type=Path,
        default=Path("results/signal_combination/deribit_large_option_flow_independence_audit.json"),
    )
    parser.add_argument("--features-path", type=Path)
    parser.add_argument("--top-n", type=int, default=8)
    parser.add_argument("--max-lag-hours", type=float, default=4.25)
    parser.add_argument("--min-context-rows", type=int, default=30)
    parser.add_argument("--min-high-pressure-event-count", type=int, default=10)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/research_swarm/pressure_context_attribution_v1"),
    )
    args = parser.parse_args()

    admission = _read_json(args.admission_report)
    features_path = args.features_path or Path(str(admission.get("features_path") or ""))
    payload = build_pressure_context_attribution(
        attribution_payload=_read_json(args.attribution_report),
        admission_payload=admission,
        independence_payload=_read_json(args.independence_report),
        features_path=features_path,
        top_n=args.top_n,
        max_lag_hours=args.max_lag_hours,
        min_context_rows=args.min_context_rows,
        min_high_pressure_event_count=args.min_high_pressure_event_count,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "pressure_context_attribution.json"
    md_path = args.output_dir / "pressure_context_attribution.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(build_pressure_context_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "json_path": str(json_path),
                "md_path": str(md_path),
                "replay_explains_reduce_flip_cadence": payload["replay_explains_reduce_flip_cadence"],
                "blocked_by": payload["blocked_by"],
                "next_action": payload["next_action"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
