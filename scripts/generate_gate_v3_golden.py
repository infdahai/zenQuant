#!/usr/bin/env python3
"""Generate the Gate v3 Python-reference fixture from synthetic episodes only.

Run with Python 3.12 or newer and --write to replace the checked-in fixture,
or --check to verify it without writing.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zenquant.domain import PositionEpisode  # noqa: E402
from zenquant.research.gate_v3 import evaluate_gate_v3  # noqa: E402

OUTPUT = ROOT / "internal/research/testdata/gate_v3_golden.json"


def episode(
    name: str, time: int, pnl: float, *, symbol: str = "BTCUSDT", side: int = 1, closed: bool = True
) -> PositionEpisode:
    return PositionEpisode(
        episode_id=name,
        symbol=symbol,
        side=side,
        opened_at_ns=time,
        closed_at_ns=time + 1 if closed else None,
        entry_price=100.0,
        exit_price=100.0 if closed else None,
        max_abs_qty=1.0,
        remaining_qty=0.0 if closed else 1.0,
        gross_realized_pnl=pnl,
        fees=0.0,
        funding_pnl=0.0,
        net_pnl=pnl,
        mfe_pnl=max(0.0, pnl),
        mae_pnl=min(0.0, pnl),
        unrealized_pnl=0.0,
        fill_count=2 if closed else 1,
        closed=closed,
    )


def build_fixture() -> dict:
    mixed = [episode("win", 10, 10.0), episode("loss", 20, -5.0, side=-1)]
    cases = [
        (
            "normal_boundary",
            mixed,
            {"switches_per_day": 1.0, "fill_profit_factor": 0.1, "action_profit_factor": {"reduce": 0.2, "add": 3.0}},
        ),
        ("soft_lower", mixed, {"switches_per_day": 1.000001}),
        ("soft_upper", mixed, {"switches_per_day": 2.0}),
        ("hard_normal_limit", mixed, {"switches_per_day": 2.000001}),
        ("hard_five_boundary", mixed, {"switches_per_day": 5.0}),
        ("hard_absolute_limit", mixed, {"switches_per_day": 5.000001, "policy": "research"}),
        ("below_minimum", [episode("win", 10, 4.0), episode("loss", 20, -5.0)], {"switches_per_day": 0.0}),
        ("no_loss_one", [episode("win", 10, 5.0)], {"switches_per_day": 0.0}),
        ("no_loss_two", [episode("win", 10, 5.0), episode("flat", 20, 0.0)], {"switches_per_day": 0.0}),
        ("no_closed", [episode("open", 10, 1000.0, closed=False)], {"switches_per_day": 0.0}),
        ("zero_profit", [episode("flat", 10, 0.0)], {"switches_per_day": 0.0}),
        (
            "compensated_float_sum",
            [
                episode(f"sum-{index}", index * 10, pnl)
                for index, pnl in enumerate([1e16, 1.0, 1.0, -10000000000000002.0], start=1)
            ],
            {"switches_per_day": 0.0},
        ),
        (
            "multi_symbol_stable_order",
            [
                episode("btc-later", 30, 1.0, side=-1),
                episode("eth-z", 10, 1.0, symbol="ETHUSDT", side=1),
                episode("btc-first", 10, 1.0, side=1),
                episode("eth-a", 10, 1.0, symbol="ETHUSDT", side=-1),
                episode("eth-m", 10, 1.0, symbol="ETHUSDT", side=1),
                episode("btc-same", 20, 1.0, side=1, closed=False),
            ],
            {"elapsed_days": 1.0},
        ),
        ("invalid_side", [episode("invalid", 10, 1.0, side=0)], {"elapsed_days": 1.0}),
        ("negative_cadence", mixed, {"switches_per_day": -1.0}),
        ("negative_minimum", mixed, {"switches_per_day": 0.0, "min_episode_profit_factor": -1.0}),
        ("missing_elapsed", mixed, {}),
        ("zero_elapsed", mixed, {"elapsed_days": 0.0}),
        ("negative_elapsed", mixed, {"elapsed_days": -1.0}),
    ]
    output = []
    for name, episodes, options in cases:
        try:
            expected = {"result": evaluate_gate_v3(episodes, **options).to_dict()}
        except ValueError as exc:
            expected = {"error": {"type": type(exc).__name__, "message": str(exc)}}
        output.append(
            {
                "name": name,
                "input": {"episodes": [asdict(item) for item in episodes], "options": options},
                "expected": expected,
            }
        )
    return {"version": 1, "source": "zenquant.research.gate_v3.evaluate_gate_v3", "cases": output}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="explicitly write the golden fixture")
    mode.add_argument("--check", action="store_true", help="check fixture freshness without writing")
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or newer is required for reference floating-point summation")
    content = json.dumps(build_fixture(), indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != content.encode("utf-8"):
            parser.exit(1, f"Fixture missing or stale: {OUTPUT.relative_to(ROOT)}\n")
        print(f"Fixture is current: {OUTPUT.relative_to(ROOT)}")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
