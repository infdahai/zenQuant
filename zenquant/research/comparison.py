"""Variant comparison helpers for historical replay research."""

from __future__ import annotations

import json
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, as_completed, wait
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.execution import ExecutionKernelConfig
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.historical.replay import ReplayContext, _build_replay_context
from zenquant.indicator import IndicatorEngineConfig
from zenquant.research.splits import DatasetSplitSpec, SplitWindow, default_btc_split_spec, period_within_split
from zenquant.risk import RiskOverlayConfig
from zenquant.strategy import ARCStrategyConfig


@dataclass(slots=True, frozen=True)
class VariantSpec:
    """One replay variant in a comparison run."""

    name: str
    label: str
    risk_config: RiskOverlayConfig | None = None
    execution_config: ExecutionKernelConfig | None = None
    strategy_config: ARCStrategyConfig | None = None
    indicator_config: IndicatorEngineConfig | None = None
    signal_weight_overrides: dict[str, float] | None = None
    liquidation_data_enabled: bool | None = None
    state_liquidation_overlay_enabled: bool | None = None


@dataclass(slots=True, frozen=True)
class WindowSpec:
    """One replay window inside a comparison run."""

    label: str
    start_ns: int | None = None
    end_ns: int | None = None


@dataclass(slots=True, frozen=True)
class _ValidationTask:
    window: WindowSpec
    leg: str
    start_ns: int
    end_ns: int


class ComparisonValidationTimeoutError(RuntimeError):
    def __init__(
        self,
        *,
        variant: str,
        window_label: str | None,
        leg: str | None,
        completed_units: int,
        message: str,
    ) -> None:
        super().__init__(message)
        self.variant = variant
        self.window_label = window_label
        self.leg = leg
        self.completed_units = completed_units


_COMPARISON_REPLAY_CONTEXT_CACHE: dict[tuple[Any, ...], ReplayContext] = {}


def paulwei_risk_baseline() -> RiskOverlayConfig:
    """Frozen PaulWei risk baseline for historical comparison."""
    return RiskOverlayConfig(
        max_leverage=6.0,
        dd_step_1_long=0.15,
        dd_step_2_long=0.22,
        dd_step_3_long=0.28,
        dd_step_1_short=0.08,
        dd_step_2_short=0.14,
        dd_step_3_short=0.20,
        dd_step_1_mult=0.65,
        dd_step_2_mult=0.40,
        dd_step_3_mult=0.20,
        vol_shock_threshold=0.95,
        vol_shock_mult=0.40,
        crowding_threshold=2.2,
        crowding_mult=0.70,
        severe_crowding_threshold=3.0,
        daily_loss_limit=0.10,
        daily_safe_lev_cap=0.50,
        kill_switch_drawdown=0.30,
        degradation_sharpe_threshold=-2.0,
        degradation_min_bars=24,
        degradation_mult=0.40,
        kill_recovery_mult=0.30,
        equity_risk_off_threshold=-2.0,
        equity_risk_off_mult=0.50,
        equity_stress_threshold=-1.5,
        equity_stress_mult=0.80,
        cot_crowding_threshold=2.5,
        cot_crowding_mult=0.70,
        overheated_cycle_threshold=1.3,
        overheated_funding_threshold=1.5,
        overheated_mult=0.40,
        high_cycle_mult=0.70,
        gex_pinning_mult=1.15,
        gex_amplifying_mult=0.70,
        basis_extreme_threshold=2.0,
        basis_extreme_mult=0.80,
        basis_discount_threshold=-2.0,
        basis_discount_mult=0.80,
        vpin_toxic_threshold=0.10,
        vpin_toxic_mult=0.70,
        weekend_vol_scalar=1.25,
        macro_event_mult=0.75,
        kill_recovery_threshold=0.12,
        kill_cooldown_bars=36,
        kill_ramp_bars=24,
    )


def paulwei_execution_baseline() -> ExecutionKernelConfig:
    """Frozen PaulWei execution baseline for historical comparison."""
    return ExecutionKernelConfig(
        min_trade_qty=0.0001,
        max_trade_qty_per_bar=1.0,
        max_slippage_bps=8.0,
        pyramid_initial_pct=0.30,
        pyramid_add_pct=0.30,
        pyramid_short_initial_pct=0.20,
        pyramid_short_add_pct=0.20,
        pyramid_enabled=True,
        dca_on_loss_long=True,
        adaptive_pyramid_enabled=True,
        pyramid_target_vol=0.60,
        pyramid_min_speed=0.10,
        pyramid_max_speed=0.40,
        dca_min_speed=0.05,
        dca_max_speed=0.20,
        gradual_exit_enabled=True,
        exit_unwind_pct=0.30,
        exit_unwind_min_pct=0.20,
        exit_unwind_max_pct=0.50,
        dca_max_adverse_atr=3.0,
        flip_immediate=True,
        flip_min_confidence=0.5,
        entry_fraction=1.0,
        exit_fraction=1.0,
    )


