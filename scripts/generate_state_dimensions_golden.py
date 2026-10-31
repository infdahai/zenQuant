#!/usr/bin/env python3
"""Generate synthetic state-dimension and playbook parity cases offline."""

import argparse
import ast
import importlib
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
domain = importlib.import_module("zenquant.domain")
MarketState, Timeframe = domain.MarketState, domain.Timeframe

# Compile the exact classifier functions without importing database adapters.
source = ROOT / "zenquant/state/engine.py"
tree = ast.parse(source.read_text(), filename=str(source))
names = {"_classify_volatility_state", "_classify_positioning_state", "_classify_quality_state"}
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
if len(functions) != len(names):
    raise RuntimeError("state reference classifiers are missing")
namespace = {}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
_classify_volatility_state = namespace["_classify_volatility_state"]
_classify_positioning_state = namespace["_classify_positioning_state"]
_classify_quality_state = namespace["_classify_quality_state"]

spec = importlib.util.spec_from_file_location(
    "offline_playbook_reference", ROOT / "zenquant/strategy/playbook_engine.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
OUTPUT = ROOT / "internal/research/testdata/state_dimensions_golden.json"


def build_fixture():
    base = dict(realized_vol=0.8, vix_z=0.0, funding_oi_z=0.0, quality_flags=[])
    cases = [("base", {})]
    for field, values in {
        "realized_vol": [0.449999, 0.45, 1.5, 1.500001, 2.8, 2.800001],
        "vix_z": [2.0, 2.000001, 3.0, 3.000001],
        "funding_oi_z": [-2.1, -2.0, -1.8, -1.799999, -0.5, 0.5, 0.500001, 1.799999, 1.8, 1.9, 2.0, 2.1],
    }.items():
        cases.extend((f"{field}_{value}", {field: value}) for value in values)
    for flags in [
        ["OK"],
        ["OK", "OTHER"],
        ["MACRO_STALE"],
        ["BOOK_STALE"],
        ["DATA_STALE"],
        ["DATA_FUTURE"],
        ["VENUE_DOWN"],
        ["BOOK_STALE", "DATA_FUTURE", "MACRO_STALE"],
        ["OK", "OK"],
    ]:
        cases.append(("flags_" + "_".join(flags), {"quality_flags": flags}))
    cases.extend(
        [
            ("vol_priority", {"realized_vol": 2.9, "vix_z": 4}),
            ("vix_over_low", {"realized_vol": 0.1, "vix_z": 2.1}),
            ("bull_crowded", {"funding_oi_z": 1.8}),
            ("risk_rounding", {}),
            ("bear", {}),
        ]
    )
    result = []
    for name, changes in cases:
        data = base | changes
        directional = dict(
            symbol="SYNTHETIC",
            timeframe="4h",
            cutoff_time_ns=123,
            directional_state="UPTREND",
            directional_confidence=0.8,
            direction_signal=1.0,
        )
        if name == "risk_rounding":
            directional["directional_confidence"] = 0.59996
        if name == "bear":
            directional |= dict(directional_state="DOWNTREND", direction_signal=-1.0)
        vol, vc = _classify_volatility_state(
            quality_flags=data["quality_flags"], realized_vol=data["realized_vol"], vix_z=data["vix_z"]
        )
        pos, pc = _classify_positioning_state(funding_oi_z=data["funding_oi_z"])
        quality, qc = _classify_quality_state(quality_flags=data["quality_flags"])
        state = directional | dict(
            directional_confidence=round(directional["directional_confidence"], 4),
            volatility_state=vol,
            volatility_confidence=round(vc, 4),
            positioning_state=pos,
            positioning_confidence=round(pc, 4),
            quality_state=quality,
            quality_confidence=round(qc, 4),
            transition_risk=round(max(0.0, 1.0 - min(directional["directional_confidence"], vc, pc, qc)), 4),
        )
        market = MarketState(
            **(state | {"timeframe": Timeframe(state["timeframe"])}), regime="RANGE", regime_confidence=0.8
        )
        result.append(
            dict(
                name=name,
                input=data,
                directional=directional,
                expected=state,
                playbook=asdict(module.PlaybookEngine().select(market)),
            )
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    contents = json.dumps(build_fixture(), indent=2) + "\n"
    if args.check:
        if OUTPUT.read_text() != contents:
            raise SystemExit("state dimensions fixture differs; regenerate it")
    else:
        OUTPUT.write_text(contents)


if __name__ == "__main__":
    main()
