"""Generate the fixed synthetic state-engine differential fixture from Python."""
import json
from dataclasses import asdict
from pathlib import Path

from zenquant.domain import IndicatorCoverage, IndicatorPack, Timeframe
from zenquant.infra.event_bus import InMemoryJetStream
from zenquant.state.engine import StateEngine, StateEngineConfig

base = dict(realized_vol=1.0, vix_z=0.0, funding_oi_z=0.0, cvd_slope=0.03,
            zen_pulse=60.0, momentum_roc_z=0.6, liq_intensity_z=0.0,
            liq_imbalance=0.0, yield_10y_z=0.0, cycle_position=1.0)
cases = [
    ("BTC", "4h", "UPTREND", {}, [], False),
    ("ETH", "4h", "UPTREND", {"momentum_roc_z": 0.3, "cvd_slope": 0.015, "zen_pulse": 50}, [], False),
    ("BTC", "1h", "UPTREND", {"momentum_roc_z": 0.3, "cvd_slope": 0.015, "zen_pulse": 50}, [], False),
    ("BTC", "4h", "UPTREND", {"momentum_roc_z": 0.3, "cvd_slope": 0.015, "zen_pulse": 50}, [], False),
    ("BTC", "4h", "UPTREND", {"momentum_roc_z": 0.2, "cvd_slope": 0.012, "zen_pulse": 50}, [], False),
    ("BTC", "4h", "DOWNTREND", {"momentum_roc_z": -0.18, "cvd_slope": -0.025, "zen_pulse": 49}, [], False),
    ("BTC", "4h", "NEUTRAL", {"momentum_roc_z": -0.1, "cvd_slope": -0.015, "zen_pulse": 50}, [], False),
    ("BTC", "4h", "NEUTRAL", {"momentum_roc_z": -0.1, "cvd_slope": -0.015, "zen_pulse": 50, "liq_intensity_z": 2.4, "liq_imbalance": 0.2}, [], False),
    ("BTC", "4h", "NEUTRAL", {"momentum_roc_z": 0, "realized_vol": 2.8, "vix_z": 3.0, "funding_oi_z": 1.8}, [], True),
    ("BTC", "4h", "NEUTRAL", {"momentum_roc_z": 0, "realized_vol": 2.80001}, ["DATA_STALE"], False),
    ("BTC", "4h", "NEUTRAL", {"momentum_roc_z": 0, "zen_pulse": 0, "cycle_position": 0, "yield_10y_z": 2.1}, ["MACRO_STALE"], False),
]
result = []
for overlay in (True, False):
    engine = StateEngine(InMemoryJetStream(), StateEngineConfig(enable_liquidation_overlay=overlay))
    for i, (symbol, tf, structure, changes, flags, stale) in enumerate(cases, 1):
        values = {**base, **changes}
        inp = dict(symbol=symbol, timeframe=tf, cutoff_time_ns=i * 100, available_time_ns=i * 100 - 1,
                   values=values, structure_state=structure, liq_stale=stale, quality_flags=flags)
        pack = IndicatorPack(ts_ns=i * 100, symbol=symbol, timeframe=Timeframe(tf), cutoff_time_ns=i * 100,
                             indicator_version="fixture", formula_hash="fixture", quality_flags=flags,
                             coverage=IndicatorCoverage(1, 1.0, 0),
                             values={**values, "structure_state": structure, "liq_stale": stale})
        state = asdict(engine.classify(pack))
        state.pop("quality_flags")
        state.pop("reason_codes")
        result.append(dict(overlay=overlay, input=inp, expected=state))
Path(__file__).with_name("state_engine_golden.json").write_text(json.dumps(result, indent=2) + "\n")
