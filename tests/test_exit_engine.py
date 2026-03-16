import unittest

from zenquant.domain import MarketState, OpenPositionState, PlaybookSelection, Timeframe
from zenquant.strategy import ExitEngine, ExitEngineConfig


def _position(**kwargs) -> OpenPositionState:
    defaults = {
        "symbol": "BTCUSDT_PERP",
        "timeframe": Timeframe.TF_4H,
        "direction": 1.0,
        "entry_price": 100.0,
        "entry_bar_ns": 1_000_000_000,
        "base_qty": 1.0,
        "current_qty": 1.0,
        "trailing_stop": 95.0,
        "pyramid_count": 0,
        "bars_held": 5,
    }
    defaults.update(kwargs)
    return OpenPositionState(**defaults)


def _state(**kwargs) -> MarketState:
    defaults = {
        "symbol": "BTCUSDT_PERP",
        "timeframe": Timeframe.TF_4H,
        "cutoff_time_ns": 2_000_000_000,
        "directional_state": "UPTREND",
        "directional_confidence": 0.8,
        "volatility_state": "NORMAL",
        "volatility_confidence": 0.8,
        "positioning_state": "HEALTHY",
        "positioning_confidence": 0.8,
        "quality_state": "TRADABLE",
        "quality_confidence": 0.9,
        "regime": "TREND",
        "regime_confidence": 0.8,
        "direction_signal": 1.0,
    }
    defaults.update(kwargs)
    return MarketState(**defaults)


def _playbook(**kwargs) -> PlaybookSelection:
    defaults = {
        "symbol": "BTCUSDT_PERP",
        "timeframe": Timeframe.TF_4H,
        "cutoff_time_ns": 2_000_000_000,
        "playbook_id": "BULL_CONTINUATION",
        "playbook_confidence": 0.8,
        "allowed_directions": [1.0, 0.0],
        "max_leverage": 3.0,
        "trailing_stop_atr_mult": 2.0,
        "allow_pyramid": True,
        "max_pyramid_count": 2,
        "time_decay_bars": None,
        "selection_reason": "Clean uptrend",
    }
    defaults.update(kwargs)
    return PlaybookSelection(**defaults)


class ExitEngineTests(unittest.TestCase):
    def test_invalidation_bull_to_downtrend(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0),
            _state(directional_state="DOWNTREND"),
            _playbook(playbook_id="BULL_CONTINUATION"),
            {"price": 99.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "INVALIDATION")
        self.assertEqual(decision.exit_urgency, "IMMEDIATE")
        self.assertEqual(decision.target_reduction_pct, 1.0)

    def test_invalidation_bear_to_uptrend(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=-1.0, trailing_stop=105.0),
            _state(directional_state="UPTREND"),
            _playbook(playbook_id="BEAR_DEFENSE", allowed_directions=[-1.0, 0.0]),
            {"price": 101.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "INVALIDATION")
        self.assertEqual(decision.exit_urgency, "IMMEDIATE")

    def test_trailing_stop_long_breach(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0, entry_price=100.0, trailing_stop=95.0),
            _state(),
            _playbook(),
            {"price": 94.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "TRAILING")
        self.assertEqual(decision.exit_urgency, "IMMEDIATE")
        self.assertEqual(decision.stop_price, 95.0)

    def test_trailing_stop_short_breach(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=-1.0, entry_price=100.0, trailing_stop=105.0),
            _state(directional_state="DOWNTREND", direction_signal=-1.0),
            _playbook(playbook_id="BEAR_DEFENSE", allowed_directions=[-1.0, 0.0]),
            {"price": 106.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "TRAILING")
        self.assertEqual(decision.exit_urgency, "IMMEDIATE")

    def test_time_decay_unprofitable(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0, entry_price=100.0, bars_held=25),
            _state(),
            _playbook(time_decay_bars=20),
            {"price": 99.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "TIME_DECAY")
        self.assertEqual(decision.exit_urgency, "GRADUAL")
        self.assertEqual(decision.target_reduction_pct, 0.5)

    def test_time_decay_profitable_no_exit(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0, entry_price=100.0, bars_held=25),
            _state(),
            _playbook(time_decay_bars=20),
            {"price": 105.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "NONE")

    def test_time_decay_none_never_triggers(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0, entry_price=100.0, bars_held=25),
            _state(),
            _playbook(time_decay_bars=None),
            {"price": 99.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "NONE")

    def test_regime_change_volatility_distorted(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0),
            _state(volatility_state="DISTORTED"),
            _playbook(),
            {"price": 105.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "REGIME_CHANGE")
        self.assertEqual(decision.exit_urgency, "GRADUAL")
        self.assertEqual(decision.target_reduction_pct, 0.5)

    def test_regime_change_quality_unsafe(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0),
            _state(quality_state="UNSAFE"),
            _playbook(),
            {"price": 105.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "REGIME_CHANGE")
        self.assertEqual(decision.exit_urgency, "IMMEDIATE")

    def test_exit_priority_invalidation_over_trailing(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0, entry_price=100.0, trailing_stop=95.0),
            _state(directional_state="DOWNTREND"),
            _playbook(playbook_id="BULL_CONTINUATION"),
            {"price": 94.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "INVALIDATION")

    def test_no_exit_all_conditions_ok(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0, entry_price=100.0, trailing_stop=95.0),
            _state(directional_state="UPTREND"),
            _playbook(playbook_id="BULL_CONTINUATION"),
            {"price": 105.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "NONE")

    def test_config_can_lower_transition_risk_exit_threshold(self) -> None:
        engine = ExitEngine(ExitEngineConfig(transition_risk_exit_threshold=0.7))
        decision = engine.check(
            _position(direction=1.0, trailing_stop=95.0),
            _state(transition_risk=0.75),
            _playbook(playbook_id="BULL_CONTINUATION"),
            {"price": 105.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "REGIME_CHANGE")
        self.assertEqual(decision.reason_code, "STATE_TRANSITION_RISK")

    def test_transition_risk_below_exit_threshold_does_not_force_exit(self) -> None:
        engine = ExitEngine()
        decision = engine.check(
            _position(direction=1.0, trailing_stop=95.0),
            _state(transition_risk=0.7),
            _playbook(playbook_id="CAPITAL_PRESERVATION", allowed_directions=[0.0]),
            {"price": 105.0, "atr": 2.0},
        )
        self.assertEqual(decision.exit_type, "NONE")


if __name__ == "__main__":
    unittest.main()
