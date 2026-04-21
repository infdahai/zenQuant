import json
import tempfile
import types
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from zenquant.app import rl_tune
from zenquant.execution import ExecutionKernelConfig
from zenquant.historical import HistoricalBacktestConfig
from zenquant.research import rl_tuner
from zenquant.research.splits import default_btc_split_spec


class RLTunerConfigTests(unittest.TestCase):
    def test_resolve_torch_device_auto_prefers_mps(self) -> None:
        fake_torch = types.SimpleNamespace(
            backends=types.SimpleNamespace(
                mps=types.SimpleNamespace(
                    is_available=lambda: True,
                    is_built=lambda: True,
                )
            )
        )

        with patch.dict("sys.modules", {"torch": fake_torch}):
            resolved, resolved_from, mps_allowed, fallback_reason = rl_tuner._resolve_torch_device("auto")

        self.assertEqual(resolved, "mps")
        self.assertEqual(resolved_from, "auto")
        self.assertTrue(mps_allowed)
        self.assertIsNone(fallback_reason)

    def test_resolve_torch_device_falls_back_when_mps_unavailable(self) -> None:
        fake_torch = types.SimpleNamespace(
            backends=types.SimpleNamespace(
                mps=types.SimpleNamespace(
                    is_available=lambda: False,
                    is_built=lambda: True,
                )
            )
        )

        with patch.dict("sys.modules", {"torch": fake_torch}):
            resolved, resolved_from, mps_allowed, fallback_reason = rl_tuner._resolve_torch_device("auto")

        self.assertEqual(resolved, "cpu")
        self.assertEqual(resolved_from, "auto")
        self.assertFalse(mps_allowed)
        self.assertEqual(fallback_reason, "mps_unavailable")

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
            terminated_early = False

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

    def test_env_backtest_respects_research_compute_mode(self) -> None:
        captured: dict[str, HistoricalBacktestConfig] = {}

        class FakeMetrics:
            sharpe = 1.0
            net_pnl = 10.0
            max_drawdown = 0.1
            trade_count = 25

        class FakeResult:
            metrics = FakeMetrics()
            terminated_early = False

        class FakeBacktest:
            def __init__(self, config: HistoricalBacktestConfig) -> None:
                captured["config"] = config

            def run(self):
                return FakeResult()

        env = rl_tuner.ParamTunerEnv(
            split_spec=default_btc_split_spec(),
            base_config=HistoricalBacktestConfig(
                execution_timeframe=rl_tuner.Timeframe.TF_5M,
                context_timeframe=rl_tuner.Timeframe.TF_1H,
                research_compute_mode="lightweight_train_search",
                profiling_enabled=True,
                early_stop_enabled=True,
                trial_runtime_budget_s=90.0,
            ),
        )

        with patch.object(rl_tuner, "HistoricalBacktest", FakeBacktest):
            env._run_backtest({}, rl_tuner.TRAIN_WINDOWS[0])

        self.assertEqual(captured["config"].research_compute_mode, "lightweight_train_search")
        self.assertTrue(captured["config"].early_stop_enabled)
        self.assertEqual(captured["config"].trial_runtime_budget_s, 90.0)

    def test_train_tuner_reports_mps_device_metadata(self) -> None:
        heartbeats: list[dict[str, object]] = []

        class FakeManager:
            def __init__(self) -> None:
                self._shared = [
                    {
                        "params": {"w_momentum": 0.45},
                        "sharpe": 1.25,
                        "pnl": 123.0,
                        "max_dd": 0.09,
                        "trades": 24,
                        "candidate_score": 1.2,
                    }
                ]

            def list(self):
                return self._shared

            def shutdown(self) -> None:
                return None

        class FakeVecEnv:
            def __init__(self, env_fns: list[Callable[..., object]], *args, **kwargs) -> None:
                self.env_fns = env_fns

            def close(self) -> None:
                return None

        class FakePPO:
            def __init__(self, *_args, device: str, **_kwargs) -> None:
                self.device = device

            def learn(self, total_timesteps: int, callback) -> None:
                callback.num_timesteps = total_timesteps
                callback._on_step()

        with (
            patch.object(rl_tuner, "_resolve_torch_device", return_value=("mps", "auto", True, None)),
            patch.object(rl_tuner, "Manager", return_value=FakeManager()),
            patch("stable_baselines3.PPO", FakePPO),
            patch("stable_baselines3.common.vec_env.DummyVecEnv", FakeVecEnv),
            patch("stable_baselines3.common.vec_env.SubprocVecEnv", FakeVecEnv),
        ):
            payload = rl_tuner.train_tuner(
                total_timesteps=1,
                max_episodes=1,
                status_callback=lambda **kwargs: heartbeats.append(kwargs),
            )

        self.assertEqual(payload["search_status"]["rl_device"], "mps")
        self.assertEqual(payload["search_status"]["rl_device_resolved_from"], "auto")
        self.assertIsNone(payload["search_status"]["rl_device_fallback_reason"])
        completed = [item for item in heartbeats if item.get("event") == "rl_completed"]
        self.assertEqual(completed[-1]["rl_device"], "mps")

    def test_train_tuner_falls_back_to_cpu_on_mps_init_error(self) -> None:
        class FakeManager:
            def __init__(self) -> None:
                self._shared = [
                    {
                        "params": {"w_momentum": 0.30},
                        "sharpe": 0.8,
                        "pnl": 50.0,
                        "max_dd": 0.1,
                        "trades": 18,
                        "candidate_score": 0.75,
                    }
                ]

            def list(self):
                return self._shared

            def shutdown(self) -> None:
                return None

        class FakeVecEnv:
            def __init__(self, env_fns: list[Callable[..., object]], *args, **kwargs) -> None:
                self.env_fns = env_fns

            def close(self) -> None:
                return None

        class FakePPO:
            def __init__(self, *_args, device: str, **_kwargs) -> None:
                if device == "mps":
                    raise RuntimeError("mps init failed")
                self.device = device

            def learn(self, total_timesteps: int, callback) -> None:
                callback.num_timesteps = total_timesteps
                callback._on_step()

        with (
            patch.object(rl_tuner, "_resolve_torch_device", return_value=("mps", "auto", True, None)),
            patch.object(rl_tuner, "Manager", return_value=FakeManager()),
            patch("stable_baselines3.PPO", FakePPO),
            patch("stable_baselines3.common.vec_env.DummyVecEnv", FakeVecEnv),
            patch("stable_baselines3.common.vec_env.SubprocVecEnv", FakeVecEnv),
        ):
            payload = rl_tuner.train_tuner(total_timesteps=1, max_episodes=1)

        self.assertEqual(payload["search_status"]["rl_device"], "cpu")
        self.assertEqual(
            payload["search_status"]["rl_device_fallback_reason"],
            "mps_init_error:RuntimeError",
        )


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
