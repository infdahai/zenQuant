"""Generate synthetic in-memory Python basis lookup reference data."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from zenquant.data._z_utils import robust_z
from zenquant.data.basis_store import BasisDataStore, BasisStoreConfig

OUTPUT = Path(__file__).resolve().parents[1] / "internal/research/testdata/basis_golden.json"


def build_fixture():
    spot = [
        {"time_ns": t, "price": p}
        for t, p in [
            (30, 100),
            (10, 100),
            (20, 100),
            (20, 200),
            (40, 100),
            (50, 100),
            (60, 100),
            (70, 0),
            (80, 100),
            (90, 100),
        ]
    ]
    perp = [
        {"time_ns": t, "price": p}
        for t, p in [
            (10, 101),
            (20, 210),
            (30, 103),
            (40, 104),
            (50, 120),
            (60, 80),
            (70, 101),
            (80, 100.00005),
            (90, 100.00015),
            (99, 100),
        ]
    ]
    window, minimum = 5, 2
    store = BasisDataStore(BasisStoreConfig(z_window=window, robust_z_min_samples=minimum))
    spots = {row["time_ns"]: row["price"] for row in spot}
    perps = {row["time_ns"]: row["price"] for row in perp}
    for t in sorted(spots.keys() & perps.keys()):
        if spots[t] <= 0:
            continue
        store._timestamps.append(t)
        store._spot_prices.append(spots[t])
        store._perp_prices.append(perps[t])
        store._basis_pct.append((perps[t] - spots[t]) / spots[t])
    cutoffs = [0, 10, 19, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    return {
        "spot": spot,
        "perp": perp,
        "window": window,
        "min_samples": minimum,
        "lookups": [{"cutoff_ns": t, "snapshot": asdict(store.lookup(t))} for t in cutoffs],
        "scores": [
            {
                "values": [-1e-8, 0, 1e-8, current],
                "current": current,
                "min_samples": 3,
                "expected": robust_z([-1e-8, 0, 1e-8, current], current, 3),
            }
            for current in (1e308, -1e308)
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build_fixture(), indent=2, allow_nan=False) + "\n"
    if args.write:
        OUTPUT.write_text(content)
    elif OUTPUT.read_text() != content:
        raise SystemExit("Basis fixture is stale")


if __name__ == "__main__":
    main()