def default_variant_specs() -> list[VariantSpec]:
    return [
        VariantSpec(name="current", label="Current Strategy"),
        VariantSpec(
            name="paulwei_baseline",
            label="PaulWei Execution/Risk Baseline",
            risk_config=paulwei_risk_baseline(),
            execution_config=paulwei_execution_baseline(),
        ),
    ]


def public_archive_variant_specs(*, mapping_path: Path) -> list[VariantSpec]:
    """Return default variants plus the optional PaulWei public-archive baseline."""
    from zenquant.research.paulwei_public import paulwei_public_archive_variant_spec

    variants = list(default_variant_specs())
    payload = paulwei_public_archive_variant_spec(mapping_path=mapping_path)
    if payload is None:
        return variants
    variants.append(
        VariantSpec(
            name=str(payload["name"]),
            label=str(payload["label"]),
            risk_config=payload.get("risk_config"),
            execution_config=payload.get("execution_config"),
            strategy_config=payload.get("strategy_config"),
        )
    )
    return variants


def liquidation_research_variant_specs() -> list[VariantSpec]:
    baseline_risk = paulwei_risk_baseline()
    return [
        VariantSpec(
            name="paulwei_baseline",
            label="PaulWei Baseline (No Liquidation)",
            risk_config=replace(baseline_risk, enable_liq_risk_guard=False),
            execution_config=paulwei_execution_baseline(),
            liquidation_data_enabled=False,
            state_liquidation_overlay_enabled=False,
        ),
        VariantSpec(
            name="execution_plus",
            label="Execution Plus (Liq Risk Guard)",
            risk_config=replace(baseline_risk, enable_liq_risk_guard=True),
            execution_config=paulwei_execution_baseline(),
            liquidation_data_enabled=True,
            state_liquidation_overlay_enabled=False,
        ),
        VariantSpec(
            name="strategy_plus",
            label="Strategy Plus (Liq State Overlay)",
            risk_config=replace(baseline_risk, enable_liq_risk_guard=False),
            execution_config=paulwei_execution_baseline(),
            liquidation_data_enabled=True,
            state_liquidation_overlay_enabled=True,
        ),
        VariantSpec(
            name="full_variant",
            label="Full Variant (Liq State + Risk)",
            risk_config=replace(baseline_risk, enable_liq_risk_guard=True),
            execution_config=paulwei_execution_baseline(),
            liquidation_data_enabled=True,
            state_liquidation_overlay_enabled=True,
        ),
    ]


