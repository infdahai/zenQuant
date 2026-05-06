"""Mechanical local data coverage audit.

This module answers two separate questions:

1. What parquet/csv coverage is physically present under data/raw?
2. Is that source allowed into formal train/dev/test truth, or only into a
   research/context role?

The audit is intentionally descriptive. It does not promote any source.
"""

from __future__ import annotations

import glob
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from zenquant.research.splits import DatasetSplitSpec, default_btc_split_spec

TIMESTAMP_CANDIDATES = (
    "close_time_ns",
    "open_time_ns",
    "bucket_start_ns",
    "observed_at_ns",
    "funding_time_ns",
    "ts_ns",
    "timestamp_ns",
    "start_ns",
    "end_ns",
    "start_ms",
    "end_ms",
    "ts_ms",
    "timestamp_ms",
    "funding_time",
    "timestamp",
    "time",
    "date",
)


@dataclass(slots=True, frozen=True)
class ArtifactCoverage:
    kind: str
    path_glob: str
    file_count: int
    row_count: int | None
    start_at: str | None
    end_at: str | None
    latest_partition: str | None
    latest_file_mtime: str | None
    timestamp_column: str | None
    error: str | None = None


@dataclass(slots=True, frozen=True)
class DataSourceSpec:
    source_id: str
    display_name: str
    source_type: str
    parquet_globs: tuple[str, ...]
    csv_globs: tuple[str, ...]
    training_role: str
    formal_train_enabled: bool
    allowed_uses: tuple[str, ...]
    policy_blockers: tuple[str, ...] = ()
    timestamp_candidates: tuple[str, ...] = TIMESTAMP_CANDIDATES


