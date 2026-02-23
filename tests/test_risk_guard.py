import unittest

from zenquant.domain.models import ExecutionReport, OrderStatus, OrderType, SubmitOrder
from zenquant.risk import RiskGuard, RiskGuardConfig


class RiskGuardTests(unittest.TestCase):
    def test_kill_switch_blocks_new_risk_and_allows_reduce_only(self) -> None:
        guard = RiskGuard(RiskGuardConfig(max_drawdown=0.05))
        guard.update_equity(100_000.0)
        guard.update_equity(93_000.0)
        self.assertTrue(guard.kill_switch_active)

        guard.positions["BTC_USDT"] = 1.0

        increase_order = SubmitOrder(
            client_order_id="1",
            symbol="BTC_USDT",
            type=OrderType.MARKET,
            limit_price=0.0,
            quantity=0.2,
            side=1,
            reduce_only=False,
            max_slippage_tolerance=0.0005,
            enable_synthetic_arb=False,
        )
        decision = guard.allow_order(increase_order, mark_price=40_000.0)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "kill_switch_active")

        reduce_order = SubmitOrder(
            client_order_id="2",
            symbol="BTC_USDT",
            type=OrderType.MARKET,
            limit_price=0.0,
            quantity=0.5,
            side=-1,
            reduce_only=True,
            max_slippage_tolerance=0.0005,
            enable_synthetic_arb=False,
        )
        reduce_decision = guard.allow_order(reduce_order, mark_price=40_000.0)
        self.assertTrue(reduce_decision.allowed)

    def test_position_limit(self) -> None:
        guard = RiskGuard(RiskGuardConfig(max_symbol_position=1.0))
        guard.positions["BTC_USDT"] = 0.8
        order = SubmitOrder(
            client_order_id="3",
            symbol="BTC_USDT",
            type=OrderType.MARKET,
            limit_price=0.0,
            quantity=0.3,
            side=1,
            reduce_only=False,
            max_slippage_tolerance=0.0005,
            enable_synthetic_arb=False,
        )
        decision = guard.allow_order(order, mark_price=40_000.0)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "symbol_position_limit")

    def test_fill_updates_position(self) -> None:
        guard = RiskGuard()
        order = SubmitOrder(
            client_order_id="4",
            symbol="BTC_USDT",
            type=OrderType.MARKET,
            limit_price=0.0,
            quantity=0.3,
            side=1,
            reduce_only=False,
            max_slippage_tolerance=0.0005,
            enable_synthetic_arb=False,
        )
        report = ExecutionReport(
            client_order_id="4",
            exchange_order_id="SIM-1",
            status=OrderStatus.FILLED,
            cumulative_filled_qty=0.3,
            volume_weighted_avg_price=40_000.0,
            commission=4.0,
            commission_asset="USDT",
            transaction_time_ns=1,
            reject_reason="",
        )
        guard.on_fill(report, order)
        self.assertAlmostEqual(guard.positions["BTC_USDT"], 0.3)


if __name__ == "__main__":
    unittest.main()

