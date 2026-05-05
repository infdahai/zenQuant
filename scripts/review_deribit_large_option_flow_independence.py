#!/usr/bin/env python3
"""Write the Deribit large-option-flow independence audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.deribit_large_option_independence import (
    write_deribit_large_option_flow_independence_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review Deribit large-option-flow feature independence")
    parser.add_argument(
        "--admission-path",
        type=Path,
        default=Path("results/data_source_review/deribit_large_option_flow_admission_review.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results/signal_combination"))
    args = parser.parse_args()

    payload = write_deribit_large_option_flow_independence_audit(
        admission_path=args.admission_path,
        output_dir=args.output_dir,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