def build_default_source_specs(*, data_root: Path = Path("data/raw"), symbol: str = "BTCUSDT") -> list[DataSourceSpec]:
    specs: list[DataSourceSpec] = []
    for timeframe in ("5m", "1h", "4h", "1d"):
        specs.append(
            DataSourceSpec(
                source_id=f"binance_futures_klines_{timeframe}",
                display_name=f"Binance USDT futures klines {timeframe}",
                source_type="price_replay_base",
                parquet_globs=(str(data_root / "binance" / "klines" / symbol / timeframe / "data.parquet"),),
                csv_globs=(str(data_root / "binance" / "klines" / symbol / timeframe / "*.csv"),),
                training_role="formal_price_truth",
                formal_train_enabled=True,
                allowed_uses=("train", "dev", "test", "replay", "backtest"),
            )
        )
    specs.extend(
        [
            DataSourceSpec(
                source_id="binance_futures_funding",
                display_name="Binance USDT futures funding",
                source_type="execution_cost_context",
                parquet_globs=(str(data_root / "binance" / "funding" / symbol / "data.parquet"),),
                csv_globs=(str(data_root / "binance" / "funding" / symbol / "*.csv"),),
                training_role="formal_funding_truth",
                formal_train_enabled=True,
                allowed_uses=("train", "dev", "test", "replay", "backtest"),
            ),
            DataSourceSpec(
                source_id="binance_futures_open_interest_4h",
                display_name="Binance USDT futures open interest 4h",
                source_type="positioning_state",
                parquet_globs=(str(data_root / "binance" / "open_interest" / symbol / "4h" / "*.parquet"),),
                csv_globs=(str(data_root / "binance" / "open_interest" / symbol / "4h" / "*.csv"),),
                training_role="secondary_state_feature",
                formal_train_enabled=False,
                allowed_uses=("train_state_feature_after_admission", "dev_diagnostic", "dashboard_context"),
                policy_blockers=("not_primary_execution_truth",),
            ),
            DataSourceSpec(
                source_id="binance_futures_aggtrades_1h",
                display_name="Binance aggTrades 1h CVD",
                source_type="microstructure_state",
                parquet_globs=(
                    str(data_root / "binance" / "aggtrades" / symbol / "1h" / "data.parquet"),
                    str(data_root / "binance" / "aggtrades" / symbol / "raw" / "*.parquet"),
                ),
                csv_globs=(),
                training_role="secondary_state_feature",
                formal_train_enabled=False,
                allowed_uses=("train_state_feature_after_admission", "dev_diagnostic", "microstructure_research"),
                policy_blockers=("not_primary_execution_truth",),
            ),
            DataSourceSpec(
                source_id="binance_liquidations_primary",
                display_name="Binance liquidation primary truth",
                source_type="liquidation_truth",
                parquet_globs=(
                    str(data_root / "binance" / "liquidations" / "aggregated" / symbol / "**" / "*.parquet"),
                ),
                csv_globs=(),
                training_role="primary_liquidation_truth",
                formal_train_enabled=False,
                allowed_uses=("research_after_readiness", "dev_diagnostic"),
                policy_blockers=("missing_or_not_readiness_promoted",),
            ),
            DataSourceSpec(
                source_id="merged_liquidations",
                display_name="Merged liquidation research input",
                source_type="liquidation_research_input",
                parquet_globs=(str(data_root / "merged_liquidations" / "aggregated" / symbol / "**" / "*.parquet"),),
                csv_globs=(),
                training_role="research_only_secondary",
                formal_train_enabled=False,
                allowed_uses=("research_only", "dev_diagnostic", "dashboard_context"),
                policy_blockers=("not_primary_truth", "not_promotion_ready"),
            ),
            DataSourceSpec(
                source_id="hyperliquid_public_proxy",
                display_name="Hyperliquid public liquidation proxy",
                source_type="liquidation_proxy",
                parquet_globs=(
                    str(data_root / "hyperliquid" / "liquidations" / "aggregated" / symbol / "**" / "*.parquet"),
                    str(data_root / "hyperliquid" / "liquidations" / "raw" / "candles" / "**" / "*.parquet"),
                    str(data_root / "hyperliquid" / "liquidations" / "raw" / "funding" / "**" / "*.parquet"),
                ),
                csv_globs=(),
                training_role="research_only_secondary",
                formal_train_enabled=False,
                allowed_uses=("research_only", "state_experiment", "dashboard_context"),
                policy_blockers=("proxy_only", "not_primary_truth"),
            ),
            DataSourceSpec(
                source_id="hyperliquid_whale_positions",
                display_name="Hyperliquid public whale positions",
                source_type="public_account_positioning",
                parquet_globs=(
                    str(data_root / "hyperliquid" / "whale_positions" / "accounts" / "**" / "*.parquet"),
                    str(data_root / "hyperliquid" / "whale_positions" / "positions" / "**" / "*.parquet"),
                ),
                csv_globs=(),
                training_role="observation_only_positioning_context",
                formal_train_enabled=False,
                allowed_uses=("observation", "research_only", "dashboard_context"),
                policy_blockers=("public_account_subset", "not_primary_truth"),
            ),
            DataSourceSpec(
                source_id="deribit_options_chain",
                display_name="Deribit options surface snapshots",
                source_type="options_state",
                parquet_globs=(str(data_root / "deribit_options" / "BTC" / "**" / "*.parquet"),),
                csv_globs=(),
                training_role="observation_only_options_context",
                formal_train_enabled=False,
                allowed_uses=("observation", "research_only", "dashboard_context"),
                policy_blockers=("sample_window_incomplete_until_readiness", "not_primary_execution_truth"),
            ),
            DataSourceSpec(
                source_id="deribit_block_flow",
                display_name="Deribit public block flow",
                source_type="options_flow_state",
                parquet_globs=(str(data_root / "deribit_block_flow" / "BTC" / "**" / "*.parquet"),),
                csv_globs=(),
                training_role="research_only_secondary",
                formal_train_enabled=False,
                allowed_uses=("research_only", "state_experiment", "dashboard_context"),
                policy_blockers=("not_account_truth", "not_primary_execution_truth"),
            ),
            DataSourceSpec(
                source_id="deribit_replay_candidate",
                display_name="Deribit replay candidate",
                source_type="perp_replay_candidate",
                parquet_globs=(
                    str(data_root / "deribit_replay" / "klines" / symbol / "**" / "*.parquet"),
                    str(data_root / "deribit_replay" / "funding" / symbol / "*.parquet"),
                ),
                csv_globs=(),
                training_role="candidate_replay_base",
                formal_train_enabled=False,
                allowed_uses=("replay_candidate_review", "source_independence_check"),
                policy_blockers=("candidate_not_promoted", "coverage_not_formal_train_truth"),
            ),
            DataSourceSpec(
                source_id="bitmex_xbtusd_2017_2020_replay_candidate",
                display_name="BitMEX XBTUSD 2017-2020 replay-base candidate",
                source_type="inverse_perp_replay_candidate",
                parquet_globs=(
                    str(data_root / "bitmex" / "klines" / "XBTUSD" / "**" / "*.parquet"),
                    str(data_root / "bitmex" / "funding" / "XBTUSD" / "*.parquet"),
                ),
                csv_globs=(
                    str(data_root / "bitmex" / "trade" / "XBTUSD" / "raw" / "*.csv"),
                    str(data_root / "bitmex" / "trade" / "XBTUSD" / "raw" / "*.csv.gz"),
                ),
                training_role="candidate_replay_base",
                formal_train_enabled=False,
                allowed_uses=("replay_candidate_review", "pre_2020_execution_truth_research"),
                policy_blockers=("candidate_not_downloaded_or_reviewed", "not_formal_train_truth"),
            ),
        ]
    )
    for timeframe in ("1h", "4h", "1d"):
        specs.append(
            DataSourceSpec(
                source_id=f"binance_spot_klines_{timeframe}",
                display_name=f"Binance spot klines {timeframe}",
                source_type="spot_context",
                parquet_globs=(str(data_root / "binance_spot" / "klines" / symbol / timeframe / "data.parquet"),),
                csv_globs=(str(data_root / "binance_spot" / "klines" / symbol / timeframe / "*.csv"),),
                training_role="context_only_not_execution_truth",
                formal_train_enabled=False,
                allowed_uses=("cycle_regime_research", "slow_context", "stress_context"),
                policy_blockers=("spot_perp_truth_boundary", "not_formal_train_truth"),
            )
        )
    specs.extend(_macro_source_specs(data_root=data_root))
    return specs


