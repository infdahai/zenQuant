"""Historical extension candidate contract helpers.

This module keeps early spot history explicitly separate from the formal
futures-backed train/dev/test truth. The artifact is a review contract, not a
promotion decision.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(slots=True, frozen=True)
class TimeframeCoverage:
    timeframe: str
    present: bool
    row_count: int
    start_at: str | None
    end_at: str | None


@dataclass(slots=True, frozen=True)
class HistoricalExtensionCandidateContract:
    generated_at: str
    symbol: str
    candidate_name: str
    candidate_source_type: str
    primary_truth_source: str
    formal_train_truth: bool
    candidate_window_start: str | None
    candidate_window_end: str | None
    timeframes: dict[str, TimeframeCoverage]
    allowed_uses: list[str]
    blocked_by: list[str]
    ready: bool
    source_boundary: str
    next_action: str


def _load_timeframe_coverage(path: Path) -> TimeframeCoverage:
    if not path.exists():
        return TimeframeCoverage(
            timeframe=path.parent.name,
            present=False,
            row_count=0,
            start_at=None,
            end_at=None,
        )
    frame = pd.read_parquet(path)
    if frame.empty:
        return TimeframeCoverage(
            timeframe=path.parent.name,
            present=False,
            row_count=0,
            start_at=None,
            end_at=None,
        )
    ts_column = "close_time_ns" if "close_time_ns" in frame.columns else "ts_ns"
    timestamps = pd.to_datetime(frame[ts_column], unit="ns", utc=True)
    return TimeframeCoverage(
        timeframe=path.parent.name,
        present=True,
        row_count=len(frame),
        start_at=timestamps.min().isoformat(),
        end_at=timestamps.max().isoformat(),
    )


def _date_floor(value: str | None) -> str | None:
    if value is None:
        return None
    return value.split("T")[0]


def build_historical_extension_candidate_contract(
    *,
    symbol: str,
    spot_root: Path = Path("data/raw/binance_spot"),
    futures_root: Path = Path("data/raw/binance"),
) -> HistoricalExtensionCandidateContract:
    spot_coverages = {
        timeframe: _load_timeframe_coverage(spot_root / "klines" / symbol / timeframe / "data.parquet")
        for timeframe in ("1d", "4h", "1h")
    }
    futures_one_hour = _load_timeframe_coverage(futures_root / "klines" / symbol / "1h" / "data.parquet")

    candidate_window_start = min(
        (coverage.start_at for coverage in spot_coverages.values() if coverage.start_at is not None),
        default=None,
    )
    candidate_window_end = max(
        (coverage.end_at for coverage in spot_coverages.values() if coverage.end_at is not None),
        default=None,
    )

    blocked_by: list[str] = []
    if not all(coverage.present for coverage in spot_coverages.values()):
        blocked_by.append("spot_timeframe_gap")
    blocked_by.extend(
        [
            "spot_perp_truth_boundary",
            "funding_history_not_available_pre_2020",
            "open_interest_history_not_available_pre_2020",
            "liquidation_history_not_available_pre_2020",
            "execution_parity_not_verified_pre_2020",
        ]
    )
    if futures_one_hour.start_at is None:
        blocked_by.append("futures_1h_missing")
    elif (_date_floor(futures_one_hour.start_at) or "9999-12-31") > "2020-01-01":
        blocked_by.append("futures_1h_incomplete_current_truth")

    return HistoricalExtensionCandidateContract(
        generated_at=datetime.now(UTC).isoformat(),
        symbol=symbol,
        candidate_name="binance_spot_2017_plus",
        candidate_source_type="spot",
        primary_truth_source="binance_futures_2020_plus",
        formal_train_truth=False,
        candidate_window_start=candidate_window_start,
        candidate_window_end=candidate_window_end,
        timeframes=spot_coverages,
        allowed_uses=[
            "cycle_regime_research",
            "slow_macro_signal_validation",
            "train_candidate_replay_contract_review",
            "pre_train_stress_tests",
        ],
        blocked_by=blocked_by,
        ready=False,
        source_boundary=(
            "Candidate history uses Binance spot data from 2017-08 onward. "
            "Formal train truth remains Binance futures from 2020 onward."
        ),
        next_action="keep_as_train_candidate_until_parity_review",
    )


def write_historical_extension_candidate_contract(
    contract: HistoricalExtensionCandidateContract,
    *,
    output_dir: Path = Path("results/data_source_review"),
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "historical_extension_candidate_contract.json"
    md_path = output_dir / "historical_extension_candidate_contract.md"
    payload = asdict(contract)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# Historical Extension Candidate Contract",
        "",
        f"- generated_at: `{contract.generated_at}`",
        f"- symbol: `{contract.symbol}`",
        f"- candidate_name: `{contract.candidate_name}`",
        f"- candidate_source_type: `{contract.candidate_source_type}`",
        f"- primary_truth_source: `{contract.primary_truth_source}`",
        f"- formal_train_truth: `{contract.formal_train_truth}`",
        f"- candidate_window_start: `{contract.candidate_window_start}`",
        f"- candidate_window_end: `{contract.candidate_window_end}`",
        f"- ready: `{contract.ready}`",
        f"- blocked_by: `{contract.blocked_by}`",
        f"- next_action: `{contract.next_action}`",
        "",
        "## Timeframes",
        "",
    ]
    for coverage in contract.timeframes.values():
        lines.extend(
            [
                f"### {coverage.timeframe}",
                "",
                f"- present: `{coverage.present}`",
                f"- row_count: `{coverage.row_count}`",
                f"- start_at: `{coverage.start_at}`",
                f"- end_at: `{coverage.end_at}`",
                "",
            ]
        )
    lines.extend(["## Source Boundary", "", f"- {contract.source_boundary}", "", "## Allowed Uses", ""])
    lines.extend(f"- `{item}`" for item in contract.allowed_uses)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "ready": contract.ready,
        "blocked_by": contract.blocked_by,
    }


def load_historical_extension_candidate_contract(
    *, output_dir: Path = Path("results/data_source_review")
) -> dict[str, Any]:
    path = output_dir / "historical_extension_candidate_contract.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
