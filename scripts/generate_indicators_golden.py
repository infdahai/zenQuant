"""Generate synthetic offline indicator reference cases from the Python engine."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from zenquant.domain.models import Timeframe
from zenquant.indicator.engine import _atr, _ou_kappa, _rate_of_change, _realized_vol

OUTPUT = Path(__file__).resolve().parents[1] / "internal/research/testdata/indicators_golden.json"


def build_fixture():
    series = {
        "empty": [],
        "single": [(105, 95, 100)],
        "varying": [(102, 98, 100), (110, 101, 108), (109, 90, 92), (105, 89, 103), (107, 101, 105)],
        "invalid_closes": [(3, 1, 2), (4, 2, 3), (4, 0, 0), (5, 1, 4), (7, 3, 6), (8, 4, -1), (9, 5, 8)],
        "flat": [(10, 10, 10)] * 4,
    }
    cases = []
    for name, rows in series.items():
        candles = [SimpleNamespace(high=high, low=low, close=close) for high, low, close in rows]
        for window in (1, 2, 4, 10):
            for timeframe in ("5m", "1h", "4h", "1d", "unknown"):
                tf = Timeframe(timeframe) if timeframe != "unknown" else timeframe
                cases.append(
                    {
                        "name": name,
                        "candles": [vars(c) for c in candles],
                        "window": window,
                        "timeframe": timeframe,
                        "atr": _atr(candles, window),
                        "vol": _realized_vol(candles, window, tf),
                        "roc": _rate_of_change(candles, window),
                        "kappa": _ou_kappa(candles, window),
                    }
                )
    return cases


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
        raise SystemExit("Indicators fixture is stale")


if __name__ == "__main__":
    main()
