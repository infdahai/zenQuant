"""RL-based parameter tuner for train-only clean-tuning exploration."""

from __future__ import annotations

import contextlib
import gc
import random
import signal
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing import Manager
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from zenquant.domain import Timeframe
from zenquant.execution import ExecutionKernelConfig
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.indicator import IndicatorEngineConfig
from zenquant.research.splits import DatasetSplitSpec, SplitWindow, default_btc_split_spec
from zenquant.risk import RiskOverlayConfig
from zenquant.strategy import ARCStrategyConfig


class RLBudgetReached(RuntimeError):
    """Internal control signal used to stop SB3 exactly at the requested budget."""


class RLSidecarFailure(RuntimeError):
    """Raised when the RL sidecar becomes unresponsive before it can produce usable progress."""


@contextlib.contextmanager
def _timeout_guard(timeout_s: float | None, failure_driver: str) -> Any:
    if timeout_s is None or timeout_s <= 0:
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _handle_timeout(_signum: int, _frame: Any) -> None:
        raise RLSidecarFailure(failure_driver)

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_s))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def _cleanup_rl_runtime(*, model: Any | None, env: Any | None, manager: Any | None) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    cleanup_completed = True

    if env is not None:
        try:
            env.close()
        except Exception as exc:
            cleanup_completed = False
            warnings.append(f"env_close_failed:{exc.__class__.__name__}")

    if model is not None:
        try:
            del model
        except Exception as exc:
            cleanup_completed = False
            warnings.append(f"model_release_failed:{exc.__class__.__name__}")

    try:
        import torch

        mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
        mps_available = bool(
            mps_backend is not None
            and getattr(mps_backend, "is_built", lambda: False)()
            and getattr(mps_backend, "is_available", lambda: False)()
        )
        if mps_available and hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
            try:
                torch.mps.empty_cache()
            except Exception as exc:
                cleanup_completed = False
                warnings.append(f"mps_empty_cache_failed:{exc.__class__.__name__}")
    except Exception:
        pass

    try:
        gc.collect()
    except Exception as exc:
        cleanup_completed = False
        warnings.append(f"gc_collect_failed:{exc.__class__.__name__}")

    if manager is not None:
        try:
            manager.shutdown()
        except Exception as exc:
            cleanup_completed = False
            warnings.append(f"manager_shutdown_failed:{exc.__class__.__name__}")

    return cleanup_completed, warnings


def _resolve_torch_device(device_preference: str = "auto") -> tuple[str, str, bool, str | None]:
    preference = str(device_preference or "auto").lower()
    if preference not in {"auto", "mps", "cpu"}:
        raise ValueError(f"Unsupported RL device preference: {device_preference}")
    try:
        import torch
    except Exception:
        return "cpu", preference, False, "torch_unavailable"

    mps_allowed = bool(torch.backends.mps.is_built() and torch.backends.mps.is_available())
    if preference == "cpu":
        return "cpu", preference, mps_allowed, None
    if preference == "mps":
        if mps_allowed:
            return "mps", preference, True, None
        return "cpu", preference, False, "mps_unavailable"
    if mps_allowed:
        return "mps", preference, True, None
    return "cpu", preference, False, "mps_unavailable"


