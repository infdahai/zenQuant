#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.loop_stability import build_loop_stability_review, write_loop_stability_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Review framework loop stability across historical runs")
    parser.add_argument("--results-root", type=Path, default=Path("results"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    args = parser.parse_args()

    payload = build_loop_stability_review(args.results_root)
    written = write_loop_stability_review(payload, args.output_dir)
    print(json.dumps({"review": written, "loop_count": payload["loop_count"]}, indent=2))


if __name__ == "__main__":
    main()
