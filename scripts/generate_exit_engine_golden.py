#!/usr/bin/env python3
"""Generate synthetic standalone ExitEngine.check fixtures (no market data)."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zenquant.domain import MarketState, OpenPositionState, PlaybookSelection, Timeframe  # noqa: E402
from zenquant.strategy import ExitEngine, ExitEngineConfig  # noqa: E402

OUTPUT = ROOT / "internal/research/testdata/exit_engine_golden.json"


def build():
    position = dict(
        symbol="BTC", timeframe="4h", direction=1.0, entry_price=100.0, current_qty=1.0, trailing_stop=95.0, bars_held=5
    )
    state = dict(
        cutoff_time_ns=100,
        directional_state="UPTREND",
        volatility_state="NORMAL",
        quality_state="TRADABLE",
        transition_risk=0.0,
    )
    playbook = dict(playbook_id="BULL_CONTINUATION", trailing_stop_atr_mult=2.0, time_decay_bars=None)
    indicators = dict(price=100.0, atr=2.0)
    config = dict(
        time_decay_enabled=True, regime_change_enabled=True, min_bars_before_trail=1, transition_risk_exit_threshold=0.8
    )
    cases = [
        ("no_exit_ratchet_long", {}, {}, {}, dict(price=105.0), {}),
        (
            "bull_invalidation_priority",
            {},
            dict(directional_state="DOWNTREND", quality_state="UNSAFE"),
            {},
            dict(price=90.0),
            {},
        ),
        ("bear_invalidation", dict(direction=-1.0, trailing_stop=105.0), {}, dict(playbook_id="BEAR_DEFENSE"), {}, {}),
        ("initial_long_no_breach", dict(trailing_stop=0.0), {}, {}, dict(price=90.0), {}),
        (
            "initial_short_no_breach",
            dict(direction=-1.0, trailing_stop=0.0),
            dict(directional_state="DOWNTREND"),
            dict(playbook_id="BEAR_DEFENSE"),
            dict(price=110.0),
            {},
        ),
        ("trailing_long_equal", {}, {}, {}, dict(price=95.0), {}),
        (
            "trailing_short_equal",
            dict(direction=-1.0, trailing_stop=105.0),
            dict(directional_state="DOWNTREND"),
            dict(playbook_id="BEAR_DEFENSE"),
            dict(price=105.0),
            {},
        ),
        (
            "short_ratchet",
            dict(direction=-1.0, trailing_stop=105.0),
            dict(directional_state="DOWNTREND"),
            dict(playbook_id="BEAR_DEFENSE"),
            dict(price=90.0),
            {},
        ),
        (
            "initial_then_decay",
            dict(trailing_stop=0.0, bars_held=25),
            {},
            dict(time_decay_bars=20),
            dict(price=90.0),
            {},
        ),
        (
            "decay_before_regime",
            dict(bars_held=25),
            dict(quality_state="UNSAFE"),
            dict(time_decay_bars=20),
            dict(price=99.0),
            {},
        ),
        ("decay_boundary", dict(bars_held=20), {}, dict(time_decay_bars=20), dict(price=99.0), {}),
        (
            "decay_short_loss",
            dict(direction=-1.0, trailing_stop=110.0, bars_held=25),
            dict(directional_state="DOWNTREND"),
            dict(playbook_id="BEAR_DEFENSE", time_decay_bars=20),
            dict(price=101.0),
            {},
        ),
        (
            "quality_priority",
            {},
            dict(quality_state="UNSAFE", volatility_state="DISTORTED", transition_risk=1.0),
            {},
            {},
            {},
        ),
        ("volatility_priority", {}, dict(volatility_state="DISTORTED", transition_risk=1.0), {}, {}, {}),
        ("transition_equal", {}, dict(transition_risk=0.8), {}, {}, {}),
        ("capital_preservation", {}, dict(transition_risk=1.0), dict(playbook_id="CAPITAL_PRESERVATION"), {}, {}),
        (
            "disabled_checks",
            dict(bars_held=25),
            dict(quality_state="UNSAFE"),
            dict(time_decay_bars=20),
            dict(price=99.0),
            dict(time_decay_enabled=False, regime_change_enabled=False),
        ),
        ("missing_price", {}, {}, {}, dict(price=0.0), {}),
        ("missing_atr", {}, {}, {}, dict(atr=0.0), {}),
        ("minimum_bars", dict(bars_held=0), {}, {}, dict(price=90.0), {}),
        ("zero_multiplier_breach", {}, {}, dict(trailing_stop_atr_mult=0.0), dict(price=101.0), {}),
        ("price_format", {}, {}, {}, dict(price=94.125), {}),
    ]
    result = []
    for name, po, st, pb, ind, cfg in cases:
        p, s, b, i, c = position | po, state | st, playbook | pb, indicators | ind, config | cfg
        py_pos = OpenPositionState(
            **(p | dict(timeframe=Timeframe(p["timeframe"]), entry_bar_ns=1, base_qty=1.0, pyramid_count=0))
        )
        py_state = MarketState(
            symbol="BTC",
            timeframe=Timeframe.TF_4H,
            directional_confidence=1,
            volatility_confidence=1,
            positioning_state="HEALTHY",
            positioning_confidence=1,
            quality_confidence=1,
            regime="TREND",
            regime_confidence=1,
            direction_signal=1,
            **s,
        )
        py_playbook = PlaybookSelection(
            symbol="BTC",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=100,
            playbook_confidence=1,
            allowed_directions=[1],
            max_leverage=1,
            allow_pyramid=False,
            max_pyramid_count=0,
            selection_reason="synthetic",
            **b,
        )
        decision = ExitEngine(ExitEngineConfig(**c)).check(py_pos, py_state, py_playbook, i)
        result.append(
            dict(
                name=name,
                position=p,
                state=s,
                playbook=b,
                indicators=i,
                config=c,
                expected=asdict(decision),
                trailing_stop=py_pos.trailing_stop,
            )
        )
    # The final standalone case uses the default state, playbook and config.
    # Reuse the same Python position across bars, including the negative stop.
    s, b, c = state, playbook, config
    initial = position | dict(trailing_stop=0.0)
    py_pos = OpenPositionState(
        **(initial | dict(timeframe=Timeframe.TF_4H, entry_bar_ns=1, base_qty=1.0, pyramid_count=0))
    )
    engine = ExitEngine(ExitEngineConfig(**config))
    steps = []
    for price, atr in [(100.0, 60.0), (105.0, 60.0), (110.0, 50.0), (9.0, 50.0)]:
        i = dict(price=price, atr=atr)
        decision = engine.check(py_pos, py_state, py_playbook, i)
        steps.append(dict(indicators=i, expected=asdict(decision), trailing_stop=py_pos.trailing_stop))
    return dict(
        version=1,
        cases=result,
        sequences=[
            dict(name="negative_stop_across_bars", position=initial, state=s, playbook=b, config=c, steps=steps)
        ],
    )


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, allow_nan=False) + "\n"
    if args.write:
        OUTPUT.write_text(content)
    elif args.check:
        if OUTPUT.read_text() != content:
            raise SystemExit("exit engine fixture differs")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