def _macro_source_specs(*, data_root: Path) -> list[DataSourceSpec]:
    specs = [
        ("btc_mvrv", "BTC MVRV", "slow_cycle_context"),
        ("btc_etf_netflow", "BTC ETF netflow", "macro_flow_context"),
        ("vix", "VIX", "macro_context"),
        ("dxy", "DXY", "macro_context"),
        ("ndx", "Nasdaq 100", "macro_context"),
        ("treasury_10y", "US 10Y Treasury", "macro_context"),
        ("gold", "Gold", "macro_context"),
        ("fear_greed", "Fear and Greed", "sentiment_context"),
        ("active_addresses", "BTC active addresses", "onchain_context"),
        ("tx_count", "BTC tx count", "onchain_context"),
        ("btc_dominance", "BTC dominance", "market_structure_context"),
    ]
    return [
        DataSourceSpec(
            source_id=f"macro_{source_id}",
            display_name=display_name,
            source_type=source_type,
            parquet_globs=(str(data_root / "macro" / source_id / "data.parquet"),),
            csv_globs=(),
            training_role="slow_context_only",
            formal_train_enabled=False,
            allowed_uses=("regime_prior", "risk_budget_context", "dashboard_context"),
            policy_blockers=("not_intraday_execution_truth",),
        )
        for source_id, display_name, source_type in specs
    ] + [
        DataSourceSpec(
            source_id="coinmetrics_onchain_factors",
            display_name="CoinMetrics onchain factors",
            source_type="onchain_context",
            parquet_globs=(
                str(data_root / "coinmetrics" / "community" / "BTC" / "data.parquet"),
                str(data_root / "coinmetrics" / "community" / "BTC" / "derived_onchain_factors.parquet"),
            ),
            csv_globs=(),
            training_role="secondary_state_feature",
            formal_train_enabled=False,
            allowed_uses=("state_experiment_after_admission", "dashboard_context"),
            policy_blockers=("not_primary_execution_truth",),
        ),
        DataSourceSpec(
            source_id="cme_cot_btc",
            display_name="CME COT BTC positioning",
            source_type="positioning_context",
            parquet_globs=(str(data_root / "cftc" / "cot_btc.parquet"),),
            csv_globs=(),
            training_role="slow_context_only",
            formal_train_enabled=False,
            allowed_uses=("regime_prior", "positioning_state", "dashboard_context"),
            policy_blockers=("weekly_context_not_execution_truth",),
        ),
        DataSourceSpec(
            source_id="binance_metrics_factors",
            display_name="Binance metrics factors",
            source_type="exchange_metrics_state",
            parquet_globs=(
                str(data_root / "binance" / "metrics" / "BTCUSDT" / "*.parquet"),
                str(data_root / "binance" / "metrics" / "BTCUSDT" / "derived_metrics_factors.parquet"),
            ),
            csv_globs=(),
            training_role="secondary_state_feature",
            formal_train_enabled=False,
            allowed_uses=("state_experiment_after_admission", "dashboard_context"),
            policy_blockers=("not_primary_execution_truth",),
        ),
    ]


