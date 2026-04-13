"""AutoResearch internal research workers.

Runs a full parameter optimization cycle:

    1. Grid/random sweep  → parallel backtests, rank by primary metric
    2. Walk-forward validation → top-N candidates validated OOS
    3. Domain randomization   → survivors tested on multiple seeds
    4. Promotion gate         → automated pass/fail vs baseline

Usage:
    campaign = Campaign(
        search_space=SearchSpace(
            strategy_params={"bull_leverage": [2.5, 3.0, 3.5]},
            plugin_params={"signals.multi_momentum_signal.momentum_threshold": [0.2, 0.3, 0.4]},
        ),
    )
    report = campaign.run()

Architecture Note:
    This module orchestrates existing building blocks (HistoricalBacktest,
    PerturbationConfig, PluginRegistry) — it does not duplicate them.

Authority Note:
    AutoResearch is an internal/dev-only research harness. It can generate
    train-split candidates plus dev-side validation/robustness artifacts, but
    it is not the authoritative promote path. Final promote truth lives in
    framework_search + run_framework_campaign.
"""

from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, as_completed, wait
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.historical.perturbation import PerturbationConfig
from zenquant.historical.replay import ReplayContext, _build_replay_context
from zenquant.research.framework_params import (
    build_execution_config as _shared_build_execution_config,
)
from zenquant.research.framework_params import (
    build_indicator_config as _shared_build_indicator_config,
)
from zenquant.research.framework_params import (
    build_replay_toggles as _shared_build_replay_toggles,
)
from zenquant.research.framework_params import (
    build_risk_config as _shared_build_risk_config,
)
from zenquant.research.framework_params import (
    build_signal_weight_overrides as _shared_build_signal_weight_overrides,
)
from zenquant.research.framework_params import (
    build_strategy_config as _shared_build_strategy_config,
)
from zenquant.strategy import ARCStrategyConfig, ExitEngineConfig, PlaybookEngineConfig

from .experiment import ExperimentTracker
from .promoter import (
    BaselineMetrics,
    CandidateMetrics,
    PromotionCriteria,
    PromotionGate,
    PromotionVerdict,
    RobustnessResult,
)
from .splits import DatasetSplitSpec, default_btc_split_spec, split_summary_json

# ── Configuration ─────────────────────────────────────────────


class SearchStageTimeoutError(RuntimeError):
    """Raised when a search stage exceeds its allowed runtime budget."""


_WORKER_REPLAY_CONTEXT_CACHE: dict[tuple[Any, ...], ReplayContext] = {}


@dataclass(slots=True)
class SearchSpace:
    """Parameter search space definition.

    ``strategy_params`` maps strategy-config field names to lists of values.
    ``plugin_params`` maps dotted TOML keys to lists of values (future use).
    """

    strategy_params: dict[str, list[Any]] = field(default_factory=dict)
    plugin_params: dict[str, list[Any]] = field(default_factory=dict)


@dataclass(slots=True)
class WalkForwardSplit:
    """One train/validation time split (nanosecond boundaries)."""

    train_start_ns: int
    train_end_ns: int
    val_start_ns: int
    val_end_ns: int


@dataclass(slots=True)
class CampaignConfig:
    """Full configuration for an AutoResearch campaign."""

    # Data
    data_root: Path = Path("data/raw/binance")
    symbol: str = "BTCUSDT"
    starting_equity: float = 100_000.0
    split_spec: DatasetSplitSpec | None = None
    execution_timeframe: Timeframe = Timeframe.TF_5M
    bias_timeframe: Timeframe = Timeframe.TF_1D
    direction_timeframe: Timeframe = Timeframe.TF_4H
    context_timeframe: Timeframe | None = Timeframe.TF_1H

    # Search
    search_space: SearchSpace = field(default_factory=SearchSpace)
    # Sweep mode: "grid" (exhaustive Cartesian product) or "bayesian" (Optuna TPE)
    sweep_mode: str = "grid"
    # Number of Optuna trials (only used when sweep_mode="bayesian")
    n_trials: int = 50
    seed_trials: list[dict[str, Any]] = field(default_factory=list)

    # Walk-forward validation
    wf_splits: list[WalkForwardSplit] = field(default_factory=list)
    top_n_for_validation: int = 3
    minimum_train_trade_count: int = 20

    # Robustness
    robustness_seeds: list[int] = field(default_factory=lambda: [42, 123, 456, 789, 1024])
    robustness_price_noise: float = 0.001  # SNOOPING_OK: domain randomization param
    robustness_volume_noise: float = 0.05
    robustness_bar_dropout: float = 0.02

    # Promotion
    promotion_criteria: PromotionCriteria = field(default_factory=PromotionCriteria)
    baseline: BaselineMetrics = field(default_factory=BaselineMetrics)

    # Output
    output_dir: Path = Path("results/campaigns")
    campaign_label: str = ""
    primary_metric: str = "sharpe"
    verbose: bool = True
    status_callback: Any | None = None
    trial_timeout_s: float | None = None
    stage_total_timeout_s: float | None = None
    stage1_parallel_mode: str = "serial"
    stage1_max_workers: int | None = None
    stage1_target_inflight: int | None = None
    stage1_batch_size: int = 1


