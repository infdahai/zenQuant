"""End-to-end research artifact pipeline for replay-driven tuning loops."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


@dataclass(slots=True)
class ResearchRunConfig:
    """Configuration for one deterministic research export run."""

    backtest: HistoricalBacktestConfig
    output_root: Path = Path("results/research_runs")
    horizons: tuple[int, ...] = (1, 3, 6)
    run_label: str = ""


@dataclass(slots=True)
class ResearchRunArtifact:
    """Filesystem artifact references for one completed research run."""

    run_id: str
    run_dir: Path
    manifest_path: Path
    summary_path: Path
    dataset_jsonl_path: Path
    dataset_csv_path: Path
    report_path: Path
    row_count: int


def run_research_pipeline(config: ResearchRunConfig) -> ResearchRunArtifact:
    """Run replay, export joined rows, and write a compact research report."""
    runner = HistoricalBacktest(config.backtest)
    result = runner.run()
    rows = runner.build_analysis_rows(horizons=config.horizons)

    run_id = _build_run_id(config)
    run_dir = config.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = run_dir / "manifest.json"
    summary_path = run_dir / "summary.json"
    dataset_jsonl_path = run_dir / "analysis_rows.jsonl"
    dataset_csv_path = run_dir / "analysis_rows.csv"
    report_path = run_dir / "report.md"

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "label": config.run_label,
        "horizons": list(config.horizons),
        "backtest_config": _to_jsonable(asdict(config.backtest)),
        "row_count": len(rows),
    }
    summary = {
        "run_id": run_id,
        "metrics": _to_jsonable(asdict(result.metrics)),
        "diagnostics": _to_jsonable(asdict(result.diagnostics)),
        "total_indicator_packs": result.total_indicator_packs,
        "total_targets": result.total_targets,
        "total_reports": result.total_reports,
        "final_equity": result.final_equity,
    }

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_jsonl(dataset_jsonl_path, rows)
    _write_csv(dataset_csv_path, rows)
    report_path.write_text(_build_report(manifest, summary, rows), encoding="utf-8")
    _update_index(config.output_root, manifest, summary)

    return ResearchRunArtifact(
        run_id=run_id,
        run_dir=run_dir,
        manifest_path=manifest_path,
        summary_path=summary_path,
        dataset_jsonl_path=dataset_jsonl_path,
        dataset_csv_path=dataset_csv_path,
        report_path=report_path,
        row_count=len(rows),
    )


def _build_run_id(config: ResearchRunConfig) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = config.run_label.strip().replace(" ", "-")
    symbol = config.backtest.symbol.lower()
    if label:
        return f"{stamp}_{symbol}_{label}"
    return f"{stamp}_{symbol}"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_to_jsonable(row), sort_keys=True))
            handle.write("\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv

    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _csv_cell(row.get(name)) for name in fieldnames})


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return "|".join(str(item) for item in value)
    return value


def _build_report(manifest: dict[str, Any], summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    metrics = summary["metrics"]
    diagnostics = summary["diagnostics"]
    horizons = [int(value) for value in manifest["horizons"]]
    coverage_lines = [
        f"- future_return_{horizon}: `{sum(1 for row in rows if row.get(f'future_return_{horizon}') is not None)}`"
        for horizon in horizons
    ]
    return "\n".join(
        [
            "# Research Run",
            "",
            f"- run_id: `{manifest['run_id']}`",
            f"- label: `{manifest['label'] or 'default'}`",
            f"- symbol: `{metrics.get('symbol', manifest.get('symbol', 'unknown'))}`",
            f"- rows: `{manifest['row_count']}`",
            f"- horizons: `{manifest['horizons']}`",
            "",
            "## Metrics",
            "",
            f"- net_pnl: `{metrics.get('net_pnl', 'n/a')}`",
            f"- max_drawdown: `{metrics.get('max_drawdown', 'n/a')}`",
            f"- sharpe: `{metrics.get('sharpe', 'n/a')}`",
            f"- profit_factor: `{metrics.get('profit_factor', 'n/a')}`",
            f"- trade_count: `{metrics.get('trade_count', metrics.get('row_count', 'n/a'))}`",
            f"- turnover_per_day: `{metrics.get('turnover_per_day', 'n/a')}`",
            "",
            "## Diagnostics",
            "",
            f"- playbook_counts: `{diagnostics['playbook_counts']}`",
            f"- actionable_exit_counts: `{diagnostics['actionable_exit_counts']}`",
            f"- risk_flag_counts: `{diagnostics['risk_flag_counts']}`",
            f"- top_target_reason_codes: `{diagnostics['top_target_reason_codes']}`",
            "",
            "## Label Coverage",
            "",
            *coverage_lines,
            "",
            "## Notes",
            "",
            "- Rows are built from the production replay event stream, not a separate research engine.",
            "- One row corresponds to one 4h cutoff.",
            "- Forward labels are point-in-time safe and exclude unavailable future bars near the end of the sample.",
        ]
    )


def _update_index(output_root: Path, manifest: dict[str, Any], summary: dict[str, Any]) -> None:
    index_path = output_root / "index.json"
    if index_path.exists():
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        payload = {"runs": []}
    payload["runs"] = [
        entry for entry in payload.get("runs", []) if entry.get("run_id") != manifest["run_id"]
    ]
    payload["runs"].append(
        {
            "run_id": manifest["run_id"],
            "created_at": manifest["created_at"],
            "label": manifest["label"],
            "symbol": summary["metrics"]["symbol"],
            "net_pnl": summary["metrics"].get("net_pnl"),
            "sharpe": summary["metrics"].get("sharpe"),
            "max_drawdown": summary["metrics"].get("max_drawdown"),
            "trade_count": summary["metrics"].get("trade_count", summary["metrics"].get("row_count")),
            "row_count": manifest["row_count"],
        }
    )
    output_root.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        try:
            return value.value
        except AttributeError:
            return str(value)
    return value
