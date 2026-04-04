import unittest

from zenquant.domain import IndicatorCoverage, IndicatorPack, TargetPosition, Timeframe
from zenquant.infra import InMemoryJetStream
from zenquant.risk import RiskOverlay


class Phase1RiskOverlayTests(unittest.TestCase):
    def test_stale_book_forces_reduce_only(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)

        final_targets = []
        decisions = []
        bus.subscribe("intent.target_position.final.>", lambda _subject, payload: final_targets.append(payload))
        bus.subscribe("risk.overlay.decision", lambda _subject, payload: decisions.append(payload))

        pack = IndicatorPack(
            ts_ns=1,
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=1,
            indicator_version="pack.v1",
            formula_hash="sha256:test",
            quality_flags=["BOOK_STALE"],
            coverage=IndicatorCoverage(venue_count=1, oi_coverage=1.0, staleness_ms=9999),
            values={"last_price": 40000.0, "realized_vol": 0.1, "funding_oi_z": 0.1},
            components={},
        )
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", pack)

        target = TargetPosition(
            intent_id="intent-1",
            portfolio="arc",
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=2,
            target_qty=1.25,
            target_leverage=2.0,
            reduce_only=False,
            regime="TREND",
            regime_confidence=0.7,
            reason_codes=["REGIME_TREND"],
        )
        bus.publish("intent.target_position.arc.BTCUSDT_PERP", target)

        self.assertTrue(final_targets)
        self.assertTrue(decisions)
        revised = final_targets[-1]
        decision = decisions[-1]
        self.assertTrue(revised.reduce_only)
        self.assertAlmostEqual(revised.target_qty, 0.0)
        self.assertIn("LIQUIDITY_STRESS", decision.flags)

    def test_drawdown_step_applies_leverage_throttle(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _subject, payload: decisions.append(payload))

        overlay.apply_fee(16_000.0)  # 16% drawdown -> DD_STEP_1 (long threshold=15%)
        pack = IndicatorPack(
            ts_ns=1,
            symbol="BTCUSDT_PERP",
            timeframe=Timeframe.TF_4H,
            cutoff_time_ns=1,
            indicator_version="pack.v1",
            formula_hash="sha256:test",
            quality_flags=["OK"],
            coverage=IndicatorCoverage(venue_count=2, oi_coverage=1.0, staleness_ms=0),
            values={"last_price": 40000.0, "realized_vol": 0.1, "funding_oi_z": 0.1},
            components={},
        )
        bus.publish("indicator.pack.v1.4h.BTCUSDT_PERP", pack)
        bus.publish(
            "intent.target_position.arc.BTCUSDT_PERP",
            TargetPosition(
                intent_id="intent-2",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                target_qty=1.0,
                target_leverage=2.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["REGIME_TREND"],
            ),
        )

        self.assertTrue(decisions)
        self.assertIn("DD_STEP_1", decisions[-1].flags)

    def test_overlay_uses_indicator_from_target_timeframe(self) -> None:
        bus = InMemoryJetStream()
        overlay = RiskOverlay(bus=bus)
        decisions = []
        bus.subscribe("risk.overlay.decision", lambda _subject, payload: decisions.append(payload))

        bus.publish(
            "indicator.pack.v1.1d.BTCUSDT_PERP",
            IndicatorPack(
                ts_ns=1,
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_1D,
                cutoff_time_ns=1,
                indicator_version="pack.v1",
                formula_hash="sha256:test",
                quality_flags=["OK"],
                coverage=IndicatorCoverage(venue_count=2, oi_coverage=1.0, staleness_ms=0),
                values={"last_price": 40000.0, "realized_vol": 1.2, "funding_oi_z": 0.1},
                components={},
            ),
        )
        bus.publish(
            "indicator.pack.v1.4h.BTCUSDT_PERP",
            IndicatorPack(
                ts_ns=2,
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=2,
                indicator_version="pack.v1",
                formula_hash="sha256:test",
                quality_flags=["OK"],
                coverage=IndicatorCoverage(venue_count=2, oi_coverage=1.0, staleness_ms=0),
                values={"last_price": 40000.0, "realized_vol": 0.1, "funding_oi_z": 0.1},
                components={},
            ),
        )
        bus.publish(
            "intent.target_position.arc.BTCUSDT_PERP",
            TargetPosition(
                intent_id="intent-3",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=3,
                target_qty=1.0,
                target_leverage=2.0,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.9,
                reason_codes=["REGIME_TREND"],
            ),
        )

        self.assertTrue(decisions)
        self.assertNotIn("VOL_SHOCK", decisions[-1].flags)


if __name__ == "__main__":
    unittest.main()
