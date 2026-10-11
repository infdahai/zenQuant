#!/usr/bin/env python3
"""Collect public Hyperliquid whale-position snapshots from a watchlist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zenquant.data.hyperliquid_whales import (
    collect_and_write_hyperliquid_whales,
    load_whale_watchlist,
    parse_account_spec,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Hyperliquid public whale-position snapshots")
    parser.add_argument("--watchlist", type=Path, default=Path("config/hyperliquid_whales.toml"))
    parser.add_argument("--account", action="append", default=None, help="NAME=0x... or 0x...; may be repeated")
    parser.add_argument("--output", type=Path, default=Path("data/raw/hyperliquid/whale_positions"))
    parser.add_argument("--coin", action="append", default=None, help="Optional coin filter, e.g. BTC")
    parser.add_argument("--dex", default=None)
    parser.add_argument("--min-abs-position-value-usd", type=float, default=100_000.0)
    args = parser.parse_args()

    accounts = load_whale_watchlist(args.watchlist)
    accounts.extend(parse_account_spec(value) for value in args.account or [])
    coins = {coin.upper() for coin in args.coin} if args.coin else None
    payload = collect_and_write_hyperliquid_whales(
        accounts=accounts,
        root=args.output,
        min_abs_position_value_usd=args.min_abs_position_value_usd,
        coins=coins,
        dex=args.dex,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
