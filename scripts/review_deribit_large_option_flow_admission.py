#!/usr/bin/env python3
"""Write the Deribit large-option-flow admission review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.deribit_large_option_admission import write_deribit_large_option_flow_admission_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Review Deribit large-option-flow factor admission")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--audit-path",
        type=Path,
        default=Path(
            "results/research_swarm/deribit_large_option_flow_factor_audit_train_only_expanded/"
            "deribit_large_option_flow_factor_audit.json"
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results/data_source_review"))
    args = parser.parse_args()

    payload = write_deribit_large_option_flow_admission_review(
        audit_path=args.audit_path,
        output_dir=args.output_dir,
        symbol=args.symbol,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