def compare_variants(
    base_config: HistoricalBacktestConfig,
    *,
    variants: list[VariantSpec] | None = None,
    windows: list[WindowSpec] | None = None,
    walk_forward_windows: list[WindowSpec] | None = None,
    split_spec: DatasetSplitSpec | None = None,
    comparison_split: str = "dev",
    report_mode: str = "full_diagnostics",
    comparison_compute_mode: str = "full",
    window_workers: int = 1,
    validation_workers: int = 1,
    validation_leg_timeout_s: float | None = None,
    validation_variant_budget_s: float | None = None,
    status_callback: Any | None = None,
) -> dict[str, Any]:
    """Run one deterministic comparison bundle and return a JSON-ready payload."""
    variant_specs = variants if variants is not None else default_variant_specs()
    resolved_split_spec = split_spec or default_btc_split_spec()
    comparison_windows = (
        windows if windows is not None else default_comparison_windows(resolved_split_spec, comparison_split)
    )
    wf_windows = (
        walk_forward_windows
        if walk_forward_windows is not None
        else default_walk_forward_windows(split_spec=resolved_split_spec, comparison_split=comparison_split)
    )
    if comparison_compute_mode == "smoke_minimal":
        wf_windows = []

    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "symbol": base_config.symbol,
        "portfolio_symbol": base_config.portfolio_symbol,
        "report_mode": report_mode,
        "comparison_compute_mode": comparison_compute_mode,
        "variants": [asdict(variant) for variant in variant_specs],
        "windows": [asdict(window) for window in comparison_windows],
        "window_reports": [],
        "validation_reports": [],
        "split_scope": "train_dev_only" if comparison_split == "dev" else "frozen_test_acceptance",
    }

    include_diagnostics = report_mode == "full_diagnostics"
    total_window_units = len(variant_specs) * len(comparison_windows)
    total_validation_units = len(variant_specs) * len(wf_windows) * 2
    total_ranking_units = max(1, len(variant_specs))
    total_units = total_window_units + total_validation_units + total_ranking_units
    completed_units = 0
    window_runs_by_variant: dict[str, list[dict[str, Any]]] = {}
    window_order = [
        (variant_index, window_index, variant, window)
        for variant_index, variant in enumerate(variant_specs)
        for window_index, window in enumerate(comparison_windows)
    ]
    window_results: dict[tuple[int, int], dict[str, Any]] = {}
    if window_order and max(1, int(window_workers)) > 1:
        with ProcessPoolExecutor(max_workers=max(1, int(window_workers))) as executor:
            futures = {
                executor.submit(_run_window, base_config, variant, window): (variant_index, window_index, variant, window)
                for variant_index, window_index, variant, window in window_order
            }
            for future in as_completed(futures):
                variant_index, window_index, variant, window = futures[future]
                try:
                    run = future.result()
                except Exception as exc:
                    if variant.name in {"current", "paulwei_baseline"}:
                        raise
                    run = _failed_window_payload(variant, window, exc)
                if not include_diagnostics:
                    run = {
                        "variant": run["variant"],
                        "window_label": run["window_label"],
                        "metrics": run["metrics"],
                        "win_rate": run["win_rate"],
                        **({"comparison_error": run["comparison_error"]} if run.get("comparison_error") else {}),
                    }
                window_results[(variant_index, window_index)] = run
                completed_units += 1
                if status_callback is not None:
                    status_callback(
                        event="comparison_window_completed",
                        variant=variant.name,
                        window_label=window.label,
                        completed_units=completed_units,
                        total_units=total_units,
                        window_units_completed=completed_units,
                        window_units_total=total_window_units,
                        validation_units_completed=max(0, completed_units - total_window_units),
                        validation_units_total=total_validation_units,
                    )
    else:
        for variant_index, window_index, variant, window in window_order:
            run = _run_window(base_config, variant, window)
            if not include_diagnostics:
                run = {
                    "variant": run["variant"],
                    "window_label": run["window_label"],
                    "metrics": run["metrics"],
                    "win_rate": run["win_rate"],
                }
            window_results[(variant_index, window_index)] = run
            completed_units += 1
            if status_callback is not None:
                status_callback(
                    event="comparison_window_completed",
                    variant=variant.name,
                    window_label=window.label,
                    completed_units=completed_units,
                    total_units=total_units,
                    window_units_completed=completed_units,
                    window_units_total=total_window_units,
                    validation_units_completed=max(0, completed_units - total_window_units),
                    validation_units_total=total_validation_units,
                )

    for variant_index, variant in enumerate(variant_specs):
        variant_runs = [
            window_results[(variant_index, window_index)]
            for window_index in range(len(comparison_windows))
            if (variant_index, window_index) in window_results
        ]
        window_runs_by_variant[variant.name] = variant_runs
        report["window_reports"].extend(variant_runs)

    baseline_runs = {run["window_label"]: run for run in window_runs_by_variant.get("paulwei_baseline", [])}
    for run in report["window_reports"]:
        if run["variant"] == "paulwei_baseline":
            run["comparison_to_baseline"] = None
            continue
        baseline_variant = baseline_runs.get(run["window_label"])
        run["comparison_to_baseline"] = _comparison_delta(run, baseline_variant) if baseline_variant else None

    for variant in variant_specs:
        variant_validation_start = completed_units
        if status_callback is not None:
            status_callback(
                event="comparison_validation_started",
                variant=variant.name,
                completed_units=completed_units,
                total_units=total_units,
                window_units_completed=total_window_units,
                window_units_total=total_window_units,
                validation_units_completed=max(0, completed_units - total_window_units),
                validation_units_total=total_validation_units,
            )
        try:
            validation, completed_units = _run_walk_forward_validation(
                base_config,
                variant,
                wf_windows,
                include_windows=include_diagnostics,
                status_callback=status_callback,
                completed_units=completed_units,
                total_units=total_units,
                workers=validation_workers,
                leg_timeout_s=validation_leg_timeout_s,
                variant_budget_s=validation_variant_budget_s,
                total_window_units=total_window_units,
                total_validation_units=total_validation_units,
            )
        except ComparisonValidationTimeoutError as exc:
            if variant.name in {"current", "paulwei_baseline"}:
                raise
            validation_units = len(wf_windows) * 2
            completed_units = max(completed_units, variant_validation_start) + max(
                0,
                validation_units - max(0, exc.completed_units - variant_validation_start),
            )
            validation = _failed_validation_payload(variant, wf_windows, exc)
        report["validation_reports"].append(validation)
        if status_callback is not None:
            status_callback(
                event="comparison_validation_completed",
                variant=variant.name,
                completed_units=completed_units,
                total_units=total_units,
                window_units_completed=total_window_units,
                window_units_total=total_window_units,
                validation_units_completed=max(0, completed_units - total_window_units),
                validation_units_total=total_validation_units,
            )

    baseline_validation = next(
        (item for item in report["validation_reports"] if item["variant"] == "paulwei_baseline"),
        None,
    )
    for validation in report["validation_reports"]:
        validation["comparison_to_baseline"] = (
            None if validation["variant"] == "paulwei_baseline" else _validation_delta(validation, baseline_validation)
        )

    ranked_variants = sorted(
        report["validation_reports"],
        key=lambda item: float(item.get("avg_test_sharpe", 0.0)),
        reverse=True,
    )
    if not ranked_variants:
        ranked_variants = [{"variant": variant.name} for variant in variant_specs]
    for rank, validation in enumerate(ranked_variants, start=1):
        completed_units += 1
        if status_callback is not None:
            status_callback(
                event="comparison_ranking_completed",
                variant=validation["variant"],
                rank=rank,
                completed_units=min(completed_units, total_units),
                total_units=total_units,
            )

    return report


