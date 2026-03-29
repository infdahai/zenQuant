#!/usr/bin/env python3
"""Unified harness gate runner — one command, all gates.

Usage:
    uv run zenquant-check          # run all gates
    uv run zenquant-check --gate a # run only Gate A (ruff)
    uv run zenquant-check --gate b # run only Gate B (import boundaries)
    uv run zenquant-check --gate e # run only Gate E (pytest)
    uv run zenquant-check --quick  # gates A + E only (fastest)

Gate Chain:
    A: ruff lint + format check
    B: import boundary enforcement
    C: golden output regression
    D: plugin/legacy parity
    E: full test suite (pytest)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

GATES = ["a", "b", "c", "d", "e"]


def _run(cmd: list[str], label: str) -> bool:
    """Run a command and return True if it passed."""
    print(f"\n{'=' * 60}")
    print(f"  Gate {label}")
    print(f"{'=' * 60}")
    start = time.time()
    result = subprocess.run(cmd, cwd=_project_root())
    elapsed = time.time() - start
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"  [{status}] {label} ({elapsed:.1f}s)")
    return result.returncode == 0


def _project_root() -> str:
    from pathlib import Path

    return str(Path(__file__).parent.parent.parent)


def gate_a() -> bool:
    """Gate A: ruff lint + format check."""
    ok = _run(
        [sys.executable, "-m", "ruff", "check", "zenquant/", "tests/", "scripts/"],
        "A: ruff lint",
    )
    ok2 = _run(
        [sys.executable, "-m", "ruff", "format", "--check", "zenquant/", "tests/", "scripts/"],
        "A: ruff format",
    )
    return ok and ok2


def gate_b() -> bool:
    """Gate B: import boundary enforcement."""
    return _run(
        [sys.executable, "-m", "pytest", "tests/test_import_boundaries.py", "-v", "--tb=short"],
        "B: import boundaries",
    )


def gate_c() -> bool:
    """Gate C: golden output regression."""
    return _run(
        [sys.executable, "-m", "pytest", "tests/test_golden_output.py", "-v", "--tb=short"],
        "C: golden output",
    )


def gate_d() -> bool:
    """Gate D: plugin/legacy parity."""
    return _run(
        [sys.executable, "-m", "pytest", "tests/test_parity.py", "-v", "--tb=short"],
        "D: plugin/legacy parity",
    )


def gate_e() -> bool:
    """Gate E: full test suite."""
    return _run(
        [sys.executable, "-m", "pytest", "--tb=short", "-q"],
        "E: full test suite",
    )


GATE_MAP = {
    "a": gate_a,
    "b": gate_b,
    "c": gate_c,
    "d": gate_d,
    "e": gate_e,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="zenQuant harness gate runner")
    parser.add_argument("--gate", choices=GATES, help="Run a specific gate only")
    parser.add_argument("--quick", action="store_true", help="Run gates A + E only")
    args = parser.parse_args()

    start = time.time()

    if args.gate:
        gates_to_run = [args.gate]
    elif args.quick:
        gates_to_run = ["a", "e"]
    else:
        gates_to_run = list(GATES)

    results: dict[str, bool] = {}
    for gate in gates_to_run:
        results[gate] = GATE_MAP[gate]()

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"  HARNESS SUMMARY ({elapsed:.1f}s)")
    print(f"{'=' * 60}")
    for gate, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  Gate {gate.upper()}: {status}")

    failed = [g for g, ok in results.items() if not ok]
    if failed:
        print(f"\n  BLOCKED: {len(failed)} gate(s) failed")
        sys.exit(1)
    else:
        print("\n  ALL GATES PASSED")


if __name__ == "__main__":
    main()
