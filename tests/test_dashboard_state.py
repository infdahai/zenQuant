import unittest

from zenquant.app.live_state import DashboardStateStore
from zenquant.domain import (
    DirectionalBias,
    IndicatorCoverage,
    IndicatorPack,
    OpenPositionState,
    TargetPosition,
    Timeframe,
)


class DashboardStateTests(unittest.TestCase):
    def test_snapshot_exposes_bias_position_and_latest_target(self) -> None:
        store = DashboardStateStore()
        store.on_indicator(
            IndicatorPack(
                ts_ns=10,
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=10,
                indicator_version="pack.v1",
                formula_hash="sha256:test",
                quality_flags=["OK"],
                coverage=IndicatorCoverage(venue_count=2, oi_coverage=1.0, staleness_ms=0),
                values={"last_price": 100.0},
                components={},
            )
        )
        store.on_directional_bias(
            DirectionalBias(
                symbol="BTCUSDT_PERP",
                cutoff_time_ns=2,
                direction=1.0,
                regime="TREND",
                confidence=0.8,
                quality_flags=["OK"],
            )
        )
        store.on_position_state(
            symbol="BTCUSDT_PERP",
            timeframe="4h",
            payload=OpenPositionState(
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                direction=1.0,
                entry_price=100.0,
                entry_bar_ns=1,
                base_qty=1.0,
                current_qty=1.0,
                trailing_stop=95.0,
                pyramid_count=0,
                bars_held=2,
            ),
        )
        store.on_target_position(
            TargetPosition(
                intent_id="intent-1",
                portfolio="arc",
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=11,
                target_qty=1.5,
                target_leverage=1.2,
                reduce_only=False,
                regime="TREND",
                regime_confidence=0.8,
                reason_codes=["REGIME_TREND"],
            )
        )

        snapshot = store.snapshot(symbol="BTCUSDT_PERP", timeframe="4h")
        self.assertIn("directional_bias", snapshot)
        self.assertIn("directional_bias_meta", snapshot)
        self.assertIn("position_state", snapshot)
        self.assertIn("latest_target", snapshot)
        self.assertFalse(snapshot["directional_bias_meta"]["is_stale"])
