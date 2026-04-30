"""Persistent paper-live scorecard for operators and the dashboard."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.app.live_validate import (
    LiveValidationConfig,
    build_live_observation,
    run_validation,
    summarize_validation,
)

SCORECARD_ROOT = Path("results/paper_live_scorecards")
SCORECARD_HISTORY_DIR = SCORECARD_ROOT / "history"
SCORECARD_LATEST_PATH = SCORECARD_ROOT / "latest.json"
SCORECARD_REVIEWS_PATH = SCORECARD_ROOT / "reviews.json"
LIVE_EXPORT_INDEX_PATH = Path("results/live_training_runs/index.json")
TWO_WEEKS_SECONDS = 14 * 24 * 60 * 60

_COMPLETED_ITEMS = [
    "历史验证已通过",
    "walk-forward OOS 已通过",
    "historical adversarial 已通过",
    "live runtime / healthz / validate / export 链路可运行",
    "前端 dashboard 已可作为可视化入口",
]

_PENDING_ITEMS = [
    "sustained 2 weeks paper-live",
    "formal paper-live review",
    "live scorecard 历史积累",
    "live vs replay parity 周期性抽检",
    "证明当前策略优于 paulwei_baseline",
    "liquidation 数据 readiness 与 promote 决策",
]


@dataclass(slots=True)
class PaperLiveScorecardConfig:
    """Configuration for one scorecard collection run."""

    base_url: str = "http://127.0.0.1:8090"
    symbol: str = "BTCUSDT_PERP"
    require_db: bool = True
    max_market_age_s: float = 300.0
    poll_interval_s: float = 30.0
    watch_seconds: float = 0.0
    output_root: Path = SCORECARD_ROOT
    record_review: str = ""
    review_summary: str = ""
    review_artifact: str = ""


def build_runtime_scorecard(
    *,
    health: dict[str, Any],
    state_4h: dict[str, Any],
    state_1d: dict[str, Any],
    symbol: str,
    require_db: bool = True,
    max_market_age_s: float = 300.0,
    output_root: Path = SCORECARD_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build an operator-facing scorecard from one in-process runtime snapshot."""
    current_time = now or datetime.now(UTC)
    observation = build_live_observation(
        health=health,
        state_4h=state_4h,
        state_1d=state_1d,
        now_ns=int(current_time.timestamp() * 1_000_000_000),
        bar_close_counts={
            "4h": int(((state_4h or {}).get("runtime_status") or {}).get("bar_close_count_4h", 0) or 0),
            "1d": int(((state_1d or {}).get("runtime_status") or {}).get("bar_close_count_1d", 0) or 0),
        },
    )
    validation = summarize_validation(
        observations=[observation],
        bar_close_counts={
            "4h": int(observation.get("bar_close_count_4h", 0) or 0),
            "1d": int(observation.get("bar_close_count_1d", 0) or 0),
        },
        config=LiveValidationConfig(
            base_url="runtime://local",
            symbol=symbol,
            require_db=require_db,
            max_market_age_s=max_market_age_s,
            watch_seconds=0.0,
        ),
    )
    return build_scorecard_from_validation(
        validation,
        output_root=output_root,
        now=current_time,
    )