# ── Results ───────────────────────────────────────────────────


@dataclass(slots=True)
class SweepResult:
    """Result from the initial parameter sweep stage."""

    total_combinations: int
    results: list[dict[str, Any]]
    top_n: list[dict[str, Any]]
    runtime_s: float
    train_candidate_diagnostics: dict[str, Any] = field(default_factory=dict)
    empty_shortlist_reason: str | None = None


@dataclass(slots=True)
class ValidationResult:
    """Walk-forward validation result for one candidate."""

    params: dict[str, Any]
    split_sharpes: list[float]
    split_drawdowns: list[float]
    split_pnls: list[float]
    split_trade_counts: list[int]
    avg_val_sharpe: float
    avg_val_drawdown: float
    total_trade_count: int


@dataclass(slots=True)
class CampaignReport:
    """Complete campaign output."""

    campaign_id: str
    config: dict[str, Any]
    sweep: SweepResult
    validations: list[ValidationResult]
    robustness: dict[str, list[RobustnessResult]]  # keyed by params_hash
    verdicts: list[tuple[dict[str, Any], PromotionVerdict]]  # (params, verdict)
    best_params: dict[str, Any] | None
    best_verdict: PromotionVerdict | None
    runtime_s: float
    authoritative_promote_path: bool = False
    split_contract_version: str | None = None
    test_evaluated: bool = False
    invalidated_reason: str | None = None


# ── Orchestrator ──────────────────────────────────────────────


def _run_backtest_worker(
    args: tuple[
        dict[str, Any],
        str,
        str,
        float,
        str,
        str,
        str,
        str | None,
        int | None,
        int | None,
        dict[str, Any] | None,
    ],
) -> tuple[dict[str, Any], dict[str, float]]:
    """Run one backtest in a subprocess.  Module-level for pickling."""
    worker_started_at = time.time()
    (
        params,
        root_dir,
        symbol,
        equity,
        execution_timeframe,
        bias_timeframe,
        direction_timeframe,
        context_timeframe,
        start_ns,
        end_ns,
        perturbation_dict,
    ) = args
    resolved_execution_timeframe = Timeframe(execution_timeframe)
    strategy_config = _build_strategy_config(params, execution_timeframe=resolved_execution_timeframe)
    risk_config = _shared_build_risk_config(params)
    execution_cfg = _shared_build_execution_config(params)
    indicator_cfg = _shared_build_indicator_config(params)
    signal_weight_overrides = _shared_build_signal_weight_overrides(params)
    liquidation_data_enabled, state_liquidation_overlay_enabled = _shared_build_replay_toggles(params)
    perturbation = PerturbationConfig(**perturbation_dict) if perturbation_dict else None
    context_key = (
        root_dir,
        symbol,
        execution_timeframe,
        bias_timeframe,
        direction_timeframe,
        context_timeframe,
        start_ns,
        end_ns,
        liquidation_data_enabled,
    )
    replay_context = _WORKER_REPLAY_CONTEXT_CACHE.get(context_key)
    context_build_started_at = time.time()
    cache_hit = replay_context is not None
    if replay_context is None:
        replay_context = _build_replay_context(
            HistoricalBacktestConfig(
                root_dir=Path(root_dir),
                symbol=symbol,
                execution_timeframe=resolved_execution_timeframe,
                bias_timeframe=Timeframe(bias_timeframe),
                direction_timeframe=Timeframe(direction_timeframe),
                context_timeframe=Timeframe(context_timeframe) if context_timeframe else None,
                start_ns=start_ns,
                end_ns=end_ns,
                liquidation_data_enabled=liquidation_data_enabled,
            )
        )
        _WORKER_REPLAY_CONTEXT_CACHE[context_key] = replay_context
    replay_context_build_s = time.time() - context_build_started_at
    config = HistoricalBacktestConfig(
        root_dir=Path(root_dir),
        symbol=symbol,
        starting_equity=equity,
        execution_timeframe=resolved_execution_timeframe,
        bias_timeframe=Timeframe(bias_timeframe),
        direction_timeframe=Timeframe(direction_timeframe),
        context_timeframe=Timeframe(context_timeframe) if context_timeframe else None,
        start_ns=start_ns,
        end_ns=end_ns,
        strategy_config=strategy_config,
        risk_config=risk_config,
        execution_config=execution_cfg,
        indicator_config=indicator_cfg,
        signal_weight_overrides=signal_weight_overrides,
        liquidation_data_enabled=liquidation_data_enabled,
        state_liquidation_overlay_enabled=state_liquidation_overlay_enabled,
        perturbation=perturbation,
        replay_context=replay_context,
    )
    build_done_at = time.time()
    bt = HistoricalBacktest(config)
    run_started_at = time.time()
    result = bt.run()
    run_finished_at = time.time()
    m = result.metrics
    return params, {
        "sharpe": m.sharpe,
        "sortino": m.sortino,
        "net_pnl": m.net_pnl,
        "max_drawdown": m.max_drawdown,
        "win_rate": m.win_rate,
        "profit_factor": m.profit_factor,
        "trade_count": m.trade_count,
        "fee_drag_pct": m.fee_drag_pct,
        "cache_hit": 1.0 if cache_hit else 0.0,
        "replay_context_build_s": round(replay_context_build_s, 6),
        "store_load_s": round(replay_context_build_s, 6),
        "event_load_s": round(replay_context_build_s, 6),
        "event_publish_run_s": round(run_finished_at - run_started_at, 6),
        "metrics_build_s": round(max(0.0, run_finished_at - run_started_at), 6),
        "trial_runtime_s": round(run_finished_at - worker_started_at, 6),
        "config_build_s": round(build_done_at - context_build_started_at - replay_context_build_s, 6),
    }


