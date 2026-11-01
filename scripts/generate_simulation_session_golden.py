#!/usr/bin/env python3
"""Capture synthetic PR55 session traces; never used by the Go runtime."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zenquant.domain import ExecutionOrder  # noqa: E402
from zenquant.execution.gateway import ExecutionGateway  # noqa: E402
from zenquant.execution.session import SimulationSession  # noqa: E402
from zenquant.infra import InMemoryJetStream  # noqa: E402

OUTPUT = ROOT / "internal/research/testdata/simulation_session_golden.json"


def build() -> dict:
    cases = []
    # Each tuple supplies order changes and simulator-result changes. Patching
    # the built-in gateway is only a fault/partial-fill test seam, not an adapter.
    scenarios = {
        "full_round_trip": [({}, {}), ({"side": -1}, {})],
        "partial_then_remaining_new_id": [
            ({}, {"status": "PARTIALLY_FILLED", "filled_qty": 0.4, "fee": 0.016}),
            ({"quantity": 0.6}, {}),
        ],
        "partial_duplicate_is_terminal": [
            ({"client_order_id": "same"}, {"status": "PARTIALLY_FILLED", "filled_qty": 0.4}),
            ({"client_order_id": "same", "quantity": 0.6}, {}),
            ({}, {}),
        ],
        "rejected_and_pending_end_attempt": [
            ({"quantity": 0.0}, {}),
            ({}, {"status": "PENDING", "filled_qty": 0.0, "fee": 0.0}),
            ({}, {}),
        ],
        "rejected_identity_cannot_retry": [
            ({"client_order_id": "same", "quantity": 0.0}, {}),
            ({"client_order_id": "same"}, {}),
        ],
        "empty_identity": [({"client_order_id": ""}, {}), ({}, {})],
        "negative_quantity_report_rejected": [({"quantity": -1.0}, {}), ({}, {})],
    }
    for field, value in [
        ("client_order_id", "wrong"),
        ("symbol", "OTHER"),
        ("side", -1),
        ("status", "UNKNOWN"),
        ("filled_qty", -1.0),
        ("filled_qty", 2.0),
        ("avg_price", 0.0),
        ("avg_price", -1.0),
        ("fee", -1.0),
        ("status", "REJECTED"),
        ("status", "PENDING"),
    ]:
        scenarios[f"invalid_{field}_{value}"] = [({}, {field: value}), ({}, {})]
    for name, attempts in scenarios.items():
        bus = InMemoryJetStream()
        gateway = ExecutionGateway()
        original = gateway.submit_order
        session = SimulationSession(bus, gateway)
        submitted, published, steps = [], [], []
        bus.subscribe("exec.order.submit", lambda _, order, output=submitted: output.append(asdict(order)))
        bus.subscribe("exec.order.report", lambda _, report, output=published: output.append(asdict(report)))
        for index, (order_changes, report_changes) in enumerate(attempts):
            order = replace(ExecutionOrder(f"attempt-{index}", "BTC", 1, 1.0, False, 8.0), **order_changes)
            report = replace(original(order, mark_price=100.0, now_ns=index + 1), **report_changes)
            gateway.submit_order = lambda *args, result=report, **kwargs: result
            failed = False
            try:
                session.submit(order, mark_price=100.0, now_ns=index + 1)
            except (ValueError, RuntimeError):
                failed = True
            steps.append(
                {
                    "order": asdict(order),
                    "simulator_report": asdict(report),
                    "failed": failed,
                    "blocked": session.blocked,
                }
            )
        cases.append({"name": name, "steps": steps, "submitted": submitted, "published": published})
    return {"reference": "Python SimulationSession at e449310 (PR55); synthetic inputs only", "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, allow_nan=False) + "\n"
    if args.write:
        OUTPUT.write_text(content)
    elif args.check:
        if OUTPUT.read_text() != content:
            raise SystemExit("simulation session golden fixture differs")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