def build_markdown_summary(payload: dict[str, Any]) -> str:
    lines = [
        "# Variant Comparison",
        "",
        f"- created_at: `{payload['created_at']}`",
        f"- symbol: `{payload['symbol']}`",
        "",
        "## Window Reports",
        "",
    ]
    for run in payload["window_reports"]:
        metrics = run["metrics"]
        lines.extend(
            [
                f"### {run['variant']} · {run['window_label']}",
                "",
                f"- sharpe: `{metrics['sharpe']}`",
                f"- net_pnl: `{metrics['net_pnl']}`",
                f"- max_drawdown: `{metrics['max_drawdown']}`",
                f"- profit_factor: `{metrics['profit_factor']}`",
                f"- trade_count: `{metrics['trade_count']}`",
                f"- directional_state_distribution: `{run['directional_state_distribution']}`",
                f"- regime_distribution: `{run['regime_distribution']}`",
                f"- playbook_usage: `{run['playbook_usage']}`",
                f"- win_rate: `{run['win_rate']}`",
                "",
            ]
        )

    lines.extend(["## Walk-Forward", ""])
    for validation in payload["validation_reports"]:
        lines.extend(
            [
                f"### {validation['variant']}",
                "",
                f"- avg_train_sharpe: `{validation['avg_train_sharpe']}`",
                f"- avg_test_sharpe: `{validation['avg_test_sharpe']}`",
                f"- avg_oos_ratio: `{validation['avg_oos_ratio']}`",
                f"- worst_test_sharpe: `{validation['worst_test_sharpe']}`",
                "",
            ]
        )
    return "\n".join(lines)


def fixed_2023_08_window() -> WindowSpec:
    return WindowSpec(
        label="2023-08",
        start_ns=_ts(2023, 7, 5, 16),
        end_ns=_ts(2023, 8, 23, 20),
    )


def windows_from_worst_periods(periods_path: Path = Path("tests/adversarial/worst_periods.json")) -> list[WindowSpec]:
    if not periods_path.exists():
        return []
    payload = json.loads(periods_path.read_text(encoding="utf-8"))
    return [
        WindowSpec(label=item["description"], start_ns=int(item["start_ns"]), end_ns=int(item["end_ns"]))
        for item in payload.get("worst_periods", [])
    ]


def split_window_spec(split_spec: DatasetSplitSpec, split_name: str) -> WindowSpec:
    mapping = {
        "train": split_spec.train,
        "dev": split_spec.dev,
        "test": split_spec.test,
    }
    window = mapping[split_name]
    return WindowSpec(label=window.name, start_ns=window.start_ns, end_ns=window.end_ns)


def default_comparison_windows(
    split_spec: DatasetSplitSpec | None = None,
    comparison_split: str = "dev",
) -> list[WindowSpec]:
    spec = split_spec or default_btc_split_spec()
    if comparison_split == "test":
        return [split_window_spec(spec, "test")]
    return [split_window_spec(spec, "dev"), fixed_2023_08_window(), *filtered_worst_period_windows(spec, comparison_split)]


def default_walk_forward_windows(
    *,
    split_spec: DatasetSplitSpec | None = None,
    comparison_split: str = "dev",
) -> list[WindowSpec]:
    spec = split_spec or default_btc_split_spec()
    if comparison_split != "dev" or spec.dev.end_ns is None:
        return []
    windows: list[WindowSpec] = []
    train_months = 24
    test_months = 6
    current_start_month = 0
    while True:
        test_start = current_start_month + train_months
        test_end = test_start + test_months
        start_ns = _month_ts(2020, test_start)
        end_ns = _month_ts(2020, test_end)
        if start_ns < spec.dev.start_ns:
            current_start_month += test_months
            continue
        if end_ns > spec.dev.end_ns:
            break
        windows.append(WindowSpec(label=f"wf_{len(windows) + 1}", start_ns=start_ns, end_ns=end_ns))
        current_start_month += test_months
    return windows