def _max_workers() -> int:
    cpu_count = os.cpu_count() or 4
    return max(1, min(cpu_count - 2, 8))


def _best_trial_snapshot(study: Any) -> tuple[float | None, dict[str, Any] | None]:
    best_trials = [trial for trial in study.trials if getattr(trial, "value", None) is not None]
    if not best_trials:
        return None, None
    best_trial = max(best_trials, key=lambda trial: float(trial.value))
    return float(best_trial.value), dict(best_trial.params)


class Campaign:
    """Internal research harness for train/dev experimentation."""

    def __init__(self, config: CampaignConfig | None = None) -> None:
        self._config = config or CampaignConfig()
        if self._config.split_spec is None:
            raise ValueError("split_spec is required for AutoResearch campaigns")
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        label = self._config.campaign_label or self._config.symbol.lower()
        self._campaign_id = f"{ts}_{label}"
        self._campaign_dir = self._config.output_dir / self._campaign_id
        self._tracker = ExperimentTracker(self._campaign_dir)
        self._workers = _max_workers()

        # Guard against accidental grid explosion
        sp = self._config.search_space
        all_vals = list(sp.strategy_params.values()) + list(sp.plugin_params.values())
        if all_vals and self._config.sweep_mode == "grid":
            total = math.prod(len(v) for v in all_vals)
            if total > 100_000:
                raise ValueError(f"Grid too large: {total:,} combinations (max 100,000)")
            if total > 10_000:
                import warnings

                warnings.warn(f"Large grid: {total:,} combinations", stacklevel=2)

    def run(self) -> CampaignReport:
        """Execute the full campaign pipeline."""
        start = time.time()
        self._log(f"Campaign {self._campaign_id} started")
        self._log(f"Workers: {self._workers}")

        # Stage 1: Sweep
        sweep = self._run_sweep()
        self._log(
            f"Sweep: {sweep.total_combinations} combos, top Sharpe = "
            f"{sweep.top_n[0]['metrics'][self._config.primary_metric]:.2f}"
            if sweep.top_n
            else "no results"
        )

        self._tracker.flush()

        # Stage 2: Walk-forward validation (if splits defined)
        validations: list[ValidationResult] = []
        if self._config.wf_splits:
            validations = self._run_walk_forward(sweep.top_n)
            self._log(f"Walk-forward: {len(validations)} candidates validated")
        else:
            raise ValueError("wf_splits are required; dev selection cannot be skipped")

        self._tracker.flush()

        # Stage 3: Robustness
        robustness: dict[str, list[RobustnessResult]] = {}
        if self._config.robustness_seeds:
            robustness = self._run_robustness(validations)
            self._log(f"Robustness: {len(robustness)} candidates tested on {len(self._config.robustness_seeds)} seeds")

        # Stage 4: Promotion
        verdicts = self._run_promotion(validations, robustness)
        promoted = [(p, v) for p, v in verdicts if v.decision == "PROMOTE"]
        best_params = promoted[0][0] if promoted else None
        best_verdict = promoted[0][1] if promoted else None

        self._log(f"Promotion: {len(promoted)}/{len(verdicts)} PROMOTED")

        report = CampaignReport(
            campaign_id=self._campaign_id,
            config=_config_to_dict(self._config),
            sweep=sweep,
            validations=validations,
            robustness=robustness,
            verdicts=verdicts,
            best_params=best_params,
            best_verdict=best_verdict,
            runtime_s=time.time() - start,
            authoritative_promote_path=False,
            split_contract_version=self._config.split_spec.version,
            test_evaluated=False,
        )
        self._save_report(report)
        return report

    # ── Stage 1: Sweep ─────────────────────────────────────

    def _run_sweep(self) -> SweepResult:
        if self._config.sweep_mode == "bayesian":
            return self._run_optuna_sweep()
        return self._run_grid_sweep()

    def _train_candidate_diagnostics(
        self,
        results: list[dict[str, Any]],
        eligible_results: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None]:
        raw_trial_count = len(results)
        raw_nonzero_trade_trials = sum(1 for item in results if int(item["metrics"].get("trade_count", 0) or 0) > 0)
        eligible_trade_count_trials = len(eligible_results)
        max_train_trade_count = max((int(item["metrics"].get("trade_count", 0) or 0) for item in results), default=0)
        max_train_sharpe = max((float(item["metrics"].get("sharpe", 0.0) or 0.0) for item in results), default=0.0)
        empty_shortlist_reason = None
        if not eligible_results:
            if raw_nonzero_trade_trials == 0:
                empty_shortlist_reason = "all_zero_trade"
            elif max_train_trade_count < self._config.minimum_train_trade_count:
                empty_shortlist_reason = "below_min_trade_threshold"
            else:
                empty_shortlist_reason = "all_negative_primary_metric"
        return (
            {
                "raw_trial_count": raw_trial_count,
                "raw_nonzero_trade_trials": raw_nonzero_trade_trials,
                "eligible_trade_count_trials": eligible_trade_count_trials,
                "max_train_trade_count": max_train_trade_count,
                "max_train_sharpe": round(max_train_sharpe, 6),
            },
            empty_shortlist_reason,
        )

    def _run_grid_sweep(self) -> SweepResult:
        start = time.time()
        param_grid = _build_param_grid(self._config.search_space)
        if not param_grid:
            return SweepResult(total_combinations=0, results=[], top_n=[], runtime_s=0)

        self._log(f"Grid sweep: {len(param_grid)} parameter combinations")

        split = self._config.split_spec
        assert split is not None
        # Build backtest args — train split only
        args_list = [
            (
                params,
                str(self._config.data_root),
                self._config.symbol,
                self._config.starting_equity,
                self._config.execution_timeframe.value,
                self._config.bias_timeframe.value,
                self._config.direction_timeframe.value,
                self._config.context_timeframe.value if self._config.context_timeframe else None,
                split.train.start_ns,
                split.train.end_ns,
                None,  # perturbation
            )
            for params in param_grid
        ]

        results = self._run_parallel(args_list)

        # Record to tracker
        for params, metrics in results:
            self._tracker.record(params, metrics, tags=["sweep"])

        ranked_results = [{"params": p, "metrics": m} for p, m in results]
        eligible_results = [
            item for item in ranked_results if int(item["metrics"].get("trade_count", 0) or 0) >= self._config.minimum_train_trade_count
        ]
        # Rank
        sorted_results = sorted(
            eligible_results,
            key=lambda x: x["metrics"].get(self._config.primary_metric, 0),
            reverse=True,
        )
        top_n = sorted_results[: self._config.top_n_for_validation]
        if len(eligible_results) != len(ranked_results):
            self._log(
                f"Filtered {len(ranked_results) - len(eligible_results)} low-trade sweep candidates "
                f"(min_train_trade_count={self._config.minimum_train_trade_count})"
            )

        diagnostics, empty_shortlist_reason = self._train_candidate_diagnostics(ranked_results, eligible_results)
        return SweepResult(
            total_combinations=len(param_grid),
            results=sorted_results,
            top_n=top_n,
            runtime_s=time.time() - start,
            train_candidate_diagnostics=diagnostics,
            empty_shortlist_reason=empty_shortlist_reason,
        )

    def _run_optuna_sweep(self) -> SweepResult:
        """Bayesian optimization via Optuna TPE sampler.

        Instead of exhaustively testing all grid combinations, Optuna builds
        a probabilistic model of the parameter space and samples promising
        regions adaptively.  Typically 10-50x more sample-efficient than grid.
        """
        try:
            import optuna
        except ImportError:
            raise ImportError(
                "Optuna required for bayesian sweep. Install: uv pip install 'zenquant[optimize]'"
            ) from None

        start = time.time()
        space = self._config.search_space
        all_params: dict[str, list[Any]] = {}
        all_params.update(space.strategy_params)
        all_params.update(space.plugin_params)

        if not all_params:
            return SweepResult(total_combinations=0, results=[], top_n=[], runtime_s=0)

        grid_size = math.prod(len(v) for v in all_params.values())
        n_trials = min(self._config.n_trials, grid_size)
        self._log(f"Bayesian sweep: {n_trials} trials (grid would be {grid_size})")

        # Collect all results for ranking
        all_results: list[dict[str, Any]] = []
        split = self._config.split_spec
        assert split is not None

        def objective(trial: optuna.Trial) -> float:
            trial_number = trial.number + 1
            started_trial_at = time.time()
            if self._config.stage_total_timeout_s is not None and started_trial_at - start > self._config.stage_total_timeout_s:
                raise SearchStageTimeoutError(
                    f"stage1_optuna_timeout: no completed result within {self._config.stage_total_timeout_s:.0f}s"
                )
            if self._config.status_callback is not None:
                self._config.status_callback(
                    event="trial_started",
                    current_trial=trial_number,
                    total_trials=n_trials,
                    runtime_s=started_trial_at - start,
                )
            params: dict[str, Any] = {}
            for key, values in all_params.items():
                params[key] = trial.suggest_categorical(key, values)

            args = (
                params,
                str(self._config.data_root),
                self._config.symbol,
                self._config.starting_equity,
                self._config.execution_timeframe.value,
                self._config.bias_timeframe.value,
                self._config.direction_timeframe.value,
                self._config.context_timeframe.value if self._config.context_timeframe else None,
                split.train.start_ns,
                split.train.end_ns,
                None,
            )
            if self._config.trial_timeout_s is not None:
                with ProcessPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(_run_backtest_worker, args)
                    try:
                        _, metrics = future.result(timeout=self._config.trial_timeout_s)
                    except FutureTimeoutError as exc:
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise SearchStageTimeoutError(
                            f"stage1_optuna_timeout: trial {trial_number} exceeded {self._config.trial_timeout_s:.0f}s"
                        ) from exc
            else:
                _, metrics = _run_backtest_worker(args)

            self._tracker.record(params, metrics, tags=["sweep", "bayesian"])
            all_results.append({"params": params, "metrics": metrics})

            primary = metrics.get(self._config.primary_metric, 0)
            return primary

        # Suppress Optuna's verbose logging
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
        )
        for params in self._config.seed_trials[:n_trials]:
            study.enqueue_trial(dict(params))

        if self._config.stage1_parallel_mode == "cpu_parallel" and n_trials > 1:
            max_workers = self._config.stage1_max_workers or self._workers
            target_inflight = max(
                1,
                min(
                    self._config.stage1_target_inflight or max_workers,
                    max_workers,
                ),
            )
            completed_trials = 0
            launched_trials = 0
            last_running_heartbeat_at = 0.0

            with ProcessPoolExecutor(max_workers=max_workers) as pool:
                pending: dict[Any, tuple[Any, int, dict[str, Any], float]] = {}

                while completed_trials < n_trials:
                    if self._config.stage_total_timeout_s is not None and (time.time() - start) > self._config.stage_total_timeout_s:
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise SearchStageTimeoutError(
                            f"stage1_optuna_timeout: no completed result within {self._config.stage_total_timeout_s:.0f}s"
                        )

                    while (
                        launched_trials < n_trials
                        and len(pending) < target_inflight
                    ):
                        trial = study.ask()
                        trial_number = trial.number + 1
                        started_trial_at = time.time()
                        params: dict[str, Any] = {}
                        for key, values in all_params.items():
                            params[key] = trial.suggest_categorical(key, values)
                        args = (
                            params,
                            str(self._config.data_root),
                            self._config.symbol,
                            self._config.starting_equity,
                            self._config.execution_timeframe.value,
                            self._config.bias_timeframe.value,
                            self._config.direction_timeframe.value,
                            self._config.context_timeframe.value if self._config.context_timeframe else None,
                            split.train.start_ns,
                            split.train.end_ns,
                            None,
                        )
                        future = pool.submit(_run_backtest_worker, args)
                        pending[future] = (trial, trial_number, params, started_trial_at)
                        launched_trials += 1
                        if self._config.status_callback is not None:
                            best_value, best_params = _best_trial_snapshot(study)
                            self._config.status_callback(
                                event="trial_started",
                                current_trial=trial_number,
                                completed_trials=completed_trials,
                                total_trials=n_trials,
                                best_value_so_far=best_value,
                                best_params_so_far=best_params,
                                runtime_s=time.time() - start,
                                pending_trials=len(pending),
                                max_workers=max_workers,
                                target_inflight=target_inflight,
                                active_workers_estimate=len(pending),
                                scheduler_mode="work_conserving",
                                underfilled=False,
                                underfilled_reason=None,
                            )

                    if not pending:
                        break

                    done, _ = wait(list(pending.keys()), timeout=1.0, return_when=FIRST_COMPLETED)
                    if not done:
                        if self._config.status_callback is not None and (time.time() - last_running_heartbeat_at) >= 5.0:
                            best_value, best_params = _best_trial_snapshot(study)
                            self._config.status_callback(
                                event="trial_running",
                                current_trial=launched_trials,
                                completed_trials=completed_trials,
                                total_trials=n_trials,
                                best_value_so_far=best_value,
                                best_params_so_far=best_params,
                                runtime_s=time.time() - start,
                                pending_trials=len(pending),
                                max_workers=max_workers,
                                target_inflight=target_inflight,
                                active_workers_estimate=len(pending),
                                scheduler_mode="work_conserving",
                                underfilled=launched_trials < n_trials and len(pending) < target_inflight,
                                underfilled_reason=(
                                    "work_available_below_target_inflight"
                                    if launched_trials < n_trials and len(pending) < target_inflight
                                    else None
                                ),
                            )
                            last_running_heartbeat_at = time.time()
                        if self._config.trial_timeout_s is not None:
                            timed_out = [
                                (future, meta)
                                for future, meta in pending.items()
                                if (time.time() - meta[3]) > self._config.trial_timeout_s
                            ]
                            if timed_out:
                                pool.shutdown(wait=False, cancel_futures=True)
                                trial_number = timed_out[0][1][1]
                                raise SearchStageTimeoutError(
                                    f"stage1_optuna_timeout: trial {trial_number} exceeded {self._config.trial_timeout_s:.0f}s"
                                )
                        continue

                    for future in done:
                        trial, trial_number, params, _started = pending.pop(future)
                        try:
                            _, metrics = future.result()
                        except Exception:
                            study.tell(trial, state=optuna.trial.TrialState.FAIL)
                            raise

                        self._tracker.record(params, metrics, tags=["sweep", "bayesian"])
                        all_results.append({"params": params, "metrics": metrics})
                        primary = metrics.get(self._config.primary_metric, 0)
                        study.tell(trial, primary)
                        completed_trials += 1

                        if self._config.status_callback is not None:
                            best_value, best_params = _best_trial_snapshot(study)
                            self._config.status_callback(
                                event="trial_completed",
                                current_trial=trial_number,
                                completed_trials=completed_trials,
                                total_trials=n_trials,
                                best_value_so_far=best_value,
                                best_params_so_far=best_params,
                                runtime_s=time.time() - start,
                                pending_trials=len(pending),
                                max_workers=max_workers,
                                target_inflight=target_inflight,
                                active_workers_estimate=len(pending),
                                scheduler_mode="work_conserving",
                                underfilled=False,
                                underfilled_reason=None,
                                trial_runtime_s=metrics.get("trial_runtime_s", 0.0),
                                replay_context_build_s=metrics.get("replay_context_build_s", 0.0),
                                event_publish_run_s=metrics.get("event_publish_run_s", 0.0),
                                cache_hit=metrics.get("cache_hit", 0.0),
                            )
        else:
            def _trial_callback(study: Any, trial: Any) -> None:
                if self._config.status_callback is None:
                    return
                best_value, best_params = _best_trial_snapshot(study)
                self._config.status_callback(
                    event="trial_completed",
                    current_trial=trial.number + 1,
                    completed_trials=len(all_results),
                    total_trials=n_trials,
                    best_value_so_far=best_value,
                    best_params_so_far=best_params,
                    runtime_s=time.time() - start,
                )

            study.optimize(objective, n_trials=n_trials, n_jobs=1, callbacks=[_trial_callback])

        eligible_results = [
            item for item in all_results if int(item["metrics"].get("trade_count", 0) or 0) >= self._config.minimum_train_trade_count
        ]
        # Sort all evaluated results
        sorted_results = sorted(
            eligible_results,
            key=lambda x: x["metrics"].get(self._config.primary_metric, 0),
            reverse=True,
        )
        top_n = sorted_results[: self._config.top_n_for_validation]
        if len(eligible_results) != len(all_results):
            self._log(
                f"Filtered {len(all_results) - len(eligible_results)} low-trade bayesian candidates "
                f"(min_train_trade_count={self._config.minimum_train_trade_count})"
            )

        self._log(
            f"Bayesian sweep complete: best {self._config.primary_metric} = {study.best_value:.4f} in {n_trials} trials"
        )

        diagnostics, empty_shortlist_reason = self._train_candidate_diagnostics(all_results, eligible_results)
        return SweepResult(
            total_combinations=n_trials,
            results=sorted_results,
            top_n=top_n,
            runtime_s=time.time() - start,
            train_candidate_diagnostics=diagnostics,
            empty_shortlist_reason=empty_shortlist_reason,
        )

    # ── Stage 2: Walk-Forward ──────────────────────────────

    def _run_walk_forward(self, candidates: list[dict[str, Any]]) -> list[ValidationResult]:
        # Build all (candidate × split) args for parallel execution
        all_args: list[tuple[dict[str, Any], tuple[Any, ...]]] = []
        for candidate in candidates:
            params = candidate["params"]
            for split in self._config.wf_splits:
                args = (
                    params,
                    str(self._config.data_root),
                    self._config.symbol,
                    self._config.starting_equity,
                    self._config.execution_timeframe.value,
                    self._config.bias_timeframe.value,
                    self._config.direction_timeframe.value,
                    self._config.context_timeframe.value if self._config.context_timeframe else None,
                    split.val_start_ns,
                    split.val_end_ns,
                    None,
                )
                all_args.append((params, args))

        # Run all backtests in parallel
        worker_args = [a[1] for a in all_args]
        results = self._run_parallel(worker_args)

        # Reassemble per-candidate
        n_splits = len(self._config.wf_splits)
        validations: list[ValidationResult] = []
        for i, candidate in enumerate(candidates):
            params = candidate["params"]
            batch = results[i * n_splits : (i + 1) * n_splits]
            split_sharpes = [m["sharpe"] for _, m in batch]
            split_drawdowns = [m["max_drawdown"] for _, m in batch]
            split_pnls = [m["net_pnl"] for _, m in batch]
            split_trade_counts = [int(m["trade_count"]) for _, m in batch]

            avg_sharpe = sum(split_sharpes) / len(split_sharpes) if split_sharpes else 0
            avg_dd = sum(split_drawdowns) / len(split_drawdowns) if split_drawdowns else 0
            total_trades = sum(split_trade_counts)

            vr = ValidationResult(
                params=params,
                split_sharpes=split_sharpes,
                split_drawdowns=split_drawdowns,
                split_pnls=split_pnls,
                split_trade_counts=split_trade_counts,
                avg_val_sharpe=avg_sharpe,
                avg_val_drawdown=avg_dd,
                total_trade_count=total_trades,
            )
            validations.append(vr)

            self._tracker.record(
                params,
                {
                    "avg_val_sharpe": avg_sharpe,
                    "avg_val_drawdown": avg_dd,
                    "total_trade_count": total_trades,
                },
                tags=["walk_forward"],
            )

        return validations

    # ── Stage 3: Robustness ────────────────────────────────

    def _run_robustness(self, validations: list[ValidationResult]) -> dict[str, list[RobustnessResult]]:
        cfg = self._config
        split = cfg.split_spec
        assert split is not None
        robustness: dict[str, list[RobustnessResult]] = {}

        # Build all (candidate × seed) backtest args
        all_args: list[tuple[str, int, tuple[Any, ...]]] = []
        for vr in validations:
            key = json.dumps(vr.params, sort_keys=True)
            for seed in cfg.robustness_seeds:
                perturbation = {
                    "seed": seed,
                    "price_noise_pct": cfg.robustness_price_noise,
                    "volume_noise_pct": cfg.robustness_volume_noise,
                    "bar_dropout_pct": cfg.robustness_bar_dropout,
                }
                args = (
                    vr.params,
                    str(cfg.data_root),
                    cfg.symbol,
                    cfg.starting_equity,
                    cfg.execution_timeframe.value,
                    cfg.bias_timeframe.value,
                    cfg.direction_timeframe.value,
                    cfg.context_timeframe.value if cfg.context_timeframe else None,
                    split.dev.start_ns,
                    split.dev.end_ns,
                    perturbation,
                )
                all_args.append((key, seed, args))

        # Run in parallel
        worker_args = [a[2] for a in all_args]
        results = self._run_parallel(worker_args)

        # Map results back
        for (key, seed, _), (_, metrics) in zip(all_args, results):
            if key not in robustness:
                robustness[key] = []
            robustness[key].append(
                RobustnessResult(
                    seed=seed,
                    sharpe=metrics["sharpe"],
                    max_drawdown=metrics["max_drawdown"],
                    net_pnl=metrics["net_pnl"],
                    trade_count=int(metrics["trade_count"]),
                )
            )

        return robustness

    # ── Stage 4: Promotion ─────────────────────────────────

    def _run_promotion(
        self,
        validations: list[ValidationResult],
        robustness: dict[str, list[RobustnessResult]],
    ) -> list[tuple[dict[str, Any], PromotionVerdict]]:
        gate = PromotionGate(
            baseline=self._config.baseline,
            criteria=self._config.promotion_criteria,
        )
        verdicts: list[tuple[dict[str, Any], PromotionVerdict]] = []

        for vr in validations:
            candidate = CandidateMetrics(
                avg_val_sharpe=vr.avg_val_sharpe,
                avg_val_drawdown=vr.avg_val_drawdown,
                avg_val_pnl=sum(vr.split_pnls),
                total_trade_count=vr.total_trade_count,
                split_sharpes=vr.split_sharpes,
            )
            key = json.dumps(vr.params, sort_keys=True)
            rob = robustness.get(key)
            verdict = gate.evaluate(candidate, rob)
            verdicts.append((vr.params, verdict))

            self._tracker.record(
                vr.params,
                {"promotion_decision": 1.0 if verdict.decision == "PROMOTE" else 0.0},
                tags=["promotion", verdict.decision.lower()],
            )

        return verdicts

    # ── Helpers ─────────────────────────────────────────────

    def _run_parallel(
        self,
        args_list: list[Any],
    ) -> list[tuple[dict[str, Any], dict[str, float]]]:
        """Run backtests in parallel, return ordered results."""
        results: list[tuple[dict[str, Any], dict[str, float]]] = []
        if len(args_list) <= 1 or self._workers <= 1:
            for args in args_list:
                results.append(_run_backtest_worker(args))
        else:
            with ProcessPoolExecutor(max_workers=self._workers) as pool:
                future_to_idx = {pool.submit(_run_backtest_worker, args): i for i, args in enumerate(args_list)}
                indexed: list[tuple[int, tuple[dict[str, Any], dict[str, float]]]] = []
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    indexed.append((idx, future.result()))
                indexed.sort(key=lambda x: x[0])
                results = [r for _, r in indexed]
        return results

    def _log(self, msg: str) -> None:
        if self._config.verbose:
            print(f"[AutoResearch] {msg}")

    def _save_report(self, report: CampaignReport) -> None:
        self._campaign_dir.mkdir(parents=True, exist_ok=True)
        path = self._campaign_dir / "campaign_report.json"

        data: dict[str, Any] = {
            "campaign_id": report.campaign_id,
            "config": report.config,
            "runtime_s": report.runtime_s,
            "authoritative_promote_path": report.authoritative_promote_path,
            "split_contract_version": report.split_contract_version,
            "test_evaluated": report.test_evaluated,
            "invalidated_reason": report.invalidated_reason,
            "sweep": {
                "total_combinations": report.sweep.total_combinations,
                "runtime_s": report.sweep.runtime_s,
                "top_n": report.sweep.top_n,
            },
            "validations": [asdict(v) for v in report.validations],
            "robustness": {k: [asdict(r) for r in rs] for k, rs in report.robustness.items()},
            "verdicts": [{"params": p, "verdict": asdict(v)} for p, v in report.verdicts],
            "best_params": report.best_params,
            "best_verdict": asdict(report.best_verdict) if report.best_verdict else None,
        }
        path.write_text(json.dumps(data, indent=2, default=str))


