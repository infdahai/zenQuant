"""BitMEX 2017-2020 replay-base candidate contract.

BitMEX is treated as a candidate source for pre-Binance-futures execution
truth. This contract never promotes it into the formal train/dev/test truth by
itself.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.research.data_coverage_audit import (
    DataSourceSpec,
    audit_source,
)
from zenquant.research.splits import default_btc_split_spec

BITMEX_PUBLIC_ARCHIVE_BASE_URL = "https://s3-eu-west-1.amazonaws.com/public.bitmex.com"
BITMEX_PUBLIC_LISTING_URL = "https://public.bitmex.com/"


@dataclass(slots=True, frozen=True)
class BitmexReplayCandidateContract:
    generated_at: str
    candidate_name: str
    source_name: str
    symbol: str
    normalized_symbol: str
    target_window_start: str
    target_window_end: str
    replay_base_candidate: bool
    formal_train_truth: bool
    promotion_ready: bool
    local_coverage_ready: bool
    official_archive_base_url: str
    official_listing_url: str
    archive_key_pattern: str
    raw_expected_path: str
    normalized_kline_paths: dict[str, str]
    coverage: dict[str, Any]
    allowed_uses: list[str]
    forbidden_uses: list[str]
    blocked_by: list[str]
    source_boundary: str
    next_action: str


def build_bitmex_replay_candidate_contract(
    *,
    data_root: Path = Path("data/raw"),
    symbol: str = "XBTUSD",
    normalized_symbol: str = "BTCUSD",
) -> BitmexReplayCandidateContract:
    source = DataSourceSpec(
        source_id="bitmex_xbtusd_2017_2020_replay_candidate",
        display_name="BitMEX XBTUSD 2017-2020 replay-base candidate",
        source_type="inverse_perp_replay_candidate",
        parquet_globs=(
            str(data_root / "bitmex" / "klines" / symbol / "**" / "*.parquet"),
            str(data_root / "bitmex" / "funding" / symbol / "*.parquet"),
        ),
        csv_globs=(
            str(data_root / "bitmex" / "trade" / symbol / "raw" / "*.csv"),
            str(data_root / "bitmex" / "trade" / symbol / "raw" / "*.csv.gz"),
        ),
        training_role="candidate_replay_base",
        formal_train_enabled=False,
        allowed_uses=("replay_candidate_review", "pre_2020_execution_truth_research"),
        policy_blockers=("candidate_not_promoted", "not_formal_train_truth"),
        timestamp_candidates=("open_time_ns", "close_time_ns", "ts_ns", "timestamp", "date"),
    )
    coverage = audit_source(source, split_spec=default_btc_split_spec())
    combined = coverage["combined_coverage"]
    local_coverage_ready = _covers_target_window(
        combined.get("start_at"),
        combined.get("end_at"),
        target_start="2017-01-01T00:00:00+00:00",
        target_end="2020-01-01T00:00:00+00:00",
    )

    blockers = [
        "candidate_not_promoted",
        "replay_parity_not_verified",
        "funding_and_contract_semantics_not_mapped",
        "source_boundary_not_admitted",
        "do_not_mix_binance_spot_as_execution_truth",
    ]
    if not local_coverage_ready:
        blockers.append("local_2017_2020_coverage_missing")

    return BitmexReplayCandidateContract(
        generated_at=datetime.now(UTC).isoformat(),
        candidate_name="bitmex_xbtusd_2017_2020_replay_base",
        source_name="bitmex_public_archive",
        symbol=symbol,
        normalized_symbol=normalized_symbol,
        target_window_start="2017-01-01T00:00:00+00:00",
        target_window_end="2020-01-01T00:00:00+00:00",
        replay_base_candidate=True,
        formal_train_truth=False,
        promotion_ready=False,
        local_coverage_ready=local_coverage_ready,
        official_archive_base_url=BITMEX_PUBLIC_ARCHIVE_BASE_URL,
        official_listing_url=BITMEX_PUBLIC_LISTING_URL,
        archive_key_pattern="data/trade/YYYYMMDD.csv.gz",
        raw_expected_path=str(data_root / "bitmex" / "trade" / symbol / "raw" / "YYYYMMDD.csv.gz"),
        normalized_kline_paths={
            "1h": str(data_root / "bitmex" / "klines" / symbol / "1h" / "YYYY-MM.parquet"),
            "4h": str(data_root / "bitmex" / "klines" / symbol / "4h" / "YYYY-MM.parquet"),
            "1d": str(data_root / "bitmex" / "klines" / symbol / "1d" / "YYYY-MM.parquet"),
        },
        coverage=coverage,
        allowed_uses=[
            "pre_2020_replay_base_candidate_review",
            "source_independence_check",
            "spot_perp_boundary_audit",
            "research_only_pre_2020_execution_stress",
        ],
        forbidden_uses=[
            "formal_train_truth_without_admission",
            "dev_or_test_selection_feedback",
            "silent_merge_with_binance_spot_context",
            "live_execution_truth",
        ],
        blocked_by=sorted(dict.fromkeys(blockers)),
        source_boundary=(
            "BitMEX XBTUSD can be reviewed as a pre-2020 inverse-perpetual replay-base candidate. "
            "It must remain separate from Binance spot context and from current Binance USDT futures truth until "
            "contract mapping, funding semantics, point-in-time replay, and parity checks pass."
        ),
        next_action="download_or_normalize_bitmex_xbtusd_2017_2020_then_run_replay_parity_review",
    )


def write_bitmex_replay_candidate_contract(
    contract: BitmexReplayCandidateContract,
    *,
    output_dir: Path = Path("results/data_source_review"),
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "bitmex_replay_candidate_contract.json"
    md_path = output_dir / "bitmex_replay_candidate_contract.md"
    payload = asdict(contract)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(build_bitmex_replay_candidate_markdown(payload), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "local_coverage_ready": contract.local_coverage_ready,
        "promotion_ready": contract.promotion_ready,
        "blocked_by": contract.blocked_by,
    }


def build_bitmex_replay_candidate_markdown(payload: dict[str, Any]) -> str:
    coverage = payload["coverage"]["combined_coverage"]
    lines = [
        "# BitMEX Replay Candidate Contract",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- candidate_name: `{payload['candidate_name']}`",
        f"- source_name: `{payload['source_name']}`",
        f"- symbol: `{payload['symbol']}`",
        f"- normalized_symbol: `{payload['normalized_symbol']}`",
        f"- target_window_start: `{payload['target_window_start']}`",
        f"- target_window_end: `{payload['target_window_end']}`",
        f"- replay_base_candidate: `{payload['replay_base_candidate']}`",
        f"- formal_train_truth: `{payload['formal_train_truth']}`",
        f"- promotion_ready: `{payload['promotion_ready']}`",
        f"- local_coverage_ready: `{payload['local_coverage_ready']}`",
        f"- local_start_at: `{coverage['start_at']}`",
        f"- local_end_at: `{coverage['end_at']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
        f"- next_action: `{payload['next_action']}`",
        "",
        "## Source Boundary",
        "",
        f"- {payload['source_boundary']}",
        "",
        "## Allowed Uses",
        "",
    ]
    lines.extend(f"- `{item}`" for item in payload["allowed_uses"])
    lines.extend(["", "## Forbidden Uses", ""])
    lines.extend(f"- `{item}`" for item in payload["forbidden_uses"])
    lines.extend(
        [
            "",
            "## Archive Contract",
            "",
            f"- official_archive_base_url: `{payload['official_archive_base_url']}`",
            f"- archive_key_pattern: `{payload['archive_key_pattern']}`",
            f"- raw_expected_path: `{payload['raw_expected_path']}`",
        ]
    )
    return "\n".join(lines)


def _covers_target_window(
    start_at: str | None,
    end_at: str | None,
    *,
    target_start: str,
    target_end: str,
) -> bool:
    if start_at is None or end_at is None:
        return False
    return _parse_iso(start_at) <= _parse_iso(target_start) and _parse_iso(end_at) >= _parse_iso(target_end)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
