#!/usr/bin/env python3
"""Anti-snooping validator — detect hardcoded Dev/Test set features.

This script checks strategy code for suspicious patterns that indicate
overfitting to specific dates, thresholds, or "magic numbers" discovered
during Dev set exploration.

Examples of VIOLATIONS:
    - if bar.timestamp > datetime(2024, 6, 15):  # Dev set date
    - if signal_strength > 0.467:  # Suspiciously precise threshold from ablation
    - BTCUSDT_specific_logic()  # Hardcoded symbol from backtest
    - if vix > 23.5 and dxy < 104.2:  # Exact Dev set conditions

Run as Gate 0 in harness to prevent snooping artifacts from landing in main.

Usage:
    uv run python scripts/validate_no_snooping.py
    uv run python scripts/validate_no_snooping.py --strict  # Fail on any suspicion
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

# Load data splits to know what's off-limits
SPLIT_PATH = Path("config/data_splits.toml")
with SPLIT_PATH.open("rb") as f:
    SPLITS = tomllib.load(f)["splits"]

# Patterns that indicate potential snooping
SUSPICIOUS_PATTERNS = [
    # Dev set dates (2024-2025)
    (r"202[45]-\d{2}-\d{2}", "Hardcoded Dev set date"),
    (r"datetime\(202[45],", "Hardcoded Dev set datetime"),
    # Test set dates (2026+)
    (r"2026-\d{2}-\d{2}", "Hardcoded Test set date"),
    (r"datetime\(2026,", "Hardcoded Test set datetime"),
    # Overly precise thresholds (3+ decimal places for probabilities/weights)
    (r"[><=]\s*0\.\d{3,}", "Suspiciously precise threshold (3+ decimals)"),
    (r"[><=]\s*\d+\.\d{3,}", "Suspiciously precise threshold"),
    # Specific Dev set discoveries from SNOOPING_LOG
    (r"0\.46[0-9]", "Macro ablation Sharpe delta (see SNOOPING_LOG 2026-04-04)"),
    (r"0\.27[0-9]", "CVD re-enable Sharpe delta"),
    (r"23\.[45]", "VIX threshold suspiciously close to Dev set mean"),
    (r"104\.[0-9]", "DXY threshold suspiciously close to Dev set mean"),
    # Symbol-specific hardcoding (should use config)
    (r"if.*symbol\s*==\s*['\"]BTCUSDT['\"]", "Hardcoded symbol check (use config)"),
]

# Patterns that are OK (escape hatch for false positives)
ALLOWLIST = [
    r"# SNOOPING_OK:",  # Explicit annotation
    r"test_.*\.py",  # Test files can reference dates
    r"SNOOPING_LOG\.md",  # The log itself
    r"data_splits\.toml",  # Config file
]


def check_file(path: Path, strict: bool = False) -> list[tuple[int, str, str]]:
    """Check a single file for snooping patterns.

    Returns: List of (line_number, pattern_description, line_content)
    """
    violations = []

    try:
        content = path.read_text()
    except Exception:
        return violations  # Skip binary/unreadable files

    # Check allowlist
    for allow_pattern in ALLOWLIST:
        if re.search(allow_pattern, str(path)):
            return violations

    lines = content.splitlines()
    for i, line in enumerate(lines, start=1):
        # Skip comments (unless strict mode — comments can reveal snooping)
        if not strict and line.strip().startswith("#"):
            continue

        # Skip lines with SNOOPING_OK annotation
        if "SNOOPING_OK:" in line:
            continue

        for pattern, description in SUSPICIOUS_PATTERNS:
            if re.search(pattern, line):
                violations.append((i, description, line.strip()))

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect data snooping artifacts")
    parser.add_argument("--strict", action="store_true", help="Fail on any suspicion")
    parser.add_argument("--path", default="zenquant", help="Path to check")
    args = parser.parse_args()

    print("=" * 70)
    print("Anti-Snooping Validator")
    print("=" * 70)
    print(f"Dev set:  {SPLITS['dev_start']} → {SPLITS['dev_end']}")
    print(f"Test set: {SPLITS['test_start']} → (secret)")
    print()

    search_path = Path(args.path)
    if not search_path.exists():
        print(f"ERROR: {search_path} not found")
        sys.exit(1)

    all_violations: dict[Path, list[tuple[int, str, str]]] = {}

    # Check all Python files in strategy/, research/, risk/
    for pattern in ["strategy/**/*.py", "research/**/*.py", "risk/**/*.py"]:
        for py_file in search_path.glob(pattern):
            violations = check_file(py_file, strict=args.strict)
            if violations:
                all_violations[py_file] = violations

    if not all_violations:
        print("✅ No snooping artifacts detected")
        print()
        print("Strategy code is clean — no hardcoded Dev/Test set features found.")
        sys.exit(0)

    # Report violations
    print("⚠️  POTENTIAL SNOOPING DETECTED")
    print()

    for path, violations in all_violations.items():
        print(f"📁 {path}")
        for line_num, description, line_content in violations:
            print(f"   Line {line_num}: {description}")
            print(f"      {line_content}")
        print()

    print("=" * 70)
    print("RECOMMENDATION:")
    print("  - If these are false positives, add # SNOOPING_OK: comment")
    print("  - If real: refactor to use config-driven thresholds")
    print("  - Check experiments/SNOOPING_LOG.md to see if these were tuned on Dev set")
    print()

    if args.strict:
        sys.exit(1)
    else:
        print("(Run with --strict to fail CI on suspicion)")
        sys.exit(0)


if __name__ == "__main__":
    main()
