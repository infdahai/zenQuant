import unittest

from zenquant.app.live_state import DashboardStateStore
from zenquant.domain import (
    DeribitRiskPack,
    DirectionalBias,
    ExitDecision,
    IndicatorCoverage,
    IndicatorPack,
    MarketState,
    OpenPositionState,
    PlaybookSelection,
    SizingDecision,
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
        store.on_market_state(
            MarketState(
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=10,
                directional_state="UPTREND",
                directional_confidence=0.8,
                volatility_state="NORMAL",
                volatility_confidence=0.75,
                positioning_state="HEALTHY",
                positioning_confidence=0.7,
                quality_state="TRADABLE",
                quality_confidence=0.9,
                regime="TREND",
                regime_confidence=0.8,
                direction_signal=1.0,
                quality_flags=["OK"],
                reason_codes=["REGIME_TREND"],
            )
        )
        store.on_playbook(
            PlaybookSelection(
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=10,
                playbook_id="BULL_CONTINUATION",
                playbook_confidence=0.82,
                allowed_directions=[1.0, 0.0],
                max_leverage=3.0,
                trailing_stop_atr_mult=3.0,
                allow_pyramid=True,
                max_pyramid_count=2,
                time_decay_bars=None,
                selection_reason="Clean uptrend",
                reason_codes=["PLAYBOOK_BULL_CONTINUATION"],
            )
        )
        store.on_sizing(
            SizingDecision(
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=10,
                base_size=1.5,
                confidence_multiplier=0.8,
                stress_discount=0.95,
                execution_haircut=1.0,
                final_leverage=1.14,
                reason_codes=["SIZE_BASE_VOL_TARGET"],
            )
        )
        store.on_exit(
            ExitDecision(
                symbol="BTCUSDT_PERP",
                timeframe=Timeframe.TF_4H,
                cutoff_time_ns=10,
                exit_type="TRAILING",
                exit_urgency="IMMEDIATE",
                target_reduction_pct=1.0,
                stop_price=95.0,
                reason_code="TRAILING_STOP_BREACH",
                reason_detail="Price breached trailing stop",
                reason_codes=["EXIT_TRAILING_STOP"],
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
        store.on_deribit_risk(
            DeribitRiskPack(
                symbol="BTC",
                ts_ns=12,
                atm_iv_30d=62.5,
                term_structure_slope=0.08,
                put_call_moneyness_skew_proxy=4.2,
                oi_concentration=0.41,
                block_flow_imbalance=0.18,
                quality_flags=["OK"],
            )
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
        self.assertIn("market_state", snapshot)
        self.assertIn("active_playbook", snapshot)
        self.assertIn("latest_sizing", snapshot)
        self.assertIn("latest_exit", snapshot)
        self.assertIn("position_state", snapshot)
        self.assertIn("latest_target", snapshot)
        self.assertIn("deribit_risk", snapshot)
        self.assertIn("deribit_history", snapshot)
        self.assertFalse(snapshot["directional_bias_meta"]["is_stale"])
        self.assertEqual(snapshot["active_playbook"]["playbook_id"], "BULL_CONTINUATION")
        self.assertAlmostEqual(snapshot["latest_sizing"]["final_leverage"], 1.14)
        self.assertEqual(snapshot["latest_exit"]["exit_type"], "TRAILING")
        self.assertAlmostEqual(snapshot["deribit_risk"]["atm_iv_30d"], 62.5)
        self.assertEqual(len(snapshot["deribit_history"]), 1)
