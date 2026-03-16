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


if __name__ == "__main__":
    unittest.main()