PARAM_GRID = {
    "dd_step_1_long": [0.10, 0.15, 0.20],
    "dd_step_1_short": [0.05, 0.08, 0.12],
    "max_leverage": [4.0, 6.0, 8.0],
    "target_vol_4h": [0.80, 1.10, 1.40],
    "vpin_threshold": [0.08, 0.10, 0.12],
    "crowding_threshold": [1.8, 2.2, 2.5],
    "vol_shock_threshold": [0.80, 0.95, 1.10],
    "pyramid_initial_pct": [0.30],
    "pyramid_add_pct": [0.30],
    "pyramid_short_initial_pct": [0.15, 0.20],
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

_FUNDING_WEIGHT_CAP = 0.12
_CROWDING_WEIGHT_CAP = 0.05
_CORRELATED_PAIR_COMBINED_CAP = 0.14


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
        execution_timeframe=Timeframe.TF_5M,
        target_vol_5m=params.get("target_vol_5m", 0.70),
        target_vol_4h=params.get("target_vol_4h", 1.10),
        target_vol_1d=1.30,
        max_leverage=params.get("max_leverage", 6.0),
        trailing_stop_atr_mult=params.get("trailing_stop_atr_mult", 2.5),
        bias_max_age_bars=int(params.get("bias_max_age_bars", 8)),
        enable_adaptive_signal_weights=bool(round(float(params.get("enable_adaptive_signal_weights", 0.0)))),
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
        dca_max_adverse_atr=params.get("dca_max_adverse_atr", 2.5),
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
    weights = {
        "multi_momentum_signal": params.get("w_momentum", 0.45),
        "crash_reversal_signal": params.get("w_crash", 0.25),
        "cvd_signal": params.get("w_cvd", 0.25),
        "funding_alpha_signal": params.get("w_funding", 0.10),
        "crowding_signal": params.get("w_crowding", 0.10),
        "liquidity_signal": params.get("w_liquidity", 0.20),
    }
    funding = min(float(weights["funding_alpha_signal"]), _FUNDING_WEIGHT_CAP)
    crowding = min(float(weights["crowding_signal"]), _CROWDING_WEIGHT_CAP)
    combined = funding + crowding
    if combined > _CORRELATED_PAIR_COMBINED_CAP and combined > 1e-12:
        scale = _CORRELATED_PAIR_COMBINED_CAP / combined
        funding *= scale
        crowding *= scale
    weights["funding_alpha_signal"] = round(funding, 6)
    weights["crowding_signal"] = round(crowding, 6)
    return weights


class ParamTunerEnv(gym.Env):
    """Gym environment where each step evaluates one train-window parameter choice."""

    metadata: dict[str, list[str]] = {"render_modes": []}  # noqa: RUF012

    def __init__(
        self,
        *,
        param_grid: dict[str, list[float]] | None = None,
        split_spec: DatasetSplitSpec | None = None,
        base_config: HistoricalBacktestConfig | None = None,
        shared_results: Any | None = None,
        seed_params: dict[str, float] | None = None,
    ) -> None:
        super().__init__()
        self._param_grid = _resolve_param_grid(param_grid or PARAM_GRID, seed_params=seed_params)
        self._split_spec = split_spec or default_btc_split_spec()
        self._base_config = base_config or HistoricalBacktestConfig(
            execution_timeframe=Timeframe.TF_5M,
            context_timeframe=Timeframe.TF_1H,
        )
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
        self._shared_results = shared_results

    def reset(self, *, seed: int | None = None, options: dict | None = None) -> tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        return self._last_obs, {}

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict]:
        params = {name: self._param_choices[index][action[index]] for index, name in enumerate(self._param_names)}
        train_window = random.choice(self._train_windows)
        result = self._run_backtest(params, train_window)
        self._results.append(result)
        if self._shared_results is not None:
            self._shared_results.append(
                {
                    "params": dict(result.params),
                    "sharpe": float(result.sharpe),
                    "pnl": float(result.pnl),
                    "max_dd": float(result.max_dd),
                    "trades": int(result.trades),
                    "candidate_score": float(result.candidate_score),
                }
            )

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
            execution_timeframe=self._base_config.execution_timeframe,
            bias_timeframe=self._base_config.bias_timeframe,
            direction_timeframe=self._base_config.direction_timeframe,
            context_timeframe=self._base_config.context_timeframe,
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
            research_compute_mode=self._base_config.research_compute_mode,
            profiling_enabled=self._base_config.profiling_enabled,
            early_stop_enabled=self._base_config.early_stop_enabled,
            early_stop_trade_check_ratio=self._base_config.early_stop_trade_check_ratio,
            early_stop_min_trade_count=self._base_config.early_stop_min_trade_count,
            early_stop_trade_floor_ratio=self._base_config.early_stop_trade_floor_ratio,
            early_stop_max_drawdown=self._base_config.early_stop_max_drawdown,
            trial_runtime_budget_s=self._base_config.trial_runtime_budget_s,
        )
        try:
            result = HistoricalBacktest(config).run()
            sharpe = result.metrics.sharpe
            pnl = result.metrics.net_pnl
            max_dd = result.metrics.max_drawdown
            trades = result.metrics.trade_count
            candidate_score = sharpe - max(0.0, max_dd - 0.10) * 12.0 - (1.0 if trades < 20 else 0.0)
            if result.terminated_early:
                candidate_score -= 1.5
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


