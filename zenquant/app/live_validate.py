"""Operational validator for the live paper-trading runtime.

This command does not start the trading process. It validates an already
running ``zenquant-web --mode live`` instance and can optionally watch it long
enough to prove bar-close turnover and audit writes.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class LiveValidationConfig:
    """Validation settings for one monitoring session."""

    base_url: str = "http://127.0.0.1:8090"
    symbol: str = "BTCUSDT_PERP"
    require_db: bool = False
    max_market_age_s: float = 300.0
    poll_interval_s: float = 30.0
    watch_seconds: float = 0.0
    output_path: Path | None = None


def build_live_observation(
    *,
    health: dict[str, Any],
    state_4h: dict[str, Any],
    state_1d: dict[str, Any],
    now_ns: int,
    bar_close_counts: dict[str, int],
) -> dict[str, Any]:
    """Normalize one live runtime snapshot into the validation schema."""
    runtime_status = dict((state_4h or {}).get("runtime_status") or {})
    database = dict(runtime_status.get("database") or {})

    return {
        "ts_ns": now_ns,
        "health_status": (health or {}).get("status"),
        "mode": (health or {}).get("mode"),
        "bootstrap_phase": runtime_status.get("bootstrap_phase", ""),
        "market_connected": bool(runtime_status.get("market_connected")),
        "last_market_event_ns": int(runtime_status.get("last_market_event_ns", 0) or 0),
        "database_enabled": bool(database.get("enabled")),
        "database_connected": bool(database.get("connected")),
        "database_writes": int(database.get("writes", 0) or 0),
        "database_dropped": int(database.get("dropped", 0) or 0),
        "indicator_4h": bool((state_4h or {}).get("latest_indicator")),
        "state_4h": bool((state_4h or {}).get("market_state")),
        "playbook_4h": bool((state_4h or {}).get("active_playbook")),
        "sizing_4h": bool((state_4h or {}).get("latest_sizing")),
        "target_4h": bool((state_4h or {}).get("latest_target")),
        "indicator_1d": bool((state_1d or {}).get("latest_indicator")),
        "state_1d": bool((state_1d or {}).get("market_state")),
        "bias_1d": bool((state_1d or {}).get("directional_bias")),
        "bar_close_count_4h": int(bar_close_counts.get("4h", 0) or 0),
        "bar_close_count_1d": int(bar_close_counts.get("1d", 0) or 0),
    }


def summarize_validation(
    *,
    observations: list[dict[str, Any]],
    bar_close_counts: dict[str, int],
    config: LiveValidationConfig,
) -> dict[str, Any]:
    """Summarize one or more validation observations into operator output."""
    return _summarize(
        observations=observations,
        bar_close_counts=bar_close_counts,
        config=config,
    )


def run_validation(config: LiveValidationConfig) -> dict[str, Any]:
    """Run one validation session and return a JSON-serializable summary."""
    started_ns = time.time_ns()
    deadline_ns = started_ns + int(max(0.0, config.watch_seconds) * 1_000_000_000)
    bar_close_counts = {"4h": 0, "1d": 0}
    seen_cutoffs = {"4h": 0, "1d": 0}
    observations: list[dict[str, Any]] = []

    while True:
        now_ns = time.time_ns()
        health = _fetch_json(f"{config.base_url}/healthz")
        state_4h = _fetch_json(
            f"{config.base_url}/api/state?{urllib.parse.urlencode({'symbol': config.symbol, 'tf': '4h'})}"
        )
        state_1d = _fetch_json(
            f"{config.base_url}/api/state?{urllib.parse.urlencode({'symbol': config.symbol, 'tf': '1d'})}"
        )

        latest_4h_cutoff = int(((state_4h or {}).get("latest_indicator") or {}).get("cutoff_time_ns") or 0)
        latest_1d_cutoff = int(((state_1d or {}).get("latest_indicator") or {}).get("cutoff_time_ns") or 0)

        if latest_4h_cutoff > 0 and latest_4h_cutoff != seen_cutoffs["4h"]:
            if seen_cutoffs["4h"] > 0:
                bar_close_counts["4h"] += 1
            seen_cutoffs["4h"] = latest_4h_cutoff
        if latest_1d_cutoff > 0 and latest_1d_cutoff != seen_cutoffs["1d"]:
            if seen_cutoffs["1d"] > 0:
                bar_close_counts["1d"] += 1
            seen_cutoffs["1d"] = latest_1d_cutoff

        observations.append(
            build_live_observation(
                health=health,
                state_4h=state_4h,
                state_1d=state_1d,
                now_ns=now_ns,
                bar_close_counts=bar_close_counts,
            )
        )

        if now_ns >= deadline_ns or config.watch_seconds <= 0:
            break
        time.sleep(max(1.0, config.poll_interval_s))

    summary = summarize_validation(
        observations=observations,
        bar_close_counts=bar_close_counts,
        config=config,
    )
    if config.output_path is not None:
        config.output_path.parent.mkdir(parents=True, exist_ok=True)
        config.output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _summarize(
    *,
    observations: list[dict[str, Any]],
    bar_close_counts: dict[str, int],
    config: LiveValidationConfig,
) -> dict[str, Any]:
    latest = observations[-1] if observations else {}
    last_event_ns = int(latest.get("last_market_event_ns", 0) or 0)
    market_age_s = max(0.0, (time.time_ns() - last_event_ns) / 1_000_000_000) if last_event_ns > 0 else float("inf")
    failures: list[str] = []

    if latest.get("health_status") != "ok":
        failures.append("healthz_not_ok")
    if latest.get("mode") != "live":
        failures.append("runtime_not_live")
    if not latest.get("market_connected"):
        failures.append("market_not_connected")
    if market_age_s > config.max_market_age_s:
        failures.append("market_feed_stale")
    if not latest.get("indicator_4h"):
        failures.append("missing_4h_indicator")
    if not latest.get("state_4h"):
        failures.append("missing_4h_state")
    if not latest.get("playbook_4h"):
        failures.append("missing_4h_playbook")
    if not latest.get("sizing_4h"):
        failures.append("missing_4h_sizing")
    if not latest.get("target_4h"):
        failures.append("missing_4h_target")
    if not latest.get("indicator_1d"):
        failures.append("missing_1d_indicator")
    if not latest.get("bias_1d"):
        failures.append("missing_1d_bias")

    if config.require_db:
        if not latest.get("database_enabled"):
            failures.append("database_not_enabled")
        if not latest.get("database_connected"):
            failures.append("database_not_connected")
        if int(latest.get("database_writes", 0) or 0) <= 0:
            failures.append("database_no_writes")
        if int(latest.get("database_dropped", 0) or 0) > 0:
            failures.append("database_dropped_writes")

    return {
        "ok": not failures,
        "failures": failures,
        "watch_seconds": config.watch_seconds,
        "poll_interval_s": config.poll_interval_s,
        "symbol": config.symbol,
        "latest": latest,
        "market_age_s": round(market_age_s, 3) if market_age_s != float("inf") else None,
        "bar_close_counts": dict(bar_close_counts),
        "observation_count": len(observations),
    }


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "zenquant/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=10.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"failed to fetch {url}") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a running zenquant live runtime")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--symbol", default="BTCUSDT_PERP")
    parser.add_argument("--require-db", action="store_true")
    parser.add_argument("--max-market-age-s", type=float, default=300.0)
    parser.add_argument("--poll-interval-s", type=float, default=30.0)
    parser.add_argument("--watch-seconds", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    summary = run_validation(
        LiveValidationConfig(
            base_url=args.base_url,
            symbol=args.symbol,
            require_db=args.require_db,
            max_market_age_s=args.max_market_age_s,
            poll_interval_s=args.poll_interval_s,
            watch_seconds=args.watch_seconds,
            output_path=args.output,
        )
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
