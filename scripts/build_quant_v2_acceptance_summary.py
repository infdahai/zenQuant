#!/usr/bin/env python3
"""Validate a raw Quant V2 report and write its canonical acceptance verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.quant_v2_acceptance import (
    AcceptanceValidationError,
    acceptance_exit_code,
    build_quant_v2_acceptance_summary,
    canonical_acceptance_summary_bytes,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the frozen Quant V2 acceptance summary")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("results/quant_v2_acceptance/architecture_attribution.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/quant_v2_acceptance/acceptance_summary.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source_size_bytes = args.input.stat().st_size
        source_sha256 = _sha256_file(args.input)
        with args.input.open(encoding="utf-8") as source:
            report = json.load(source)
        summary = build_quant_v2_acceptance_summary(
            report,
            source_artifact=str(args.input),
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
        )
        encoded = canonical_acceptance_summary_bytes(summary)
    except (AcceptanceValidationError, json.JSONDecodeError, OSError, UnicodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    exit_code = acceptance_exit_code(summary)
    print(f"{summary['verdict']}: {args.output} (exit={exit_code})")
    return exit_code


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
