"""Variant comparison helpers for historical replay research."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.execution import ExecutionKernelConfig
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.risk import RiskOverlayConfig


@dataclass(slots=True, frozen=True)
class VariantSpec:
    """One replay variant in a comparison run."""

    name: str
    label: str
    risk_config: RiskOverlayConfig | None = None
    execution_config: ExecutionKernelConfig | None = None
    liquidation_data_enabled: bool | None = None
    state_liquidation_overlay_enabled: bool | None = None


@dataclass(slots=True, frozen=True)
class WindowSpec:
    """One replay window inside a comparison run."""

    label: str
    start_ns: int | None = None
    end_ns: int | None = None


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
) -> dict[str, Any]:
    """Run one deterministic comparison bundle and return a JSON-ready payload."""
    variant_specs = variants if variants is not None else default_variant_specs()
    comparison_windows = windows if windows is not None else [WindowSpec(label="overall"), fixed_2023_08_window()]
    wf_windows = walk_forward_windows if walk_forward_windows is not None else default_walk_forward_windows()

    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "symbol": base_config.symbol,
        "portfolio_symbol": base_config.portfolio_symbol,
        "variants": [asdict(variant) for variant in variant_specs],
        "windows": [asdict(window) for window in comparison_windows],
        "window_reports": [],
        "validation_reports": [],
    }

    window_runs_by_variant: dict[str, list[dict[str, Any]]] = {}
    for variant in variant_specs:
        variant_runs: list[dict[str, Any]] = []
        for window in comparison_windows:
            variant_runs.append(_run_window(base_config, variant, window))
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
        validation = _run_walk_forward_validation(base_config, variant, wf_windows)
        report["validation_reports"].append(validation)

    baseline_validation = next(
        (item for item in report["validation_reports"] if item["variant"] == "paulwei_baseline"),
        None,
    )
    for validation in report["validation_reports"]:
        validation["comparison_to_baseline"] = (
            None
            if validation["variant"] == "paulwei_baseline"
            else _validation_delta(validation, baseline_validation)
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


def default_walk_forward_windows() -> list[WindowSpec]:
    windows: list[WindowSpec] = []
    start_year = 2020
    end_year = 2026
    train_months = 24
    test_months = 6
    current_start_month = 0
    while True:
        train_start = current_start_month
        train_end = train_start + train_months
        test_start = train_end
        test_end = test_start + test_months
        ts_year = start_year + test_end // 12
        ts_month = test_end % 12 + 1
        if ts_year > end_year or (ts_year == end_year and ts_month > 6):
            break
        windows.append(
            WindowSpec(
                label=f"wf_{len(windows) + 1}",
                start_ns=_month_ts(start_year, test_start),
                end_ns=_month_ts(start_year, test_end),
            )
        )
        current_start_month += test_months
    return windows


def _run_window(base_config: HistoricalBacktestConfig, variant: VariantSpec, window: WindowSpec) -> dict[str, Any]:
    runner, result = _run_variant_backtest(base_config, variant, start_ns=window.start_ns, end_ns=window.end_ns)
    states = [
        state for state in runner._market_states if state.timeframe == base_config.execution_timeframe
    ]
    playbooks = [
        playbook for playbook in runner._playbooks if playbook.timeframe == base_config.execution_timeframe
    ]
    return {
        "variant": variant.name,
        "window_label": window.label,
        "metrics": asdict(result.metrics),
        "diagnostics": asdict(result.diagnostics),
        "cycle_context": asdict(result.cycle_context),
        "directional_state_distribution": _normalized_counter(state.directional_state for state in states),
        "regime_distribution": _normalized_counter(state.regime for state in states),
        "playbook_usage": _normalized_counter(playbook.playbook_id for playbook in playbooks),
        "win_rate": result.metrics.win_rate,
    }


def _run_walk_forward_validation(
    base_config: HistoricalBacktestConfig,
    variant: VariantSpec,
    windows: list[WindowSpec],
) -> dict[str, Any]:
    if not windows:
        return {
            "variant": variant.name,
            "windows": [],
            "avg_train_sharpe": 0.0,
            "avg_test_sharpe": 0.0,
            "avg_oos_ratio": 0.0,
            "worst_test_sharpe": 0.0,
        }

    results: list[dict[str, Any]] = []
    train_months = 24
    for window in windows:
        assert window.start_ns is not None and window.end_ns is not None
        train_end = window.start_ns
        train_start = _shift_months(train_end, -train_months)
        _, train_result = _run_variant_backtest(base_config, variant, start_ns=train_start, end_ns=train_end)
        _, test_result = _run_variant_backtest(base_config, variant, start_ns=window.start_ns, end_ns=window.end_ns)
        train_sharpe = train_result.metrics.sharpe
        test_sharpe = test_result.metrics.sharpe
        ratio = test_sharpe / train_sharpe if abs(train_sharpe) > 0.01 else 0.0
        results.append(
            {
                "window_label": window.label,
                "train_sharpe": train_sharpe,
                "test_sharpe": test_sharpe,
                "test_pnl": test_result.metrics.net_pnl,
                "test_drawdown": test_result.metrics.max_drawdown,
                "oos_ratio": round(ratio, 4),
            }
        )
    return {
        "variant": variant.name,
        "windows": results,
        "avg_train_sharpe": round(sum(item["train_sharpe"] for item in results) / len(results), 4),
        "avg_test_sharpe": round(sum(item["test_sharpe"] for item in results) / len(results), 4),
        "avg_oos_ratio": round(sum(item["oos_ratio"] for item in results) / len(results), 4),
        "worst_test_sharpe": round(min(item["test_sharpe"] for item in results), 4),
    }


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
    runner = HistoricalBacktest(config)
    return runner, runner.run()


def _normalized_counter(values: Any) -> dict[str, float]:
    counts = Counter(str(value) for value in values)
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {key: round(value / total, 4) for key, value in sorted(counts.items())}


def _comparison_delta(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    metrics_keys = ("sharpe", "net_pnl", "max_drawdown", "profit_factor", "trade_count")
    return {
        "metrics_delta": {
            key: round(float(current["metrics"][key]) - float(baseline["metrics"][key]), 6)
            for key in metrics_keys
        },
        "diagnostics_diff": {
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
        },
    }


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
