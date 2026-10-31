#!/usr/bin/env python3
"""Generate synthetic Python episode-ledger reference data for Go parity tests.

Run with --write to regenerate or --check to compare without writing. No market
or training data is read. See the fixture's schema field for the wire contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zenquant.domain import ExecutionReport  # noqa: E402
from zenquant.research.episode_ledger import (  # noqa: E402
    FundingAllocation,
    PositionEpisodeLedger,
    PriceObservation,
    build_position_episodes,
)

OUTPUT = ROOT / "internal/research/testdata/episode_ledger_golden.json"


def fill(time: int, side: int, qty: float, price: float, fee: float = 0.0, *, symbol: str = "BTC") -> dict:
    return {
        "type": "fill",
        "client_order_id": f"order-{symbol}-{time}",
        "symbol": symbol,
        "side": side,
        "status": "FILLED",
        "filled_qty": qty,
        "avg_price": price,
        "fee": fee,
        "transaction_time_ns": time,
    }


def funding(time: int, pnl: float, symbol: str = "BTC") -> dict:
    return {"type": "funding", "symbol": symbol, "timestamp_ns": time, "pnl": pnl}


def price(time: int, high: float, low: float, close: float, symbol: str = "BTC") -> dict:
    return {"type": "price", "symbol": symbol, "timestamp_ns": time, "high": high, "low": low, "close": close}


def event_object(event: dict) -> ExecutionReport | FundingAllocation | PriceObservation:
    fields = {key: value for key, value in event.items() if key != "type"}
    return {"fill": ExecutionReport, "funding": FundingAllocation, "price": PriceObservation}[event["type"]](**fields)


def snapshot(ledger: PositionEpisodeLedger, symbols: list[str]) -> dict:
    return {
        "episodes": [asdict(item) for item in ledger.episodes],
        "open_episodes": [asdict(item) for item in ledger.open_episodes],
        "closed_episodes": [asdict(item) for item in ledger.closed_episodes],
        "position_qty": {symbol: ledger.position_qty(symbol) for symbol in symbols},
        "position_switch_count": ledger.position_switch_count,
    }


def case(name: str, events: list[dict]) -> dict:
    ledger = PositionEpisodeLedger()
    symbols = sorted({event["symbol"] for event in events} | {"MISSING"})
    steps = []
    for event in events:
        item = event_object(event)
        try:
            if isinstance(item, ExecutionReport):
                ledger.on_fill(item)
            elif isinstance(item, FundingAllocation):
                ledger.allocate_funding(item)
            else:
                ledger.observe_price(item)
            error = None
        except ValueError as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
        steps.append({"event": event, "error": error, "state": snapshot(ledger, symbols)})
    return {"name": name, "steps": steps}


def build_fixture() -> dict:
    cases = [
        case(
            "adds_partial_funding_excursions",
            [
                funding(1, 9),
                price(2, 105, 95, 100),
                fill(3, 1, 1, 100, 1),
                fill(4, 1, 1, 120, 1),
                price(5, 140, 90, 125),
                funding(6, -2),
                fill(7, -1, 0.5, 130, 0.5),
                price(8, 135, 100, 108),
                fill(9, -1, 1.5, 125, 1.5),
                funding(10, 3),
            ],
        ),
        case(
            "flips_and_flat_reopens",
            [
                fill(1, 1, 2, 100, 2),
                fill(2, -1, 3, 90, 3),
                price(3, 95, 75, 80),
                funding(4, 1.5),
                fill(5, 1, 1, 80, 1),
                fill(6, -1, 1, 85),
                fill(7, 1, 1, 90),
                fill(8, 1, 1, 91),
            ],
        ),
        case(
            "same_timestamp_order_and_symbols",
            [
                fill(10, 1, 1, 100),
                fill(10, -1, 2, 50, symbol="ETH"),
                price(10, 110, 90, 105),
                funding(10, -1),
                fill(10, -1, 2, 110, 2),
                funding(10, 2),
                price(10, 120, 100, 105),
                fill(10, 1, 1, 100),
                fill(1, 1, 1, 40, symbol="OTHER"),
            ],
        ),
        case(
            "dust_and_ignored_fills",
            [
                fill(10, 1, 0.3, 100),
                fill(11, -1, 0.1 + 0.2, 101),
                fill(1, 0, 0, 0),
                fill(12, -1, 1, 100),
            ],
        ),
        case(
            "errors_and_recovery",
            [
                fill(5, 1, 1, 100),
                fill(6, 0, 1, 100),
                price(6, 90, 100, 95),
                price(6, 100, 0, 95),
                price(6, 100, 90, 0),
                funding(4, 1),
                price(4, 110, 90, 100),
                fill(4, -1, 1, 100),
                funding(6, -1),
                fill(6, -1, 1, 110),
            ],
        ),
    ]
    # Separate streams are sorted by (timestamp, type priority), with stable order
    # within each stream. This differs from direct ledger call order.
    streams = {
        "fills": [fill(10, 1, 1, 100), fill(20, -1, 2, 110, 2)],
        "funding": [funding(10, -5), funding(20, -2)],
        "prices": [price(10, 120, 90, 100), price(20, 130, 80, 105)],
    }
    built = build_position_episodes(
        [event_object(e) for e in streams["fills"]],
        funding_allocations=[event_object(e) for e in streams["funding"]],
        price_observations=[event_object(e) for e in streams["prices"]],
    )
    return {
        "version": 1,
        "source": "zenquant.research.episode_ledger.PositionEpisodeLedger",
        "schema": {
            "cases": "Array of independent ledgers. Replay steps in array order, including after errors.",
            "step": "event has type fill, funding or price and remaining fields map to the Python input dataclass; error is null or {type,message}; state is the post-call snapshot.",
            "state": "episodes, open_episodes and closed_episodes are ordered arrays of all PositionEpisode dataclass fields; position_qty maps symbol to signed number; position_switch_count is an integer.",
            "numbers": "Timestamps and durations are integer nanoseconds; other quantities are JSON numbers interpreted as IEEE-754 binary64. Nullable episode fields remain null.",
            "builder": "Separate input streams and expected episodes for build_position_episodes; equal timestamps order price, funding, fill, stable within each stream.",
        },
        "cases": cases,
        "builder": {"input": streams, "expected_episodes": [asdict(item) for item in built]},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = (json.dumps(build_fixture(), indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != content:
            parser.exit(1, f"Fixture missing or stale: {OUTPUT.relative_to(ROOT)}\n")
        print(f"Fixture is current: {OUTPUT.relative_to(ROOT)}")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(content)
        print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
