"""RL-based parameter tuner for parameter-only research optimization.

Uses PPO to learn optimal signal weights, risk thresholds, and execution
speeds/gating parameters.
The skeleton (signals, risk guards, execution) is FIXED.
RL only tunes parameters (血肉), never the architecture (骨架).

Key design:
  - Action space: discrete parameter choices (small, ~37k combinations)
  - State: regime distribution + rolling performance
  - Reward: Sharpe ratio with DD penalty
  - Episode: random 6-month window from Train set (anti-overfit)
  - Validation: Dev set (single run, no tuning)

Usage:
    from zenquant.research.rl_tuner import ParamTunerEnv, train_tuner
    best_params = train_tuner(total_timesteps=5000)
"""

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
from zenquant.risk import RiskOverlayConfig
from zenquant.strategy import ARCStrategyConfig

# Parameter grid: each parameter has discrete choices
# Expanded from 8 → 20 params (26% coverage of 78 total)
PARAM_GRID = {
    # Signal weights (6 active)
    "w_momentum": [0.20, 0.30, 0.45, 0.60],
    "w_crash": [0.15, 0.25, 0.35],
    "w_cvd": [0.15, 0.25, 0.35],
    "w_funding": [0.05, 0.10, 0.15],
    "w_crowding": [0.05, 0.10, 0.15],
    "w_liquidity": [0.10, 0.20, 0.30],
    # Risk: DD stepping (high impact — PaulWei calibration)
    "dd_step_1_long": [0.10, 0.15, 0.20],
    "dd_step_1_short": [0.05, 0.08, 0.12],
    # Risk: leverage and vol targets (core sizing)
    "max_leverage": [4.0, 6.0, 8.0],
    "target_vol_4h": [0.80, 1.10, 1.40],
    # Risk: guard thresholds
    "vpin_threshold": [0.08, 0.10, 0.12],
    "crowding_threshold": [1.8, 2.2, 2.5],
    "vol_shock_threshold": [0.80, 0.95, 1.10],
    # Strategy: state / execution behavior
    "trailing_stop_atr_mult": [2.0, 2.5, 3.0],
    "bias_max_age_bars": [6, 8, 12],
    # Indicator: computation windows
    "vol_window": [15, 20, 30],
    "cvd_window": [6, 8, 12],
    "roc_lookback": [8, 12, 18],
    # Execution: PaulWei long-only DCA + profitable pyramiding + gradual exit
    "pyramid_initial_pct": [0.20, 0.30, 0.40],
    "pyramid_add_pct": [0.15, 0.30, 0.45],
    "dca_max_adverse_atr": [2.0, 3.0, 4.0],
    "exit_unwind_pct": [0.20, 0.30, 0.40],
}

# Train period windows (6-month chunks from 2020-2023)
TRAIN_WINDOWS = []
for year in range(2020, 2024):
    # H1: Jan-Jun, H2: Jul-Dec
    h1_start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1e9)
    h1_end = int(datetime(year, 7, 1, tzinfo=UTC).timestamp() * 1e9)
    h2_start = h1_end
    h2_end = int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1e9)
    TRAIN_WINDOWS.append((h1_start, h1_end))
    TRAIN_WINDOWS.append((h2_start, h2_end))


@dataclass
class TunerResult:
    """Result of one RL episode (one backtest run with specific params)."""

    params: dict[str, float]
    sharpe: float
    pnl: float
    max_dd: float
    trades: int


def build_strategy_config(params: dict[str, float]) -> ARCStrategyConfig:
    """Map tuned params into strategy config without changing strategy structure."""
    return ARCStrategyConfig(
        target_vol_4h=params.get("target_vol_4h", 1.10),
        target_vol_1d=1.30,
        max_leverage=params.get("max_leverage", 6.0),
        trailing_stop_atr_mult=params.get("trailing_stop_atr_mult", 2.5),
        bias_max_age_bars=int(params.get("bias_max_age_bars", 8)),
    )


def build_risk_config(params: dict[str, float]) -> RiskOverlayConfig:
    """Map tuned params into risk overlay thresholds."""
    return RiskOverlayConfig(
        max_leverage=params.get("max_leverage", 6.0),
        dd_step_1_long=params.get("dd_step_1_long", 0.15),
        dd_step_1_short=params.get("dd_step_1_short", 0.08),
        vpin_toxic_threshold=params.get("vpin_threshold", 0.10),
        crowding_threshold=params.get("crowding_threshold", 2.2),
        vol_shock_threshold=params.get("vol_shock_threshold", 0.95),
    )


def build_execution_config(params: dict[str, float]) -> ExecutionKernelConfig:
    """Tune execution speeds while keeping the PaulWei philosophy fixed."""
    return ExecutionKernelConfig(
        pyramid_enabled=True,
        dca_on_loss_long=True,
        adaptive_pyramid_enabled=True,
        gradual_exit_enabled=True,
        pyramid_initial_pct=params.get("pyramid_initial_pct", 0.30),
        pyramid_add_pct=params.get("pyramid_add_pct", 0.30),
        dca_max_adverse_atr=params.get("dca_max_adverse_atr", 3.0),
        exit_unwind_pct=params.get("exit_unwind_pct", 0.30),
    )


def build_indicator_config(params: dict[str, float]) -> IndicatorEngineConfig:
    """Map tuned params into indicator windows."""
    return IndicatorEngineConfig(
        vol_window=int(params.get("vol_window", 20)),
        cvd_window=int(params.get("cvd_window", 8)),
        roc_lookback=int(params.get("roc_lookback", 12)),
    )


