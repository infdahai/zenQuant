#!/usr/bin/env python3
"""Generate synthetic replay preparation references; never reads market data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zenquant.historical.replay import (  # noqa: E402
    _aggregate_five_min_rows_to_one_hour,
    _filter_rows_by_time,
    _timeframe_priority,
)

OUTPUT = ROOT / "internal/research/testdata/replay_events_golden.json"
STEP = 300_000_000_000


def candle(tf: str, start: int, close: int, price: int = 100) -> dict:
    return {
        "symbol": "BTC",
        "timeframe": tf,
        "open_time_ns": start,
        "close_time_ns": close,
        "open": price,
        "high": price + 2,
        "low": price - 1,
        "close": price + 1,
        "volume": 0.5,
        "trade_count": 2,
    }


def case(name: str, rows: dict, timeframes: list, execution: str, start: int | None, end: int | None) -> dict:
    filtered = {
        tf: _filter_rows_by_time(rows.get(tf, []), column="close_time_ns", start_ns=start, end_ns=end)
        for tf in timeframes
    }
    if "1h" in timeframes and not filtered["1h"] and execution == "5m":
        filtered["1h"] = _aggregate_five_min_rows_to_one_hour(filtered["5m"])
    events = [{**row, "event_timeframe": tf} for tf in timeframes for row in filtered[tf]]
    events.sort(key=lambda row: (row["close_time_ns"], _timeframe_priority(row["event_timeframe"])))
    return {
        "name": name,
        "rows": rows,
        "timeframes": timeframes,
        "execution": execution,
        "start_ns": start,
        "end_ns": end,
        "events": events,
    }


def build() -> dict:
    hour = [candle("5m", i * STEP, (i + 1) * STEP - 1, 100 + i) for i in range(12)]
    next_hour = [candle("5m", (12 + i) * STEP, (13 + i) * STEP - 1, 120 + i) for i in range(12)]
    tie = hour[-1]["close_time_ns"]
    higher = {tf: [candle(tf, 0, duration - 1)] for tf, duration in (("1d", 24 * 12 * STEP), ("4h", 4 * 12 * STEP))}
    tied = {
        tf: [candle(tf, -duration, -1)]
        for tf, duration in (("1d", 24 * 12 * STEP), ("4h", 4 * 12 * STEP), ("1h", 12 * STEP), ("5m", STEP))
    }
    exclusive = [candle("5m", i * STEP, (i + 1) * STEP, 100 + i) for i in range(12)]
    inclusive_ms = [candle("5m", i * STEP, (i + 1) * STEP - 1_000_000, 100 + i) for i in range(12)]
    for row, volume in zip(exclusive, [1e16, *([1.0] * 11)], strict=True):
        row["volume"] = volume
    return {
        "cases": [
            case("exclusive_close_compensated_volume", {"5m": exclusive}, ["1h", "5m"], "5m", None, 12 * STEP),
            case("all_timeframes_tie", tied, ["5m", "1h", "4h", "1d"], "5m", None, None),
            case("inclusive_millisecond_close", {"5m": inclusive_ms}, ["1h", "5m"], "5m", None, 12 * STEP - 1_000_000),
            case(
                "fallback_and_ties", {**higher, "5m": list(reversed(hour))}, ["5m", "1h", "4h", "1d"], "5m", None, None
            ),
            case(
                "inclusive_bounds_partial_hour",
                {"5m": hour + next_hour},
                ["1h", "5m"],
                "5m",
                hour[1]["close_time_ns"],
                next_hour[-1]["close_time_ns"],
            ),
            case(
                "duplicate_and_missing", {"5m": hour[:-1] + [hour[0]] + next_hour[:-1]}, ["1h", "5m"], "5m", None, None
            ),
            case(
                "native_suppresses_fallback",
                {"5m": hour + next_hour, "1h": [candle("1h", 0, tie)]},
                ["1h", "5m"],
                "5m",
                None,
                None,
            ),
            case(
                "native_outside_bounds_falls_back",
                {"5m": hour, "1h": [candle("1h", 0, tie + 1)]},
                ["1h", "5m"],
                "5m",
                None,
                tie,
            ),
            case("no_fallback_for_4h_execution", {"1h": [], "4h": higher["4h"]}, ["1h", "4h"], "4h", None, None),
            case(
                "negative_epoch",
                {"5m": [candle("5m", (i - 12) * STEP, (i - 11) * STEP - 1, 100 + i) for i in range(12)]},
                ["1h", "5m"],
                "5m",
                None,
                None,
            ),
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2) + "\n"
    if args.write:
        OUTPUT.write_text(content)
    elif args.check:
        if OUTPUT.read_text() != content:
            raise SystemExit("replay events golden fixture differs")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
