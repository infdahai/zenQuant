"""Generate synthetic stateful risk references from the Python implementation."""

import json
from dataclasses import replace
from pathlib import Path

from zenquant.domain import ExecutionReport, IndicatorCoverage, IndicatorPack, TargetPosition, Timeframe
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay, RiskOverlayConfig

symbol = "BTC"
base = 1704067200000000000  # Monday 2024-01-01 UTC
bus = InMemoryJetStream()
overlay = RiskOverlay(bus, RiskOverlayConfig(kill_cooldown_bars=2, kill_ramp_bars=3))
pack = IndicatorPack(
    ts_ns=base,
    symbol=symbol,
    timeframe=Timeframe.TF_4H,
    cutoff_time_ns=base,
    indicator_version="v1",
    formula_hash="test",
    quality_flags=[],
    coverage=IndicatorCoverage(venue_count=1, oi_coverage=1, staleness_ms=0),
    values={"last_price": 100.0},
    components={},
)
target = TargetPosition(
    intent_id="a",
    portfolio="arc",
    symbol=symbol,
    timeframe=Timeframe.TF_4H,
    cutoff_time_ns=base,
    target_qty=1,
    target_leverage=2,
    reduce_only=False,
    regime="TREND",
    regime_confidence=1,
    reason_codes=["TEST"],
)
steps = []


def record(event, output=None):
    day = overlay._day_anchor
    state = {
        "cash": overlay._cash,
        "equity": overlay._equity,
        "high_water_mark": overlay._high_water_mark,
        "positions": overlay._actual_positions.copy(),
        "prices": overlay._last_price.copy(),
        "has_day": day is not None,
        "day_id": day[0] - 719163 if day else 0,
        "day_equity": day[1] if day else 0,
        "kill_active": overlay._kill_active,
        "kill_bars": overlay._kill_bar_count,
        "recovery_bars": overlay._recovery_bar_count,
        "degraded_bars": overlay._degraded_mode_bars,
        "equity_history": list(overlay._equity_history),
    }
    steps.append({"event": event, "state": state, "output": output})


def indicator(price, vol=0, crowd=0, quality=None):
    p = replace(
        pack, values={"last_price": price, "realized_vol": vol, "funding_oi_z": crowd}, quality_flags=quality or []
    )
    overlay.prime_indicator(p)
    record({"type": "indicator", "price": price, "vol": vol, "crowd": crowd, "quality": quality or []})


def fill(side, qty, price):
    report = ExecutionReport(
        client_order_id="f",
        symbol=symbol,
        side=side,
        status="FILLED",
        filled_qty=qty,
        avg_price=price,
        fee=0,
        transaction_time_ns=base,
    )
    overlay._on_execution_report("", report)
    record({"type": "fill", "side": side, "qty": qty, "price": price})


def fee(amount):
    overlay.apply_fee(amount)
    record({"type": "fee", "amount": amount})


def apply(qty, day=0, reduce=False):
    t = replace(target, target_qty=qty, cutoff_time_ns=base + day * 86400000000000, reduce_only=reduce)
    revised, decision, snap = overlay._apply_overlay(t)
    record(
        {"type": "target", "qty": qty, "day": day, "reduce": reduce},
        {
            "qty": revised.target_qty,
            "leverage": revised.target_leverage,
            "reduce": revised.reduce_only,
            "reasons": revised.reason_codes,
            "flags": decision.flags,
            "cap": decision.lev_cap,
            "equity": snap.equity,
            "high_water_mark": snap.high_water_mark,
            "drawdown": snap.drawdown,
        },
    )


indicator(100)
fill(1, 100, 100)
fill(1, 50, 100)
apply(200)
indicator(50)
apply(-10)
fee(25000)
apply(200)
indicator(300)
apply(200)
apply(200)
apply(200)
indicator(300, 1, 3, ["BOOK_STALE"])
apply(200, 1)
fill(-1, 50, 300)
apply(-5, 1)
indicator(300)
apply(1, 2)
# Prolonged steadily worsening equity activates the 120-sample degradation guard.
for _ in range(220):
    fee(50)
    apply(1, 2)
Path("internal/research/testdata/stateful_risk_trace.json").write_text(json.dumps(steps, separators=(",", ":")) + "\n")