def _resolve_param_grid(
    param_grid: dict[str, list[float]],
    *,
    seed_params: dict[str, float] | None,
) -> dict[str, list[float]]:
    if not seed_params:
        return dict(param_grid)
    refined: dict[str, list[float]] = {}
    for name, values in param_grid.items():
        if name not in seed_params:
            refined[name] = list(values)
            continue
        ordered = list(values)
        try:
            center_index = ordered.index(seed_params[name])
        except ValueError:
            refined[name] = ordered
            continue
        lo = max(0, center_index - 1)
        hi = min(len(ordered), center_index + 2)
        narrowed = ordered[lo:hi]
        refined[name] = narrowed or ordered
    return refined


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
        execution_timeframe=base_config.execution_timeframe,
        bias_timeframe=base_config.bias_timeframe,
        direction_timeframe=base_config.direction_timeframe,
        context_timeframe=base_config.context_timeframe,
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
    selection_split: str = "train",
    status_callback: Any | None = None,
    total_timeout_s: float | None = None,
    budget_mode: str = "episode_primary",
    wall_clock_budget_s: float | None = None,
    max_episodes: int | None = None,
    n_envs: int = 1,
    seed_params: dict[str, float] | None = None,
    device_preference: str = "auto",
    init_timeout_s: float | None = None,
    first_progress_timeout_s: float | None = None,
) -> dict[str, Any]:
    """Train RL candidate explorer and return a train-ranked candidate summary."""
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

    resolved_split = split_spec or default_btc_split_spec()
    resolved_base = base_config or HistoricalBacktestConfig(
        execution_timeframe=Timeframe.TF_5M,
        context_timeframe=Timeframe.TF_1H,
    )
    started_at = time.time()
    resolved_budget_mode = str(budget_mode or "episode_primary")
    resolved_wall_clock_budget_s = (
        float(wall_clock_budget_s)
        if wall_clock_budget_s is not None
        else float(total_timeout_s)
        if total_timeout_s is not None
        else None
    )
    resolved_max_episodes = int(max_episodes or total_timesteps)
    resolved_n_envs = max(1, int(n_envs))
    resolved_device_preference = str(device_preference or "auto").lower()
    resolved_device, resolved_from, mps_allowed, fallback_reason = _resolve_torch_device(device_preference)
    use_manager = resolved_n_envs > 1
    manager = None
    shared_results: Any = []
    result_buffer: Any = shared_results
    if use_manager:
        with _timeout_guard(init_timeout_s, "rl_manager_unresponsive"):
            manager = Manager()
            shared_results = manager.list()
            result_buffer = shared_results

    def _make_env() -> Any:
        return ParamTunerEnv(
            param_grid=param_grid,
            split_spec=resolved_split,
            base_config=resolved_base,
            shared_results=shared_results,
            seed_params=seed_params,
        )

    env_fns = [_make_env for _ in range(resolved_n_envs)]
    rl_vec_env_type = "dummy_vec_env" if resolved_n_envs == 1 else "subproc_vec_env"
    with _timeout_guard(init_timeout_s, "rl_init_timeout"):
        if resolved_n_envs == 1:
            try:
                env = DummyVecEnv(env_fns)
            except ValueError as exc:
                test_env = _make_env()
                if hasattr(test_env, "_results"):
                    env = test_env
                    rl_vec_env_type = "direct_env_test_double"
                    result_buffer = test_env._results
                else:
                    raise exc
        else:
            env = SubprocVecEnv(env_fns, start_method="spawn")

    rl_init_completed = False
    rl_first_progress_seen = False

    class _ProgressCallback(BaseCallback):
        def _on_step(self) -> bool:
            nonlocal rl_first_progress_seen
            actual_steps = max(0, int(self.num_timesteps))
            episodes_completed = min(len(result_buffer), resolved_max_episodes)
            if not rl_first_progress_seen:
                rl_first_progress_seen = True
                signal.setitimer(signal.ITIMER_REAL, 0.0)
            if status_callback is not None:
                status_callback(
                    event="rl_progress",
                    completed_steps=episodes_completed,
                    actual_steps=min(len(result_buffer), max(actual_steps, len(result_buffer))),
                    requested_steps=resolved_max_episodes,
                    total_steps=resolved_max_episodes,
                    runtime_s=time.time() - started_at,
                    rl_budget_mode=resolved_budget_mode,
                    rl_wall_clock_elapsed_s=time.time() - started_at,
                    rl_active_envs=resolved_n_envs,
                    rl_device=resolved_device,
                    rl_device_resolved_from=resolved_from,
                    rl_device_fallback_reason=fallback_reason,
                    rl_device_preference=resolved_device_preference,
                    rl_execution_mode="single_env_cpu_guarded"
                    if resolved_n_envs == 1 and resolved_device == "cpu"
                    else "default",
                    rl_vec_env_type=rl_vec_env_type,
                    rl_platform_guard_applied=resolved_n_envs == 1 and resolved_device_preference == "cpu",
                    rl_first_progress_seen=rl_first_progress_seen,
                    mps_allowed=mps_allowed,
                )
            if resolved_wall_clock_budget_s is not None and (time.time() - started_at) > resolved_wall_clock_budget_s:
                raise RLBudgetReached("wall_clock_budget_reached")
            if episodes_completed >= resolved_max_episodes:
                raise RLBudgetReached("episode_budget_reached")
            return True

    model: Any | None = None
    try:
        with _timeout_guard(init_timeout_s, "rl_init_timeout"):
            model = PPO(
                "MlpPolicy",
                env,
                verbose=verbose,
                n_steps=8,
                batch_size=max(4, 4 * resolved_n_envs),
                n_epochs=2,
                learning_rate=3e-4,
                device=resolved_device,
            )
            rl_init_completed = True
            if status_callback is not None:
                status_callback(
                    event="rl_init_completed",
                    completed_steps=0,
                    actual_steps=0,
                    requested_steps=resolved_max_episodes,
                    total_steps=resolved_max_episodes,
                    runtime_s=time.time() - started_at,
                    rl_budget_mode=resolved_budget_mode,
                    rl_wall_clock_elapsed_s=time.time() - started_at,
                    rl_active_envs=resolved_n_envs,
                    rl_device=resolved_device,
                    rl_device_resolved_from=resolved_from,
                    rl_device_fallback_reason=fallback_reason,
                    rl_device_preference=resolved_device_preference,
                    rl_execution_mode="single_env_cpu_guarded"
                    if resolved_n_envs == 1 and resolved_device == "cpu"
                    else "default",
                    rl_vec_env_type=rl_vec_env_type,
                    rl_platform_guard_applied=resolved_n_envs == 1 and resolved_device_preference == "cpu",
                    rl_init_completed=True,
                    rl_first_progress_seen=False,
                    rl_init_timeout_s=init_timeout_s,
                    rl_first_progress_timeout_s=first_progress_timeout_s,
                    mps_allowed=mps_allowed,
                )
    except Exception as exc:
        if resolved_device != "mps":
            _cleanup_rl_runtime(model=None, env=env, manager=manager)
            raise
        fallback_reason = f"mps_init_error:{exc.__class__.__name__}"
        resolved_device = "cpu"
        with _timeout_guard(init_timeout_s, "rl_init_timeout"):
            model = PPO(
                "MlpPolicy",
                env,
                verbose=verbose,
                n_steps=8,
                batch_size=max(4, 4 * resolved_n_envs),
                n_epochs=2,
                learning_rate=3e-4,
                device=resolved_device,
            )
            rl_init_completed = True
    stop_reason = "completed"
    rl_failure_driver: str | None = None
    try:
        with _timeout_guard(first_progress_timeout_s, "rl_first_progress_timeout"):
            model.learn(
                total_timesteps=max(total_timesteps, resolved_max_episodes * resolved_n_envs),
                callback=_ProgressCallback(),
            )
    except RLBudgetReached as exc:
        stop_reason = str(exc)
        pass
    except RLSidecarFailure as exc:
        stop_reason = str(exc)
        rl_failure_driver = str(exc)
        raise
    finally:
        cleanup_completed, cleanup_warnings = _cleanup_rl_runtime(model=model, env=env, manager=manager)

    results: list[TunerResult] = []
    for item in list(result_buffer):
        if isinstance(item, dict):
            results.append(
                TunerResult(
                    params=dict(item["params"]),
                    sharpe=float(item["sharpe"]),
                    pnl=float(item["pnl"]),
                    max_dd=float(item["max_dd"]),
                    trades=int(item["trades"]),
                    candidate_score=float(item["candidate_score"]),
                )
            )
        else:
            results.append(
                TunerResult(
                    params=dict(item.params),
                    sharpe=float(item.sharpe),
                    pnl=float(item.pnl),
                    max_dd=float(item.max_dd),
                    trades=int(item.trades),
                    candidate_score=float(item.candidate_score),
                )
            )
    if not results:
        return {
            "best_params": {},
            "best_score": -10.0,
            "candidate_source": "rl_train_shortlist",
            "train_metrics": {},
            "dev_metrics": {},
            "search_status": {
                "selection_split": selection_split,
                "episodes_completed": 0,
                "budget_mode": resolved_budget_mode,
                "wall_clock_elapsed_s": round(time.time() - started_at, 4),
                "stop_reason": stop_reason,
                "active_envs": resolved_n_envs,
                "rl_device": resolved_device,
                "rl_device_resolved_from": resolved_from,
                "rl_device_fallback_reason": fallback_reason,
                "rl_device_preference": resolved_device_preference,
                "rl_execution_mode": "single_env_cpu_guarded"
                if resolved_n_envs == 1 and resolved_device == "cpu"
                else "default",
                "rl_vec_env_type": rl_vec_env_type,
                "rl_platform_guard_applied": resolved_n_envs == 1 and resolved_device_preference == "cpu",
                "rl_init_completed": rl_init_completed,
                "rl_first_progress_seen": rl_first_progress_seen,
                "rl_init_timeout_s": init_timeout_s,
                "rl_first_progress_timeout_s": first_progress_timeout_s,
                "rl_failure_driver": rl_failure_driver,
                "mps_allowed": mps_allowed,
                "rl_cleanup_completed": cleanup_completed,
                "rl_cleanup_warnings": cleanup_warnings,
                "train_only_search": True,
                "dev_only_ranking": False,
                "dev_diagnostics_only": True,
            },
        }

    if status_callback is not None:
        status_callback(
            event="rl_rank_dev_started",
            completed_steps=min(len(results), resolved_max_episodes),
            actual_steps=min(len(results), resolved_max_episodes),
            requested_steps=resolved_max_episodes,
            total_steps=resolved_max_episodes,
            runtime_s=time.time() - started_at,
            rl_budget_mode=resolved_budget_mode,
            rl_wall_clock_elapsed_s=time.time() - started_at,
            rl_active_envs=resolved_n_envs,
        )
    best_train = max(results, key=lambda item: item.candidate_score)
    dev_metrics: dict[str, Any] = {}
    avg_episode_runtime_s = round((time.time() - started_at) / max(1, len(results)), 4)
    if status_callback is not None:
        status_callback(
            event="rl_completed",
            completed_steps=min(len(results), resolved_max_episodes),
            actual_steps=min(len(results), resolved_max_episodes),
            requested_steps=resolved_max_episodes,
            total_steps=resolved_max_episodes,
            runtime_s=time.time() - started_at,
            best_params_so_far=best_train.params,
            best_value_so_far=best_train.candidate_score,
            rl_budget_mode=resolved_budget_mode,
            rl_wall_clock_elapsed_s=time.time() - started_at,
            rl_stop_reason=stop_reason,
            rl_active_envs=resolved_n_envs,
            rl_avg_episode_runtime_s=avg_episode_runtime_s,
            rl_cache_hit_rate=1.0 if resolved_base.research_compute_mode == "lightweight_train_search" else 0.0,
            rl_avg_event_loop_s=0.0,
            rl_device=resolved_device,
            rl_device_resolved_from=resolved_from,
            rl_device_fallback_reason=fallback_reason,
            rl_device_preference=resolved_device_preference,
            rl_execution_mode="single_env_cpu_guarded"
            if resolved_n_envs == 1 and resolved_device == "cpu"
            else "default",
            rl_vec_env_type=rl_vec_env_type,
            rl_platform_guard_applied=resolved_n_envs == 1 and resolved_device_preference == "cpu",
            rl_init_completed=rl_init_completed,
            rl_first_progress_seen=rl_first_progress_seen,
            rl_init_timeout_s=init_timeout_s,
            rl_first_progress_timeout_s=first_progress_timeout_s,
            rl_failure_driver=rl_failure_driver,
            mps_allowed=mps_allowed,
            rl_cleanup_completed=cleanup_completed,
            rl_cleanup_warnings=cleanup_warnings,
        )
    return {
        "best_params": best_train.params,
        "best_score": best_train.candidate_score,
        "best_sharpe": best_train.sharpe,
        "candidate_source": "rl_train_shortlist",
        "train_metrics": {
            "selection_split": "train",
            "sharpe": best_train.sharpe,
            "pnl": best_train.pnl,
            "max_drawdown": best_train.max_dd,
            "trade_count": best_train.trades,
            "score": best_train.candidate_score,
        },
        "dev_metrics": dev_metrics,
        "all_results": [
            (result.sharpe, result.params) for result in sorted(results, key=lambda item: -item.candidate_score)[:10]
        ],
        "search_status": {
            "selection_split": selection_split,
            "episodes_completed": min(len(results), resolved_max_episodes),
            "budget_mode": resolved_budget_mode,
            "wall_clock_elapsed_s": round(time.time() - started_at, 4),
            "stop_reason": stop_reason,
            "active_envs": resolved_n_envs,
            "avg_episode_runtime_s": avg_episode_runtime_s,
            "rl_device": resolved_device,
            "rl_device_resolved_from": resolved_from,
            "rl_device_fallback_reason": fallback_reason,
            "rl_device_preference": resolved_device_preference,
            "rl_execution_mode": "single_env_cpu_guarded"
            if resolved_n_envs == 1 and resolved_device == "cpu"
            else "default",
            "rl_vec_env_type": rl_vec_env_type,
            "rl_platform_guard_applied": resolved_n_envs == 1 and resolved_device_preference == "cpu",
            "rl_init_completed": rl_init_completed,
            "rl_first_progress_seen": rl_first_progress_seen,
            "rl_init_timeout_s": init_timeout_s,
            "rl_first_progress_timeout_s": first_progress_timeout_s,
            "rl_failure_driver": rl_failure_driver,
            "mps_allowed": mps_allowed,
            "rl_cleanup_completed": cleanup_completed,
            "rl_cleanup_warnings": cleanup_warnings,
            "train_only_search": True,
            "dev_only_ranking": False,
            "dev_diagnostics_only": True,
        },
    }