def build_scorecard_from_validation(
    validation: dict[str, Any],
    *,
    output_root: Path = SCORECARD_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Merge live validation output with persisted history, exports, and reviews."""
    current_time = now or datetime.now(UTC)
    checked_at = current_time.astimezone(UTC).isoformat()
    history_records = load_scorecard_history(output_root)
    latest_export = load_latest_export()
    latest_review = load_latest_review(output_root)

    current_record = {
        "checked_at": checked_at,
        "healthy": bool(validation.get("ok")),
        "failures": list(validation.get("failures") or []),
        "market_age_s": validation.get("market_age_s"),
        "bar_close_counts": dict(validation.get("bar_close_counts") or {}),
        "latest": dict(validation.get("latest") or {}),
        "observation_count": int(validation.get("observation_count", 0) or 0),
        "watch_seconds": float(validation.get("watch_seconds", 0.0) or 0.0),
    }

    combined = history_records + [current_record]
    windows = {
        "24h": _window_stats(combined, hours=24, now=current_time),
        "7d": _window_stats(combined, hours=24 * 7, now=current_time),
    }
    streak = _healthy_streak(combined)
    last_failure = _latest_failure(combined)

    complete = bool(
        current_record["healthy"]
        and streak["duration_seconds"] >= TWO_WEEKS_SECONDS
        and latest_export is not None
        and latest_review is not None
        and latest_review.get("verdict") in {"PASS", "APPROVED", "COMPLETE"}
    )

    if complete:
        status_label = "paper trading complete"
    elif history_records:
        status_label = "paper trading in progress"
    else:
        status_label = "paper-live started"

    return {
        "checked_at": checked_at,
        "healthy": current_record["healthy"],
        "status_label": status_label,
        "paper_live_started": bool(combined),
        "paper_trading_in_progress": bool(combined) and not complete,
        "paper_trading_complete": complete,
        "failures": current_record["failures"],
        "validation": validation,
        "current": current_record,
        "sustained_run": {
            "healthy_duration_seconds": streak["duration_seconds"],
            "healthy_duration_hours": round(streak["duration_seconds"] / 3600.0, 2),
            "healthy_started_at": streak["started_at"],
            "last_failure_at": last_failure.get("checked_at"),
            "last_failure_reasons": list(last_failure.get("failures") or []),
        },
        "pass_rates": windows,
        "latest_export": latest_export,
        "latest_review": latest_review,
        "completed_items": list(_COMPLETED_ITEMS),
        "pending_items": list(_PENDING_ITEMS),
    }


def collect_scorecard(config: PaperLiveScorecardConfig) -> dict[str, Any]:
    """Run live validation, merge context, and persist a scorecard record."""
    validation = run_validation(
        LiveValidationConfig(
            base_url=config.base_url,
            symbol=config.symbol,
            require_db=config.require_db,
            max_market_age_s=config.max_market_age_s,
            poll_interval_s=config.poll_interval_s,
            watch_seconds=config.watch_seconds,
        )
    )
    if config.record_review.strip():
        persist_review(
            output_root=config.output_root,
            verdict=config.record_review.strip().upper(),
            summary=config.review_summary.strip(),
            artifact_path=config.review_artifact.strip(),
        )
    scorecard = build_scorecard_from_validation(validation, output_root=config.output_root)
    persist_scorecard(scorecard, output_root=config.output_root)
    return scorecard


def persist_scorecard(scorecard: dict[str, Any], *, output_root: Path = SCORECARD_ROOT) -> Path:
    """Write one timestamped scorecard record and update the latest pointer."""
    history_dir = output_root / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = _safe_stamp(str(scorecard.get("checked_at") or datetime.now(UTC).isoformat()))
    path = history_dir / f"{stamp}.json"
    path.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "latest.json").write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    return path


def persist_review(
    *,
    output_root: Path,
    verdict: str,
    summary: str,
    artifact_path: str = "",
) -> dict[str, Any]:
    """Append one formal review record for scorecard readiness tracking."""
    output_root.mkdir(parents=True, exist_ok=True)
    record = {
        "reviewed_at": datetime.now(UTC).isoformat(),
        "verdict": verdict,
        "summary": summary,
        "artifact_path": artifact_path,
    }
    existing = {"reviews": []}
    review_path = output_root / "reviews.json"
    if review_path.exists():
        existing = json.loads(review_path.read_text(encoding="utf-8"))
    reviews = list(existing.get("reviews") or [])
    reviews.append(record)
    review_path.write_text(json.dumps({"reviews": reviews}, indent=2), encoding="utf-8")
    return record


def load_scorecard_history(output_root: Path = SCORECARD_ROOT) -> list[dict[str, Any]]:
    """Load persisted scorecard history ordered by time."""
    history_dir = output_root / "history"
    if not history_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(history_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        current = dict(payload.get("current") or {})
        if current:
            records.append(current)
    records.sort(key=lambda item: str(item.get("checked_at") or ""))
    return records


def load_scorecard_history_payloads(output_root: Path = SCORECARD_ROOT, limit: int = 50) -> list[dict[str, Any]]:
    """Load full persisted scorecard payloads for the history API."""
    history_dir = output_root / "history"
    if not history_dir.exists():
        return []
    payloads: list[dict[str, Any]] = []
    for path in sorted(history_dir.glob("*.json"))[-limit:]:
        payloads.append(json.loads(path.read_text(encoding="utf-8")))
    return payloads


def load_latest_review(output_root: Path = SCORECARD_ROOT) -> dict[str, Any] | None:
    """Return the latest formal paper-live review record, if present."""
    path = output_root / "reviews.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    reviews = list(payload.get("reviews") or [])
    return reviews[-1] if reviews else None


def load_latest_export() -> dict[str, Any] | None:
    """Return the latest live export manifest summary from the index."""
    if not LIVE_EXPORT_INDEX_PATH.exists():
        return None
    payload = json.loads(LIVE_EXPORT_INDEX_PATH.read_text(encoding="utf-8"))
    runs = list(payload.get("runs") or [])
    if not runs:
        return None
    latest = dict(runs[-1])
    run_id = str(latest.get("run_id") or "")
    if run_id:
        latest["run_dir"] = str(LIVE_EXPORT_INDEX_PATH.parent / run_id)
    return latest


def _window_stats(records: list[dict[str, Any]], *, hours: int, now: datetime) -> dict[str, Any]:
    cutoff = now.timestamp() - (hours * 3600)
    window = [
        record
        for record in records
        if _parse_iso(record.get("checked_at")) is not None
        and _parse_iso(record.get("checked_at")).timestamp() >= cutoff
    ]
    if not window:
        return {"window_hours": hours, "observations": 0, "healthy_observations": 0, "pass_rate": None}
    healthy = sum(1 for record in window if record.get("healthy"))
    return {
        "window_hours": hours,
        "observations": len(window),
        "healthy_observations": healthy,
        "pass_rate": round(healthy / len(window), 4),
    }


def _healthy_streak(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"started_at": None, "duration_seconds": 0}
    latest = records[-1]
    if not latest.get("healthy"):
        return {"started_at": None, "duration_seconds": 0}

    start_record = latest
    for record in reversed(records):
        if not record.get("healthy"):
            break
        start_record = record

    start_dt = _parse_iso(start_record.get("checked_at"))
    end_dt = _parse_iso(latest.get("checked_at"))
    if start_dt is None or end_dt is None:
        return {"started_at": None, "duration_seconds": 0}
    return {
        "started_at": start_dt.astimezone(UTC).isoformat(),
        "duration_seconds": max(0, int((end_dt - start_dt).total_seconds())),
    }


def _latest_failure(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in reversed(records):
        if not record.get("healthy"):
            return record
    return {}


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    cleaned = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_stamp(value: str) -> str:
    return value.replace(":", "").replace("+00:00", "Z").replace("-", "").replace(".", "_")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect and persist a paper-live scorecard")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--symbol", default="BTCUSDT_PERP")
    parser.add_argument("--require-db", action="store_true", default=True)
    parser.add_argument("--max-market-age-s", type=float, default=300.0)
    parser.add_argument("--poll-interval-s", type=float, default=30.0)
    parser.add_argument("--watch-seconds", type=float, default=0.0)
    parser.add_argument("--output-root", type=Path, default=SCORECARD_ROOT)
    parser.add_argument("--record-review", default="", help="Optional formal review verdict such as PASS or FAIL")
    parser.add_argument("--review-summary", default="", help="One-line operator review summary")
    parser.add_argument("--review-artifact", default="", help="Optional path to the review artifact")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    scorecard = collect_scorecard(
        PaperLiveScorecardConfig(
            base_url=args.base_url,
            symbol=args.symbol,
            require_db=args.require_db,
            max_market_age_s=args.max_market_age_s,
            poll_interval_s=args.poll_interval_s,
            watch_seconds=args.watch_seconds,
            output_root=args.output_root,
            record_review=args.record_review,
            review_summary=args.review_summary,
            review_artifact=args.review_artifact,
        )
    )
    print(json.dumps(scorecard, indent=2))
    return 0 if bool(scorecard.get("healthy")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