def filtered_worst_period_windows(
    split_spec: DatasetSplitSpec | None = None,
    comparison_split: str = "dev",
) -> list[WindowSpec]:
    spec = split_spec or default_btc_split_spec()
    split_window: SplitWindow = spec.dev if comparison_split == "dev" else spec.test
    windows = windows_from_worst_periods()
    return [
        window
        for window in windows
        if window.start_ns is not None
        and window.end_ns is not None
        and period_within_split(window.start_ns, window.end_ns, split_window)
    ]


def _run_window(base_config: HistoricalBacktestConfig, variant: VariantSpec, window: WindowSpec) -> dict[str, Any]:
    runner, result = _run_variant_backtest(base_config, variant, start_ns=window.start_ns, end_ns=window.end_ns)
    payload = {
        "variant": variant.name,
        "window_label": window.label,
        "metrics": asdict(result.metrics),
        "win_rate": result.metrics.win_rate,
    }
    states = [state for state in runner._market_states if state.timeframe == base_config.execution_timeframe]
    playbooks = [playbook for playbook in runner._playbooks if playbook.timeframe == base_config.execution_timeframe]
    payload["diagnostics"] = asdict(result.diagnostics)
    payload["cycle_context"] = asdict(result.cycle_context)
    payload["directional_state_distribution"] = _normalized_counter(state.directional_state for state in states)
    payload["regime_distribution"] = _normalized_counter(state.regime for state in states)
    payload["playbook_usage"] = _normalized_counter(playbook.playbook_id for playbook in playbooks)
    del runner
    return payload


def _failed_window_payload(variant: VariantSpec, window: WindowSpec, exc: Exception) -> dict[str, Any]:
    return {
        "variant": variant.name,
        "window_label": window.label,
        "metrics": {
            "sharpe": -10.0,
            "net_pnl": 0.0,
            "max_drawdown": 1.0,
            "profit_factor": 0.0,
            "trade_count": 0,
        },
        "win_rate": 0.0,
        "diagnostics": {},
        "cycle_context": {},
        "directional_state_distribution": {},
        "regime_distribution": {},
        "playbook_usage": {},
        "comparison_error": f"{type(exc).__name__}:{exc}",
    }


