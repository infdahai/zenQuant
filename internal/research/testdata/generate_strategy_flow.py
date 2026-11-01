"""Generate synthetic legacy strategy outputs from the Python event bus."""
import json
from dataclasses import asdict
from pathlib import Path

from zenquant.domain import ExecutionReport, IndicatorCoverage, IndicatorPack, MarketState, Timeframe
from zenquant.infra.event_bus import InMemoryJetStream
from zenquant.strategy.engine import ARCStrategyConfig, ARCStrategyEngine

bus = InMemoryJetStream()
engine = ARCStrategyEngine(bus, ARCStrategyConfig(intent_entry_confirmation_bars=2, intent_flip_confirmation_bars=2))
outputs = []
for pattern in ("strategy.playbook.>", "strategy.sizing.>", "strategy.exit.>", "intent.target_position.>", "intent.reason.>", "strategy.intent_confirmation.>"):
    bus.subscribe(pattern, lambda subject, payload: outputs.append((subject, asdict(payload) if hasattr(payload, "__dataclass_fields__") else payload)))
publish_fill = bus.claim_publisher("exec.order.report")
steps = []
symbol = "BTCUSDT_PERP"

def state(t, direction="UPTREND", regime="TREND", quality="TRADABLE", volatility="NORMAL", timeframe="4h", signal=1):
    return dict(symbol=symbol, timeframe=timeframe, cutoff_time_ns=t, directional_state=direction,
                directional_confidence=0.8, volatility_state=volatility, volatility_confidence=0.8,
                positioning_state="HEALTHY", positioning_confidence=0.8, quality_state=quality,
                quality_confidence=0.8, regime=regime, regime_confidence=0.8,
                direction_signal=signal, transition_risk=0.0, quality_flags=["OK"])

def bar(t, direction="UPTREND", cvd=0.1, price=100.0, **kw):
    p = dict(symbol=symbol, timeframe="4h", cutoff_time_ns=t, indicator_version="v1", formula_hash="f1",
             price=price, atr=2.0, realized_vol=0.8, funding_oi_z=0.0, cvd_slope=cvd,
             ob_liq_z=0.0, structure_state=direction, momentum_roc_z=0.5 if cvd > 0 else -0.5)
    s = state(t, direction=direction, signal=1 if cvd > 0 else -1, **kw)
    values = dict(last_price=price, atr=p["atr"], realized_vol=p["realized_vol"], funding_oi_z=0.0,
                  cvd_slope=cvd, ob_liq_z=0.0, structure_state=direction, momentum_roc_z=p["momentum_roc_z"])
    bus.publish("indicator.4h." + symbol, IndicatorPack(t, symbol, Timeframe.TF_4H, t, "v1", "f1", [], IndicatorCoverage(1, 1, 0), values))
    outputs.clear()
    bus.publish("state.market.4h." + symbol, MarketState(**{**s, "timeframe": Timeframe.TF_4H}))
    result = {}
    for subject, payload in outputs:
        if subject.startswith("strategy.playbook."): result["playbook"] = payload
        elif subject.startswith("strategy.sizing."): result["sizing"] = payload
        elif subject.startswith("strategy.exit."): result["exit"] = payload
        elif subject.startswith("intent.target_position."): result["target"] = payload
        elif subject.startswith("intent.reason."): result["reason"] = payload
        elif subject.startswith("strategy.intent_confirmation."): result["confirmation"] = payload["reason"]
    steps.append(dict(kind="bar", indicator=p, state=s, expected=result))

def fill(side, qty, price):
    publish_fill(ExecutionReport("synthetic", symbol, side, "FILLED", qty, price, 0.0, 1))
    steps.append(dict(kind="fill", side=side, quantity=qty, price=price))

# Two-bar entry, real adds and partial fills, reversal confirmation, then a
# risk-driven exit and decimal sub-minimum cancellation.
bar(1)
bar(2)
fill(1, 1, 100)
fill(1, .5, 102)
bar(3, price=104)
fill(-1, .25, 104)
bar(4, direction="DOWNTREND", cvd=-.1, price=102)
bar(5, direction="DOWNTREND", cvd=-.1, price=102)
fill(-1, 2.25, 102)
bar(6, direction="DOWNTREND", cvd=-.1, price=101, quality="UNSAFE")
fill(1, 1, 101)
fill(1, .00005, 100)
fill(-1, .00005, 100)
bar(7)
Path(__file__).with_name("strategy_flow_golden.json").write_text(json.dumps(steps, indent=2) + "\n")
