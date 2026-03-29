"""Export training datasets from PostgreSQL live/paper audit logs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import connect

from .pipeline import (
    ResearchRunArtifact,
    _build_report,
    _to_jsonable,
    _update_index,
    _write_csv,
    _write_jsonl,
)


@dataclass(slots=True)
class LiveExportConfig:
    """Configuration for exporting live-monitoring audit rows."""

    dsn: str
    symbol: str = "BTCUSDT_PERP"
    timeframe: str = "4h"
    output_root: Path = Path("results/live_training_runs")
    horizons: tuple[int, ...] = (1, 3, 6)
    run_label: str = ""
    start_ns: int | None = None
    end_ns: int | None = None


def export_live_training_data(config: LiveExportConfig) -> ResearchRunArtifact:
    """Export joined cutoff rows from PostgreSQL audit tables."""
    with connect(config.dsn) as conn:
        rows = _fetch_base_rows(conn, config)

    _add_forward_labels(rows, config.horizons)
    run_id = _build_live_run_id(config)
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
        "source": "postgresql_audit",
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "horizons": list(config.horizons),
        "start_ns": config.start_ns,
        "end_ns": config.end_ns,
        "row_count": len(rows),
    }
    summary = {
        "run_id": run_id,
        "metrics": {
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "row_count": len(rows),
            "first_cutoff_time_ns": rows[0]["cutoff_time_ns"] if rows else None,
            "last_cutoff_time_ns": rows[-1]["cutoff_time_ns"] if rows else None,
        },
        "diagnostics": {
            "playbook_counts": _count_values(rows, "playbook_id"),
            "actionable_exit_counts": _count_values(rows, "exit_type", exclude={"NONE", "", None}),
            "risk_flag_counts": _count_pipe_values(rows, "risk_flags"),
            "top_target_reason_codes": _count_pipe_values(rows, "target_reason_codes"),
        },
    }

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(_to_jsonable(summary), indent=2), encoding="utf-8")
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


def _fetch_base_rows(conn: Any, config: LiveExportConfig) -> list[dict[str, Any]]:
    clauses = [
        "ip.symbol = %(symbol)s",
        "ip.timeframe = %(timeframe)s",
    ]
    params: dict[str, Any] = {"symbol": config.symbol, "timeframe": config.timeframe}
    if config.start_ns is not None:
        clauses.append("ip.cutoff_time_ns >= %(start_ns)s")
        params["start_ns"] = config.start_ns
    if config.end_ns is not None:
        clauses.append("ip.cutoff_time_ns <= %(end_ns)s")
        params["end_ns"] = config.end_ns
    where_sql = " AND ".join(clauses)

    sql = f"""
        SELECT
            ip.symbol,
            ip.timeframe,
            ip.cutoff_time_ns,
            ip.quality_flags AS indicator_quality_flags,
            ip.values AS indicator_values,
            ms.directional_state,
            ms.directional_confidence,
            ms.volatility_state,
            ms.volatility_confidence,
            ms.positioning_state,
            ms.positioning_confidence,
            ms.quality_state,
            ms.quality_confidence,
            ms.regime,
            ms.regime_confidence,
            ms.direction_signal,
            ms.transition_risk,
            ms.quality_flags AS state_quality_flags,
            ms.reason_codes AS state_reason_codes,
            ps.playbook_id,
            ps.playbook_confidence,
            ps.max_leverage AS playbook_max_leverage,
            ps.trailing_stop_atr_mult,
            ps.allow_pyramid,
            ps.max_pyramid_count,
            ps.time_decay_bars,
            ps.reason_codes AS playbook_reason_codes,
            sd.base_size,
            sd.confidence_multiplier,
            sd.stress_discount,
            sd.execution_haircut,
            sd.final_leverage,
            sd.reason_codes AS sizing_reason_codes,
            ed.exit_type,
            ed.exit_urgency,
            ed.target_reduction_pct,
            ed.stop_price,
            ed.reason_code AS exit_reason_code,
            ed.reason_detail AS exit_reason_detail,
            ed.reason_codes AS exit_reason_codes,
            si.target_qty,
            si.target_leverage,
            si.reduce_only,
            si.regime AS target_regime,
            si.regime_confidence AS target_regime_confidence,
            si.reason_codes AS target_reason_codes,
            rs.equity,
            rs.drawdown,
            rs.lev_cap,
            rs.reduce_only_mode,
            rs.flags AS risk_flags
        FROM indicator_packs ip
        LEFT JOIN market_states ms
            ON ms.symbol = ip.symbol
           AND ms.timeframe = ip.timeframe
           AND ms.cutoff_time_ns = ip.cutoff_time_ns
        LEFT JOIN playbook_selections ps
            ON ps.symbol = ip.symbol
           AND ps.timeframe = ip.timeframe
           AND ps.cutoff_time_ns = ip.cutoff_time_ns
        LEFT JOIN sizing_decisions sd
            ON sd.symbol = ip.symbol
           AND sd.timeframe = ip.timeframe
           AND sd.cutoff_time_ns = ip.cutoff_time_ns
        LEFT JOIN exit_decisions ed
            ON ed.symbol = ip.symbol
           AND ed.timeframe = ip.timeframe
           AND ed.cutoff_time_ns = ip.cutoff_time_ns
        LEFT JOIN strategy_intents si
            ON si.symbol = ip.symbol
           AND si.timeframe = ip.timeframe
           AND si.cutoff_time_ns = ip.cutoff_time_ns
        LEFT JOIN LATERAL (
            SELECT equity, drawdown, lev_cap, reduce_only_mode, flags
            FROM risk_snapshots
            WHERE ts_ns <= ip.cutoff_time_ns
            ORDER BY ts_ns DESC
            LIMIT 1
        ) rs ON TRUE
        WHERE {where_sql}
        ORDER BY ip.cutoff_time_ns ASC
    """

    rows: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [item.name for item in cur.description]
        for record in cur.fetchall():
            raw = dict(zip(columns, record))
            indicator_values = raw.pop("indicator_values") or {}
            row = {
                "symbol": raw["symbol"],
                "timeframe": raw["timeframe"],
                "cutoff_time_ns": raw["cutoff_time_ns"],
                "close_price": float(indicator_values.get("last_price", 0.0) or 0.0),
                "realized_vol": float(indicator_values.get("realized_vol", 0.0) or 0.0),
                "cvd_slope": float(indicator_values.get("cvd_slope", 0.0) or 0.0),
                "funding_oi_z": float(indicator_values.get("funding_oi_z", 0.0) or 0.0),
                "ob_liq_z": float(indicator_values.get("ob_liq_z", 0.0) or 0.0),
                "zen_pulse": float(indicator_values.get("zen_pulse", 0.0) or 0.0),
                "atr": float(indicator_values.get("atr", 0.0) or 0.0),
                "momentum_roc_z": float(indicator_values.get("momentum_roc_z", 0.0) or 0.0),
                "structure_state": str(
                    indicator_values.get("structure_state") or indicator_values.get("swing_structure") or "NEUTRAL"
                ),
                "indicator_quality_flags": _jsonb_to_pipe(raw.pop("indicator_quality_flags")),
                "target_direction": 0.0,
            }
            row.update(
                {
                    key: _normalize_json_value(value)
                    for key, value in raw.items()
                    if key not in {"symbol", "timeframe", "cutoff_time_ns"}
                }
            )
            row["state_quality_flags"] = _jsonb_to_pipe(row.get("state_quality_flags"))
            row["state_reason_codes"] = _jsonb_to_pipe(row.get("state_reason_codes"))
            row["playbook_reason_codes"] = _jsonb_to_pipe(row.get("playbook_reason_codes"))
            row["sizing_reason_codes"] = _jsonb_to_pipe(row.get("sizing_reason_codes"))
            row["exit_reason_codes"] = _jsonb_to_pipe(row.get("exit_reason_codes"))
            row["target_reason_codes"] = _jsonb_to_pipe(row.get("target_reason_codes"))
            row["risk_flags"] = _jsonb_to_pipe(row.get("risk_flags"))
            target_qty = row.get("target_qty")
            if isinstance(target_qty, (float, int)):
                row["target_direction"] = 1.0 if target_qty > 0 else -1.0 if target_qty < 0 else 0.0
            rows.append(row)
    return rows


def _add_forward_labels(rows: list[dict[str, Any]], horizons: tuple[int, ...]) -> None:
    for index, row in enumerate(rows):
        close_price = float(row.get("close_price") or 0.0)
        for horizon in horizons:
            future_index = index + horizon
            if close_price <= 0 or future_index >= len(rows):
                row[f"future_return_{horizon}"] = None
            else:
                future_close = float(rows[future_index].get("close_price") or 0.0)
                row[f"future_return_{horizon}"] = (future_close - close_price) / close_price
        window = rows[index + 1 : min(len(rows), index + 4)]
        if close_price > 0 and window:
            future_prices = [
                float(item.get("close_price") or 0.0) for item in window if float(item.get("close_price") or 0.0) > 0
            ]
            if future_prices:
                row["mae_3"] = (min(future_prices) - close_price) / close_price
                row["mfe_3"] = (max(future_prices) - close_price) / close_price
            else:
                row["mae_3"] = None
                row["mfe_3"] = None
        else:
            row["mae_3"] = None
            row["mfe_3"] = None
        row["bars_to_end"] = len(rows) - index - 1


def _count_values(rows: list[dict[str, Any]], key: str, *, exclude: set[Any] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    blocked = exclude or set()
    for row in rows:
        value = row.get(key)
        if value in blocked:
            continue
        if value is None:
            continue
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _count_pipe_values(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key) or ""
        for token in str(value).split("|"):
            if not token:
                continue
            counts[token] = counts.get(token, 0) + 1
    return counts


def _build_live_run_id(config: LiveExportConfig) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = config.run_label.strip().replace(" ", "-")
    symbol = config.symbol.lower().replace("/", "_")
    if label:
        return f"{stamp}_{symbol}_{label}"
    return f"{stamp}_{symbol}"


def _jsonb_to_pipe(value: Any) -> str:
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    return value
