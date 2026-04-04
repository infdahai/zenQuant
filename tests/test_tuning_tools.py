import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PARAM_SWEEP = _load_module("param_sweep_module", "scripts/param_sweep.py")


def _mock_backtest_worker(args):
    """Module-level mock for backtest worker (must be picklable)."""
    params = args[0]
    bull_lev = params.get("bull_leverage", 2.5)
    return params, {
        "sharpe": bull_lev * 1.0,
        "sortino": bull_lev * 0.8,
        "net_pnl": bull_lev * 10000,
        "max_drawdown": 0.1,
        "win_rate": 0.55,
        "profit_factor": 1.5,
        "trade_count": 100,
        "fee_drag_pct": 0.02,
    }
COMPARE_RESULTS = _load_module("compare_results_module", "scripts/compare_results.py")


@dataclass(slots=True)
class FakeMetrics:
    sharpe: float
    net_pnl: float
    max_drawdown: float
    profit_factor: float
    win_rate: float
    trade_count: int


@dataclass(slots=True)
class FakeDiagnostics:
    playbook_counts: dict[str, int]
    actionable_exit_counts: dict[str, int]
    risk_flag_counts: dict[str, int]
    top_target_reason_codes: dict[str, int]


@dataclass(slots=True)
class FakeBacktestResult:
    metrics: FakeMetrics
    diagnostics: FakeDiagnostics


class TuningToolsTests(unittest.TestCase):
    def test_quick_and_full_sweep_sizes_are_explicit(self) -> None:
        self.assertEqual(len(PARAM_SWEEP._build_param_grid(PARAM_SWEEP.quick_sweep())), 3)
        self.assertEqual(len(PARAM_SWEEP._build_param_grid(PARAM_SWEEP.full_sweep())), 243)

    def test_run_sweep_emits_current_metrics_schema(self) -> None:
        fake_metrics = FakeMetrics(
            sharpe=1.25,
            net_pnl=4200.0,
            max_drawdown=0.12,
            profit_factor=1.8,
            win_rate=0.55,
            trade_count=21,
        )
        fake_diagnostics = FakeDiagnostics(
            playbook_counts={"BULL_CONTINUATION": 3},
            actionable_exit_counts={"TRAILING": 1},
            risk_flag_counts={"OK": 5},
            top_target_reason_codes={"PLAYBOOK": 3},
        )
        fake_result = FakeBacktestResult(metrics=fake_metrics, diagnostics=fake_diagnostics)

        class FakeBacktest:
            def __init__(self, config):
                self.config = config

            def run(self):
                return fake_result

        config = PARAM_SWEEP.quick_sweep()
        config.verbose = False

        with patch.object(PARAM_SWEEP, "HistoricalBacktest", FakeBacktest):
            results = PARAM_SWEEP.run_sweep(config)

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].metrics["sharpe"], 1.25)
        self.assertEqual(results[0].metrics["trade_count"], 21)
        self.assertIn("playbook_counts", results[0].diagnostics)

    def test_compare_results_loads_saved_sweep_output(self) -> None:
        payload = {
            "schema_version": "param_sweep.v1",
            "total_runs": 1,
            "results": [
                {
                    "run_id": 1,
                    "params": {"bull_leverage": 3.0},
                    "metrics": {
                        "sharpe": 1.1,
                        "net_pnl": 1000.0,
                        "max_drawdown": 0.08,
                        "profit_factor": 1.7,
                        "win_rate": 0.51,
                        "trade_count": 12,
                    },
                    "diagnostics": {
                        "playbook_counts": {"BULL_CONTINUATION": 2},
                        "actionable_exit_counts": {"TRAILING": 1},
                        "risk_flag_counts": {"OK": 2},
                        "top_target_reason_codes": {"PLAYBOOK": 2},
                    },
                    "runtime_s": 1.4,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "sweep.json"
            output.write_text(json.dumps(payload), encoding="utf-8")
            results = COMPARE_RESULTS.load_results(output)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].sharpe, 1.1)
        self.assertEqual(results[0].total_trades, 12)
        ranked = COMPARE_RESULTS.rank_by_metric(results, "sharpe", top_n=1)
        self.assertEqual(ranked[0].run_id, 1)


    def test_optuna_bayesian_sweep_finds_best_params(self) -> None:
        """Verify Bayesian sweep via Optuna runs and finds optimal parameters."""
        try:
            import optuna  # noqa: F401
        except ImportError:
            self.skipTest("optuna not installed")

        from zenquant.research.auto_research import Campaign, CampaignConfig, SearchSpace

        # Create a simple search space
        config = CampaignConfig(
            search_space=SearchSpace(
                strategy_params={
                    "bull_leverage": [2.0, 2.5, 3.0, 3.5],
                    "bear_leverage": [1.5, 2.0, 2.5],
                },
            ),
            sweep_mode="bayesian",
            n_trials=5,  # Small for test speed
            verbose=False,
        )

        with patch("zenquant.research.auto_research._run_backtest_worker", _mock_backtest_worker):
            campaign = Campaign(config)
            campaign._workers = 1  # Force single-process for test
            report = campaign.run()

        # Should have run n_trials backtests
        self.assertEqual(report.sweep.total_combinations, 5)
        self.assertGreater(len(report.sweep.results), 0)

        # Best params should prefer high bull_leverage (highest sharpe)
        best = report.sweep.top_n[0]
        self.assertEqual(best["params"]["bull_leverage"], 3.5)


if __name__ == "__main__":
    unittest.main()
