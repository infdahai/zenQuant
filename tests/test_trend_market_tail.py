"""Deterministic repair contracts for the frozen trend-research market tail."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import zenquant.data.trend_market_tail as market_tail
from zenquant.data.trend_market_tail import (
    FIVE_MINUTES_NS,
    MarketTailError,
    MarketTailInputs,
    prepare_market_tail,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _perp_row(open_ns: int, close: float = 105.0) -> dict[str, int | float]:
    return {
        "open_time": open_ns // 1_000_000,
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": close,
        "volume": 10.0,
        "close_time": (open_ns + FIVE_MINUTES_NS - 1_000_000) // 1_000_000,
        "quote_volume": 1_000.0,
        "trade_count": 5,
        "taker_buy_base": 6.0,
        "taker_buy_quote": 600.0,
        "ignore": 0,
        "open_time_ns": open_ns,
        "close_time_ns": open_ns + FIVE_MINUTES_NS - 1_000_000,
    }


def _rest_perp_row(open_ns: int, close: float = 105.0) -> list[object]:
    return [
        open_ns // 1_000_000,
        "100.0",
        "110.0",
        "90.0",
        str(close),
        "10.0",
        (open_ns + FIVE_MINUTES_NS - 1_000_000) // 1_000_000,
        "1000.0",
        5,
        "6.0",
        "600.0",
        "0",
    ]


def _inputs(tmp_path: Path, *, funding_offsets_ms: tuple[int, ...] = (0, 16)) -> MarketTailInputs:
    start_ns = 1_800_000_000_000_000_000
    end_ns = start_ns + 16 * 60 * 60 * 1_000_000_000
    perp_source = tmp_path / "canonical-perp.parquet"
    funding_source = tmp_path / "canonical-funding.parquet"
    pd.DataFrame([_perp_row(ts) for ts in range(start_ns, end_ns, FIVE_MINUTES_NS)]).to_parquet(
        perp_source, index=False
    )
    anchors = (start_ns, start_ns + 8 * 60 * 60 * 1_000_000_000)
    pd.DataFrame(
        {
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "funding_time": [anchor // 1_000_000 + offset for anchor, offset in zip(anchors, funding_offsets_ms)],
            "funding_time_ns": [anchor + offset * 1_000_000 for anchor, offset in zip(anchors, funding_offsets_ms)],
            "funding_rate": [0.0001, -0.0002],
            "mark_price": [100.0, 101.0],
        }
    ).to_parquet(funding_source, index=False)
    return MarketTailInputs(
        perp_source_path=perp_source,
        funding_source_path=funding_source,
        output_root=tmp_path / "derived" / "BTCUSDT",
        start_ns=start_ns,
        end_ns=end_ns,
        repository_root=Path.cwd(),
    )


def test_funding_jitter_is_canonicalized_and_raw_timestamp_is_retained(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    provenance = prepare_market_tail(inputs, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])

    funding = pd.read_parquet(inputs.funding_output_path)
    assert funding["funding_time_ns"].tolist() == [inputs.start_ns, inputs.start_ns + 8 * 60 * 60 * 1_000_000_000]
    assert funding["raw_funding_time_ns"].tolist() == [
        inputs.start_ns,
        inputs.start_ns + 8 * 60 * 60 * 1_000_000_000 + 16_000_000,
    ]
    assert provenance["outputs"]["BINANCE_PERPETUAL_BTCUSDT_FUNDING"]["sha256"] == _sha(inputs.funding_output_path)


def test_funding_normalization_accepts_canonical_nanosecond_schema_without_millisecond_alias(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    funding = pd.read_parquet(inputs.funding_source_path)[["symbol", "funding_time_ns", "funding_rate"]]
    funding.to_parquet(inputs.funding_source_path, index=False)

    prepare_market_tail(inputs, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])

    normalized = pd.read_parquet(inputs.funding_output_path)
    assert normalized["funding_time_ns"].tolist() == [
        inputs.start_ns,
        inputs.start_ns + 8 * 60 * 60 * 1_000_000_000,
    ]


def test_funding_timestamp_more_than_60_seconds_after_anchor_is_rejected_atomically(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path, funding_offsets_ms=(0, 60_001))

    with pytest.raises(MarketTailError, match="60 seconds"):
        prepare_market_tail(inputs, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])

    assert not inputs.output_root.exists()


def test_missing_perp_bar_is_filled_only_from_rest_and_canonical_input_is_unchanged(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    source = pd.read_parquet(inputs.perp_source_path)
    missing_ns = inputs.start_ns + 5 * FIVE_MINUTES_NS
    source[source["open_time_ns"] != missing_ns].to_parquet(inputs.perp_source_path, index=False)
    before = inputs.perp_source_path.read_bytes()
    calls: list[tuple[int, int, int]] = []

    def fetch(start_ms: int, end_ms: int, limit: int):
        calls.append((start_ms, end_ms, limit))
        return [_rest_perp_row(missing_ns)]

    provenance = prepare_market_tail(inputs, fetch_perp_page=fetch, fetch_funding_page=lambda *_: [])

    repaired = pd.read_parquet(inputs.perp_output_path)
    assert repaired["open_time_ns"].tolist() == list(range(inputs.start_ns, inputs.end_ns, FIVE_MINUTES_NS))
    assert calls == [(missing_ns // 1_000_000, (missing_ns + FIVE_MINUTES_NS) // 1_000_000, 1)]
    assert inputs.perp_source_path.read_bytes() == before
    query = provenance["rest_pages"][0]
    assert query["source_id"] == "BINANCE_PERPETUAL_BTCUSDT_5M"
    assert query["query"] == {
        "endpoint": "/fapi/v1/klines",
        "params": {
            "symbol": "BTCUSDT",
            "interval": "5m",
            "startTime": missing_ns // 1_000_000,
            "endTime": (missing_ns + FIVE_MINUTES_NS) // 1_000_000 - 1,
            "limit": 1,
        },
    }
    assert (
        query["payload_sha256"]
        == hashlib.sha256(
            (json.dumps([_rest_perp_row(missing_ns)], separators=(",", ":"), sort_keys=True) + "\n").encode()
        ).hexdigest()
    )


def test_rest_fill_paginates_deterministically(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    missing = [inputs.start_ns + index * FIVE_MINUTES_NS for index in (2, 3, 4)]
    source = pd.read_parquet(inputs.perp_source_path)
    source[~source["open_time_ns"].isin(missing)].to_parquet(inputs.perp_source_path, index=False)
    calls: list[tuple[int, int, int]] = []

    def fetch(start_ms: int, end_ms: int, limit: int):
        calls.append((start_ms, end_ms, limit))
        return [_rest_perp_row(ts) for ts in missing if start_ms <= ts // 1_000_000 < end_ms][:limit]

    prepare_market_tail(
        inputs,
        fetch_perp_page=fetch,
        fetch_funding_page=lambda *_: [],
        page_limit=2,
    )

    assert calls == [
        (missing[0] // 1_000_000, (missing[1] + FIVE_MINUTES_NS) // 1_000_000, 2),
        (missing[2] // 1_000_000, (missing[2] + FIVE_MINUTES_NS) // 1_000_000, 1),
    ]


def test_provenance_binds_source_output_queries_and_repository_without_wall_clock(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    provenance = prepare_market_tail(inputs, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])

    assert provenance["schema_version"] == "trend-market-tail-provenance.v1"
    assert provenance["window"] == [inputs.start_ns, inputs.end_ns]
    assert provenance["sources"]["BINANCE_PERPETUAL_BTCUSDT_5M"]["sha256"] == _sha(inputs.perp_source_path)
    assert provenance["outputs"]["BINANCE_PERPETUAL_BTCUSDT_5M"]["sha256"] == _sha(inputs.perp_output_path)
    assert provenance["repository"]["head"]
    assert provenance["normalizer"]["sha256"]
    assert "created_at" not in json.dumps(provenance)
    assert json.loads(inputs.provenance_path.read_text()) == provenance


def test_bad_rest_page_leaves_no_partial_outputs(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    source = pd.read_parquet(inputs.perp_source_path)
    missing_ns = inputs.start_ns + FIVE_MINUTES_NS
    source[source["open_time_ns"] != missing_ns].to_parquet(inputs.perp_source_path, index=False)

    with pytest.raises(MarketTailError, match="REST page"):
        prepare_market_tail(
            inputs,
            fetch_perp_page=lambda *_: [[missing_ns // 1_000_000]],
            fetch_funding_page=lambda *_: [],
        )

    assert not inputs.output_root.exists()


def test_cli_rejects_dynamic_window_before_network_or_output(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    command = [
        sys.executable,
        "scripts/prepare_trend_market_tail.py",
        "--perp-source",
        str(inputs.perp_source_path),
        "--funding-source",
        str(inputs.funding_source_path),
        "--output-root",
        str(inputs.output_root),
        "--start",
        "2026-01-01T00:00:00Z",
        "--end",
        "latest",
    ]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert "fixed end" in completed.stderr
    assert not inputs.output_root.exists()


def test_publication_rolls_back_all_market_tail_files_and_preserves_existing_spot(tmp_path: Path, monkeypatch) -> None:
    inputs = _inputs(tmp_path)
    spot = inputs.output_root / "1d/data.parquet"
    spot.parent.mkdir(parents=True)
    spot.write_bytes(b"existing immutable spot")
    original_replace = market_tail.os.replace
    calls = 0

    def fail_second_publish(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(market_tail.os, "replace", fail_second_publish)

    with pytest.raises(OSError, match="simulated publication failure"):
        prepare_market_tail(inputs, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])

    assert spot.read_bytes() == b"existing immutable spot"
    assert not inputs.perp_output_path.exists()
    assert not inputs.funding_output_path.exists()
    assert not inputs.provenance_path.exists()