def build_signal_weight_overrides(params: dict[str, float]) -> dict[str, float]:
    """Map tuned params into plugin signal weights."""
    return {
        "multi_momentum_signal": params.get("w_momentum", 0.45),
        "crash_reversal_signal": params.get("w_crash", 0.25),
        "cvd_signal": params.get("w_cvd", 0.25),
        "funding_alpha_signal": params.get("w_funding", 0.10),
        "crowding_signal": params.get("w_crowding", 0.10),
        "liquidity_signal": params.get("w_liquidity", 0.20),
    }


class ParamTunerEnv(gym.Env):
    """Gym environment: each step = choose params → run backtest → get reward.

    One episode = one parameter choice → one backtest on random Train window.
    The agent learns which parameter combinations produce best Sharpe.
    """

    metadata: dict[str, list[str]] = {"render_modes": []}  # noqa: RUF012

    def __init__(self) -> None:
        super().__init__()

        # Action space: one discrete choice per parameter
        self._param_names = list(PARAM_GRID.keys())
        self._param_choices = [PARAM_GRID[k] for k in self._param_names]
        self._n_params = len(self._param_names)

        # MultiDiscrete: each dimension is the number of choices for that param
        self.action_space = spaces.MultiDiscrete([len(c) for c in self._param_choices])

        # Observation: last episode's performance metrics
        # [sharpe, pnl_normalized, max_dd, trades_normalized, regime_trend_pct, regime_range_pct]
        self.observation_space = spaces.Box(
            low=np.array([-10.0, -1.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32),
            high=np.array([10.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32),
        )

        self._last_obs = np.zeros(6, dtype=np.float32)
        self._episode_count = 0
        self._results: list[TunerResult] = []

    def reset(self, *, seed: int | None = None, options: dict | None = None) -> tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        self._episode_count += 1
        return self._last_obs, {}

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict]:
        # Decode action → parameter values
        params = {}
        for i, name in enumerate(self._param_names):
            params[name] = self._param_choices[i][action[i]]

        # Pick random train window
        window = random.choice(TRAIN_WINDOWS)

        # Run backtest with these parameters
        result = self._run_backtest(params, window)
        self._results.append(result)

        # Compute reward: Sharpe with DD penalty
        reward = result.sharpe
        if result.max_dd > 0.25:
            reward -= (result.max_dd - 0.25) * 10  # Heavy penalty for DD > 25%
        if result.trades < 20:
            reward -= 1.0  # Penalty for too few trades (degenerate solution)

        # Update observation
        self._last_obs = np.array(
            [
                min(10.0, max(-10.0, result.sharpe)),
                min(1.0, max(-1.0, result.pnl / 100_000)),  # Normalized
                min(1.0, result.max_dd),
                min(1.0, result.trades / 500),
                0.5,  # Placeholder for regime distribution
                0.5,
            ],
            dtype=np.float32,
        )

        # Each step is a complete episode (one backtest = done)
        return self._last_obs, float(reward), True, False, {"result": result}

    def _run_backtest(self, params: dict[str, float], window: tuple[int, int]) -> TunerResult:
        """Run one backtest with specified parameters on a train window."""
        start_ns, end_ns = window

        strategy_config = build_strategy_config(params)
        risk_config = build_risk_config(params)
        execution_config = build_execution_config(params)
        weight_overrides = build_signal_weight_overrides(params)
        indicator_config = build_indicator_config(params)

        try:
            result = HistoricalBacktest(
                HistoricalBacktestConfig(
                    start_ns=start_ns,
                    end_ns=end_ns,
                    strategy_config=strategy_config,
                    risk_config=risk_config,
                    signal_weight_overrides=weight_overrides,
                    indicator_config=indicator_config,
                    execution_config=execution_config,
                )
            ).run()

            return TunerResult(
                params=params,
                sharpe=result.metrics.sharpe,
                pnl=result.metrics.net_pnl,
                max_dd=result.metrics.max_drawdown,
                trades=result.metrics.trade_count,
            )
        except Exception:
            return TunerResult(params=params, sharpe=-5.0, pnl=0.0, max_dd=1.0, trades=0)


def train_tuner(total_timesteps: int = 200, verbose: int = 1) -> dict[str, Any]:
    """Train RL agent to find optimal parameters.

    Returns the best parameter combination found.
    """
    from stable_baselines3 import PPO

    env = ParamTunerEnv()
    model = PPO(
        "MlpPolicy",
        env,
        verbose=verbose,
        n_steps=16,  # Small because each step = full backtest
        batch_size=8,
        n_epochs=4,
        learning_rate=3e-4,
    )

    print(f"Training RL tuner for {total_timesteps} episodes...")
    print(f"Parameter grid: {len(PARAM_GRID)} params")
    print(f"Train windows: {len(TRAIN_WINDOWS)} x 6-month windows")
    print("=" * 60)

    model.learn(total_timesteps=total_timesteps)

    # Find best result
    results = env._results
    if not results:
        return {}

    best = max(results, key=lambda r: r.sharpe)
    print(f"\nBest found: Sharpe={best.sharpe:.2f}, PnL=${best.pnl:,.0f}, DD={best.max_dd:.1%}")
    print(f"Parameters: {best.params}")

    return {
        "best_params": best.params,
        "best_sharpe": best.sharpe,
        "all_results": [(r.sharpe, r.params) for r in sorted(results, key=lambda r: -r.sharpe)[:10]],
    }
