#!/usr/bin/env python3
"""Prepare the immutable 2026-08-24 BTC trend-research market tail."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
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
    payload = _request_json_with_retry(request)
    _validate_endpoint_payload(path, payload)
    return payload


def _request_json_with_retry(
    request: urllib.request.Request,
    *,
    urlopen=urllib.request.urlopen,
    sleep=time.sleep,
    max_attempts: int = 3,
) -> list[Any]:
    if not 0 < max_attempts <= 4:
        raise MarketTailError("max_attempts must be in [1, 4]")
    for attempt in range(1, max_attempts + 1):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read())
            if not isinstance(payload, list):
                raise MarketTailError("Binance REST response must be a list")
            return payload
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == max_attempts:
                raise MarketTailError(f"Binance REST HTTP failure: {exc.code}") from exc
        except TimeoutError as exc:
            if attempt == max_attempts:
                raise MarketTailError("Binance REST request exhausted retries") from exc
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason, TimeoutError):
                raise MarketTailError("Binance REST transport failure is not retryable") from exc
            if attempt == max_attempts:
                raise MarketTailError("Binance REST request exhausted retries") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MarketTailError("Binance REST response is not valid JSON") from exc
        sleep(0.25 * (2 ** (attempt - 1)))
    raise MarketTailError("Binance REST request exhausted retries")


def _validate_endpoint_payload(path: str, payload: list[Any]) -> None:
    if path == "/fapi/v1/klines":
        if any(not isinstance(row, list) or len(row) < 12 for row in payload):
            raise MarketTailError("Binance kline response contains a malformed element")
        return
    if path == "/fapi/v1/fundingRate":
        if any(
            not isinstance(row, dict)
            or row.get("symbol") != "BTCUSDT"
            or "fundingTime" not in row
            or "fundingRate" not in row
            for row in payload
        ):
            raise MarketTailError("Binance funding response contains a malformed element")
        return
    raise MarketTailError(f"unsupported Binance REST endpoint: {path}")


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
    repository_root = Path(__file__).resolve().parents[1]
    expected_paths = {
        "perpetual": repository_root / "data/raw/binance/klines/BTCUSDT/5m/data.parquet",
        "funding": repository_root / "data/raw/binance/funding/BTCUSDT/data.parquet",
    }
    supplied_paths = {"perpetual": args.perp_source, "funding": args.funding_source}
    for label, supplied in supplied_paths.items():
        if supplied.is_symlink() or supplied.resolve() != expected_paths[label].resolve():
            parser.error(f"{label} source must be the exact canonical BTCUSDT Binance path")
    try:
        prepare_market_tail(
            MarketTailInputs(
                perp_source_path=args.perp_source,
                funding_source_path=args.funding_source,
                output_root=args.output_root,
                start_ns=REUSED_START_NS,
                end_ns=REUSED_END_NS,
                repository_root=repository_root,
            ),
            fetch_perp_page=_fetch_perp,
            fetch_funding_page=_fetch_funding,
        )
    except MarketTailError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
