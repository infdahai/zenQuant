#!/usr/bin/env python3
"""Generate synthetic offline playbook parity cases from the Python reference."""

import argparse
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zenquant.domain import MarketState, Timeframe

# Load the reference directly; importing the strategy package also loads database adapters.
spec = importlib.util.spec_from_file_location(
    "offline_playbook_reference", Path(__file__).resolve().parents[1] / "zenquant/strategy/playbook_engine.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
PlaybookEngine = module.PlaybookEngine
PlaybookEngineConfig = module.PlaybookEngineConfig

OUTPUT = Path(__file__).resolve().parents[1] / "internal/research/testdata/playbook_golden.json"


def build_fixture():
    state_base = dict(
        symbol="SYNTHETIC",
        timeframe="4h",
        cutoff_time_ns=123,
        directional_state="UPTREND",
        directional_confidence=0.8,
        volatility_state="NORMAL",
        volatility_confidence=0.8,
        positioning_state="HEALTHY",
        positioning_confidence=0.8,
        quality_state="TRADABLE",
        quality_confidence=0.8,
        direction_signal=0.0,
        transition_risk=0.1,
    )
    cases = [
        ("bull", {}, {}),
        ("bear", {"directional_state": "DOWNTREND"}, {}),
        ("range", {"directional_state": "NEUTRAL"}, {}),
        ("transition_override", {"directional_state": "TRANSITION", "direction_signal": -0.5}, {}),
        ("transition_above_signal", {"directional_state": "TRANSITION", "direction_signal": -0.499999}, {}),
        (
            "transition_below_floor",
            {"directional_state": "TRANSITION", "direction_signal": -0.5, "directional_confidence": 0.499999},
            {},
        ),
        (
            "transition_at_floor",
            {"directional_state": "TRANSITION", "direction_signal": -0.5, "directional_confidence": 0.5},
            {},
        ),
        ("bull_below_floor", {"directional_confidence": 0.499999}, {}),
        ("bull_at_floor", {"directional_confidence": 0.5}, {}),
        ("bear_below_floor", {"directional_state": "DOWNTREND", "directional_confidence": 0.499999}, {}),
        ("bear_at_floor", {"directional_state": "DOWNTREND", "directional_confidence": 0.5}, {}),
        ("crowded_long", {"positioning_state": "CROWDED_LONG"}, {}),
        ("crowded_short", {"directional_state": "DOWNTREND", "positioning_state": "CROWDED_SHORT"}, {}),
        (
            "crowded_override",
            {"directional_state": "TRANSITION", "direction_signal": -1.0, "positioning_state": "CROWDED_SHORT"},
            {},
        ),
        ("noisy", {"quality_state": "NOISY"}, {}),
        ("unknown_quality", {"quality_state": "OTHER"}, {}),
        ("distorted_priority", {"volatility_state": "DISTORTED"}, {}),
        ("degraded_priority", {"quality_state": "DEGRADED"}, {}),
        ("unsafe_priority", {"quality_state": "UNSAFE"}, {}),
        ("transition_below", {"transition_risk": 0.599999}, {}),
        ("transition_at", {"transition_risk": 0.6}, {}),
        (
            "confidence_below",
            {
                "directional_confidence": 0.349999,
                "volatility_confidence": 0.349999,
                "positioning_confidence": 0.349999,
                "quality_confidence": 0.349999,
            },
            {},
        ),
        (
            "confidence_at",
            {
                "directional_confidence": 0.35,
                "volatility_confidence": 0.35,
                "positioning_confidence": 0.35,
                "quality_confidence": 0.35,
            },
            {},
        ),
        (
            "custom_bull",
            {"transition_risk": 0.7},
            {"preserve_capital_transition_risk": 0.8, "bull_max_leverage": 1.1, "bull_trailing_stop_atr_mult": 1.2},
        ),
        (
            "custom_bear",
            {"directional_state": "TRANSITION", "direction_signal": -0.2, "directional_confidence": 0.4},
            {
                "bearish_override_direction_signal": -0.2,
                "bearish_override_confidence_floor": 0.4,
                "bear_max_leverage": 0.7,
                "bear_trailing_stop_atr_mult": 1.1,
            },
        ),
        (
            "custom_range",
            {"directional_confidence": 0.8},
            {
                "trade_directional_confidence_floor": 0.9,
                "range_max_leverage": 0.6,
                "range_trailing_stop_atr_mult": 1.4,
                "range_time_decay_bars": 7,
            },
        ),
        (
            "custom_preserve",
            {},
            {
                "preserve_capital_min_overall_confidence": 0.9,
                "preserve_capital_max_leverage": 0.2,
                "preserve_capital_trailing_stop_atr_mult": 1.1,
                "preserve_capital_time_decay_bars": 9,
            },
        ),
        ("confidence_cap", {"directional_confidence": 2.0}, {}),
        ("rounding", {"directional_confidence": 0.81275}, {}),
        ("reason_rounding", {"transition_risk": 0.625}, {}),
    ]
    result = []
    for name, changes, config_changes in cases:
        data = state_base | changes
        config = PlaybookEngineConfig(**config_changes)
        state = MarketState(
            **(data | {"timeframe": Timeframe(data["timeframe"])}), regime="TREND", regime_confidence=0.8
        )
        result.append(
            dict(name=name, state=data, config=asdict(config), expected=asdict(PlaybookEngine(config).select(state)))
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    contents = json.dumps(build_fixture(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if OUTPUT.read_text() != contents:
            raise SystemExit("playbook fixture differs; regenerate it")
    else:
        OUTPUT.write_text(contents)


if __name__ == "__main__":
    main()
