import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.app import rl_tune
from zenquant.execution import ExecutionKernelConfig
from zenquant.historical import HistoricalBacktestConfig
from zenquant.research import rl_tuner
from zenquant.research.splits import default_btc_split_spec


class RLTunerConfigTests(unittest.TestCase):
    def test_signal_weight_builder_caps_redundant_funding_crowding_pair(self) -> None:
        weights = rl_tuner.build_signal_weight_overrides(
            {
                "w_funding": 0.20,
                "w_crowding": 0.20,
            }
        )

        self.assertLessEqual(weights["funding_alpha_signal"], 0.12)
        self.assertLessEqual(weights["crowding_signal"], 0.05)
        self.assertLessEqual(
            weights["funding_alpha_signal"] + weights["crowding_signal"],
            0.14,
        )

    def test_execution_builder_keeps_paulwei_philosophy_fixed(self) -> None:
        config = rl_tuner.build_execution_config(
            {
                "pyramid_initial_pct": 0.4,
                "pyramid_add_pct": 0.45,
                "pyramid_short_initial_pct": 0.15,
                "pyramid_short_add_pct": 0.12,
                "pyramid_spacing_atr": 1.0,
                "dca_max_adverse_atr": 4.0,
                "dca_spacing_atr": 1.5,
                "dca_size_decay": 0.6,
                "exit_unwind_pct": 0.2,
                "flip_min_confidence": 0.7,
            }
        )

        self.assertIsInstance(config, ExecutionKernelConfig)
        self.assertTrue(config.dca_on_loss_long)
        self.assertTrue(config.gradual_exit_enabled)
        self.assertTrue(config.adaptive_pyramid_enabled)
        self.assertAlmostEqual(config.pyramid_initial_pct, 0.4)
        self.assertAlmostEqual(config.pyramid_add_pct, 0.45)
        self.assertAlmostEqual(config.pyramid_short_initial_pct, 0.15)
        self.assertAlmostEqual(config.pyramid_short_add_pct, 0.12)
        self.assertAlmostEqual(config.pyramid_spacing_atr, 1.0)
        self.assertAlmostEqual(config.dca_max_adverse_atr, 4.0)
        self.assertAlmostEqual(config.dca_spacing_atr, 1.5)
        self.assertAlmostEqual(config.dca_size_decay, 0.6)
        self.assertAlmostEqual(config.exit_unwind_pct, 0.2)
        self.assertAlmostEqual(config.flip_min_confidence, 0.7)

    def test_env_backtest_passes_execution_config_into_replay(self) -> None:
        captured: dict[str, HistoricalBacktestConfig] = {}

        class FakeMetrics:
            sharpe = 1.2
            net_pnl = 1234.0
            max_drawdown = 0.11
            trade_count = 22

        class FakeResult:
            metrics = FakeMetrics()

        class FakeBacktest:
            def __init__(self, config: HistoricalBacktestConfig) -> None:
                captured["config"] = config

            def run(self):
                return FakeResult()

        env = rl_tuner.ParamTunerEnv(split_spec=default_btc_split_spec())
        params = {
            "pyramid_initial_pct": 0.4,
            "pyramid_add_pct": 0.15,
            "pyramid_short_initial_pct": 0.15,
            "pyramid_short_add_pct": 0.12,
            "pyramid_spacing_atr": 1.0,
            "dca_max_adverse_atr": 2.0,
            "dca_spacing_atr": 1.5,
            "dca_size_decay": 0.6,
            "exit_unwind_pct": 0.4,
            "flip_min_confidence": 0.7,
        }

        with patch.object(rl_tuner, "HistoricalBacktest", FakeBacktest):
            result = env._run_backtest(params, rl_tuner.TRAIN_WINDOWS[0])

        self.assertEqual(result.sharpe, 1.2)
        config = captured["config"]
        self.assertIsNotNone(config.execution_config)
        assert config.execution_config is not None
        self.assertAlmostEqual(config.execution_config.pyramid_initial_pct, 0.4)
        self.assertAlmostEqual(config.execution_config.pyramid_add_pct, 0.15)
        self.assertAlmostEqual(config.execution_config.pyramid_short_initial_pct, 0.15)
        self.assertAlmostEqual(config.execution_config.pyramid_short_add_pct, 0.12)
        self.assertAlmostEqual(config.execution_config.pyramid_spacing_atr, 1.0)
        self.assertAlmostEqual(config.execution_config.dca_max_adverse_atr, 2.0)
        self.assertAlmostEqual(config.execution_config.dca_spacing_atr, 1.5)
        self.assertAlmostEqual(config.execution_config.dca_size_decay, 0.6)
        self.assertAlmostEqual(config.execution_config.exit_unwind_pct, 0.4)
        self.assertAlmostEqual(config.execution_config.flip_min_confidence, 0.7)


class RLTuneCliTests(unittest.TestCase):
    def test_cli_writes_json_summary(self) -> None:
        payload = {
            "best_params": {"pyramid_initial_pct": 0.3, "w_momentum": 0.45},
            "train_sharpe": 1.75,
            "dev_sharpe": 1.25,
            "all_results": [(1.75, {"w_momentum": 0.45})],
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "rl.json"
            with (
                patch.object(rl_tune, "train_tuner", return_value=payload),
                patch(
                    "sys.argv",
                    ["zenquant-rl-tune", "--episodes", "12", "--quiet", "--output", str(output)],
                ),
            ):
                rl_tune.main()

            saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(saved["train_sharpe"], 1.75)
        self.assertEqual(saved["best_params"]["pyramid_initial_pct"], 0.3)


if __name__ == "__main__":
    unittest.main()
