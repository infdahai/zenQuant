#!/usr/bin/env python3
"""Compare the exact Gate C fixture across two source revisions without mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import runpy
import subprocess
import sys
import tempfile
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _semantic(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _semantic(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _semantic(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_semantic(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite semantic value: {value!r}")
    return value


def _accounting_state(backtest: Any) -> dict[str, Any]:
    overlay = backtest.risk_overlay
    snapshots = backtest._risk_snapshots
    return {
        "cash": float(overlay._cash),
        "equity": float(overlay._equity),
        "high_water_mark": float(overlay._high_water_mark),
        "positions": {key: float(value) for key, value in sorted(overlay._actual_positions.items())},
        "marks": {key: float(value) for key, value in sorted(overlay._last_price.items())},
        "latest_risk_snapshot": _semantic(snapshots[-1]) if snapshots else None,
    }


def _fixture_payload(namespace: dict[str, Any]) -> tuple[dict[str, list[Any]], dict[str, Any]]:
    rows = {
        "daily": namespace["_daily_rows"](),
        "four_hour": namespace["_four_hour_rows"](),
        "funding": namespace["_funding_rows"](),
        "open_interest": namespace["_open_interest_rows"](),
    }
    config = {
        "symbol": "BTCUSDT",
        "portfolio_symbol": "BTCUSDT_PERP",
        "starting_equity": 100_000.0,
        "execution_timeframe": "4h",
        "bias_timeframe": "1d",
        "direction_timeframe": "4h",
        "profile_path": None,
        "macro_dir": "<fixture_root>/macro",
        "options_dir": "<fixture_root>/deribit_options",
        "cot_path": "<fixture_root>/cftc/cot_btc.parquet",
        "liquidation_dir": "<fixture_root>/liquidations/aggregated",
    }
    canonical_rows = {key: _semantic(value) for key, value in rows.items()}
    identity_payload = {"config": config, "rows": canonical_rows}
    return rows, {
        "input_sha256": _sha256_bytes(_canonical_json(identity_payload).encode("utf-8")),
        "config": config,
        "row_counts": {key: len(value) for key, value in rows.items()},
        "row_sha256": {
            key: _sha256_bytes(_canonical_json(canonical_rows[key]).encode("utf-8")) for key in sorted(canonical_rows)
        },
    }


def _source_revision(source_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_worker(source_root: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    sys.path.insert(0, str(source_root))
    fixture_path = source_root / "tests" / "test_golden_output.py"
    fixture_bytes = fixture_path.read_bytes()
    namespace = runpy.run_path(str(fixture_path), run_name="golden_fixture_probe")
    rows, fixture = _fixture_payload(namespace)
    fixture["test_source_sha256"] = hashlib.sha256(fixture_bytes).hexdigest()

    from zenquant.historical import (
        HistoricalBacktest,
        HistoricalBacktestConfig,
        HistoricalDatasetLayout,
        write_parquet_rows,
    )

    fees: list[dict[str, Any]] = []
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        layout = HistoricalDatasetLayout(root_dir=root)
        write_parquet_rows(layout.kline_path("BTCUSDT", "1d", "2024-01"), rows["daily"])
        write_parquet_rows(layout.kline_path("BTCUSDT", "4h", "2024-01"), rows["four_hour"])
        write_parquet_rows(layout.funding_path("BTCUSDT", "2024-01"), rows["funding"])
        write_parquet_rows(
            layout.open_interest_path("BTCUSDT", "4h", "2024-01"),
            rows["open_interest"],
        )
        try:
            os.chdir(root)
            backtest = HistoricalBacktest(
                HistoricalBacktestConfig(
                    root_dir=root,
                    symbol="BTCUSDT",
                    macro_dir=root / "macro",
                    options_dir=root / "deribit_options",
                    cot_path=root / "cftc" / "cot_btc.parquet",
                    liquidation_dir=root / "liquidations" / "aggregated",
                    profile_path=None,
                )
            )

            def record_fee_transition(report: Any, before: dict[str, Any], after: dict[str, Any]) -> None:
                semantic_report = _semantic(report)
                fee = float(report.fee)
                fees.append(
                    {
                        "index": len(fees),
                        "report": semantic_report,
                        "fee": fee,
                        "clamp_condition": before["cash"] - fee < 0.0,
                        "expected_signed_cash_after": before["cash"] - fee,
                        "before": before,
                        "after": after,
                    }
                )

            original_apply_execution_report = getattr(backtest.risk_overlay, "apply_execution_report", None)
            if original_apply_execution_report is not None:

                def traced_apply_execution_report(report: Any) -> bool:
                    atomic_before = _accounting_state(backtest)
                    applied = original_apply_execution_report(report)
                    if applied is not False:
                        after = _accounting_state(backtest)
                        fee = float(report.fee)
                        equity_before_fee = after["equity"] + fee
                        before = {
                            **after,
                            "cash": after["cash"] + fee,
                            "equity": equity_before_fee,
                            "high_water_mark": max(atomic_before["high_water_mark"], equity_before_fee),
                        }
                        record_fee_transition(report, before, after)
                    return bool(applied)

                backtest.risk_overlay.apply_execution_report = traced_apply_execution_report
            else:
                original_apply_fee = backtest.risk_overlay.apply_fee

                def traced_apply_fee(fee: float) -> None:
                    before = _accounting_state(backtest)
                    report = backtest._reports[-1] if backtest._reports else None
                    original_apply_fee(fee)
                    if report is not None:
                        record_fee_transition(report, before, _accounting_state(backtest))

                backtest.risk_overlay.apply_fee = traced_apply_fee
            result = backtest.run()
            fixture["cwd_isolated"] = Path.cwd().resolve() == root.resolve()
        finally:
            os.chdir(original_cwd)

    return {
        "schema_version": "golden-accounting-source-trace.v1",
        "source_root": str(source_root),
        "source_revision": _source_revision(source_root),
        "fixture": fixture,
        "fees": fees,
        "risk_snapshots": _semantic(backtest._risk_snapshots),
        "targets": _semantic(backtest._targets),
        "reports": _semantic(backtest._reports),
        "exits": _semantic(backtest._exits),
        "semantic_result": {
            "metrics": _semantic(result.metrics),
            "diagnostics": _semantic(result.diagnostics),
            "total_indicator_packs": result.total_indicator_packs,
            "total_targets": result.total_targets,
            "total_reports": result.total_reports,
            "final_equity": result.final_equity,
            "cycle_context": _semantic(result.cycle_context),
            "terminated_early": result.terminated_early,
            "termination_reason": result.termination_reason,
            "processed_event_ratio": result.processed_event_ratio,
            "position_episodes": _semantic(result.position_episodes),
            "decision_snapshots": _semantic(result.decision_snapshots),
            "options_state_sidecar": _semantic(result.options_state_sidecar),
            "options_state_links": _semantic(result.options_state_links),
        },
    }


def run_source_probe(source_root: Path) -> dict[str, Any]:
    """Run one source tree in an isolated interpreter and return its trace."""
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--source-root",
        str(source_root.resolve()),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _first_divergence(baseline: list[Any], repaired: list[Any]) -> dict[str, Any] | None:
    for index, (baseline_item, repaired_item) in enumerate(zip(baseline, repaired, strict=False)):
        if baseline_item != repaired_item:
            return {"index": index, "baseline": baseline_item, "repaired": repaired_item}
    if len(baseline) == len(repaired):
        return None
    index = min(len(baseline), len(repaired))
    return {
        "index": index,
        "baseline": baseline[index] if index < len(baseline) else None,
        "repaired": repaired[index] if index < len(repaired) else None,
    }


def _fee_clamp_is_first_divergence(
    baseline: dict[str, Any],
    repaired: dict[str, Any],
    fee_divergence: dict[str, Any] | None,
) -> bool:
    if baseline["fixture"] != repaired["fixture"] or fee_divergence is None:
        return False
    legacy_fee = fee_divergence["baseline"]
    repaired_fee = fee_divergence["repaired"]
    if legacy_fee is None or repaired_fee is None:
        return False
    if legacy_fee["report"] != repaired_fee["report"] or legacy_fee["fee"] != repaired_fee["fee"]:
        return False
    if legacy_fee["before"] != repaired_fee["before"]:
        return False
    before_cash = float(legacy_fee["before"]["cash"])
    fee = float(legacy_fee["fee"])
    legacy_cash_after = float(legacy_fee["after"]["cash"])
    repaired_cash_after = float(repaired_fee["after"]["cash"])
    return (
        before_cash - fee < 0.0
        and math.isclose(legacy_cash_after, max(0.0, before_cash - fee), rel_tol=0.0, abs_tol=1e-9)
        and math.isclose(repaired_cash_after, before_cash - fee, rel_tol=0.0, abs_tol=1e-9)
        and not math.isclose(legacy_cash_after, repaired_cash_after, rel_tol=0.0, abs_tol=1e-9)
    )


def _divergence_time_ns(divergence: dict[str, Any] | None) -> int | None:
    if divergence is None:
        return None
    for side in ("baseline", "repaired"):
        item = divergence.get(side)
        if not isinstance(item, dict):
            continue
        for field in ("transaction_time_ns", "cutoff_time_ns", "ts_ns"):
            value = item.get(field)
            if isinstance(value, int):
                return value
    return None


def _accounting_mark_state_is_first_divergence(
    baseline: dict[str, Any],
    repaired: dict[str, Any],
    fee_divergence: dict[str, Any] | None,
    downstream_divergences: tuple[dict[str, Any] | None, ...],
) -> bool:
    if baseline["fixture"] != repaired["fixture"] or fee_divergence is None:
        return False
    legacy_fee = fee_divergence["baseline"]
    repaired_fee = fee_divergence["repaired"]
    if legacy_fee is None or repaired_fee is None:
        return False
    if legacy_fee["report"] != repaired_fee["report"] or legacy_fee["fee"] != repaired_fee["fee"]:
        return False
    if legacy_fee.get("clamp_condition", False) or repaired_fee.get("clamp_condition", False):
        return False

    for phase in ("before", "after"):
        legacy_state = legacy_fee[phase]
        repaired_state = repaired_fee[phase]
        for stable_field in ("cash", "high_water_mark", "positions", "latest_risk_snapshot"):
            if legacy_state.get(stable_field) != repaired_state.get(stable_field):
                return False
        if legacy_state.get("marks") == repaired_state.get("marks"):
            return False
        if legacy_state.get("equity") == repaired_state.get("equity"):
            return False

    fee_time_ns = legacy_fee["report"].get("transaction_time_ns")
    if not isinstance(fee_time_ns, int):
        return False
    return all(
        divergence_time is None or divergence_time >= fee_time_ns
        for divergence_time in map(_divergence_time_ns, downstream_divergences)
    )


def compare_source_traces(baseline: dict[str, Any], repaired: dict[str, Any]) -> dict[str, Any]:
    """Return a compact first-divergence comparison for two source traces."""
    fee_divergence = _first_divergence(baseline["fees"], repaired["fees"])
    risk_divergence = _first_divergence(baseline["risk_snapshots"], repaired["risk_snapshots"])
    target_divergence = _first_divergence(baseline["targets"], repaired["targets"])
    report_divergence = _first_divergence(baseline["reports"], repaired["reports"])
    exit_divergence = _first_divergence(baseline["exits"], repaired["exits"])
    fee_clamp_first = _fee_clamp_is_first_divergence(baseline, repaired, fee_divergence)
    accounting_mark_first = _accounting_mark_state_is_first_divergence(
        baseline,
        repaired,
        fee_divergence,
        (risk_divergence, target_divergence, report_divergence, exit_divergence),
    )
    if fee_clamp_first:
        causal_verdict = "FEE_CLAMP_IS_FIRST_DIVERGENCE"
    elif accounting_mark_first:
        causal_verdict = "ACCOUNTING_MARK_STATE_IS_FIRST_DIVERGENCE"
    else:
        causal_verdict = "CAUSALITY_NOT_PROVEN"
    return {
        "schema_version": "golden-accounting-causal-comparison.v1",
        "causal_verdict": causal_verdict,
        "fixture_identity_equal": baseline["fixture"] == repaired["fixture"],
        "baseline_revision": baseline.get("source_revision"),
        "repaired_revision": repaired.get("source_revision"),
        "first_fee_divergence": fee_divergence,
        "first_risk_divergence": risk_divergence,
        "first_target_divergence": target_divergence,
        "first_report_divergence": report_divergence,
        "first_exit_divergence": exit_divergence,
        "semantic_result": {
            "baseline": baseline["semantic_result"],
            "repaired": repaired["semantic_result"],
            "equal": baseline["semantic_result"] == repaired["semantic_result"],
        },
        "trace_counts": {
            name: {"baseline": len(baseline[name]), "repaired": len(repaired[name])}
            for name in ("fees", "risk_snapshots", "targets", "reports", "exits")
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--current-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-root", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.worker:
        if args.source_root is None:
            raise SystemExit("--worker requires --source-root")
        print(_canonical_json(_run_worker(args.source_root)))
        return 0
    if args.baseline_root is None or args.current_root is None or args.output is None:
        raise SystemExit("comparison requires --baseline-root, --current-root and --output")
    comparison = compare_source_traces(
        run_source_probe(args.baseline_root),
        run_source_probe(args.current_root),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{comparison['causal_verdict']}: {args.output}")
    proven_verdicts = {
        "FEE_CLAMP_IS_FIRST_DIVERGENCE",
        "ACCOUNTING_MARK_STATE_IS_FIRST_DIVERGENCE",
    }
    return 0 if comparison["causal_verdict"] in proven_verdicts else 1


if __name__ == "__main__":
    raise SystemExit(main())
