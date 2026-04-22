#!/usr/bin/env python3
"""Run the research-only microstructure sidecar pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from zenquant.research.microstructure import (
    write_microstructure_admission_review,
    write_microstructure_dataset_contract,
    write_microstructure_feature_baseline_review,
    write_microstructure_preprocessing_review,
    write_microstructure_sidecar_features,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build microstructure sidecar research artifacts.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("results/microstructure_research"))
    parser.add_argument("--feature-output-root", type=Path, default=Path("data/raw/microstructure_sidecar"))
    parser.add_argument("--venue", default="bybit")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--cadence-ms", type=int, default=1000)
    parser.add_argument("--horizon-ms", type=int, default=1000)
    args = parser.parse_args()

    write_microstructure_dataset_contract(
        data_root=args.data_root,
        venue=args.venue,
        symbol=args.symbol,
        depth=args.depth,
        cadence_ms=args.cadence_ms,
        output_dir=args.output_dir,
    )
    write_microstructure_preprocessing_review(
        data_root=args.data_root,
        venue=args.venue,
        symbol=args.symbol,
        depth=args.depth,
        cadence_ms=args.cadence_ms,
        output_dir=args.output_dir,
    )
    write_microstructure_sidecar_features(
        data_root=args.data_root,
        venue=args.venue,
        symbol=args.symbol,
        depth=args.depth,
        cadence_ms=args.cadence_ms,
        feature_output_root=args.feature_output_root,
    )
    write_microstructure_feature_baseline_review(
        data_root=args.data_root,
        venue=args.venue,
        symbol=args.symbol,
        depth=args.depth,
        cadence_ms=args.cadence_ms,
        horizon_ms=args.horizon_ms,
        output_dir=args.output_dir,
    )
    write_microstructure_admission_review(
        data_root=args.data_root,
        venue=args.venue,
        symbol=args.symbol,
        depth=args.depth,
        cadence_ms=args.cadence_ms,
        horizon_ms=args.horizon_ms,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