def build_data_coverage_audit(
    *,
    data_root: Path = Path("data/raw"),
    symbol: str = "BTCUSDT",
    split_spec: DatasetSplitSpec | None = None,
) -> dict[str, Any]:
    spec = split_spec or default_btc_split_spec()
    sources = [
        audit_source(source, split_spec=spec)
        for source in build_default_source_specs(data_root=data_root, symbol=symbol)
    ]
    blockers = sorted(
        {
            blocker
            for source in sources
            for blocker in source["training_integration"]["blocked_by"]
            if source["training_integration"]["integration_status"] in {"missing", "blocked", "partial_formal_truth"}
        }
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "split_policy": {
            "version": spec.version,
            "train": _split_window_payload(spec.train.start_ns, spec.train.end_ns),
            "dev": _split_window_payload(spec.dev.start_ns, spec.dev.end_ns),
            "test": _split_window_payload(spec.test.start_ns, spec.test.end_ns),
        },
        "source_count": len(sources),
        "sources": sources,
        "overall_blockers": blockers,
        "truth_boundary": (
            "Formal replay truth remains Binance futures unless a separate replay-base candidate passes coverage, "
            "point-in-time, execution-parity, and source-boundary review. Binance spot is context-only and is never "
            "silently merged into futures/perp execution truth."
        ),
    }


def audit_source(source: DataSourceSpec, *, split_spec: DatasetSplitSpec) -> dict[str, Any]:
    parquet = [_scan_parquet_glob(path_glob, source.timestamp_candidates) for path_glob in source.parquet_globs]
    csv = [_scan_csv_glob(path_glob) for path_glob in source.csv_globs]
    parquet_start_ns = _min_ns(
        [_iso_to_ns(item.start_at) for item in parquet if item.file_count > 0 and item.error is None]
    )
    combined_start_ns = parquet_start_ns or _min_ns([_iso_to_ns(item.start_at) for item in parquet + csv])
    combined_end_ns = _max_ns([_iso_to_ns(item.end_at) for item in parquet + csv])
    combined_start_at = _ns_to_iso(combined_start_ns)
    combined_end_at = _ns_to_iso(combined_end_ns)
    split_coverage = _split_coverage(start_ns=combined_start_ns, end_ns=combined_end_ns, split_spec=split_spec)
    integration = _training_integration(source, split_coverage, parquet + csv)
    return {
        "source_id": source.source_id,
        "display_name": source.display_name,
        "source_type": source.source_type,
        "training_role": source.training_role,
        "combined_coverage": {
            "start_at": combined_start_at,
            "end_at": combined_end_at,
        },
        "parquet": [asdict(item) for item in parquet],
        "csv": [asdict(item) for item in csv],
        "split_coverage": split_coverage,
        "training_integration": integration,
    }


def write_data_coverage_audit(
    payload: dict[str, Any],
    *,
    output_dir: Path = Path("results/data_source_review"),
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "data_coverage_audit.json"
    md_path = output_dir / "data_coverage_audit.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(build_data_coverage_markdown(payload), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "source_count": payload["source_count"],
        "overall_blockers": payload["overall_blockers"],
    }


def build_data_coverage_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Data Coverage Audit",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- symbol: `{payload['symbol']}`",
        f"- source_count: `{payload['source_count']}`",
        f"- overall_blockers: `{payload['overall_blockers']}`",
        "",
        "## Truth Boundary",
        "",
        f"- {payload['truth_boundary']}",
        "",
        "## Sources",
        "",
    ]
    for source in payload["sources"]:
        integration = source["training_integration"]
        coverage = source["combined_coverage"]
        lines.extend(
            [
                f"### {source['source_id']}",
                "",
                f"- display_name: `{source['display_name']}`",
                f"- source_type: `{source['source_type']}`",
                f"- training_role: `{source['training_role']}`",
                f"- integration_status: `{integration['integration_status']}`",
                f"- formal_train_enabled: `{integration['formal_train_enabled']}`",
                f"- start_at: `{coverage['start_at']}`",
                f"- end_at: `{coverage['end_at']}`",
                f"- parquet_files: `{sum(item['file_count'] for item in source['parquet'])}`",
                f"- csv_files: `{sum(item['file_count'] for item in source['csv'])}`",
                f"- blocked_by: `{integration['blocked_by']}`",
                "",
            ]
        )
    return "\n".join(lines)


