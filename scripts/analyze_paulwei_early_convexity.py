#!/usr/bin/env python3
"""Build a research-only PaulWei early BTC-convexity audit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.paulwei_public import write_early_convexity_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze PaulWei early BTC-unit convexity")
    parser.add_argument("--data-root", type=Path, default=Path("data/raw/paulwei_public"))
    parser.add_argument("--spot-root", type=Path, default=Path("data/raw/binance_spot"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/paulwei_early_convexity"))
    parser.add_argument("--mature-start-at", default="2021-05-28T12:00:00+00:00")
    args = parser.parse_args()

    result = write_early_convexity_audit(
        data_root=args.data_root,
        spot_root=args.spot_root,
        output_dir=args.output_dir,
        mature_start_at=args.mature_start_at,
    )
    print(f"Saved JSON report to {result['json_path']}")
    print(f"Saved Markdown summary to {result['md_path']}")
    print(f"ready={result['ready']}")
    print(f"artifact_id={result['artifact_id']}")


if __name__ == "__main__":
    main()
