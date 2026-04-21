#!/usr/bin/env python3
"""Run behavior, mapping, and AHR999 research on the public PaulWei archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.comparison import compare_variants, public_archive_variant_specs
from zenquant.research.paulwei_public import (
    _read_json,
    write_ahr999_alignment,
    write_ahr999_execution_guidance_review,
    write_baseline_mapping,
    write_behavior_profile,
    write_execution_research_review,
    write_paulwei_dataset_contract,
    write_promotion_eligibility_review,
    write_strict_execution_learning_review,
    write_variant_comparison_review,
)
from zenquant.research.splits import default_btc_split_spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Research the public PaulWei / BitMEX archive")
    parser.add_argument("--data-root", type=Path, default=Path("data/raw/paulwei_public"))
    parser.add_argument("--spot-root", type=Path, default=Path("data/raw/binance_spot"))
    parser.add_argument("--macro-root", type=Path, default=Path("data/raw/macro"))
    parser.add_argument("--binance-root", type=Path, default=Path("data/raw/binance"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", type=Path, default=Path("results/paulwei_research"))
    parser.add_argument("--skip-compare", action="store_true")
    args = parser.parse_args()

    contract = write_paulwei_dataset_contract(data_root=args.data_root, output_dir=args.output_dir)
    behavior = write_behavior_profile(data_root=args.data_root, spot_root=args.spot_root, output_dir=args.output_dir)
    profile = _read_json(args.output_dir / "behavior_profile.json")
    mapping = write_baseline_mapping(behavior_profile=profile, output_dir=args.output_dir)
    mapping_payload = _read_json(args.output_dir / "baseline_mapping.json")
    execution_review = write_execution_research_review(
        dataset_contract=_read_json(args.output_dir / "paulwei_dataset_contract.json"),
        behavior_profile=profile,
        mapping=mapping_payload,
        dataset_contract_path=args.output_dir / "paulwei_dataset_contract.json",
        behavior_profile_path=args.output_dir / "behavior_profile.json",
        mapping_path=args.output_dir / "baseline_mapping.json",
        output_dir=args.output_dir,
    )
    ahr999 = write_ahr999_alignment(
        data_root=args.data_root,
        spot_root=args.spot_root,
        macro_root=args.macro_root,
        output_dir=args.output_dir,
    )
    ahr_payload = _read_json(args.output_dir / "ahr999_alignment.json")
    ahr_guidance = write_ahr999_execution_guidance_review(
        alignment=ahr_payload,
        dataset_contract_path=args.output_dir / "paulwei_dataset_contract.json",
        dataset_contract_id=contract.get("artifact_id"),
        behavior_profile_path=args.output_dir / "behavior_profile.json",
        behavior_profile_id=behavior.get("artifact_id"),
        mapping_path=args.output_dir / "baseline_mapping.json",
        mapping_id=mapping.get("artifact_id"),
        ahr_alignment_path=args.output_dir / "ahr999_alignment.json",
        output_dir=args.output_dir,
    )

    comparison_info = None
    promotion_info = None
    strict_learning_info = None
    if not args.skip_compare:
        comparison_payload = compare_variants(
            HistoricalBacktestConfig(
                root_dir=args.binance_root,
                symbol=args.symbol,
                portfolio_symbol=f"{args.symbol}_PERP",
            ),
            variants=public_archive_variant_specs(mapping_path=args.output_dir / "baseline_mapping.json"),
            split_spec=default_btc_split_spec(),
            comparison_split="dev",
            walk_forward_windows=[],
        )
        comparison_payload_path = args.output_dir / "variant_comparison_payload.json"
        comparison_payload_path.write_text(json.dumps(comparison_payload, indent=2), encoding="utf-8")
        comparison_info = write_variant_comparison_review(
            comparison_payload=comparison_payload,
            mapping=mapping_payload,
            dataset_contract_path=args.output_dir / "paulwei_dataset_contract.json",
            dataset_contract_id=contract.get("artifact_id"),
            behavior_profile_path=args.output_dir / "behavior_profile.json",
            behavior_profile_id=behavior.get("artifact_id"),
            mapping_path=args.output_dir / "baseline_mapping.json",
            comparison_run_path=comparison_payload_path,
            output_dir=args.output_dir,
        )
        promotion_info = write_promotion_eligibility_review(
            variant_review=_read_json(args.output_dir / "variant_comparison_review.json"),
            mapping=mapping_payload,
            dataset_contract_path=args.output_dir / "paulwei_dataset_contract.json",
            dataset_contract_id=contract.get("artifact_id"),
            behavior_profile_path=args.output_dir / "behavior_profile.json",
            behavior_profile_id=behavior.get("artifact_id"),
            mapping_path=args.output_dir / "baseline_mapping.json",
            ahr_guidance_path=args.output_dir / "ahr999_execution_guidance_review.json",
            ahr_guidance_id=ahr_guidance.get("artifact_id"),
            comparison_run_path=comparison_payload_path,
            output_dir=args.output_dir,
        )
        strict_learning_info = write_strict_execution_learning_review(
            execution_review=_read_json(args.output_dir / "execution_research_review.json"),
            variant_review=_read_json(args.output_dir / "variant_comparison_review.json"),
            promotion_review=_read_json(args.output_dir / "promotion_eligibility_review.json"),
            ahr_guidance_review=_read_json(args.output_dir / "ahr999_execution_guidance_review.json"),
            dataset_contract_path=args.output_dir / "paulwei_dataset_contract.json",
            dataset_contract_id=contract.get("artifact_id"),
            behavior_profile_path=args.output_dir / "behavior_profile.json",
            behavior_profile_id=behavior.get("artifact_id"),
            mapping_path=args.output_dir / "baseline_mapping.json",
            mapping_id=mapping.get("artifact_id"),
            ahr_guidance_path=args.output_dir / "ahr999_execution_guidance_review.json",
            comparison_run_path=comparison_payload_path,
            output_dir=args.output_dir,
        )

    print(
        json.dumps(
            {
                "contract": contract,
                "behavior": behavior,
                "mapping": mapping,
                "execution_research_review": execution_review,
                "ahr999_alignment": ahr999,
                "ahr999_execution_guidance_review": ahr_guidance,
                "variant_comparison_review": comparison_info,
                "promotion_eligibility_review": promotion_info,
                "strict_execution_learning_review": strict_learning_info,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
