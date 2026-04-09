"""Framework-level historical optimizer with shared anti-overfit scoring."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from typing import Any

from zenquant.data.deribit_status import collect_deribit_status
from zenquant.data.liquidation_pipeline import liquidation_readiness
from zenquant.historical import HistoricalBacktestConfig
from zenquant.research.auto_research import CampaignConfig, SearchSpace, run_search_stage
from zenquant.research.comparison import (
    VariantSpec,
    WindowSpec,
    _run_variant_backtest,
    default_comparison_windows,
    default_variant_specs,
    default_walk_forward_windows,
    filtered_worst_period_windows,
    paulwei_risk_baseline,
)
from zenquant.research.framework_params import (
    build_execution_config,
    build_indicator_config,
    build_replay_toggles,
    build_risk_config,
    build_signal_weight_overrides,
    build_strategy_config,
    core_param_grid,
    search_space_profile,
)
from zenquant.research.splits import DatasetSplitSpec, default_btc_split_spec, split_summary_json


@dataclass(slots=True, frozen=True)
class CandidateSummary:
    variant: str
    robust_score: float
    selection_split: str
    dev_sharpe: float
    dev_pnl: float
    dev_drawdown: float
    avg_dev_sharpe: float
    avg_dev_ratio: float
    worst_dev_sharpe: float
    weak_window_sharpe: float
    negative_dev_worst_windows: int
    promotion_candidate: bool
    test_sharpe: float | None = None
    test_drawdown: float | None = None
    test_adversarial_failures: int | None = None
    test_evaluated: bool = False
    blocked_by: str | None = None
    failed_gate_metrics: dict[str, bool] | None = None
    promotion_failure_reason: str | None = None


@dataclass(slots=True)
class SearchReport:
    created_at: str
    symbol: str
    readiness: dict[str, Any]
    deribit_status: dict[str, Any]
    split_contract: dict[str, Any]
    training_window_coverage: dict[str, Any]
    search_status: dict[str, Any]
    current: CandidateSummary
    paulwei_baseline: CandidateSummary
    optuna_best: CandidateSummary | None
    rl_best: CandidateSummary | None
    best_variant: str
    best_score: float
    best_params: dict[str, Any] | None
    comparison_payload: dict[str, Any]
    test_comparison_payload: dict[str, Any] | None = None
    test_evaluated: bool = False
    report_mode: str = "fast"


class FrameworkEvaluator:
    """Evaluate framework candidates against current and PaulWei baselines."""

    def __init__(self, base_config: HistoricalBacktestConfig, *, split_spec: DatasetSplitSpec | None = None) -> None:
        self._base_config = base_config
        self._split_spec = split_spec or default_btc_split_spec()
        readiness = liquidation_readiness(
            base_config.root_dir,
            base_config.symbol,
            liquidation_dir=base_config.liquidation_dir,
        )
        self._readiness = {
            "total_bars": readiness.total_bars,
            "nonzero_bars": readiness.nonzero_bars,
            "nonstale_bars": readiness.nonstale_bars,
            "extreme_bars": readiness.extreme_bars,
            "max_total_liq_usd": readiness.max_total_liq_usd,
            "data_ready": readiness.data_ready,
            "promotion_ready": readiness.promotion_ready,
            "blocked_by": readiness.blocked_by,
            "detail": readiness.detail,
        }
        self._backtest_cache: dict[tuple[str, int | None, int | None], tuple[Any, Any]] = {}
        self._validation_cache: dict[tuple[str, tuple[str, ...]], dict[str, float]] = {}
        self._summary_cache: dict[tuple[str, bool], CandidateSummary] = {}
        self._dev_windows = [
            *default_comparison_windows(self._split_spec, "dev"),
            *filtered_worst_period_windows(self._split_spec, "dev"),
        ]
        self._test_windows = [
            *default_comparison_windows(self._split_spec, "test"),
            *filtered_worst_period_windows(self._split_spec, "test"),
        ]
        self._fast_dev_wf_windows = default_walk_forward_windows(split_spec=self._split_spec, comparison_split="dev")[:2]
        self._full_dev_wf_windows = default_walk_forward_windows(split_spec=self._split_spec, comparison_split="dev")
        self._current_variant = default_variant_specs()[0]
        self._paulwei_variant = default_variant_specs()[1]
        self._deribit_status = asdict(collect_deribit_status(base_config.options_dir, "BTC"))
        self._training_window_coverage = {
            "train_window": split_summary_json(self._split_spec)["windows"]["train"],
            "dev_windows": [window.label for window in self._dev_windows],
            "dev_walk_forward_windows": [window.label for window in self._full_dev_wf_windows],
            "test_windows": [window.label for window in self._test_windows],
        }
        self._search_param_grid = core_param_grid(include_liquidation_toggles=self._readiness["promotion_ready"])
        self._search_space_profile = search_space_profile(include_liquidation_toggles=self._readiness["promotion_ready"])
        self._paulwei_fast = self._evaluate_variant(self._paulwei_variant, fast=True)
        self._current_fast = self._evaluate_variant(self._current_variant, fast=True)

    @property
    def readiness(self) -> dict[str, Any]:
        return dict(self._readiness)

    @property
    def search_param_grid(self) -> dict[str, list[float]]:
        return dict(self._search_param_grid)

    @property
    def search_space_profile(self) -> dict[str, Any]:
        return dict(self._search_space_profile)

    def candidate_variant(self, params: dict[str, Any], *, name: str, label: str) -> VariantSpec:
        risk_config = build_risk_config(params)
        execution_config = build_execution_config(params)
        strategy_config = build_strategy_config(params)
        indicator_config = build_indicator_config(params)
        signal_weight_overrides = build_signal_weight_overrides(params)
        liquidation_data_enabled, state_liquidation_overlay_enabled = build_replay_toggles(params)
        if not self._readiness["data_ready"]:
            liquidation_data_enabled = False
            state_liquidation_overlay_enabled = False
            risk_config = paulwei_risk_baseline() if not risk_config.enable_liq_risk_guard else risk_config
        return VariantSpec(
            name=name,
            label=label,
            risk_config=risk_config,
            execution_config=execution_config,
            strategy_config=strategy_config,
            indicator_config=indicator_config,
            signal_weight_overrides=signal_weight_overrides,
            liquidation_data_enabled=liquidation_data_enabled,
            state_liquidation_overlay_enabled=state_liquidation_overlay_enabled,
        )

    def evaluate_params(
        self,
        params: dict[str, Any],
        *,
        name: str = "candidate",
        label: str = "Candidate",
        fast: bool = True,
    ) -> CandidateSummary:
        requested_liquidation, requested_state_overlay = build_replay_toggles(params)
        requested_liq_guard = bool(round(float(params.get("liq_risk_guard_enabled", 0.0))))
        variant = self.candidate_variant(params, name=name, label=label)
        summary = self._evaluate_variant(variant, fast=fast)
        if (
            (requested_liquidation or requested_state_overlay or requested_liq_guard)
            and not self._readiness["data_ready"]
        ):
            summary = CandidateSummary(
                **{**asdict(summary), "promotion_candidate": False, "blocked_by": self._readiness["blocked_by"]}
            )
        return summary

    def final_report(
        self,
        *,
        optuna_params: dict[str, Any] | None,
        rl_params: dict[str, Any] | None,
        search_status: dict[str, Any] | None = None,
        full: bool = False,
        evaluate_test: bool = False,
    ) -> SearchReport:
        optuna_variant = (
            self.candidate_variant(optuna_params, name="optuna_tuned", label="Optuna Tuned") if optuna_params else None
        )
        rl_variant = self.candidate_variant(rl_params, name="rl_tuned", label="RL Tuned") if rl_params else None
        summaries = {
            "current": self._evaluate_variant(self._current_variant, fast=not full),
            "paulwei_baseline": self._evaluate_variant(self._paulwei_variant, fast=not full),
        }
        if optuna_variant is not None:
            summaries["optuna_tuned"] = self._evaluate_variant(optuna_variant, fast=not full)
        if rl_variant is not None:
            summaries["rl_tuned"] = self._evaluate_variant(rl_variant, fast=not full)

        best_name, best_summary = max(summaries.items(), key=lambda item: item[1].robust_score)
        evaluated_best_summary = best_summary
        if evaluate_test:
            best_variant_spec = next(variant for variant in [self._current_variant, self._paulwei_variant, optuna_variant, rl_variant] if variant is not None and variant.name == best_name)
            evaluated_best_summary = self._evaluate_test_acceptance(best_variant_spec, best_summary)
            summaries[best_name] = evaluated_best_summary
        variants = [
            self._current_variant,
            self._paulwei_variant,
            *([optuna_variant] if optuna_variant is not None else []),
            *([rl_variant] if rl_variant is not None else []),
        ]
        comparison_payload = _build_comparison_payload(
            self._base_config,
            variants=[variant for variant in variants if variant is not None],
            windows=self._dev_windows,
            walk_forward_windows=self._full_dev_wf_windows if full else self._fast_dev_wf_windows,
            split_spec=self._split_spec,
            comparison_split="dev",
        )
        comparison_payload["liquidation_readiness"] = dict(self._readiness)
        test_payload = None
        if evaluate_test:
            test_payload = _build_comparison_payload(
                self._base_config,
                variants=[variant for variant in variants if variant is not None and variant.name == best_name],
                windows=self._test_windows,
                walk_forward_windows=[],
                split_spec=self._split_spec,
                comparison_split="test",
            )
        return SearchReport(
            created_at=datetime.now(UTC).isoformat(),
            symbol=self._base_config.symbol,
            readiness=dict(self._readiness),
            deribit_status=dict(self._deribit_status),
            split_contract=split_summary_json(self._split_spec),
            training_window_coverage=dict(self._training_window_coverage),
            search_status=dict(search_status or {}),
            current=summaries["current"],
            paulwei_baseline=summaries["paulwei_baseline"],
            optuna_best=summaries.get("optuna_tuned"),
            rl_best=summaries.get("rl_tuned"),
            best_variant=best_name,
            best_score=evaluated_best_summary.robust_score,
            best_params=optuna_params if best_name == "optuna_tuned" else rl_params if best_name == "rl_tuned" else None,
            comparison_payload=comparison_payload,
            test_comparison_payload=test_payload,
            test_evaluated=evaluate_test,
            report_mode="full" if full else "fast",
        )

    def _evaluate_variant(self, variant: VariantSpec, *, fast: bool) -> CandidateSummary:
        cache_key = (self._variant_key(variant), fast)
        cached = self._summary_cache.get(cache_key)
        if cached is not None:
            return cached
        windows = self._dev_windows
        wf_windows = self._fast_dev_wf_windows if fast else self._full_dev_wf_windows

        dev_window = next(window for window in windows if window.label == "dev")
        dev_runner, dev_result = self._run_variant_backtest_cached(
            variant,
            start_ns=dev_window.start_ns,
            end_ns=dev_window.end_ns,
        )
        del dev_runner
        weak_window = next(window for window in windows if window.label == "2023-08")
        _, weak_result = self._run_variant_backtest_cached(variant, start_ns=weak_window.start_ns, end_ns=weak_window.end_ns)
        worst_periods = [window for window in windows if window.label not in {"dev", "2023-08"}]
        worst_results = [
            self._run_variant_backtest_cached(variant, start_ns=window.start_ns, end_ns=window.end_ns)[1]
            for window in worst_periods
        ]
        wf_metrics = self._run_validation_bundle_cached(variant, wf_windows)
        negative_worst_windows = sum(1 for result in worst_results if result.metrics.sharpe < 0)

        baseline = None
        if variant.name != "paulwei_baseline" and hasattr(self, "_paulwei_fast"):
            baseline = self._paulwei_fast
        score = _candidate_score(
            avg_test_sharpe=wf_metrics["avg_test_sharpe"],
            worst_test_sharpe=wf_metrics["worst_test_sharpe"],
            avg_oos_ratio=wf_metrics["avg_oos_ratio"],
            overall_sharpe=dev_result.metrics.sharpe,
            overall_pnl=dev_result.metrics.net_pnl,
            weak_window_sharpe=weak_result.metrics.sharpe,
            overall_drawdown=dev_result.metrics.max_drawdown,
            negative_worst_windows=negative_worst_windows,
            baseline=baseline,
        )
        gate_metrics, failure_reason = _promotion_diagnostics(
            avg_test_sharpe=wf_metrics["avg_test_sharpe"],
            worst_test_sharpe=wf_metrics["worst_test_sharpe"],
            overall_drawdown=dev_result.metrics.max_drawdown,
            negative_worst_windows=negative_worst_windows,
            baseline=baseline,
        )
        promote = all(gate_metrics.values())
        summary = CandidateSummary(
            variant=variant.name,
            robust_score=round(score, 4),
            selection_split="dev",
            dev_sharpe=dev_result.metrics.sharpe,
            dev_pnl=dev_result.metrics.net_pnl,
            dev_drawdown=dev_result.metrics.max_drawdown,
            avg_dev_sharpe=wf_metrics["avg_test_sharpe"],
            avg_dev_ratio=wf_metrics["avg_oos_ratio"],
            worst_dev_sharpe=wf_metrics["worst_test_sharpe"],
            weak_window_sharpe=weak_result.metrics.sharpe,
            negative_dev_worst_windows=negative_worst_windows,
            promotion_candidate=promote,
            blocked_by=None,
            failed_gate_metrics=gate_metrics,
            promotion_failure_reason=failure_reason,
        )
        self._summary_cache[cache_key] = summary
        return summary

    def _evaluate_test_acceptance(self, variant: VariantSpec, summary: CandidateSummary) -> CandidateSummary:
        test_window = next(window for window in self._test_windows if window.label == "test")
        _, test_result = self._run_variant_backtest_cached(variant, start_ns=test_window.start_ns, end_ns=test_window.end_ns)
        worst_test_windows = [window for window in self._test_windows if window.label != "test"]
        worst_failures = 0
        for window in worst_test_windows:
            _, result = self._run_variant_backtest_cached(variant, start_ns=window.start_ns, end_ns=window.end_ns)
            if result.metrics.sharpe < 0:
                worst_failures += 1
        return CandidateSummary(
            **{
                **asdict(summary),
                "test_sharpe": test_result.metrics.sharpe,
                "test_drawdown": test_result.metrics.max_drawdown,
                "test_adversarial_failures": worst_failures,
                "test_evaluated": True,
                "promotion_candidate": summary.promotion_candidate and test_result.metrics.sharpe >= 0 and worst_failures == 0,
                "promotion_failure_reason": (
                    summary.promotion_failure_reason
                    or ("test_sharpe" if test_result.metrics.sharpe < 0 else "test_adversarial" if worst_failures else None)
                ),
            }
        )

    def _run_variant_backtest_cached(
        self,
        variant: VariantSpec,
        *,
        start_ns: int | None,
        end_ns: int | None,
    ) -> tuple[Any, Any]:
        key = (self._variant_key(variant), start_ns, end_ns)
        cached = self._backtest_cache.get(key)
        if cached is not None:
            return cached
        result = _run_variant_backtest(self._base_config, variant, start_ns=start_ns, end_ns=end_ns)
        self._backtest_cache[key] = result
        return result

    def _run_validation_bundle_cached(self, variant: VariantSpec, windows: list[WindowSpec]) -> dict[str, float]:
        key = (self._variant_key(variant), tuple(window.label for window in windows))
        cached = self._validation_cache.get(key)
        if cached is not None:
            return cached
        result = _run_validation_bundle(self._base_config, variant, windows, run_backtest=self._run_variant_backtest_cached)
        self._validation_cache[key] = result
        return result

    @staticmethod
    def _variant_key(variant: VariantSpec) -> str:
        payload = json.dumps(asdict(variant), sort_keys=True, default=str)
        return sha1(payload.encode("utf-8")).hexdigest()


def _run_validation_bundle(
    base_config: HistoricalBacktestConfig,
    variant: VariantSpec,
    windows: list[WindowSpec],
    *,
    run_backtest: Any = None,
) -> dict[str, float]:
    if not windows:
        return {"avg_test_sharpe": 0.0, "avg_oos_ratio": 0.0, "worst_test_sharpe": 0.0}
    run = run_backtest or (lambda v, start_ns, end_ns: _run_variant_backtest(base_config, v, start_ns=start_ns, end_ns=end_ns))
    test_sharpes: list[float] = []
    ratios: list[float] = []
    for window in windows:
        train_end = window.start_ns
        assert train_end is not None
        train_start = _shift_months(train_end, -24)
        _, train_result = run(variant, start_ns=train_start, end_ns=train_end)
        _, test_result = run(variant, start_ns=window.start_ns, end_ns=window.end_ns)
        ratio = test_result.metrics.sharpe / train_result.metrics.sharpe if abs(train_result.metrics.sharpe) > 0.01 else 0.0
        test_sharpes.append(test_result.metrics.sharpe)
        ratios.append(ratio)
    return {
        "avg_test_sharpe": round(sum(test_sharpes) / len(test_sharpes), 4),
        "avg_oos_ratio": round(sum(ratios) / len(ratios), 4),
        "worst_test_sharpe": round(min(test_sharpes), 4),
    }


def _candidate_score(
    *,
    avg_test_sharpe: float,
    worst_test_sharpe: float,
    avg_oos_ratio: float,
    overall_sharpe: float,
    overall_pnl: float,
    weak_window_sharpe: float,
    overall_drawdown: float,
    negative_worst_windows: int,
    baseline: CandidateSummary | None,
) -> float:
    score = (
        0.34 * avg_test_sharpe
        + 0.22 * worst_test_sharpe
        + 0.18 * avg_oos_ratio
        + 0.05 * overall_sharpe
        + 0.04 * weak_window_sharpe
        + 0.01 * (overall_pnl / 1_000_000.0)
        - 8.0 * max(0.0, overall_drawdown - 0.10)
        - 1.25 * negative_worst_windows
    )
    if baseline is not None:
        score -= 12.0 * max(0.0, overall_drawdown - (baseline.dev_drawdown + 0.02))
        score -= 3.0 * max(0.0, baseline.worst_dev_sharpe - worst_test_sharpe)
        score -= 2.0 * max(0, negative_worst_windows - baseline.negative_dev_worst_windows)
    return score


def _promotion_diagnostics(
    *,
    avg_test_sharpe: float,
    worst_test_sharpe: float,
    overall_drawdown: float,
    negative_worst_windows: int,
    baseline: CandidateSummary | None,
) -> tuple[dict[str, bool], str | None]:
    if baseline is None:
        return {
            "avg_test_sharpe": True,
            "worst_test_sharpe": True,
            "overall_drawdown": True,
            "negative_worst_windows": True,
        }, None
    gate_metrics = {
        "avg_test_sharpe": avg_test_sharpe >= baseline.avg_dev_sharpe,
        "worst_test_sharpe": worst_test_sharpe >= baseline.worst_dev_sharpe,
        "overall_drawdown": overall_drawdown <= baseline.dev_drawdown + 0.03,
        "negative_worst_windows": negative_worst_windows <= baseline.negative_dev_worst_windows,
    }
    for key in ("overall_drawdown", "worst_test_sharpe", "negative_worst_windows", "avg_test_sharpe"):
        if not gate_metrics[key]:
            return gate_metrics, key
    return gate_metrics, None


def _shift_months(ts_ns: int, months: int) -> int:
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC)
    month_index = (dt.month - 1) + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1e9)


def _build_comparison_payload(
    base_config: HistoricalBacktestConfig,
    *,
    variants: list[VariantSpec],
    windows: list[WindowSpec],
    walk_forward_windows: list[WindowSpec],
    split_spec: DatasetSplitSpec,
    comparison_split: str,
) -> dict[str, Any]:
    from zenquant.research.comparison import compare_variants

    return compare_variants(
        base_config,
        variants=variants,
        windows=windows,
        walk_forward_windows=walk_forward_windows,
        split_spec=split_spec,
        comparison_split=comparison_split,
    )


def run_optuna_search(
    evaluator: FrameworkEvaluator,
    *,
    n_trials: int = 12,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    try:
        import optuna  # noqa: F401
    except ImportError:
        return None, []
    worker_config = CampaignConfig(
        data_root=evaluator._base_config.root_dir,
        symbol=evaluator._base_config.symbol,
        split_spec=evaluator._split_spec,
        search_space=SearchSpace(strategy_params=evaluator.search_param_grid),
        sweep_mode="bayesian",
        n_trials=n_trials,
        wf_splits=[],
        top_n_for_validation=min(5, max(1, n_trials)),
        robustness_seeds=[],
        campaign_label="framework_stage1",
        output_dir=Path("results/archive/dev_runs/auto_research_stage1"),
        verbose=False,
    )
    sweep = run_search_stage(worker_config)
    ranked: list[dict[str, Any]] = []
    for candidate in sweep.top_n:
        params = dict(candidate["params"])
        summary = evaluator.evaluate_params(params, name="optuna_candidate", label="Optuna Candidate", fast=True)
        ranked.append(
            {
                "params": params,
                "train_metrics": candidate["metrics"],
                "summary": asdict(summary),
                "candidate_source": "auto_research_train_only",
                "split_contract_version": evaluator._split_spec.version,
            }
        )
    ranked.sort(key=lambda item: item["summary"]["robust_score"], reverse=True)
    best = ranked[0] if ranked else None
    return (best["params"] if best else None), ranked


def build_markdown_report(report: SearchReport) -> str:
    lines = [
        "# Framework Optimization",
        "",
        f"- created_at: `{report.created_at}`",
        f"- symbol: `{report.symbol}`",
        f"- best_variant: `{report.best_variant}`",
        f"- best_score: `{report.best_score}`",
        f"- test_evaluated: `{report.test_evaluated}`",
        "",
        "## Readiness",
        "",
        f"- liquidation_data_ready: `{report.readiness['data_ready']}`",
        f"- liquidation_promotion_ready: `{report.readiness['promotion_ready']}`",
        f"- blocked_by: `{report.readiness['blocked_by']}`",
        f"- detail: `{report.readiness['detail']}`",
        f"- deribit_enabled: `{report.deribit_status['enabled']}`",
        f"- deribit_observation_ready: `{report.deribit_status['observation_ready']}`",
        f"- deribit_research_ready: `{report.deribit_status['research_ready']}`",
        f"- split_contract_version: `{report.split_contract['version']}`",
        "",
        "## Search Status",
        "",
        f"- optuna_available: `{report.search_status.get('optuna_available')}`",
        f"- optuna_trials_requested: `{report.search_status.get('optuna_trials_requested')}`",
        f"- rl_episodes_requested: `{report.search_status.get('rl_episodes_requested')}`",
        f"- optuna_candidate_found: `{report.search_status.get('optuna_candidate_found')}`",
        f"- rl_candidate_found: `{report.search_status.get('rl_candidate_found')}`",
        f"- liquidation_in_search: `{report.search_status.get('liquidation_in_search')}`",
        f"- liquidation_blocked_by: `{report.search_status.get('liquidation_blocked_by')}`",
        f"- search_space_profile: `{report.search_status.get('search_space_profile')}`",
        "",
    ]
    for item in [report.current, report.paulwei_baseline, report.optuna_best, report.rl_best]:
        if item is None:
            continue
        lines.extend(
            [
                f"## {item.variant}",
                "",
                f"- robust_score: `{item.robust_score}`",
                f"- selection_split: `{item.selection_split}`",
                f"- dev_sharpe: `{item.dev_sharpe}`",
                f"- avg_dev_sharpe: `{item.avg_dev_sharpe}`",
                f"- avg_dev_ratio: `{item.avg_dev_ratio}`",
                f"- weak_window_sharpe: `{item.weak_window_sharpe}`",
                f"- dev_drawdown: `{item.dev_drawdown}`",
                f"- test_sharpe: `{item.test_sharpe}`",
                f"- test_evaluated: `{item.test_evaluated}`",
                f"- promotion_candidate: `{item.promotion_candidate}`",
                f"- promotion_failure_reason: `{item.promotion_failure_reason}`",
                f"- failed_gate_metrics: `{item.failed_gate_metrics}`",
                f"- blocked_by: `{item.blocked_by}`",
                "",
            ]
        )
    return "\n".join(lines)


def write_search_report(path: Path, report: SearchReport) -> None:
    payload = asdict(report)
    best_summary = next(
        (
            item
            for item in [report.current, report.paulwei_baseline, report.optuna_best, report.rl_best]
            if item is not None and item.variant == report.best_variant
        ),
        None,
    )
    payload["data_sufficiency"] = {
        "deribit_status": payload["deribit_status"],
        "liquidation_readiness": payload["readiness"],
        "training_window_coverage": payload["training_window_coverage"],
        "split_contract": payload["split_contract"],
    }
    payload["historical_leaderboard"] = [
        asdict(report.current),
        asdict(report.paulwei_baseline),
        asdict(report.optuna_best) if report.optuna_best else None,
        asdict(report.rl_best) if report.rl_best else None,
    ]
    comparison_to_current = None
    comparison_to_paulwei = None
    if best_summary is not None:
        comparison_to_current = {
            "variant": best_summary.variant,
            "drawdown_not_worse": best_summary.dev_drawdown <= report.current.dev_drawdown + 0.03,
            "worst_dev_sharpe_not_worse": best_summary.worst_dev_sharpe >= report.current.worst_dev_sharpe,
            "negative_worst_windows_not_worse": (
                best_summary.negative_dev_worst_windows <= report.current.negative_dev_worst_windows
            ),
        }
        comparison_to_paulwei = {
            "variant": best_summary.variant,
            "drawdown_not_worse": best_summary.dev_drawdown <= report.paulwei_baseline.dev_drawdown + 0.03,
            "worst_dev_sharpe_not_worse": best_summary.worst_dev_sharpe >= report.paulwei_baseline.worst_dev_sharpe,
            "negative_worst_windows_not_worse": (
                best_summary.negative_dev_worst_windows <= report.paulwei_baseline.negative_dev_worst_windows
            ),
        }
    verdict_kind = "blocked_by_data_readiness" if payload["readiness"]["blocked_by"] else "promote_candidate_found"
    if best_summary is not None and not best_summary.promotion_candidate:
        verdict_kind = (
            "no_promote_candidate_found_param_side"
            if best_summary.promotion_failure_reason == "avg_test_sharpe"
            else "no_promote_candidate_found_architecture_side"
        )
    payload["final_verdict"] = {
        "best_variant": report.best_variant,
        "candidate_ready_for_test": bool(best_summary.promotion_candidate) if best_summary and not report.test_evaluated else False,
        "promote_to_paper_live_candidate": bool(best_summary.promotion_candidate) if best_summary and report.test_evaluated else False,
        "test_evaluated": report.test_evaluated,
        "blocked_by": payload["readiness"]["blocked_by"],
        "promotion_failure_reason": best_summary.promotion_failure_reason if best_summary else None,
        "failed_gate_metrics": best_summary.failed_gate_metrics if best_summary else None,
        "split_contract_version": payload["split_contract"]["version"],
        "train_window": payload["split_contract"]["windows"]["train"],
        "dev_window": payload["split_contract"]["windows"]["dev"],
        "test_window": payload["split_contract"]["windows"]["test"],
        "liquidation_in_search": payload["search_status"].get("liquidation_in_search"),
        "search_space_profile": payload["search_status"].get("search_space_profile"),
        "verdict_kind": verdict_kind,
        "comparison_to_current": comparison_to_current,
        "comparison_to_paulwei_baseline": comparison_to_paulwei,
        "next_data_priority": (
            "liquidation_history"
            if not payload["readiness"]["promotion_ready"]
            else "deribit_history"
            if not payload["deribit_status"]["research_ready"]
            else "none"
        ),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
