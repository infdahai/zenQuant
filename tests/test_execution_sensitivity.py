"""Execution parameter sensitivity gate — prevents pyramid/DCA overfitting.

If ±50% parameter change causes >40% Sharpe change, the strategy is
parameter-sensitive and likely overfitted to the specific execution config.

This is the MISSING gate: walk-forward validates signals, but execution
params (pyramid %, DCA multiplier) were never OOS-tested.
"""

import unittest
from pathlib import Path

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


def _run_with_execution_params(init_pct: float, add_pct: float, dca: bool) -> dict:
    """Run backtest with specific execution params, return key metrics."""
    bt = HistoricalBacktest(
        HistoricalBacktestConfig(
            profile_path=Path("config/profiles/default.toml"),
        )
    )
    bt.execution_kernel._config.pyramid_initial_pct = init_pct
    bt.execution_kernel._config.pyramid_add_pct = add_pct
    bt.execution_kernel._config.dca_on_loss_long = dca
    # Disable adaptive scaling to isolate base parameter sensitivity
    bt.execution_kernel._config.adaptive_pyramid_enabled = False
    bt.execution_kernel._config.gradual_exit_enabled = False
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

    def test_init_pct_20_stable(self) -> None:
        """init_pct=20% (vs 30%) → Sharpe should be within ±40%."""
        v = _run_with_execution_params(0.20, 0.30, True)
        self._assert_sharpe_stable(v, "init_pct=20%")

    def test_init_pct_50_stable(self) -> None:
        """init_pct=50% (vs 30%) → Sharpe should be within ±40%."""
        v = _run_with_execution_params(0.50, 0.30, True)
        self._assert_sharpe_stable(v, "init_pct=50%")

    def test_add_pct_15_stable(self) -> None:
        """add_pct=15% (vs 30%) → Sharpe should be within ±40%."""
        v = _run_with_execution_params(0.30, 0.15, True)
        self._assert_sharpe_stable(v, "add_pct=15%")

    def test_add_pct_50_stable(self) -> None:
        """add_pct=50% (vs 30%) → Sharpe should be within ±40%."""
        v = _run_with_execution_params(0.30, 0.50, True)
        self._assert_sharpe_stable(v, "add_pct=50%")

    def test_dca_toggle_stable(self) -> None:
        """DCA on vs off → Sharpe should be within ±40%."""
        v = _run_with_execution_params(0.30, 0.30, False)
        self._assert_sharpe_stable(v, "DCA=off")

    def test_dd_never_catastrophic(self) -> None:
        """No execution config should produce DD > 25%."""
        configs = [
            (0.20, 0.20, True),
            (0.50, 0.50, True),
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
