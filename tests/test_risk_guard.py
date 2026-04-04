"""Unit tests for RiskOverlay — asymmetric DD, kill switch, configurable thresholds."""

import unittest

from zenquant.domain import (
    IndicatorCoverage,
    IndicatorPack,
    TargetPosition,
    Timeframe,
)
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay, RiskOverlayConfig


def _make_pack(values: dict | None = None) -> IndicatorPack:
    base = {"last_price": 40000.0, "realized_vol": 0.1, "funding_oi_z": 0.1}
    if values:
        base.update(values)
    return IndicatorPack(
        ts_ns=1, symbol="BTCUSDT_PERP", timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1, indicator_version="v1", formula_hash="test",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=2, oi_coverage=1.0, staleness_ms=0),
        values=base, components={},
    )


def _make_target(qty: float = 1.0, reduce_only: bool = False) -> TargetPosition:
    return TargetPosition(
        intent_id="test", portfolio="arc", symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H, cutoff_time_ns=2,
        target_qty=qty, target_leverage=2.0, reduce_only=reduce_only,
        regime="TREND", regime_confidence=0.9, reason_codes=["TEST"],
    )


class AsymmetricDDTests(unittest.TestCase):
    """Verify long DD steps are wider than short DD steps (PaulWei philosophy)."""

    def test_long_dd_step1_wider_than_short(self) -> None:
        config = RiskOverlayConfig()
        self.assertGreater(config.dd_step_1_long, config.dd_step_1_short)
        self.assertGreater(config.dd_step_2_long, config.dd_step_2_short)
        self.assertGreater(config.dd_step_3_long, config.dd_step_3_short)

    def test_long_position_no_dd_flag_at_10pct(self) -> None:
        """10% DD should NOT trigger DD_STEP_1 for longs (threshold=15%)."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(10_000.0)  # 10% DD
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))

        self.assertTrue(decisions)
        self.assertNotIn("DD_STEP_1", decisions[-1].flags)

    def test_short_position_triggers_dd_at_10pct(self) -> None:
        """10% DD SHOULD trigger DD_STEP_1 for shorts (threshold=8%)."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(10_000.0)  # 10% DD
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0))

        self.assertTrue(decisions)
        self.assertIn("DD_STEP_1", decisions[-1].flags)


class KillSwitchTests(unittest.TestCase):
    def test_kill_switch_at_30pct_dd(self) -> None:
        """Kill switch activates at 30% DD."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(31_000.0)  # 31% DD
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertTrue(decisions)
        self.assertIn("KILL_SWITCH", decisions[-1].flags)


class VolShockTests(unittest.TestCase):
    def test_vol_shock_flag(self) -> None:
        """High realized vol triggers VOL_SHOCK."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"realized_vol": 1.5}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertTrue(decisions)
        self.assertIn("VOL_SHOCK", decisions[-1].flags)


class CrowdingTests(unittest.TestCase):
    def test_extreme_crowding(self) -> None:
        """Extreme funding_oi_z triggers CROWDING_EXTREME."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"funding_oi_z": 3.0}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertTrue(decisions)
        self.assertIn("CROWDING_EXTREME", decisions[-1].flags)

    def test_cot_crowding_directional(self) -> None:
        """COT crowding only fires when position direction matches crowd."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        # Long position + leveraged funds extremely long → COT_CROWDING
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP",
                     _make_pack({"cot_leveraged_z": 3.0}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))
        self.assertIn("COT_CROWDING", decisions[-1].flags)

        # Short position + leveraged funds extremely long → NO COT_CROWDING
        decisions.clear()
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP",
                     _make_pack({"cot_leveraged_z": 3.0}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0))
        self.assertNotIn("COT_CROWDING", decisions[-1].flags)


class ConfigurabilityTests(unittest.TestCase):
    def test_custom_config_respected(self) -> None:
        """Custom RiskOverlayConfig thresholds are used."""
        bus = InMemoryJetStream()
        config = RiskOverlayConfig(
            dd_step_1_long=0.05,  # Very tight for testing
            vol_shock_threshold=0.5,
        )
        overlay = RiskOverlay(bus=bus, config=config)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(6_000.0)  # 6% DD (above custom 5% threshold)
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP",
                     _make_pack({"realized_vol": 0.6}))  # Above custom 0.5 threshold
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertIn("DD_STEP_1", decisions[-1].flags)
        self.assertIn("VOL_SHOCK", decisions[-1].flags)


class VPINTests(unittest.TestCase):
    def test_vpin_toxic_flag(self) -> None:
        """High VPIN triggers VPIN_TOXIC flag."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"vpin": 0.15}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertTrue(decisions)
        self.assertIn("VPIN_TOXIC", decisions[-1].flags)

    def test_low_vpin_no_flag(self) -> None:
        """Normal VPIN does not trigger flag."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"vpin": 0.03}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertTrue(decisions)
        self.assertNotIn("VPIN_TOXIC", decisions[-1].flags)

if __name__ == "__main__":
    unittest.main()