def _run_walk_forward_validation(
    base_config: HistoricalBacktestConfig,
    variant: VariantSpec,
    windows: list[WindowSpec],
    *,
    include_windows: bool = True,
    status_callback: Any | None = None,
    completed_units: int = 0,
    total_units: int = 0,
    workers: int = 1,
    leg_timeout_s: float | None = None,
    variant_budget_s: float | None = None,
    total_window_units: int = 0,
    total_validation_units: int = 0,
) -> tuple[dict[str, Any], int]:
    if not windows:
        return (
            {
                "variant": variant.name,
                "windows": [],
                "avg_train_sharpe": 0.0,
                "avg_test_sharpe": 0.0,
                "avg_oos_ratio": 0.0,
                "worst_test_sharpe": 0.0,
            },
            completed_units,
        )

    results_map: dict[str, dict[str, Any]] = {window.label: {"window_label": window.label} for window in windows}
    train_months = 24
    pending: dict[Any, tuple[_ValidationTask, float]] = {}
    remaining_windows = list(windows)
    variant_started_at = time.time()

    if max(1, int(workers)) <= 1:
        for window in windows:
            assert window.start_ns is not None and window.end_ns is not None
            train_end = window.start_ns
            train_start = _shift_months(train_end, -train_months)
            if status_callback is not None:
                status_callback(
                    event="comparison_validation_window_train_started",
                    variant=variant.name,
                    window_label=window.label,
                    unit=f"{window.label}:train",
                    completed_units=completed_units,
                    total_units=total_units,
                    window_units_completed=total_window_units,
                    window_units_total=total_window_units,
                    validation_units_completed=max(0, completed_units - total_window_units),
                    validation_units_total=total_validation_units,
                )
            train_leg = _run_validation_leg(
                base_config,
                variant,
                _ValidationTask(window=window, leg="train", start_ns=train_start, end_ns=train_end),
            )
            results_map[window.label]["train_sharpe"] = train_leg["sharpe"]
            completed_units += 1
            if status_callback is not None:
                status_callback(
                    event="comparison_validation_window_train_completed",
                    variant=variant.name,
                    window_label=window.label,
                    unit=f"{window.label}:train",
                    completed_units=completed_units,
                    total_units=total_units,
                    window_units_completed=total_window_units,
                    window_units_total=total_window_units,
                    validation_units_completed=max(0, completed_units - total_window_units),
                    validation_units_total=total_validation_units,
                )
                status_callback(
                    event="comparison_validation_window_test_started",
                    variant=variant.name,
                    window_label=window.label,
                    unit=f"{window.label}:test",
                    completed_units=completed_units,
                    total_units=total_units,
                    window_units_completed=total_window_units,
                    window_units_total=total_window_units,
                    validation_units_completed=max(0, completed_units - total_window_units),
                    validation_units_total=total_validation_units,
                )
            test_leg = _run_validation_leg(
                base_config,
                variant,
                _ValidationTask(window=window, leg="test", start_ns=window.start_ns, end_ns=window.end_ns),
            )
            results_map[window.label]["test_sharpe"] = test_leg["sharpe"]
            results_map[window.label]["test_pnl"] = test_leg["net_pnl"]
            results_map[window.label]["test_drawdown"] = test_leg["max_drawdown"]
            train_sharpe = float(results_map[window.label].get("train_sharpe", 0.0) or 0.0)
            test_sharpe = float(results_map[window.label]["test_sharpe"])
            ratio = test_sharpe / train_sharpe if abs(train_sharpe) > 0.01 else 0.0
            results_map[window.label]["oos_ratio"] = round(ratio, 4)
            completed_units += 1
            if status_callback is not None:
                status_callback(
                    event="comparison_validation_window_test_completed",
                    variant=variant.name,
                    window_label=window.label,
                    unit=f"{window.label}:test",
                    completed_units=completed_units,
                    total_units=total_units,
                    window_units_completed=total_window_units,
                    window_units_total=total_window_units,
                    validation_units_completed=max(0, completed_units - total_window_units),
                    validation_units_total=total_validation_units,
                )
        results = [results_map[window.label] for window in windows]
        payload = {
            "variant": variant.name,
            "avg_train_sharpe": round(sum(item["train_sharpe"] for item in results) / len(results), 4),
            "avg_test_sharpe": round(sum(item["test_sharpe"] for item in results) / len(results), 4),
            "avg_oos_ratio": round(sum(item["oos_ratio"] for item in results) / len(results), 4),
            "worst_test_sharpe": round(min(item["test_sharpe"] for item in results), 4),
        }
        payload["windows"] = results if include_windows else []
        return payload, completed_units

    def _submit_train(executor: ProcessPoolExecutor, window: WindowSpec) -> None:
        assert window.start_ns is not None and window.end_ns is not None
        train_end = window.start_ns
        task = _ValidationTask(
            window=window,
            leg="train",
            start_ns=_shift_months(train_end, -train_months),
            end_ns=train_end,
        )
        if status_callback is not None:
            status_callback(
                event="comparison_validation_window_train_started",
                variant=variant.name,
                window_label=window.label,
                unit=f"{window.label}:train",
                completed_units=completed_units,
                total_units=total_units,
                window_units_completed=total_window_units,
                window_units_total=total_window_units,
                validation_units_completed=max(0, completed_units - total_window_units),
                validation_units_total=total_validation_units,
            )
        pending[executor.submit(_run_validation_leg, base_config, variant, task)] = (task, time.time())

    def _submit_test(executor: ProcessPoolExecutor, window: WindowSpec) -> None:
        assert window.start_ns is not None and window.end_ns is not None
        task = _ValidationTask(
            window=window,
            leg="test",
            start_ns=window.start_ns,
            end_ns=window.end_ns,
        )
        if status_callback is not None:
            status_callback(
                event="comparison_validation_window_test_started",
                variant=variant.name,
                window_label=window.label,
                unit=f"{window.label}:test",
                completed_units=completed_units,
                total_units=total_units,
                window_units_completed=total_window_units,
                window_units_total=total_window_units,
                validation_units_completed=max(0, completed_units - total_window_units),
                validation_units_total=total_validation_units,
            )
        pending[executor.submit(_run_validation_leg, base_config, variant, task)] = (task, time.time())

    def _raise_timeout(task: _ValidationTask) -> None:
        raise ComparisonValidationTimeoutError(
            variant=variant.name,
            window_label=task.window.label,
            leg=task.leg,
            completed_units=completed_units,
            message=(
                f"comparison_validation_timeout: variant={variant.name} "
                f"wf={task.window.label} leg={task.leg}"
            ),
        )

    max_workers = max(1, int(workers))
    executor = ProcessPoolExecutor(max_workers=max_workers)
    shutdown_wait = True
    try:
        while remaining_windows and len(pending) < max_workers:
            _submit_train(executor, remaining_windows.pop(0))

        while pending:
            now = time.time()
            if variant_budget_s is not None and (now - variant_started_at) > variant_budget_s:
                first_task = next(iter(pending.values()))[0]
                shutdown_wait = False
                executor.shutdown(wait=False, cancel_futures=True)
                raise ComparisonValidationTimeoutError(
                    variant=variant.name,
                    window_label=first_task.window.label,
                    leg=first_task.leg,
                    completed_units=completed_units,
                    message=(
                        f"comparison_validation_timeout: variant={variant.name} "
                        f"wf={first_task.window.label} leg={first_task.leg} budget_s={variant_budget_s:.0f}"
                    ),
                )
            if leg_timeout_s is not None:
                for task, started_at in pending.values():
                    if (now - started_at) > leg_timeout_s:
                        shutdown_wait = False
                        executor.shutdown(wait=False, cancel_futures=True)
                        _raise_timeout(task)

            done, _ = wait(list(pending.keys()), timeout=1.0, return_when=FIRST_COMPLETED)
            if not done:
                continue

            for future in done:
                task, _started = pending.pop(future)
                try:
                    leg_payload = future.result()
                except Exception as exc:
                    shutdown_wait = False
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise ComparisonValidationTimeoutError(
                        variant=variant.name,
                        window_label=task.window.label,
                        leg=task.leg,
                        completed_units=completed_units,
                        message=(
                            f"comparison_validation_error: variant={variant.name} "
                            f"wf={task.window.label} leg={task.leg} cause={type(exc).__name__}:{exc}"
                        ),
                    ) from exc

                window_payload = results_map[task.window.label]
                if task.leg == "train":
                    window_payload["train_sharpe"] = leg_payload["sharpe"]
                    completed_units += 1
                    if status_callback is not None:
                        status_callback(
                            event="comparison_validation_window_train_completed",
                            variant=variant.name,
                            window_label=task.window.label,
                            unit=f"{task.window.label}:train",
                            completed_units=completed_units,
                            total_units=total_units,
                            window_units_completed=total_window_units,
                            window_units_total=total_window_units,
                            validation_units_completed=max(0, completed_units - total_window_units),
                            validation_units_total=total_validation_units,
                        )
                    _submit_test(executor, task.window)
                    continue

                window_payload["test_sharpe"] = leg_payload["sharpe"]
                window_payload["test_pnl"] = leg_payload["net_pnl"]
                window_payload["test_drawdown"] = leg_payload["max_drawdown"]
                train_sharpe = float(window_payload.get("train_sharpe", 0.0) or 0.0)
                test_sharpe = float(window_payload["test_sharpe"])
                ratio = test_sharpe / train_sharpe if abs(train_sharpe) > 0.01 else 0.0
                window_payload["oos_ratio"] = round(ratio, 4)
                completed_units += 1
                if status_callback is not None:
                        status_callback(
                            event="comparison_validation_window_test_completed",
                            variant=variant.name,
                            window_label=task.window.label,
                            unit=f"{task.window.label}:test",
                            completed_units=completed_units,
                            total_units=total_units,
                            window_units_completed=total_window_units,
                            window_units_total=total_window_units,
                            validation_units_completed=max(0, completed_units - total_window_units),
                            validation_units_total=total_validation_units,
                        )
                if remaining_windows:
                    _submit_train(executor, remaining_windows.pop(0))
    finally:
        executor.shutdown(wait=shutdown_wait, cancel_futures=not shutdown_wait)

    results = [results_map[window.label] for window in windows]
    payload = {
        "variant": variant.name,
        "avg_train_sharpe": round(sum(item["train_sharpe"] for item in results) / len(results), 4),
        "avg_test_sharpe": round(sum(item["test_sharpe"] for item in results) / len(results), 4),
        "avg_oos_ratio": round(sum(item["oos_ratio"] for item in results) / len(results), 4),
        "worst_test_sharpe": round(min(item["test_sharpe"] for item in results), 4),
    }
    payload["windows"] = results if include_windows else []
    return payload, completed_units


