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


if __name__ == "__main__":
    unittest.main()
