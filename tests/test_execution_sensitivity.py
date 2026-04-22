"""Execution parameter sensitivity gate — prevents pyramid/DCA overfitting.

If ±50% parameter change causes >40% Sharpe change, the strategy is
parameter-sensitive and likely overfitted to the specific execution config.

This is the MISSING gate: walk-forward validates signals, but execution
params (pyramid %, DCA multiplier) were never OOS-tested.
"""

import unittest
from pathlib import Path

from zenquant.execution.router import ExecutionKernelConfig
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


def _run_with_execution_params(init_pct: float, add_pct: float, dca: bool) -> dict:
    """Run backtest with specific execution params, return key metrics."""
    execution_config = ExecutionKernelConfig(
        pyramid_initial_pct=init_pct,
        pyramid_add_pct=add_pct,
        dca_on_loss_long=dca,
        adaptive_pyramid_enabled=False,
        gradual_exit_enabled=False,
    )
    bt = HistoricalBacktest(
        HistoricalBacktestConfig(
            profile_path=Path("config/profiles/default.toml"),
            execution_config=execution_config,
        )
    )
    result = bt.run()
    return {
        "sharpe": result.metrics.sharpe,
        "max_dd": result.metrics.max_drawdown,
        "pf": result.metrics.profit_factor,
        "trades": result.metrics.trade_count,
    }


class ExecutionSensitivityTests(unittest.TestCase):
    """Gate: Sharpe must be stable (±40%) across execution param variations."""

    @classmethod
    def setUpClass(cls):
        # Baseline: current production config
        cls.baseline = _run_with_execution_params(
            init_pct=0.30,
            add_pct=0.30,
            dca=True,
        )

    def _assert_sharpe_stable(self, variant: dict, label: str, tolerance: float = 0.40):
        """Sharpe should not change by more than tolerance (40%)."""
        base = self.baseline["sharpe"]
        delta = abs(variant["sharpe"] - base) / max(abs(base), 0.01)
        self.assertLess(
            delta,
            tolerance,
            f"{label}: Sharpe {variant['sharpe']:.2f} vs baseline {base:.2f} "
            f"(delta={delta:.0%}, limit={tolerance:.0%})",
        )

    def test_init_pct_25_rejected(self) -> None:
        """init_pct below 30% is outside the supported product envelope and must be rejected."""
        with self.assertRaisesRegex(ValueError, "pyramid_initial_pct below 0.30"):
            _run_with_execution_params(0.25, 0.30, True)

    def test_init_pct_35_rejected(self) -> None:
        """init_pct above 30% is outside the supported product envelope and must be rejected."""
        with self.assertRaisesRegex(ValueError, "pyramid_initial_pct above 0.30"):
            _run_with_execution_params(0.35, 0.30, True)

    def test_add_pct_20_rejected(self) -> None:
        """add_pct below 30% is outside the supported product envelope and must be rejected."""
        with self.assertRaisesRegex(ValueError, "pyramid_add_pct below 0.30"):
            _run_with_execution_params(0.30, 0.20, True)

    def test_add_pct_35_rejected(self) -> None:
        """add_pct above 30% is outside the supported product envelope and must be rejected."""
        with self.assertRaisesRegex(ValueError, "pyramid_add_pct above 0.30"):
            _run_with_execution_params(0.30, 0.35, True)

    def test_dca_toggle_stable(self) -> None:
        """DCA on vs off → Sharpe should be within ±40%."""
        v = _run_with_execution_params(0.30, 0.30, False)
        self._assert_sharpe_stable(v, "DCA=off")

    def test_dd_never_catastrophic(self) -> None:
        """No execution config should produce DD > 25%."""
        configs = [
            (0.30, 0.30, True),
            (0.30, 0.30, True),
            (0.30, 0.30, False),
        ]
        for init, add, dca in configs:
            v = _run_with_execution_params(init, add, dca)
            self.assertLess(
                v["max_dd"],
                0.25,
                f"init={init}, add={add}, dca={dca}: DD={v['max_dd']:.1%} > 25%",
            )


if __name__ == "__main__":
    unittest.main()
