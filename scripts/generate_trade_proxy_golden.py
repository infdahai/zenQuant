"""Generate a synthetic offline Python trade proxy reference fixture."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from zenquant.data.hyperliquid_public import fetch_recent_trades, recent_trades_to_buckets

OUTPUT = Path(__file__).resolve().parents[1] / "internal/research/testdata/trade_proxy_golden.json"


def build_fixture():
    rows = [
        {"side": "A", "px": "25000", "sz": "2", "time": 14400000, "tid": 1, "hash": None},
        {"side": "B", "px": "20000", "sz": "3", "time": 14399999, "tid": 2, "hash": "synthetic"},
        {"side": "B", "px": "25000", "sz": "2", "time": 14400001, "tid": 3},
        {"side": "A", "px": "1", "sz": "1", "time": 1, "tid": 4},
        {"side": "A", "px": "25000", "sz": "2", "time": 14400000, "tid": 1, "hash": None},
    ]
    with patch("zenquant.data.hyperliquid_public._post_info", return_value=rows):
        trades = fetch_recent_trades("btc")
    return {
        "rows": rows,
        "trades": [asdict(t) for t in trades],
        "buckets": [asdict(b) for b in recent_trades_to_buckets(trades)],
    }


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build_fixture(), indent=2, allow_nan=False) + "\n"
    if args.write:
        OUTPUT.write_text(content)
    elif OUTPUT.read_text() != content:
        raise SystemExit("Trade proxy fixture is stale")


if __name__ == "__main__":
    main()