def _scan_parquet_glob(path_glob: str, timestamp_candidates: tuple[str, ...]) -> ArtifactCoverage:
    files = sorted(Path(path) for path in glob.glob(path_glob, recursive=True))
    latest_mtime = _latest_mtime(files)
    if not files:
        return ArtifactCoverage(
            kind="parquet",
            path_glob=path_glob,
            file_count=0,
            row_count=0,
            start_at=None,
            end_at=None,
            latest_partition=None,
            latest_file_mtime=latest_mtime,
            timestamp_column=None,
        )
    try:
        with duckdb.connect(database=":memory:") as conn:
            columns = [
                str(row[0])
                for row in conn.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?, union_by_name=true)", [path_glob]
                ).fetchall()
            ]
            timestamp_column = _select_timestamp_column(columns, timestamp_candidates)
            if timestamp_column is None:
                row_count = int(
                    conn.execute("SELECT count(*) FROM read_parquet(?, union_by_name=true)", [path_glob]).fetchone()[0]
                )
                return ArtifactCoverage(
                    kind="parquet",
                    path_glob=path_glob,
                    file_count=len(files),
                    row_count=row_count,
                    start_at=None,
                    end_at=None,
                    latest_partition=_latest_partition_from_files(files),
                    latest_file_mtime=latest_mtime,
                    timestamp_column=None,
                )
            row_count, min_ts, max_ts = conn.execute(
                f"SELECT count(*), min({timestamp_column}), max({timestamp_column}) "
                "FROM read_parquet(?, union_by_name=true)",
                [path_glob],
            ).fetchone()
    except Exception as exc:
        return ArtifactCoverage(
            kind="parquet",
            path_glob=path_glob,
            file_count=len(files),
            row_count=None,
            start_at=None,
            end_at=None,
            latest_partition=_latest_partition_from_files(files),
            latest_file_mtime=latest_mtime,
            timestamp_column=None,
            error=str(exc),
        )
    return ArtifactCoverage(
        kind="parquet",
        path_glob=path_glob,
        file_count=len(files),
        row_count=int(row_count or 0),
        start_at=_timestamp_to_iso(min_ts, timestamp_column),
        end_at=_timestamp_to_iso(max_ts, timestamp_column),
        latest_partition=_latest_partition_from_files(files),
        latest_file_mtime=latest_mtime,
        timestamp_column=timestamp_column,
    )


def _scan_csv_glob(path_glob: str) -> ArtifactCoverage:
    files = sorted(Path(path) for path in glob.glob(path_glob, recursive=True))
    latest_mtime = _latest_mtime(files)
    partitions = [_filename_partition(path) for path in files]
    partitions = [partition for partition in partitions if partition is not None]
    if not files:
        return ArtifactCoverage(
            kind="csv",
            path_glob=path_glob,
            file_count=0,
            row_count=None,
            start_at=None,
            end_at=None,
            latest_partition=None,
            latest_file_mtime=latest_mtime,
            timestamp_column=None,
        )
    start_ns = _min_ns([_partition_start_ns(partition) for partition in partitions])
    end_ns = _max_ns([_partition_end_ns(partition) for partition in partitions])
    return ArtifactCoverage(
        kind="csv",
        path_glob=path_glob,
        file_count=len(files),
        row_count=None,
        start_at=_ns_to_iso(start_ns),
        end_at=_ns_to_iso(end_ns),
        latest_partition=max(partitions) if partitions else None,
        latest_file_mtime=latest_mtime,
        timestamp_column="filename_partition" if partitions else None,
    )


def _select_timestamp_column(columns: list[str], timestamp_candidates: tuple[str, ...]) -> str | None:
    normalized = {column.lower(): column for column in columns}
    for candidate in timestamp_candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    for column in columns:
        lower = column.lower()
        if lower in {"timeframe", "symbol", "source"}:
            continue
        if lower.endswith("_at") or "timestamp" in lower or lower in {"time", "date"} or lower.endswith("_ns"):
            return column
    return None


def _training_integration(
    source: DataSourceSpec,
    split_coverage: dict[str, dict[str, Any]],
    artifacts: list[ArtifactCoverage],
) -> dict[str, Any]:
    has_data = any(item.file_count > 0 and item.error is None for item in artifacts)
    blockers = list(source.policy_blockers)
    status = "missing"
    if has_data:
        if source.formal_train_enabled:
            missing_splits = [
                name
                for name in ("train", "dev")
                if split_coverage[name]["status"] not in {"covered", "covered_to_latest"}
            ]
            if missing_splits:
                blockers.extend(f"{name}_coverage_incomplete" for name in missing_splits)
                status = "partial_formal_truth"
            else:
                status = "formal_truth_available"
        elif source.training_role == "candidate_replay_base":
            status = "candidate_present_pending_review"
        else:
            status = "research_or_context_available"
    else:
        blockers.append("local_data_missing")
    blockers.extend(f"{item.kind}_scan_error" for item in artifacts if item.error)
    return {
        "integration_status": status,
        "formal_train_enabled": source.formal_train_enabled,
        "allowed_uses": list(source.allowed_uses),
        "blocked_by": sorted(dict.fromkeys(blockers)),
    }