def _run_variant_backtest(
    base_config: HistoricalBacktestConfig,
    variant: VariantSpec,
    *,
    start_ns: int | None,
    end_ns: int | None,
) -> tuple[HistoricalBacktest, Any]:
    config = replace(
        base_config,
        start_ns=start_ns,
        end_ns=end_ns,
        risk_config=variant.risk_config or base_config.risk_config,
        execution_config=variant.execution_config or base_config.execution_config,
        strategy_config=variant.strategy_config or base_config.strategy_config,
        indicator_config=variant.indicator_config or base_config.indicator_config,
        signal_weight_overrides=variant.signal_weight_overrides or base_config.signal_weight_overrides,
        liquidation_data_enabled=(
            base_config.liquidation_data_enabled
            if variant.liquidation_data_enabled is None
            else variant.liquidation_data_enabled
        ),
        state_liquidation_overlay_enabled=(
            base_config.state_liquidation_overlay_enabled
            if variant.state_liquidation_overlay_enabled is None
            else variant.state_liquidation_overlay_enabled
        ),
    )
    replay_context = _COMPARISON_REPLAY_CONTEXT_CACHE.get(_comparison_context_key(config))
    if replay_context is None:
        replay_context = _build_replay_context(config)
        _COMPARISON_REPLAY_CONTEXT_CACHE[_comparison_context_key(config)] = replay_context
    runner = HistoricalBacktest(replace(config, replay_context=replay_context))
    return runner, runner.run()


