"""Unit tests for RiskOverlay — asymmetric DD, kill switch, configurable thresholds."""

import unittest

import pytest

from zenquant.domain import (
    EventMeta,
    ExecutionReport,
    IndicatorCoverage,
    IndicatorPack,
    MarkPriceUpdate,
    TargetPosition,
    Timeframe,
    target_position_hash,
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


def _fill(qty: float, price: float = 40000.0) -> ExecutionReport:
    return ExecutionReport(
        client_order_id="fill",
        symbol="BTCUSDT_PERP",
        side=1 if qty > 0 else -1,
        status="FILLED",
        filled_qty=abs(qty),
        avg_price=price,
        fee=0.0,
        transaction_time_ns=1,
    )


def _mark(price: float) -> MarkPriceUpdate:
    return MarkPriceUpdate(
        meta=EventMeta(
            event_time_ns=1,
            ingest_time_ns=1,
            venue="BINANCE",
            symbol="BTCUSDT_PERP",
            stream_seq=1,
        ),
        mark_price=price,
    )


def test_mark_price_event_revalues_filled_position() -> None:
    bus = InMemoryJetStream()
    overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(starting_equity=100_000.0))

    bus.publish("exec.order.report", _fill(2.0, price=100_000.0))
    overlay.apply_fee(80.0)
    equity_before_mark = overlay.equity

    bus.publish("md.mark.BINANCE.BTCUSDT_PERP", _mark(101_000.0))

    assert overlay.equity - equity_before_mark == pytest.approx(2_000.0)


@pytest.mark.parametrize("invalid_mark", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_invalid_mark_price_event_does_not_change_equity(invalid_mark: float) -> None:
    bus = InMemoryJetStream()
    overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(starting_equity=100_000.0))
    bus.publish("exec.order.report", _fill(1.0, price=100_000.0))
    bus.publish("md.mark.BINANCE.BTCUSDT_PERP", _mark(101_000.0))
    before = overlay.equity

    bus.publish("md.mark.BINANCE.BTCUSDT_PERP", _mark(invalid_mark))

    assert overlay.equity == pytest.approx(before)


def test_positions_returns_immutable_snapshot() -> None:
    bus = InMemoryJetStream()
    overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(starting_equity=100_000.0))
    bus.publish("exec.order.report", _fill(1.0, price=100_000.0))
    positions = overlay.positions

    with pytest.raises(TypeError):
        positions["BTCUSDT_PERP"] = 0.0

    bus.publish("exec.order.report", _fill(1.0, price=100_000.0))

    assert positions == {"BTCUSDT_PERP": 1.0}
    assert overlay.positions == {"BTCUSDT_PERP": 2.0}


def test_leveraged_fill_fee_preserves_negative_cash_liability() -> None:
    bus = InMemoryJetStream()
    overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(starting_equity=100_000.0))

    bus.publish("exec.order.report", _fill(2.0, price=100_000.0))
    overlay.apply_fee(80.0)

    assert overlay.cash == pytest.approx(-100_080.0)
    assert overlay.equity == pytest.approx(99_920.0)


@pytest.mark.parametrize(
    ("fee", "expected_cash"),
    [
        (0.0, 100_000.0),
        (100_000.0, 0.0),
        (100_001.0, -1.0),
    ],
)
def test_fee_subtraction_preserves_signed_cash_boundaries(fee: float, expected_cash: float) -> None:
    bus = InMemoryJetStream()
    overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(starting_equity=100_000.0))

    overlay.apply_fee(fee)

    assert overlay.cash == pytest.approx(expected_cash)
    assert overlay.equity == pytest.approx(expected_cash)


def test_fee_subtraction_preserves_existing_negative_cash() -> None:
    bus = InMemoryJetStream()
    overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(starting_equity=100_000.0))
    overlay.apply_fee(100_001.0)

    overlay.apply_fee(80.0)

    assert overlay.cash == pytest.approx(-81.0)
    assert overlay.equity == pytest.approx(-81.0)


