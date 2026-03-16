#!/usr/bin/env python3
"""Monitor progress of optimization tasks.

Usage:
    uv run python scripts/monitor_optimization.py
"""

import json
import time
from pathlib import Path
from datetime import datetime


def check_param_sweep_progress():
    """Check parameter sweep progress."""
    result_file = Path("results/binance_full_sweep_540d.json")

    if not result_file.exists():
        return "Not started", 0, 243

    try:
        with open(result_file) as f:
            data = json.load(f)
            completed = len(data.get("results", []))
            total = 243  # Full sweep
            return "Running", completed, total
    except Exception:
        return "Error reading results", 0, 243


def check_deribit_download():
    """Check Deribit download progress."""
    klines_file = Path("data/raw/deribit/klines/BTC-PERPETUAL/klines_1h.json")
    funding_file = Path("data/raw/deribit/funding/BTC-PERPETUAL/funding_history.json")

    status = []
    if klines_file.exists():
        size_mb = klines_file.stat().st_size / 1024 / 1024
        status.append(f"Klines: {size_mb:.1f} MB")
    else:
        status.append("Klines: Not started")

    if funding_file.exists():
        size_mb = funding_file.stat().st_size / 1024 / 1024
        status.append(f"Funding: {size_mb:.1f} MB")
    else:
        status.append("Funding: Not started")

    return ", ".join(status)


def main():
    """Monitor all optimization tasks."""
    print("=" * 60)
    print("Optimization Progress Monitor")
    print("=" * 60)
    print()

    while True:
        # Clear screen (optional)
        # print("\033[2J\033[H")

        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()

        # Check parameter sweep
        status, completed, total = check_param_sweep_progress()
        pct = (completed / total * 100) if total > 0 else 0
        print(f"Parameter Sweep (Binance 540d):")
        print(f"  Status: {status}")
        print(f"  Progress: {completed}/{total} ({pct:.1f}%)")

        if completed > 0:
            # Estimate time remaining
            # Assume ~30 seconds per combination
            remaining = (total - completed) * 30
            remaining_min = remaining / 60
            print(f"  Estimated time remaining: {remaining_min:.1f} minutes")

        print()

        # Check Deribit download
        deribit_status = check_deribit_download()
        print(f"Deribit Download:")
        print(f"  Status: {deribit_status}")
        print()

        # Check if all tasks complete
        if completed >= total and "Not started" not in deribit_status:
            print("✅ All tasks complete!")
            print()
            print("Next steps:")
            print("  1. Convert Deribit data: uv run python scripts/convert_deribit_to_parquet.py")
            print("  2. Compare results: uv run python scripts/compare_results.py results/binance_full_sweep_540d.json")
            print("  3. Run walk-forward validation: uv run python scripts/walk_forward_tune.py --full")
            break

        print("Press Ctrl+C to exit")
        print("-" * 60)

        time.sleep(10)  # Update every 10 seconds


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")