def _split_coverage(
    *,
    start_ns: int | None,
    end_ns: int | None,
    split_spec: DatasetSplitSpec,
) -> dict[str, dict[str, Any]]:
    windows = {
        "train": split_spec.train,
        "dev": split_spec.dev,
        "test": split_spec.test,
    }
    if start_ns is None or end_ns is None:
        return {
            name: {"status": "missing", "start_at": _ns_to_iso(window.start_ns), "end_at": _ns_to_iso(window.end_ns)}
            for name, window in windows.items()
        }
    result: dict[str, dict[str, Any]] = {}
    for name, window in windows.items():
        split_end = window.end_ns
        if split_end is None:
            status = "covered_to_latest" if end_ns >= window.start_ns and start_ns <= window.start_ns else "missing"
        elif start_ns <= window.start_ns and end_ns >= split_end:
            status = "covered"
        elif end_ns <= window.start_ns or start_ns >= split_end:
            status = "missing"
        else:
            status = "partial"
        result[name] = {
            "status": status,
            "start_at": _ns_to_iso(window.start_ns),
            "end_at": _ns_to_iso(window.end_ns),
        }
    return result


def _split_window_payload(start_ns: int, end_ns: int | None) -> dict[str, str | None]:
    return {"start_at": _ns_to_iso(start_ns), "end_at": _ns_to_iso(end_ns)}


def _timestamp_to_iso(value: Any, column: str) -> str | None:
    ns = _timestamp_to_ns(value, column)
    return _ns_to_iso(ns)


def _timestamp_to_ns(value: Any, column: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1_000_000_000)
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        parsed = datetime(value.year, value.month, value.day, tzinfo=UTC)
        return int(parsed.timestamp() * 1_000_000_000)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        parsed = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1_000_000_000)
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return None
    lower = column.lower()
    if lower.endswith("_ns") or numeric >= 10**17:
        return numeric
    if lower.endswith("_ms") or numeric >= 10**11:
        return numeric * 1_000_000
    return numeric * 1_000_000


def _iso_to_ns(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    parsed = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1_000_000_000)


def _ns_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC).isoformat()


def _min_ns(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _max_ns(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _latest_mtime(files: list[Path]) -> str | None:
    if not files:
        return None
    return datetime.fromtimestamp(max(path.stat().st_mtime for path in files), tz=UTC).isoformat()


def _latest_partition_from_files(files: list[Path]) -> str | None:
    partitions = [_filename_partition(path) for path in files]
    partitions = [partition for partition in partitions if partition is not None]
    return max(partitions) if partitions else None


def _filename_partition(path: Path) -> str | None:
    match = re.search(r"(20\d{2}|19\d{2})[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])", path.name)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = re.search(r"(20\d{2}|19\d{2})[-_](0[1-9]|1[0-2])", path.name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    if re.fullmatch(r"(20\d{2}|19\d{2})-(0[1-9]|1[0-2])", path.parent.name):
        return path.parent.name
    return None


def _partition_start_ns(partition: str | None) -> int | None:
    if partition is None:
        return None
    parts = partition.split("-")
    if len(parts) == 2:
        parsed = datetime(int(parts[0]), int(parts[1]), 1, tzinfo=UTC)
    else:
        parsed = datetime(int(parts[0]), int(parts[1]), int(parts[2]), tzinfo=UTC)
    return int(parsed.timestamp() * 1_000_000_000)


def _partition_end_ns(partition: str | None) -> int | None:
    start = _partition_start_ns(partition)
    if start is None:
        return None
    parts = partition.split("-")
    parsed = datetime.fromtimestamp(start / 1_000_000_000, tz=UTC)
    if len(parts) == 2:
        if parsed.month == 12:
            end = datetime(parsed.year + 1, 1, 1, tzinfo=UTC)
        else:
            end = datetime(parsed.year, parsed.month + 1, 1, tzinfo=UTC)
    else:
        end = datetime.fromtimestamp(start / 1_000_000_000 + 86400, tz=UTC)
    return int(end.timestamp() * 1_000_000_000)
