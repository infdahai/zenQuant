"""Deribit collector readiness helpers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

_SNAPSHOT_RE = re.compile(r"options_chain_(\d{8}T\d{6}Z)\.parquet$")
_STALE_HOURS = 8.1


@dataclass(slots=True)
class DeribitCollectorStatus:
    enabled: bool
    collector_running: bool
    last_success_ts: str | None
    last_error: str | None
    snapshot_count_24h: int
    snapshot_count_7d: int
    expected_snapshot_count_24h: int
    expected_snapshot_count_7d: int
    snapshot_coverage_ratio_24h: float
    snapshot_coverage_ratio_7d: float
    invalid_snapshot_count_7d: int
    duplicate_snapshot_count_7d: int
    max_gap_hours_7d: float
    observation_ready: bool
    research_ready: bool
    stale: bool
    blocked_by: str | None
    next_action: str


def _snapshot_candidates(root: Path, currency: str) -> list[tuple[Path, datetime]]:
    currency_root = root / currency
    if not currency_root.exists():
        return []
    found: list[tuple[Path, datetime]] = []
    for path in currency_root.rglob("options_chain_*.parquet"):
        match = _SNAPSHOT_RE.search(path.name)
        if not match:
            continue
        found.append((path, datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)))
    found.sort(key=lambda item: (item[1], str(item[0])))
    return found


def _validated_snapshot_time(path: Path, filename_ts: datetime) -> datetime | None:
    """Return the embedded source timestamp for a readable full-chain snapshot."""
    try:
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        required = {
            "snapshot_ts_ns",
            "expiry_ts_ms",
            "strike",
            "option_type",
            "underlying_price",
            "open_interest",
            "mark_iv",
        }
        schema = pq.read_schema(path)
        if not required.issubset(schema.names):
            return None
        table = pq.read_table(path, columns=sorted(required))
        if table.num_rows <= 0:
            return None
        timestamps = pc.unique(table["snapshot_ts_ns"]).to_pylist()
        if len(timestamps) != 1 or timestamps[0] is None:
            return None
        source_ns = int(timestamps[0])
        source_ts = datetime.fromtimestamp(source_ns / 1_000_000_000, tz=UTC)
        if abs((source_ts - filename_ts).total_seconds()) > 300:
            return None
        option_types = {str(value).lower() for value in pc.unique(table["option_type"]).to_pylist()}
        if not {"call", "put"}.issubset(option_types):
            return None
        expiries = [int(value) for value in pc.unique(table["expiry_ts_ms"]).to_pylist() if value is not None]
        dtes = [(expiry_ms / 1_000 - source_ns / 1_000_000_000) / 86_400 for expiry_ms in expiries]
        tenor_tolerances = ((7, 5), (30, 15), (90, 35))
        if any(not any(abs(dte - target) <= tolerance for dte in dtes) for target, tolerance in tenor_tolerances):
            return None
        numeric_columns = ("strike", "underlying_price", "open_interest", "mark_iv")
        for column in numeric_columns:
            values = table[column].to_pylist()
            if not values or any(value is None for value in values):
                return None
        return source_ts
    except (ArithmeticError, OSError, TypeError, ValueError):
        return None


def _validated_snapshot_times(
    root: Path,
    currency: str,
    now: datetime,
) -> tuple[list[datetime], int, int, bool]:
    candidates = _snapshot_candidates(root, currency)
    if not candidates:
        return [], 0, 0, False
    recent_candidates = [item for item in candidates if (now - item[1]).total_seconds() <= 7 * 24 * 3_600]
    if not recent_candidates:
        recent_candidates = [candidates[-1]]
    valid: list[datetime] = []
    invalid = 0
    for path, filename_ts in recent_candidates:
        source_ts = _validated_snapshot_time(path, filename_ts)
        if source_ts is None:
            invalid += 1
        else:
            valid.append(source_ts)
    duplicate_count = len(valid) - len(set(valid))
    return sorted(set(valid)), invalid, duplicate_count, True


def collect_deribit_status(root: Path, currency: str = "BTC") -> DeribitCollectorStatus:
    now = datetime.now(UTC)
    times, invalid_count, duplicate_count, has_candidates = _validated_snapshot_times(root, currency, now)
    if not has_candidates:
        return DeribitCollectorStatus(
            enabled=False,
            collector_running=False,
            last_success_ts=None,
            last_error="no_deribit_snapshots_found",
            snapshot_count_24h=0,
            snapshot_count_7d=0,
            expected_snapshot_count_24h=6,
            expected_snapshot_count_7d=42,
            snapshot_coverage_ratio_24h=0.0,
            snapshot_coverage_ratio_7d=0.0,
            invalid_snapshot_count_7d=0,
            duplicate_snapshot_count_7d=0,
            max_gap_hours_7d=0.0,
            observation_ready=False,
            research_ready=False,
            stale=True,
            blocked_by="no_deribit_snapshots_found",
            next_action="start_collector",
        )

    if not times:
        return DeribitCollectorStatus(
            enabled=True,
            collector_running=False,
            last_success_ts=None,
            last_error="no_valid_deribit_snapshots_found",
            snapshot_count_24h=0,
            snapshot_count_7d=0,
            expected_snapshot_count_24h=6,
            expected_snapshot_count_7d=42,
            snapshot_coverage_ratio_24h=0.0,
            snapshot_coverage_ratio_7d=0.0,
            invalid_snapshot_count_7d=invalid_count,
            duplicate_snapshot_count_7d=duplicate_count,
            max_gap_hours_7d=0.0,
            observation_ready=False,
            research_ready=False,
            stale=True,
            blocked_by="deribit_snapshot_integrity_incomplete",
            next_action="inspect_invalid_snapshots",
        )

    last = times[-1]
    snapshot_count_24h = sum(1 for ts in times if (now - ts).total_seconds() <= 24 * 3600)
    snapshot_count_7d = sum(1 for ts in times if (now - ts).total_seconds() <= 7 * 24 * 3600)
    recent_7d = [ts for ts in times if (now - ts).total_seconds() <= 7 * 24 * 3600]
    max_gap_hours_7d = 0.0
    for prev, curr in pairwise(recent_7d):
        max_gap_hours_7d = max(max_gap_hours_7d, round((curr - prev).total_seconds() / 3600.0, 2))
    stale = (now - last).total_seconds() > _STALE_HOURS * 3600
    integrity_complete = invalid_count == 0 and duplicate_count == 0
    observation_ready = (
        snapshot_count_7d >= 35 and max_gap_hours_7d <= _STALE_HOURS and not stale and integrity_complete
    )
    research_ready = snapshot_count_7d >= 42 and max_gap_hours_7d <= _STALE_HOURS and not stale and integrity_complete
    blocked_by = None if research_ready else "deribit_sample_window_incomplete"
    if not integrity_complete:
        blocked_by = "deribit_snapshot_integrity_incomplete"
    if stale:
        blocked_by = "deribit_collector_stale"
    next_action = "keep_collector_running"
    if not integrity_complete:
        next_action = "inspect_invalid_snapshots"
    if stale:
        next_action = "restart_collector"
    return DeribitCollectorStatus(
        enabled=True,
        collector_running=not stale,
        last_success_ts=last.isoformat(),
        last_error=None,
        snapshot_count_24h=snapshot_count_24h,
        snapshot_count_7d=snapshot_count_7d,
        expected_snapshot_count_24h=6,
        expected_snapshot_count_7d=42,
        snapshot_coverage_ratio_24h=min(1.0, snapshot_count_24h / 6),
        snapshot_coverage_ratio_7d=min(1.0, snapshot_count_7d / 42),
        invalid_snapshot_count_7d=invalid_count,
        duplicate_snapshot_count_7d=duplicate_count,
        max_gap_hours_7d=max_gap_hours_7d,
        observation_ready=observation_ready,
        research_ready=research_ready,
        stale=stale,
        blocked_by=blocked_by,
        next_action=next_action,
    )


def write_deribit_status(root: Path, currency: str = "BTC") -> Path:
    status = collect_deribit_status(root, currency)
    path = root / currency / "_collector_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(status), indent=2), encoding="utf-8")
    return path
