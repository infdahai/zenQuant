#!/usr/bin/env python3
"""Build a research-only PaulWei order/fill tranche behavior profile."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.paulwei_public import write_tranche_behavior_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze PaulWei order/fill/episode tranching behavior")
    parser.add_argument("--data-root", type=Path, default=Path("data/raw/paulwei_public"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/paulwei_tranche_behavior"))
    parser.add_argument("--mature-start-at", default="2021-05-28T12:00:00+00:00")
    args = parser.parse_args()

    result = write_tranche_behavior_profile(
        data_root=args.data_root,
        output_dir=args.output_dir,
        mature_start_at=args.mature_start_at,
    )
    print(f"Saved JSON report to {result['json_path']}")
    print(f"Saved Markdown summary to {result['md_path']}")
    print(f"artifact_id={result['artifact_id']}")


if __name__ == "__main__":
    main()
