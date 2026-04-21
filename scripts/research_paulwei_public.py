#!/usr/bin/env python3
"""Run behavior, mapping, and AHR999 research on the public PaulWei archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.paulwei_public import (
    _read_json,
    write_ahr999_alignment,
    write_baseline_mapping,
    write_behavior_profile,
    write_paulwei_dataset_contract,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Research the public PaulWei / BitMEX archive")
    parser.add_argument("--data-root", type=Path, default=Path("data/raw/paulwei_public"))
    parser.add_argument("--spot-root", type=Path, default=Path("data/raw/binance_spot"))
    parser.add_argument("--macro-root", type=Path, default=Path("data/raw/macro"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/paulwei_research"))
    args = parser.parse_args()

    contract = write_paulwei_dataset_contract(data_root=args.data_root, output_dir=args.output_dir)
    behavior = write_behavior_profile(data_root=args.data_root, spot_root=args.spot_root, output_dir=args.output_dir)
    profile = _read_json(args.output_dir / "behavior_profile.json")
    mapping = write_baseline_mapping(behavior_profile=profile, output_dir=args.output_dir)
    ahr999 = write_ahr999_alignment(
        data_root=args.data_root,
        spot_root=args.spot_root,
        macro_root=args.macro_root,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "contract": contract,
                "behavior": behavior,
                "mapping": mapping,
                "ahr999_alignment": ahr999,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
