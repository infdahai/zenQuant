#!/usr/bin/env python3
"""Publish a frozen late-development cost diagnosis from an explicit JSON input."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.late_dev_cost_attribution import (
    LateDevAttributionValidationError,
    build_late_dev_cost_attribution,
    build_late_dev_cost_attribution_markdown,
    build_regime_objective_hypothesis,
    canonical_late_dev_attribution_bytes,
)

_HASH_CHUNK_BYTES = 1024 * 1024
_REPORT_FILENAME = "late_dev_cost_attribution.json"
_MARKDOWN_FILENAME = "late_dev_cost_attribution.md"
_HYPOTHESIS_FILENAME = "regime_objective_redesign_from_late_dev.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish frozen Quant V2 late-development cost attribution")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("results/quant_v2_acceptance/architecture_attribution.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/quant_v2_late_dev_cost_attribution"),
    )
    return parser.parse_args()


def _read_input(path: Path) -> tuple[dict[str, Any], str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as source_file:
        while chunk := source_file.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            size_bytes += len(chunk)
    with path.open(encoding="utf-8") as source_file:
        payload = json.load(source_file)
    if not isinstance(payload, dict):
        raise LateDevAttributionValidationError("raw report must be a JSON object")
    return payload, digest.hexdigest(), size_bytes


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")


def _publish_atomically(output_dir: Path, files: dict[str, bytes]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_paths: list[Path] = []
    try:
        for filename, content in files.items():
            target = output_dir / filename
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(content)
            temporary_paths.append(temporary)
        for temporary in temporary_paths:
            temporary.replace(temporary.with_suffix(""))
        if _HYPOTHESIS_FILENAME not in files:
            (output_dir / _HYPOTHESIS_FILENAME).unlink(missing_ok=True)
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)


def main() -> int:
    args = _parse_args()
    try:
        raw_report, source_sha256, source_size_bytes = _read_input(args.input)
        attribution = build_late_dev_cost_attribution(
            raw_report,
            source_artifact=str(args.input),
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
        )
        report_bytes = canonical_late_dev_attribution_bytes(attribution)
        markdown_bytes = build_late_dev_cost_attribution_markdown(attribution).encode("utf-8")
        hypothesis = build_regime_objective_hypothesis(attribution)
        files = {
            _REPORT_FILENAME: report_bytes,
            _MARKDOWN_FILENAME: markdown_bytes,
        }
        if hypothesis is not None:
            files[_HYPOTHESIS_FILENAME] = _canonical_json_bytes(hypothesis)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, LateDevAttributionValidationError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2

    try:
        _publish_atomically(args.output_dir, files)
    except OSError as exc:
        print(f"output error: {exc}", file=sys.stderr)
        return 2

    recommendation = attribution["terminal_recommendation"]
    print(json.dumps({"output_dir": str(args.output_dir), "terminal_recommendation": recommendation}, sort_keys=True))
    return 1 if recommendation == "insufficient_evidence_no_strategy_change" else 0


if __name__ == "__main__":
    raise SystemExit(main())
