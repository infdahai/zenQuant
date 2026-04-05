"""RL-based parameter tuner — replaces grid/Bayesian sweep.

Uses PPO to learn optimal signal weights and risk thresholds.
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

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.risk import RiskOverlayConfig
from zenquant.strategy import ARCStrategyConfig

# Parameter grid: each parameter has discrete choices
PARAM_GRID = {
    # Signal weights (6 active)
    "w_momentum": [0.20, 0.30, 0.45, 0.60],
    "w_crash": [0.15, 0.25, 0.35],
    "w_cvd": [0.15, 0.25, 0.35],
    "w_funding": [0.05, 0.10, 0.15],
    "w_crowding": [0.05, 0.10, 0.15],
    "w_liquidity": [0.10, 0.20, 0.30],
    # Risk params
    "dd_step_1_long": [0.10, 0.15, 0.20],
    "vpin_threshold": [0.08, 0.10, 0.12],
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
        self._last_obs = np.array([
            min(10.0, max(-10.0, result.sharpe)),
            min(1.0, max(-1.0, result.pnl / 100_000)),  # Normalized
            min(1.0, result.max_dd),
            min(1.0, result.trades / 500),
            0.5,  # Placeholder for regime distribution
            0.5,
        ], dtype=np.float32)

        # Each step is a complete episode (one backtest = done)
        return self._last_obs, float(reward), True, False, {"result": result}

    def _run_backtest(self, params: dict[str, float], window: tuple[int, int]) -> TunerResult:
        """Run one backtest with specified parameters on a train window."""
        start_ns, end_ns = window

        strategy_config = ARCStrategyConfig(
            target_vol_4h=1.10,  # PaulWei calibrated
            target_vol_1d=1.30,
            max_leverage=6.0,
        )

        risk_config = RiskOverlayConfig(
            dd_step_1_long=params.get("dd_step_1_long", 0.15),
            vpin_toxic_threshold=params.get("vpin_threshold", 0.10),
        )

        # Map RL param names → plugin signal names
        weight_overrides = {
            "multi_momentum_signal": params.get("w_momentum", 0.45),
            "crash_reversal_signal": params.get("w_crash", 0.25),
            "cvd_signal": params.get("w_cvd", 0.25),
            "funding_alpha_signal": params.get("w_funding", 0.10),
            "crowding_signal": params.get("w_crowding", 0.10),
            "liquidity_signal": params.get("w_liquidity", 0.20),
        }

        try:
            result = HistoricalBacktest(
                HistoricalBacktestConfig(
                    start_ns=start_ns,
                    end_ns=end_ns,
                    strategy_config=strategy_config,
                    risk_config=risk_config,
                    signal_weight_overrides=weight_overrides,
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
    print(f"Parameter grid: {len(PARAM_GRID)} params, ~37k combinations")
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
