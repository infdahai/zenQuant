#!/usr/bin/env python3
"""Build a read-only mature PaulWei progression readiness artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.mature_progression import (
    build_mature_progression_markdown,
    build_mature_progression_payload,
)


def _load_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Check mature PaulWei progression readiness")
    parser.add_argument("--data-readiness", type=Path, default=Path("results/data_readiness/data_readiness.json"))
    parser.add_argument(
        "--framework-report",
        type=Path,
        default=Path("results/framework_optimization/framework_optimization.json"),
    )
    parser.add_argument(
        "--mature-baseline",
        type=Path,
        default=Path("config/research_baselines/paulwei_mature_style_2021_05_28.json"),
    )
    parser.add_argument(
        "--behavior-profile",
        type=Path,
        default=Path("results/paulwei_research_mature_style_2021_05_28/behavior_profile.json"),
    )
    parser.add_argument(
        "--strict-learning-review",
        type=Path,
        default=Path("results/paulwei_research/strict_execution_learning_review.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results/mature_progression_readiness"))
    args = parser.parse_args()

    payload = build_mature_progression_payload(
        data_readiness_payload=json.loads(args.data_readiness.read_text(encoding="utf-8")),
        framework_payload=json.loads(args.framework_report.read_text(encoding="utf-8")),
        mature_baseline_payload=json.loads(args.mature_baseline.read_text(encoding="utf-8")),
        behavior_profile_payload=_load_optional_json(args.behavior_profile),
        strict_learning_payload=_load_optional_json(args.strict_learning_review),
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "mature_progression_readiness.json"
    md_path = args.output_dir / "mature_progression_readiness.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(build_mature_progression_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "json_path": str(json_path),
                "md_path": str(md_path),
                "decision": payload["decision"],
                "blockers": payload["blockers"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
