#!/usr/bin/env python3
"""Generate offline sizing parity cases using synthetic inputs only."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zenquant.domain import MarketState, PlaybookSelection, Timeframe
from zenquant.strategy import SizingEngine, SizingEngineConfig

OUTPUT = Path(__file__).resolve().parents[1] / "internal/research/testdata/sizing_golden.json"


def build_fixture():
    cases = []
    specs = [
        ("default_floor", {}, {}, {}, {}),
        ("cap", {}, {"max_leverage": 1.0}, {"realized_vol": 0.05}, {}),
        ("noisy_crowded", {"quality_state": "NOISY"}, {}, {"realized_vol": 0.4, "funding_oi_z": -2.1}, {}),
        (
            "degraded_high",
            {"quality_state": "DEGRADED", "volatility_state": "HIGH", "timeframe": "1h"},
            {},
            {"realized_vol": 0.8, "funding_oi_z": 1.8},
            {},
        ),
        (
            "unsafe_distorted",
            {"quality_state": "UNSAFE", "volatility_state": "DISTORTED", "timeframe": "1d"},
            {},
            {"realized_vol": 2.0},
            {},
        ),
        ("unknown_quality", {"quality_state": "OTHER", "timeframe": "5m"}, {}, {"realized_vol": 0.9}, {}),
        (
            "configured",
            {},
            {},
            {"realized_vol": 0.8, "funding_oi_z": 2.1},
            {"crowding_reason_threshold": 3.0, "volatility_high_multiple": 1.1, "volatility_high_discount": 0.8},
        ),
        (
            "confidence_clamp",
            {
                "directional_confidence": 1.0,
                "volatility_confidence": 1.0,
                "positioning_confidence": 1.0,
                "quality_confidence": 1.0,
            },
            {"playbook_confidence": 2.0},
            {"realized_vol": 0.6},
            {},
        ),
        (
            "round_down",
            {
                "directional_confidence": 1.0,
                "volatility_confidence": 1.0,
                "positioning_confidence": 1.0,
                "quality_confidence": 1.0,
            },
            {"playbook_confidence": 1.0, "max_leverage": 0.12345},
            {"realized_vol": 0.6},
            {},
        ),
        (
            "round_up",
            {
                "directional_confidence": 1.0,
                "volatility_confidence": 1.0,
                "positioning_confidence": 1.0,
                "quality_confidence": 1.0,
            },
            {"playbook_confidence": 1.0, "max_leverage": 0.12355},
            {"realized_vol": 0.6},
            {},
        ),
    ]
    for name, state_changes, playbook_changes, indicators, config_changes in specs:
        state_data = dict(
            symbol="SYNTHETIC",
            timeframe="4h",
            cutoff_time_ns=123,
            directional_confidence=0.8,
            volatility_state="NORMAL",
            volatility_confidence=0.8,
            positioning_confidence=0.8,
            quality_state="TRADABLE",
            quality_confidence=0.9,
        )
        state_data.update(state_changes)
        playbook_data = dict(playbook_confidence=0.82, max_leverage=3.0)
        playbook_data.update(playbook_changes)
        state = MarketState(
            **{**state_data, "timeframe": Timeframe(state_data["timeframe"])},
            directional_state="UPTREND",
            positioning_state="HEALTHY",
            regime="TREND",
            regime_confidence=0.8,
            direction_signal=1.0,
        )
        playbook = PlaybookSelection(
            symbol=state.symbol,
            timeframe=state.timeframe,
            cutoff_time_ns=state.cutoff_time_ns,
            playbook_id="TEST",
            allowed_directions=[1.0],
            trailing_stop_atr_mult=2.0,
            allow_pyramid=False,
            max_pyramid_count=0,
            time_decay_bars=None,
            selection_reason="synthetic",
            **playbook_data,
        )
        config = SizingEngineConfig(**config_changes)
        result = asdict(SizingEngine(config).compute(state, playbook, 100000.0, indicators))
        cases.append(
            dict(
                name=name,
                state=state_data,
                playbook=playbook_data,
                indicators=indicators,
                config=asdict(config),
                expected=result,
            )
        )
    return {
        "version": 1,
        "cases": cases,
        "rounding": [
            {"value": v, "digits": d, "expected": round(v, d)}
            for v, d in [(0.12345, 4), (0.12355, 4), (1.234565, 6), (1.234575, 6), (2.5, 0), (3.5, 0), (-2.5, 0)]
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
        raise SystemExit("Sizing fixture is stale")


if __name__ == "__main__":
    main()
