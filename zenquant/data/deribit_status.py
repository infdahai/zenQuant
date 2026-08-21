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
    max_gap_hours_7d: float
    observation_ready: bool
    research_ready: bool
    stale: bool
    blocked_by: str | None
    next_action: str


def _snapshot_times(root: Path, currency: str) -> list[datetime]:
    currency_root = root / currency
    if not currency_root.exists():
        return []
    found: list[datetime] = []
    for path in currency_root.rglob("options_chain_*.parquet"):
        match = _SNAPSHOT_RE.search(path.name)
        if not match:
            continue
        found.append(datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))
    found.sort()
    return found


def collect_deribit_status(root: Path, currency: str = "BTC") -> DeribitCollectorStatus:
    times = _snapshot_times(root, currency)
    if not times:
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
            max_gap_hours_7d=0.0,
            observation_ready=False,
            research_ready=False,
            stale=True,
            blocked_by="no_deribit_snapshots_found",
            next_action="start_collector",
        )

    now = datetime.now(UTC)
    last = times[-1]
    snapshot_count_24h = sum(1 for ts in times if (now - ts).total_seconds() <= 24 * 3600)
    snapshot_count_7d = sum(1 for ts in times if (now - ts).total_seconds() <= 7 * 24 * 3600)
    recent_7d = [ts for ts in times if (now - ts).total_seconds() <= 7 * 24 * 3600]
    max_gap_hours_7d = 0.0
    for prev, curr in pairwise(recent_7d):
        max_gap_hours_7d = max(max_gap_hours_7d, round((curr - prev).total_seconds() / 3600.0, 2))
    stale = (now - last).total_seconds() > _STALE_HOURS * 3600
    observation_ready = snapshot_count_7d >= 35 and max_gap_hours_7d <= _STALE_HOURS and not stale
    research_ready = snapshot_count_7d >= 150 and max_gap_hours_7d <= _STALE_HOURS and not stale
    blocked_by = None if research_ready else "deribit_sample_window_incomplete"
    if stale:
        blocked_by = "deribit_collector_stale"
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
        max_gap_hours_7d=max_gap_hours_7d,
        observation_ready=observation_ready,
        research_ready=research_ready,
        stale=stale,
        blocked_by=blocked_by,
        next_action="keep_collector_running" if not stale else "restart_collector",
    )


def write_deribit_status(root: Path, currency: str = "BTC") -> Path:
    status = collect_deribit_status(root, currency)
    path = root / currency / "_collector_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(status), indent=2), encoding="utf-8")
    return path
