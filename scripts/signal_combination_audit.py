#!/usr/bin/env python3
"""Run the research-only signal independence audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.research.signal_combination import generate_signal_combination_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal independence audit")
    parser.add_argument("--root", default="data/raw/binance")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-dir", default="results/signal_combination")
    parser.add_argument("--profile-path", default="config/profiles/default.toml")
    args = parser.parse_args()

    summary = generate_signal_combination_audit(
        data_root=Path(args.root),
        symbol=args.symbol,
        output_dir=Path(args.output_dir),
        profile_path=Path(args.profile_path),
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
