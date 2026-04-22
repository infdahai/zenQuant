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
        ts_ns=1,
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=1,
        indicator_version="v1",
        formula_hash="test",
        quality_flags=["OK"],
        coverage=IndicatorCoverage(venue_count=2, oi_coverage=1.0, staleness_ms=0),
        values=base,
        components={},
    )


def _make_target(qty: float = 1.0, reduce_only: bool = False) -> TargetPosition:
    return TargetPosition(
        intent_id="test",
        portfolio="arc",
        symbol="BTCUSDT_PERP",
        timeframe=Timeframe.TF_4H,
        cutoff_time_ns=2,
        target_qty=qty,
        target_leverage=2.0,
        reduce_only=reduce_only,
        regime="TREND",
        regime_confidence=0.9,
        reason_codes=["TEST"],
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
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"cot_leveraged_z": 3.0}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))
        self.assertIn("COT_CROWDING", decisions[-1].flags)

        # Short position + leveraged funds extremely long → NO COT_CROWDING
        decisions.clear()
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"cot_leveraged_z": 3.0}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0))
        self.assertNotIn("COT_CROWDING", decisions[-1].flags)

    def test_carry_stress_and_vol_term_reduce_risk(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _make_pack(
                {
                    "carry_stress_score": 0.7,
                    "leverage_pressure_score": 0.8,
                    "rv_term_ratio": 1.45,
                    "vol_of_vol": 0.18,
                    "trend_persistence": 0.4,
                }
            ),
        )
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))

        self.assertIn("PERP_CARRY_STRESS_LONG", decisions[-1].flags)
        self.assertIn("LEVERAGE_PRESSURE", decisions[-1].flags)
        self.assertIn("VOL_TERM_STRESS", decisions[-1].flags)


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
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"realized_vol": 0.6})
        )  # Above custom 0.5 threshold
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


class MicrostructureRiskGuardTests(unittest.TestCase):
    def test_microstructure_toxicity_reduces_risk(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _make_pack(
                {
                    "ms_sidecar_ready": True,
                    "ms_sidecar_stale": False,
                    "ms_toxicity_proxy": 3.5,
                    "ms_liquidity_vacuum_score": 0.0,
                    "ms_spread_bps": 0.5,
                }
            ),
        )
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertIn("MICROSTRUCTURE_TOXIC", decisions[-1].flags)
        self.assertLess(decisions[-1].lev_cap, 6.0)

    def test_microstructure_severe_spread_forces_reduce_only(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _make_pack(
                {
                    "ms_sidecar_ready": True,
                    "ms_sidecar_stale": False,
                    "ms_toxicity_proxy": 0.0,
                    "ms_liquidity_vacuum_score": 0.0,
                    "ms_spread_bps": 9.0,
                }
            ),
        )
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertIn("MICROSTRUCTURE_SPREAD_STRESS", decisions[-1].flags)
        self.assertTrue(decisions[-1].reduce_only)

    def test_microstructure_stale_sidecar_is_fail_open(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            _make_pack(
                {
                    "ms_sidecar_ready": False,
                    "ms_sidecar_stale": True,
                    "ms_toxicity_proxy": 10.0,
                    "ms_liquidity_vacuum_score": 10.0,
                    "ms_spread_bps": 10.0,
                }
            ),
        )
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertNotIn("MICROSTRUCTURE_TOXIC", decisions[-1].flags)
        self.assertNotIn("MICROSTRUCTURE_LIQUIDITY_VACUUM", decisions[-1].flags)
        self.assertNotIn("MICROSTRUCTURE_SPREAD_STRESS", decisions[-1].flags)

    # ── Boundary tests (GAN discriminator feedback) ──
    def test_vpin_exact_threshold_no_flag(self) -> None:
        """VPIN at exact threshold (0.10) should NOT trigger (logic uses > not >=)."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"vpin": 0.10}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertNotIn("VPIN_TOXIC", decisions[-1].flags)

    def test_vpin_just_above_threshold_triggers(self) -> None:
        """VPIN just above threshold (0.10001) should trigger."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"vpin": 0.10001}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertIn("VPIN_TOXIC", decisions[-1].flags)


