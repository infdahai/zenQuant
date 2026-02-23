import unittest

from zenquant.app.live_state import DashboardStateStore
from zenquant.domain.models import (
    AlphaSignal,
    ExecutionReport,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderStatus,
    PAPattern,
    SignalType,
    TradeTick,
)
from zenquant.factor_lab.models import FactorWeight
from zenquant.risk import RiskGuard


class DashboardStateStoreTests(unittest.TestCase):
    def test_snapshot_contains_latest_views(self) -> None:
        state = DashboardStateStore()
        risk = RiskGuard()

        state.on_trade(
            TradeTick(
                symbol="BTC_USDT",
                exchange="BINANCE",
                price=40_123.4,
                size=0.23,
                trade_id=1,
                is_buyer_maker=False,
                exchange_timestamp_ns=10,
                local_timestamp_ns=11,
            )
        )
        state.on_orderbook(
            OrderBookSnapshot(
                symbol="BTC_USDT",
                exchange="BINANCE",
                exchange_timestamp_ns=10,
                local_timestamp_ns=11,
                is_snapshot=True,
                bids=[OrderBookLevel(price=40_120.0, quantity=1.3)],
                asks=[OrderBookLevel(price=40_121.5, quantity=1.1)],
            )
        )
        state.on_signal(
            AlphaSignal(
                factor_id="MOM",
                symbol="BTC_USDT",
                type=SignalType.CONTINUOUS,
                continuous_value=0.82,
                pattern_event=PAPattern.NONE,
                confidence_ic=0.61,
                timestamp_ns=11,
            )
        )
        state.on_report(
            ExecutionReport(
                client_order_id="cid-1",
                exchange_order_id="eid-1",
                status=OrderStatus.FILLED,
                cumulative_filled_qty=0.2,
                volume_weighted_avg_price=40_123.4,
                commission=3.2,
                commission_asset="USDT",
                transaction_time_ns=12,
            )
        )
        state.on_weight(
            FactorWeight(
                symbol="BTC_USDT",
                factor_id="MOM",
                weight=0.42,
                ic_mean=0.18,
                orthogonal_penalty=0.95,
                portfolio_sigma=0.21,
                timestamp_ns=12,
            )
        )

        snapshot = state.snapshot(risk)
        self.assertEqual(snapshot["symbol"], "BTC_USDT")
        self.assertEqual(snapshot["exchange"], "BINANCE")
        self.assertAlmostEqual(snapshot["last_price"], 40_123.4)
        self.assertAlmostEqual(snapshot["spread"], 1.5)
        self.assertEqual(len(snapshot["signals"]), 1)
        self.assertEqual(len(snapshot["reports"]), 1)
        self.assertEqual(len(snapshot["weights"]), 1)


if __name__ == "__main__":
    unittest.main()