def _run_validation_leg(
    base_config: HistoricalBacktestConfig,
    variant: VariantSpec,
    task: _ValidationTask,
) -> dict[str, float]:
    _runner, result = _run_variant_backtest(
        base_config,
        variant,
        start_ns=task.start_ns,
        end_ns=task.end_ns,
    )
    metrics = result.metrics
    return {
        "sharpe": float(metrics.sharpe),
        "net_pnl": float(metrics.net_pnl),
        "max_drawdown": float(metrics.max_drawdown),
    }


def _failed_validation_payload(
    variant: VariantSpec,
    windows: list[WindowSpec],
    exc: Exception,
) -> dict[str, Any]:
    window_reports = [
        {
            "window_label": window.label,
            "train_sharpe": -10.0,
            "test_sharpe": -10.0,
            "test_pnl": 0.0,
            "test_drawdown": 1.0,
            "oos_ratio": 0.0,
        }
        for window in windows
    ]
    return {
        "variant": variant.name,
        "windows": window_reports,
        "avg_train_sharpe": -10.0,
        "avg_test_sharpe": -10.0,
        "avg_oos_ratio": 0.0,
        "worst_test_sharpe": -10.0,
        "comparison_error": f"{type(exc).__name__}:{exc}",
    }


def _comparison_context_key(config: HistoricalBacktestConfig) -> tuple[Any, ...]:
    return (
        str(config.root_dir),
        config.symbol,
        config.execution_timeframe.value,
        config.bias_timeframe.value,
        config.direction_timeframe.value,
        config.context_timeframe.value if config.context_timeframe else None,
        config.start_ns,
        config.end_ns,
        config.liquidation_data_enabled,
    )


def _normalized_counter(values: Any) -> dict[str, float]:
    counts = Counter(str(value) for value in values)
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {key: round(value / total, 4) for key, value in sorted(counts.items())}


def _comparison_delta(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    metrics_keys = ("sharpe", "net_pnl", "max_drawdown", "profit_factor", "trade_count")
    payload = {
        "metrics_delta": {
            key: round(float(current["metrics"][key]) - float(baseline["metrics"][key]), 6) for key in metrics_keys
        },
    }
    if "diagnostics" in current and "diagnostics" in baseline:
        payload["diagnostics_diff"] = {
            "playbook_counts": _counter_diff(
                current["diagnostics"]["playbook_counts"],
                baseline["diagnostics"]["playbook_counts"],
            ),
            "risk_flag_counts": _counter_diff(
                current["diagnostics"]["risk_flag_counts"],
                baseline["diagnostics"]["risk_flag_counts"],
            ),
            "actionable_exit_counts": _counter_diff(
                current["diagnostics"]["actionable_exit_counts"],
                baseline["diagnostics"]["actionable_exit_counts"],
            ),
        }
    return payload


def _validation_delta(current: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any] | None:
    if baseline is None:
        return None
    keys = ("avg_train_sharpe", "avg_test_sharpe", "avg_oos_ratio", "worst_test_sharpe")
    return {key: round(float(current[key]) - float(baseline[key]), 6) for key in keys}


def _counter_diff(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, int]:
    keys = set(current) | set(baseline)
    return {key: int(current.get(key, 0)) - int(baseline.get(key, 0)) for key in sorted(keys)}


def _month_ts(start_year: int, months_offset: int) -> int:
    year = start_year + months_offset // 12
    month = months_offset % 12 + 1
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1_000_000_000)


def _shift_months(timestamp_ns: int, months: int) -> int:
    dt = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC)
    total_months = dt.year * 12 + (dt.month - 1) + months
    year = total_months // 12
    month = total_months % 12 + 1
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1_000_000_000)


def _ts(year: int, month: int, day: int, hour: int) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp() * 1_000_000_000)