def run_search_stage(config: CampaignConfig) -> SweepResult:
    """Run AutoResearch search only on the train split.

    This helper exists so the authoritative framework campaign can reuse
    AutoResearch's train-only candidate generation without inheriting its
    legacy promotion semantics.
    """
    campaign = Campaign(config)
    return campaign._run_sweep()


def run_dev_validation_stage(config: CampaignConfig, candidates: list[dict[str, Any]]) -> list[ValidationResult]:
    """Run dev-only validation for candidate params."""
    campaign = Campaign(config)
    return campaign._run_walk_forward(candidates)


def run_dev_robustness_stage(
    config: CampaignConfig,
    validations: list[ValidationResult],
) -> dict[str, list[RobustnessResult]]:
    """Run dev-only robustness checks for validated candidates."""
    campaign = Campaign(config)
    return campaign._run_robustness(validations)


# ── Utility functions ─────────────────────────────────────────


def _build_param_grid(space: SearchSpace) -> list[dict[str, Any]]:
    """Build Cartesian product of all parameter ranges."""
    all_params: dict[str, list[Any]] = {}
    all_params.update(space.strategy_params)
    # Plugin params use dotted keys — pass through as-is
    all_params.update(space.plugin_params)

    if not all_params:
        return [{}]

    keys = list(all_params.keys())
    value_lists = [all_params[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in product(*value_lists)]


def _build_strategy_config(
    params: dict[str, Any],
    *,
    execution_timeframe: Timeframe = Timeframe.TF_5M,
) -> ARCStrategyConfig:
    """Build ARCStrategyConfig from shared mapping plus legacy playbook aliases."""
    config = _shared_build_strategy_config(params)
    playbook_kwargs: dict[str, Any] = {}
    exit_kwargs: dict[str, Any] = {}
    for key, value in params.items():
        if key == "preserve_capital_risk":
            playbook_kwargs["preserve_capital_transition_risk"] = value
        elif key == "bull_leverage":
            playbook_kwargs["bull_max_leverage"] = value
        elif key == "bear_leverage":
            playbook_kwargs["bear_max_leverage"] = value
        elif key == "range_leverage":
            playbook_kwargs["range_max_leverage"] = value
        elif key == "exit_transition_risk":
            exit_kwargs["transition_risk_exit_threshold"] = value
    existing_playbook = vars(config.playbook_config) if config.playbook_config is not None else {}
    existing_exit = vars(config.exit_config) if config.exit_config is not None else {}
    return ARCStrategyConfig(
        **{
            **asdict(config),
            "execution_timeframe": execution_timeframe,
            "playbook_config": (
                PlaybookEngineConfig(**{**existing_playbook, **playbook_kwargs})
                if config.playbook_config is not None or playbook_kwargs
                else None
            ),
            "exit_config": (
                ExitEngineConfig(**{**existing_exit, **exit_kwargs})
                if config.exit_config is not None or exit_kwargs
                else None
            ),
        }
    )


def _config_to_dict(config: CampaignConfig) -> dict[str, Any]:
    """Serialize CampaignConfig to JSON-safe dict."""
    return {
        "data_root": str(config.data_root),
        "symbol": config.symbol,
        "starting_equity": config.starting_equity,
        "split_contract": split_summary_json(config.split_spec or default_btc_split_spec()),
        "search_space": asdict(config.search_space),
        "seed_trials": config.seed_trials,
        "wf_splits": [asdict(s) for s in config.wf_splits],
        "top_n_for_validation": config.top_n_for_validation,
        "robustness_seeds": config.robustness_seeds,
        "primary_metric": config.primary_metric,
        "campaign_label": config.campaign_label,
    }
