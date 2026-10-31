#!/usr/bin/env python3
"""Generate deterministic synthetic final-sizing results from RiskOverlay."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zenquant.domain import TargetPosition, Timeframe  # noqa: E402
from zenquant.infra import InMemoryJetStream  # noqa: E402
from zenquant.risk import RiskOverlay, RiskOverlayConfig  # noqa: E402

OUTPUT = ROOT / "internal/research/testdata/risk_target_golden.json"


def build():
    cases = []
    for name, qty, lev, position, equity, cap, price, reduce_only, reasons in [
        ("within_cap", 1.25, 2.0, 0.0, 100.0, 3.0, 100.0, False, ["Z", "A", "Z"]),
        ("long_cap", 8.0, 5.0, 0.0, 100.0, 2.0, 100.0, False, []),
        ("short_cap", -8.0, 5.0, 0.0, 100.0, 2.0, 100.0, False, []),
        ("long_add", 3.0, 2.0, 1.0, 100.0, 10.0, 100.0, True, ["Z"]),
        ("long_flip", -1.0, 2.0, 1.0, 100.0, 10.0, 100.0, True, []),
        ("short_add", -3.0, 2.0, -1.0, 100.0, 10.0, 100.0, True, []),
        ("short_flip", 1.0, 2.0, -1.0, 100.0, 10.0, 100.0, True, []),
        ("flat", 1.0, 2.0, 0.0, 100.0, 10.0, 100.0, True, []),
        ("reduction", 0.5, 2.0, 1.0, 100.0, 10.0, 100.0, True, []),
        ("cap_then_reduce", 3.0, 2.0, 2.0, 100.0, 1.0, 100.0, True, ["REDUCE_ONLY_BLOCK_ADD"]),
        ("round_above_limit", 2.0, 1.23445, 0.0, 1.2345676, 1.0, 1.0, False, []),
        ("round_binary_tie", 0.1234565, 1.23445, 0.0, 10.0, 2.0, 1.0, False, []),
        ("round_reduce_only", 0.0000006, 0.99996, 0.0000006, 1.0, 1.0, 1.0, True, []),
        ("round_leverage_cap", 0.1, 1.0, 0.0, 1.0, 0.99996, 1.0, False, []),
        ("drawdown_flag", 3.0, 2.0, 1.0, 100.0, 1.0, 100.0, True, ["Z", "DD_STEP_3"]),
        ("zero_equity", 2.0, 1.0, 0.0, 0.0, 1.0, 1.0, False, []),
    ]:
        flagged = name == "drawdown_flag"
        config = RiskOverlayConfig(max_leverage=cap / 0.2 if flagged else cap, kill_switch_drawdown=2.0)
        overlay = RiskOverlay(InMemoryJetStream(), config)
        overlay._cash = equity - position * price
        overlay._high_water_mark = equity / 0.7 if flagged else max(equity, 0.0)
        overlay._actual_positions["BTC"] = position
        overlay._last_price["BTC"] = price
        target = TargetPosition("id", "arc", "BTC", Timeframe.TF_4H, 1, qty, lev, reduce_only, "", 0.0, reasons)
        revised, decision, _ = overlay._apply_overlay(target)
        cases.append(
            {
                "name": name,
                "input": {
                    "target_qty": qty,
                    "target_leverage": lev,
                    "current_position": position,
                    "equity": equity,
                    "leverage_cap": cap,
                    "price": price,
                    "reduce_only": reduce_only,
                    "reason_codes": reasons,
                    "flags": ["DD_STEP_3"] if flagged else [],
                },
                "expected": {
                    "target_qty": revised.target_qty,
                    "target_leverage": revised.target_leverage,
                    "reduce_only": revised.reduce_only,
                    "reason_codes": revised.reason_codes,
                    "flags": decision.flags,
                },
            }
        )
    return {"version": 1, "cases": cases}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, allow_nan=False) + "\n"
    if args.write:
        OUTPUT.write_text(content)
    elif args.check:
        if OUTPUT.read_text() != content:
            raise SystemExit("risk target fixture differs")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
