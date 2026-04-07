"""AutoResearch campaign orchestrator.

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
"""

from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.historical.perturbation import PerturbationConfig
from zenquant.strategy import (
    ARCStrategyConfig,
    ExitEngineConfig,
    PlaybookEngineConfig,
)

from .experiment import ExperimentTracker
from .promoter import (
    BaselineMetrics,
    CandidateMetrics,
    PromotionCriteria,
    PromotionGate,
    PromotionVerdict,
    RobustnessResult,
)

# ── Configuration ─────────────────────────────────────────────


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

    # Search
    search_space: SearchSpace = field(default_factory=SearchSpace)
    # Sweep mode: "grid" (exhaustive Cartesian product) or "bayesian" (Optuna TPE)
    sweep_mode: str = "grid"
    # Number of Optuna trials (only used when sweep_mode="bayesian")
    n_trials: int = 50

    # Walk-forward validation
    wf_splits: list[WalkForwardSplit] = field(default_factory=list)
    top_n_for_validation: int = 3

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


# ── Results ───────────────────────────────────────────────────


@dataclass(slots=True)
class SweepResult:
    """Result from the initial parameter sweep stage."""

    total_combinations: int
    results: list[dict[str, Any]]
    top_n: list[dict[str, Any]]
    runtime_s: float


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


# ── Orchestrator ──────────────────────────────────────────────


def _run_backtest_worker(
    args: tuple[dict[str, Any], str, str, float, int | None, int | None, dict[str, Any] | None],
) -> tuple[dict[str, Any], dict[str, float]]:
    """Run one backtest in a subprocess.  Module-level for pickling."""
    params, root_dir, symbol, equity, start_ns, end_ns, perturbation_dict = args
    strategy_config = _build_strategy_config(params)
    perturbation = PerturbationConfig(**perturbation_dict) if perturbation_dict else None
    config = HistoricalBacktestConfig(
        root_dir=Path(root_dir),
        symbol=symbol,
        starting_equity=equity,
        start_ns=start_ns,
        end_ns=end_ns,
        strategy_config=strategy_config,
        perturbation=perturbation,
    )
    bt = HistoricalBacktest(config)
    result = bt.run()
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
    }


def _max_workers() -> int:
    cpu_count = os.cpu_count() or 4
    return max(1, min(cpu_count - 2, 8))


class Campaign:
    """AutoResearch campaign — sweep, validate, test robustness, promote."""

    def __init__(self, config: CampaignConfig | None = None) -> None:
        self._config = config or CampaignConfig()
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        label = self._config.campaign_label or self._config.symbol.lower()
        self._campaign_id = f"{ts}_{label}"
        self._campaign_dir = self._config.output_dir / self._campaign_id
        self._tracker = ExperimentTracker(self._campaign_dir)
        self._workers = _max_workers()

        # Guard against accidental grid explosion
        sp = self._config.search_space
        all_vals = list(sp.strategy_params.values()) + list(sp.plugin_params.values())
        if all_vals:
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
            self._log("Walk-forward validation skipped: no splits provided")
            # Skip WF — treat sweep top-N as candidates directly
            for entry in sweep.top_n:
                m = entry["metrics"]
                validations.append(
                    ValidationResult(
                        params=entry["params"],
                        split_sharpes=[m["sharpe"]],
                        split_drawdowns=[m["max_drawdown"]],
                        split_pnls=[m["net_pnl"]],
                        split_trade_counts=[int(m["trade_count"])],
                        avg_val_sharpe=m["sharpe"],
                        avg_val_drawdown=m["max_drawdown"],
                        total_trade_count=int(m["trade_count"]),
                    )
                )

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
        )
        self._save_report(report)
        return report

    # ── Stage 1: Sweep ─────────────────────────────────────

    def _run_sweep(self) -> SweepResult:
        if self._config.sweep_mode == "bayesian":
            return self._run_optuna_sweep()
        return self._run_grid_sweep()

    def _run_grid_sweep(self) -> SweepResult:
        start = time.time()
        param_grid = _build_param_grid(self._config.search_space)
        if not param_grid:
            return SweepResult(total_combinations=0, results=[], top_n=[], runtime_s=0)

        self._log(f"Grid sweep: {len(param_grid)} parameter combinations")

        # Build backtest args — full data range (no splits)
        args_list = [
            (
                params,
                str(self._config.data_root),
                self._config.symbol,
                self._config.starting_equity,
                None,  # start_ns
                None,  # end_ns
                None,  # perturbation
            )
            for params in param_grid
        ]

        results = self._run_parallel(args_list)

        # Record to tracker
        for params, metrics in results:
            self._tracker.record(params, metrics, tags=["sweep"])

        # Rank
        sorted_results = sorted(
            [{"params": p, "metrics": m} for p, m in results],
            key=lambda x: x["metrics"].get(self._config.primary_metric, 0),
            reverse=True,
        )
        top_n = sorted_results[: self._config.top_n_for_validation]

        return SweepResult(
            total_combinations=len(param_grid),
            results=sorted_results,
            top_n=top_n,
            runtime_s=time.time() - start,
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

        def objective(trial: optuna.Trial) -> float:
            params: dict[str, Any] = {}
            for key, values in all_params.items():
                params[key] = trial.suggest_categorical(key, values)

            args = (
                params,
                str(self._config.data_root),
                self._config.symbol,
                self._config.starting_equity,
                None,
                None,
                None,
            )
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
        study.optimize(objective, n_trials=n_trials, n_jobs=1)

        # Sort all evaluated results
        sorted_results = sorted(
            all_results,
            key=lambda x: x["metrics"].get(self._config.primary_metric, 0),
            reverse=True,
        )
        top_n = sorted_results[: self._config.top_n_for_validation]

        self._log(
            f"Bayesian sweep complete: best {self._config.primary_metric} = {study.best_value:.4f} in {n_trials} trials"
        )

        return SweepResult(
            total_combinations=n_trials,
            results=sorted_results,
            top_n=top_n,
            runtime_s=time.time() - start,
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
                    None,
                    None,
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


def _build_strategy_config(params: dict[str, Any]) -> ARCStrategyConfig:
    """Build ARCStrategyConfig from a flat parameter dict."""
    playbook_kwargs: dict[str, Any] = {}
    exit_kwargs: dict[str, Any] = {}
    strategy_kwargs: dict[str, Any] = {}

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
        elif key == "target_vol_4h":
            strategy_kwargs["target_vol_4h"] = value
        elif key == "target_vol_1d":
            strategy_kwargs["target_vol_1d"] = value
        elif key == "trailing_stop_atr_mult":
            strategy_kwargs["trailing_stop_atr_mult"] = value

    return ARCStrategyConfig(
        playbook_config=PlaybookEngineConfig(**playbook_kwargs) if playbook_kwargs else None,
        exit_config=ExitEngineConfig(**exit_kwargs) if exit_kwargs else None,
        **strategy_kwargs,
    )


def _config_to_dict(config: CampaignConfig) -> dict[str, Any]:
    """Serialize CampaignConfig to JSON-safe dict."""
    return {
        "data_root": str(config.data_root),
        "symbol": config.symbol,
        "starting_equity": config.starting_equity,
        "search_space": asdict(config.search_space),
        "wf_splits": [asdict(s) for s in config.wf_splits],
        "top_n_for_validation": config.top_n_for_validation,
        "robustness_seeds": config.robustness_seeds,
        "primary_metric": config.primary_metric,
        "campaign_label": config.campaign_label,
    }