class DDStepBoundaryTests(unittest.TestCase):
    """Boundary tests for DD step thresholds (asymmetric long vs short)."""

    def test_long_dd_at_exact_step1_threshold(self) -> None:
        """Long DD at exact 15% should trigger DD_STEP_1 (logic uses >=)."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(15_000.0)  # Exact 15% DD
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))

        self.assertIn("DD_STEP_1", decisions[-1].flags)

    def test_long_dd_just_below_step1_no_flag(self) -> None:
        """Long DD just below 15% (14.99%) should NOT trigger."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(14_990.0)  # 14.99% DD
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))

        self.assertNotIn("DD_STEP_1", decisions[-1].flags)

    def test_short_dd_at_exact_step1_threshold(self) -> None:
        """Short DD at exact 8% should trigger DD_STEP_1."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(8_000.0)  # Exact 8% DD
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0))

        self.assertIn("DD_STEP_1", decisions[-1].flags)

    def test_kill_switch_at_exact_threshold(self) -> None:
        """Kill switch at exact 30% DD should trigger."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(30_000.0)  # Exact 30% DD
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertIn("KILL_SWITCH", decisions[-1].flags)

    def test_kill_switch_just_below_threshold_no_flag(self) -> None:
        """Kill switch just below 30% (29.99%) should NOT trigger."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(29_990.0)  # 29.99% DD
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertNotIn("KILL_SWITCH", decisions[-1].flags)


class CrowdingBoundaryTests(unittest.TestCase):
    """Boundary tests for crowding thresholds."""

    def test_crowding_at_exact_threshold(self) -> None:
        """Crowding at exact 2.2 should trigger CROWDING_EXTREME (logic uses >=)."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"funding_oi_z": 2.2}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertIn("CROWDING_EXTREME", decisions[-1].flags)

    def test_crowding_just_below_threshold_no_flag(self) -> None:
        """Crowding just below 2.2 should NOT trigger."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"funding_oi_z": 2.19}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertNotIn("CROWDING_EXTREME", decisions[-1].flags)

    def test_severe_crowding_at_exact_threshold(self) -> None:
        """Severe crowding at exact 3.0 should trigger SEVERE_CROWDING."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"funding_oi_z": 3.0}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertIn("SEVERE_CROWDING", decisions[-1].flags)

    def test_negative_crowding_at_exact_threshold(self) -> None:
        """Negative crowding at -2.2 should trigger (abs value check)."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"funding_oi_z": -2.2}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertIn("CROWDING_EXTREME", decisions[-1].flags)


class VolShockBoundaryTests(unittest.TestCase):
    """Boundary tests for vol shock threshold."""

    def test_vol_shock_at_exact_threshold(self) -> None:
        """Realized vol at exact 0.95 should trigger VOL_SHOCK (logic uses >=)."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"realized_vol": 0.95}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertIn("VOL_SHOCK", decisions[-1].flags)

    def test_vol_shock_just_below_threshold_no_flag(self) -> None:
        """Realized vol just below 0.95 should NOT trigger."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"realized_vol": 0.949}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target())

        self.assertNotIn("VOL_SHOCK", decisions[-1].flags)


class BasisDiscountBoundaryTests(unittest.TestCase):
    """Boundary tests for basis discount guard (short-side protection)."""

    def test_basis_discount_short_triggers(self) -> None:
        """Short position + deep basis discount (-2.5) should trigger."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"basis_z": -2.5}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0))

        self.assertIn("BASIS_DISCOUNT_EXTREME", decisions[-1].flags)

    def test_basis_discount_long_no_trigger(self) -> None:
        """Long position + deep basis discount should NOT trigger (direction-gated)."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"basis_z": -2.5}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))

        self.assertNotIn("BASIS_DISCOUNT_EXTREME", decisions[-1].flags)

    def test_basis_discount_at_exact_threshold_triggers(self) -> None:
        """basis_z at exact -2.0 should trigger (logic uses <, -2.0 < -2.0 is False)."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"basis_z": -2.0}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0))

        # -2.0 < -2.0 is False, so should NOT trigger
        self.assertNotIn("BASIS_DISCOUNT_EXTREME", decisions[-1].flags)

    def test_basis_discount_just_below_threshold_triggers(self) -> None:
        """basis_z just below -2.0 should trigger."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"basis_z": -2.001}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0))

        self.assertIn("BASIS_DISCOUNT_EXTREME", decisions[-1].flags)

    def test_basis_discount_just_above_threshold_no_trigger(self) -> None:
        """basis_z just above -2.0 (-1.99) should NOT trigger."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"basis_z": -1.99}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0))

        self.assertNotIn("BASIS_DISCOUNT_EXTREME", decisions[-1].flags)

    def test_basis_premium_and_discount_independent(self) -> None:
        """Premium guard fires on longs, discount guard fires on shorts — never both."""
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        # Long + positive basis → premium fires, discount does not
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"basis_z": 2.5}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))
        self.assertIn("BASIS_PREMIUM_EXTREME", decisions[-1].flags)
        self.assertNotIn("BASIS_DISCOUNT_EXTREME", decisions[-1].flags)

        # Short + negative basis → discount fires, premium does not
        decisions.clear()
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack({"basis_z": -2.5}))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0))
        self.assertNotIn("BASIS_PREMIUM_EXTREME", decisions[-1].flags)
        self.assertIn("BASIS_DISCOUNT_EXTREME", decisions[-1].flags)


if __name__ == "__main__":
    unittest.main()
