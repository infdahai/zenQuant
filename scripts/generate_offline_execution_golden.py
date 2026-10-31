#!/usr/bin/env python3
"""Generate synthetic references from the Python mark-price gateway."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zenquant.domain import ExecutionOrder  # noqa: E402

# Avoid importing the execution package's unrelated database dependencies.
spec = importlib.util.spec_from_file_location("offline_gateway_reference", ROOT / "zenquant/execution/gateway.py")
assert spec is not None and spec.loader is not None
gateway = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gateway
spec.loader.exec_module(gateway)
ExecutionGateway = gateway.ExecutionGateway
ExecutionGatewayConfig = gateway.ExecutionGatewayConfig

OUTPUT = ROOT / "internal/research/testdata/offline_execution_golden.json"


def build() -> dict:
    cases = []
    for name, side, qty, mark, rate, timestamp, order_type, reduce_only in [
        ("buy", 1, 2.5, 100.0, 0.0004, 123, "MARKET", False),
        ("sell_custom_fee", -1, 0.25, 123.5, 0.001, 124, "MARKET", False),
        ("limit_and_reduce_ignored", -1, 1.0, 200.0, 0.0004, 125, "LIMIT", True),
        ("zero_fee", 1, 1.0, 90.0, 0.0, 126, "MARKET", False),
        ("zero_quantity", 1, 0.0, 100.0, 0.0004, 127, "MARKET", False),
        ("negative_quantity", -1, -1.0, 100.0, 0.0004, 128, "MARKET", False),
        ("zero_mark", 1, 1.0, 0.0, 0.0004, 129, "MARKET", False),
        ("negative_mark", -1, 1.0, -10.0, 0.0004, 130, "MARKET", False),
    ]:
        order = ExecutionOrder(
            name, "BTC", side, qty, reduce_only, 1.0, order_type, 50.0 if order_type == "LIMIT" else None
        )
        report = ExecutionGateway(ExecutionGatewayConfig(taker_fee_rate=rate)).submit_order(
            order, mark, best_bid=1.0, best_ask=2.0, now_ns=timestamp
        )
        cases.append(
            {
                "name": name,
                "order": asdict(order),
                "unsupported": reduce_only or order_type != "MARKET",
                "mark_price": mark,
                "taker_fee_rate": rate,
                "timestamp_ns": timestamp,
                "report": asdict(report),
            }
        )
    return {"cases": cases}


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
            raise SystemExit("offline execution golden fixture differs")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