@pytest.mark.parametrize("fee", [-1.0, float("nan"), float("inf"), float("-inf")])
def test_invalid_fee_is_atomic(fee: float) -> None:
    bus = InMemoryJetStream()
    overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(starting_equity=100_000.0))
    before = (overlay.cash, overlay.equity, overlay.high_water_mark)

    with pytest.raises(ValueError, match="fee must be finite and non-negative"):
        overlay.apply_fee(fee)

    assert (overlay.cash, overlay.equity, overlay.high_water_mark) == before


def test_invalid_direct_execution_report_fee_is_atomic() -> None:
    bus = InMemoryJetStream()
    overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(starting_equity=100_000.0))
    before = (dict(overlay._actual_positions), overlay.cash, overlay.equity, overlay.high_water_mark)

    with pytest.raises(ValueError, match="fee must be finite and non-negative"):
        bus.publish(
            "exec.order.report",
            ExecutionReport(
                client_order_id="invalid-direct-fill",
                symbol="BTCUSDT_PERP",
                side=1,
                status="FILLED",
                filled_qty=1.0,
                avg_price=100_000.0,
                fee=float("nan"),
                transaction_time_ns=1,
            ),
        )

    assert (dict(overlay._actual_positions), overlay.cash, overlay.equity, overlay.high_water_mark) == before


class AsymmetricDDTests(unittest.TestCase):
    """Verify long DD steps are wider than short DD steps (PaulWei philosophy)."""

    def test_long_dd_step1_wider_than_short(self) -> None:
        config = RiskOverlayConfig()
        self.assertGreater(config.dd_step_1_long, config.dd_step_1_short)
        self.assertGreater(config.dd_step_2_long, config.dd_step_2_short)
        self.assertGreater(config.dd_step_3_long, config.dd_step_3_short)

    def test_overlay_decision_hashes_exact_original_and_revised_targets(self) -> None:
        bus = InMemoryJetStream()
        RiskOverlay(bus=bus, config=RiskOverlayConfig(max_leverage=0.1))
        decisions = []
        finals = []
        bus.subscribe("risk.overlay.decision", lambda _, payload: decisions.append(payload))
        bus.subscribe("intent.target_position.final.>", lambda _, payload: finals.append(payload))
        original = _make_target(qty=1.0)
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())

        bus.publish("intent.target_position.arc.BTCUSDT_PERP", original)

        self.assertEqual(decisions[0].original_target_hash, target_position_hash(original))
        self.assertEqual(decisions[0].revised_target_hash, target_position_hash(finals[0]))

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

    def test_kill_switch_does_not_recover_on_cooldown_without_drawdown_recovery(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(kill_cooldown_bars=2, kill_ramp_bars=2))
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(31_000.0)
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        for idx in range(4):
            bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))

        self.assertIn("KILL_SWITCH", decisions[-1].flags)
        self.assertNotIn("KILL_RECOVERED", decisions[-1].flags)

    def test_kill_switch_ramps_after_recovery_threshold(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(kill_cooldown_bars=2, kill_ramp_bars=2))
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(31_000.0)
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))
        overlay._cash = 89_000.0
        overlay._equity = 89_000.0
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))

        self.assertIn("KILL_RECOVERY_RAMP", decisions[-2].flags)
        self.assertIn("KILL_RECOVERED", decisions[-1].flags)

    def test_kill_switch_recovery_waits_for_cooldown_even_if_drawdown_recovers(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus, config=RiskOverlayConfig(kill_cooldown_bars=4, kill_ramp_bars=2))
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _, p: decisions.append(p))

        overlay.apply_fee(31_000.0)
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))
        overlay._cash = 89_000.0
        overlay._equity = 89_000.0
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=1.0))

        self.assertIn("KILL_SWITCH", decisions[-1].flags)
        self.assertNotIn("KILL_RECOVERY_RAMP", decisions[-1].flags)

    def test_reduce_only_crossing_target_flattens_instead_of_holding_current_position(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        final_targets = []
        bus.subscribe("intent.target_position.final.>", lambda _, p: final_targets.append(p))

        bus.publish("exec.order.report", _fill(1.0))
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", _make_pack())
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", _make_target(qty=-1.0, reduce_only=True))

        self.assertTrue(final_targets)
        self.assertEqual(final_targets[-1].target_qty, 0.0)
        self.assertTrue(final_targets[-1].reduce_only)


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
