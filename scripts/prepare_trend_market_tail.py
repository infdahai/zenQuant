#!/usr/bin/env python3
"""Prepare the immutable 2026-08-24 BTC trend-research market tail."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from zenquant.data.trend_market_tail import MarketTailError, MarketTailInputs, prepare_market_tail
from zenquant.research.registration import REUSED_END_NS, REUSED_START_NS

FIXED_START: Final = "2026-01-01T00:00:00Z"
FIXED_END: Final = "2026-08-24T00:00:00Z"
_BASE_URL: Final = "https://fapi.binance.com"


def _fetch(path: str, params: dict[str, int | str]) -> list[Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{_BASE_URL}{path}?{query}", headers={"User-Agent": "zenquant/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, list):
        raise MarketTailError("Binance REST response must be a list")
    return payload


def _fetch_perp(start_ms: int, end_ms: int, limit: int) -> list[Any]:
    return _fetch(
        "/fapi/v1/klines",
        {
            "symbol": "BTCUSDT",
            "interval": "5m",
            "startTime": start_ms,
            "endTime": end_ms - 1,
            "limit": limit,
        },
    )


def _fetch_funding(start_ms: int, end_ms: int, limit: int) -> list[Any]:
    return _fetch(
        "/fapi/v1/fundingRate",
        {
            "symbol": "BTCUSDT",
            "startTime": start_ms,
            "endTime": end_ms - 1,
            "limit": limit,
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--perp-source", required=True, type=Path)
    parser.add_argument("--funding-source", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.start != FIXED_START:
        parser.error(f"fixed start must be {FIXED_START}")
    if args.end != FIXED_END:
        parser.error(f"fixed end must be {FIXED_END}")
    try:
        prepare_market_tail(
            MarketTailInputs(
                perp_source_path=args.perp_source,
                funding_source_path=args.funding_source,
                output_root=args.output_root,
                start_ns=REUSED_START_NS,
                end_ns=REUSED_END_NS,
                repository_root=Path(__file__).resolve().parents[1],
            ),
            fetch_perp_page=_fetch_perp,
            fetch_funding_page=_fetch_funding,
        )
    except MarketTailError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
