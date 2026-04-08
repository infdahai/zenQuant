#!/usr/bin/env python3
"""Report Deribit collector freshness and readiness."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from zenquant.data.deribit_status import collect_deribit_status, write_deribit_status


def main() -> None:
    parser = argparse.ArgumentParser(description="Deribit collector status")
    parser.add_argument("--root", type=Path, default=Path("data/raw/deribit_options"))
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--write-status", action="store_true")
    args = parser.parse_args()

    status = collect_deribit_status(args.root, args.currency)
    if args.write_status:
        write_deribit_status(args.root, args.currency)
    print(json.dumps(asdict(status), indent=2))


if __name__ == "__main__":
    main()
