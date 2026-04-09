"""RL-based parameter tuner for train-only clean-tuning exploration."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from zenquant.execution import ExecutionKernelConfig
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.indicator import IndicatorEngineConfig
from zenquant.research.splits import DatasetSplitSpec, SplitWindow, default_btc_split_spec
from zenquant.risk import RiskOverlayConfig
from zenquant.strategy import ARCStrategyConfig

PARAM_GRID = {
    "dd_step_1_long": [0.10, 0.15, 0.20],
    "dd_step_1_short": [0.05, 0.08, 0.12],
    "max_leverage": [4.0, 6.0, 8.0],
    "target_vol_4h": [0.80, 1.10, 1.40],
    "vpin_threshold": [0.08, 0.10, 0.12],
    "crowding_threshold": [1.8, 2.2, 2.5],
    "vol_shock_threshold": [0.80, 0.95, 1.10],
    "pyramid_initial_pct": [0.20, 0.25, 0.30],
    "pyramid_add_pct": [0.10, 0.20, 0.30],
    "pyramid_short_initial_pct": [0.10, 0.15, 0.20],
    "pyramid_short_add_pct": [0.08, 0.12, 0.16],
    "pyramid_spacing_atr": [0.50, 0.75, 1.00],
    "dca_max_adverse_atr": [2.5, 3.0, 3.5],
    "dca_spacing_atr": [0.75, 1.25, 1.75],
    "dca_size_decay": [0.60, 0.75, 0.90],
    "exit_unwind_pct": [0.20, 0.25, 0.30],
    "flip_min_confidence": [0.55, 0.65, 0.75],
    "trailing_stop_atr_mult": [2.0, 2.5, 3.0],
    "bias_max_age_bars": [6, 8, 12],
}

@dataclass(slots=True, frozen=True)
class TunerResult:
    params: dict[str, float]
    sharpe: float
    pnl: float
    max_dd: float
    trades: int
    candidate_score: float = -5.0


def _window_chunks(window: SplitWindow, *, months: int = 6) -> list[tuple[int, int]]:
    if window.end_ns is None:
        raise ValueError("train/dev windows must have finite end_ns")
    dt = datetime.fromtimestamp(window.start_ns / 1e9, tz=UTC)
    end_dt = datetime.fromtimestamp(window.end_ns / 1e9, tz=UTC)
    chunks: list[tuple[int, int]] = []
    current = datetime(dt.year, dt.month, 1, tzinfo=UTC)
    while current < end_dt:
        month_index = (current.month - 1) + months
        year = current.year + month_index // 12
        month = month_index % 12 + 1
        next_dt = datetime(year, month, 1, tzinfo=UTC)
        chunks.append((int(current.timestamp() * 1e9), int(min(next_dt, end_dt).timestamp() * 1e9)))
        current = next_dt
    return chunks


def _train_windows(split_spec: DatasetSplitSpec) -> list[tuple[int, int]]:
    return _window_chunks(split_spec.train, months=6)


TRAIN_WINDOWS = _train_windows(default_btc_split_spec())


def build_strategy_config(params: dict[str, float]) -> ARCStrategyConfig:
    return ARCStrategyConfig(
        target_vol_4h=params.get("target_vol_4h", 1.10),
        target_vol_1d=1.30,
        max_leverage=params.get("max_leverage", 6.0),
        trailing_stop_atr_mult=params.get("trailing_stop_atr_mult", 2.5),
        bias_max_age_bars=int(params.get("bias_max_age_bars", 8)),
    )


def build_risk_config(params: dict[str, float]) -> RiskOverlayConfig:
    return RiskOverlayConfig(
        max_leverage=params.get("max_leverage", 6.0),
        dd_step_1_long=params.get("dd_step_1_long", 0.15),
        dd_step_1_short=params.get("dd_step_1_short", 0.08),
        vpin_toxic_threshold=params.get("vpin_threshold", 0.10),
        crowding_threshold=params.get("crowding_threshold", 2.2),
        vol_shock_threshold=params.get("vol_shock_threshold", 0.95),
    )


def build_execution_config(params: dict[str, float]) -> ExecutionKernelConfig:
    return ExecutionKernelConfig(
        pyramid_enabled=True,
        dca_on_loss_long=True,
        adaptive_pyramid_enabled=True,
        gradual_exit_enabled=True,
        pyramid_initial_pct=params.get("pyramid_initial_pct", 0.30),
        pyramid_add_pct=params.get("pyramid_add_pct", 0.30),
        pyramid_short_initial_pct=params.get("pyramid_short_initial_pct", 0.20),
        pyramid_short_add_pct=params.get("pyramid_short_add_pct", 0.20),
        pyramid_spacing_atr=params.get("pyramid_spacing_atr", 0.50),
        dca_max_adverse_atr=params.get("dca_max_adverse_atr", 3.0),
        dca_spacing_atr=params.get("dca_spacing_atr", 0.75),
        dca_size_decay=params.get("dca_size_decay", 0.75),
        exit_unwind_pct=params.get("exit_unwind_pct", 0.30),
        flip_min_confidence=params.get("flip_min_confidence", 0.50),
    )


def build_indicator_config(params: dict[str, float]) -> IndicatorEngineConfig:
    return IndicatorEngineConfig(
        vol_window=int(params.get("vol_window", 20)),
        cvd_window=int(params.get("cvd_window", 8)),
        roc_lookback=int(params.get("roc_lookback", 12)),
    )


def build_signal_weight_overrides(params: dict[str, float]) -> dict[str, float]:
    return {
        "multi_momentum_signal": params.get("w_momentum", 0.45),
        "crash_reversal_signal": params.get("w_crash", 0.25),
        "cvd_signal": params.get("w_cvd", 0.25),
        "funding_alpha_signal": params.get("w_funding", 0.10),
        "crowding_signal": params.get("w_crowding", 0.10),
        "liquidity_signal": params.get("w_liquidity", 0.20),
    }


class ParamTunerEnv(gym.Env):
    """Gym environment where each step evaluates one train-window parameter choice."""

    metadata: dict[str, list[str]] = {"render_modes": []}  # noqa: RUF012

    def __init__(
        self,
        *,
        param_grid: dict[str, list[float]] | None = None,
        split_spec: DatasetSplitSpec | None = None,
        base_config: HistoricalBacktestConfig | None = None,
    ) -> None:
        super().__init__()
        self._param_grid = param_grid or PARAM_GRID
        self._split_spec = split_spec or default_btc_split_spec()
        self._base_config = base_config or HistoricalBacktestConfig()
        self._train_windows = _train_windows(self._split_spec)

        self._param_names = list(self._param_grid.keys())
        self._param_choices = [self._param_grid[key] for key in self._param_names]
        self.action_space = spaces.MultiDiscrete([len(choices) for choices in self._param_choices])
        self.observation_space = spaces.Box(
            low=np.array([-10.0, -1.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32),
            high=np.array([10.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32),
        )
        self._last_obs = np.zeros(6, dtype=np.float32)
        self._results: list[TunerResult] = []

    def reset(self, *, seed: int | None = None, options: dict | None = None) -> tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        return self._last_obs, {}

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict]:
        params = {
            name: self._param_choices[index][action[index]]
            for index, name in enumerate(self._param_names)
        }
        train_window = random.choice(self._train_windows)
        result = self._run_backtest(params, train_window)
        self._results.append(result)

        reward = result.candidate_score
        self._last_obs = np.array(
            [
                min(10.0, max(-10.0, result.sharpe)),
                min(1.0, max(-1.0, result.pnl / 100_000.0)),
                min(1.0, result.max_dd),
                min(1.0, result.trades / 500.0),
                1.0,
                0.0,
            ],
            dtype=np.float32,
        )
        return self._last_obs, float(reward), True, False, {"result": result}

    def _run_backtest(self, params: dict[str, float], window: tuple[int, int]) -> TunerResult:
        start_ns, end_ns = window
        config = HistoricalBacktestConfig(
            root_dir=self._base_config.root_dir,
            symbol=self._base_config.symbol,
            portfolio_symbol=self._base_config.portfolio_symbol,
            starting_equity=self._base_config.starting_equity,
            start_ns=start_ns,
            end_ns=end_ns,
            profile_path=self._base_config.profile_path,
            options_dir=self._base_config.options_dir,
            liquidation_dir=self._base_config.liquidation_dir,
            strategy_config=build_strategy_config(params),
            risk_config=build_risk_config(params),
            execution_config=build_execution_config(params),
            indicator_config=build_indicator_config(params),
            signal_weight_overrides=build_signal_weight_overrides(params),
            liquidation_data_enabled=self._base_config.liquidation_data_enabled,
            state_liquidation_overlay_enabled=self._base_config.state_liquidation_overlay_enabled,
        )
        try:
            result = HistoricalBacktest(config).run()
            sharpe = result.metrics.sharpe
            pnl = result.metrics.net_pnl
            max_dd = result.metrics.max_drawdown
            trades = result.metrics.trade_count
            candidate_score = (
                sharpe
                - max(0.0, max_dd - 0.10) * 12.0
                - (1.0 if trades < 20 else 0.0)
            )
            return TunerResult(
                params=params,
                sharpe=sharpe,
                pnl=pnl,
                max_dd=max_dd,
                trades=trades,
                candidate_score=candidate_score,
            )
        except Exception:
            return TunerResult(params=params, sharpe=-5.0, pnl=0.0, max_dd=1.0, trades=0, candidate_score=-10.0)


def _evaluate_dev_candidate(
    params: dict[str, float],
    *,
    base_config: HistoricalBacktestConfig,
    split_spec: DatasetSplitSpec,
) -> dict[str, float]:
    config = HistoricalBacktestConfig(
        root_dir=base_config.root_dir,
        symbol=base_config.symbol,
        portfolio_symbol=base_config.portfolio_symbol,
        starting_equity=base_config.starting_equity,
        start_ns=split_spec.dev.start_ns,
        end_ns=split_spec.dev.end_ns,
        profile_path=base_config.profile_path,
        options_dir=base_config.options_dir,
        liquidation_dir=base_config.liquidation_dir,
        strategy_config=build_strategy_config(params),
        risk_config=build_risk_config(params),
        execution_config=build_execution_config(params),
        indicator_config=build_indicator_config(params),
        signal_weight_overrides=build_signal_weight_overrides(params),
        liquidation_data_enabled=base_config.liquidation_data_enabled,
        state_liquidation_overlay_enabled=base_config.state_liquidation_overlay_enabled,
    )
    try:
        result = HistoricalBacktest(config).run()
        return {
            "selection_split": "dev",
            "sharpe": result.metrics.sharpe,
            "pnl": result.metrics.net_pnl,
            "max_drawdown": result.metrics.max_drawdown,
            "trade_count": result.metrics.trade_count,
            "score": result.metrics.sharpe - max(0.0, result.metrics.max_drawdown - 0.18) * 8.0,
        }
    except Exception:
        return {
            "selection_split": "dev",
            "sharpe": -5.0,
            "pnl": 0.0,
            "max_drawdown": 1.0,
            "trade_count": 0,
            "score": -10.0,
        }


def train_tuner(
    total_timesteps: int = 200,
    verbose: int = 1,
    *,
    param_grid: dict[str, list[float]] | None = None,
    split_spec: DatasetSplitSpec | None = None,
    base_config: HistoricalBacktestConfig | None = None,
    selection_split: str = "dev",
) -> dict[str, Any]:
    """Train RL candidate explorer and return train/dev-ranked candidate summary."""
    from stable_baselines3 import PPO

    resolved_split = split_spec or default_btc_split_spec()
    resolved_base = base_config or HistoricalBacktestConfig()
    env = ParamTunerEnv(param_grid=param_grid, split_spec=resolved_split, base_config=resolved_base)
    model = PPO(
        "MlpPolicy",
        env,
        verbose=verbose,
        n_steps=16,
        batch_size=8,
        n_epochs=4,
        learning_rate=3e-4,
    )
    model.learn(total_timesteps=total_timesteps)

    results = env._results
    if not results:
        return {
            "best_params": {},
            "best_score": -10.0,
            "candidate_source": "rl_train_dev_shortlist",
            "train_metrics": {},
            "dev_metrics": {},
            "search_status": {
                "selection_split": selection_split,
                "episodes_completed": 0,
                "train_only_search": True,
                "dev_only_ranking": True,
            },
        }

    best_train = max(results, key=lambda item: item.candidate_score)
    dev_metrics = _evaluate_dev_candidate(best_train.params, base_config=resolved_base, split_spec=resolved_split)
    return {
        "best_params": best_train.params,
        "best_score": dev_metrics["score"],
        "best_sharpe": best_train.sharpe,
        "candidate_source": "rl_train_dev_shortlist",
        "train_metrics": {
            "selection_split": "train",
            "sharpe": best_train.sharpe,
            "pnl": best_train.pnl,
            "max_drawdown": best_train.max_dd,
            "trade_count": best_train.trades,
            "score": best_train.candidate_score,
        },
        "dev_metrics": dev_metrics,
        "all_results": [(result.sharpe, result.params) for result in sorted(results, key=lambda item: -item.candidate_score)[:10]],
        "search_status": {
            "selection_split": selection_split,
            "episodes_completed": len(results),
            "train_only_search": True,
            "dev_only_ranking": selection_split == "dev",
        },
    }
