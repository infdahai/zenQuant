"""Deterministic repair contracts for the frozen trend-research market tail."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pandas as pd
import pytest

import zenquant.data.trend_market_tail as market_tail
from zenquant.data.trend_market_tail import (
    FIVE_MINUTES_NS,
    FUNDING_COLUMNS,
    PERP_COLUMNS,
    MarketTailError,
    MarketTailInputs,
    prepare_market_tail,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_prepare_cli():
    path = Path("scripts/prepare_trend_market_tail.py")
    spec = importlib.util.spec_from_file_location("prepare_trend_market_tail_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def test_canonical_funding_rejects_wrong_instrument(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    funding = pd.read_parquet(inputs.funding_source_path)
    funding.loc[1, "symbol"] = "ETHUSDT"
    funding.to_parquet(inputs.funding_source_path, index=False)

    with pytest.raises(MarketTailError, match="BTCUSDT"):
        prepare_market_tail(inputs, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])


def test_canonical_source_file_symlink_is_rejected(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    target = tmp_path / "perp-target.parquet"
    inputs.perp_source_path.rename(target)
    inputs.perp_source_path.symlink_to(target)

    with pytest.raises(MarketTailError, match="symlink"):
        prepare_market_tail(inputs, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])


def test_missing_funding_tail_paginates_and_rejects_wrong_rest_instrument(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    funding = pd.read_parquet(inputs.funding_source_path).iloc[0:0]
    funding.to_parquet(inputs.funding_source_path, index=False)
    anchors = [inputs.start_ns, inputs.start_ns + 8 * 60 * 60 * 1_000_000_000]
    calls: list[tuple[int, int, int]] = []

    def wrong_symbol(start_ms: int, end_ms: int, limit: int):
        calls.append((start_ms, end_ms, limit))
        selected = [anchor for anchor in anchors if start_ms <= anchor // 1_000_000 < end_ms][:limit]
        return [
            {"symbol": "ETHUSDT", "fundingTime": anchor // 1_000_000, "fundingRate": "0.0001"} for anchor in selected
        ]

    with pytest.raises(MarketTailError, match="BTCUSDT"):
        prepare_market_tail(
            inputs,
            fetch_perp_page=lambda *_: [],
            fetch_funding_page=wrong_symbol,
            page_limit=1,
        )

    assert calls == [(anchors[0] // 1_000_000, (anchors[0] + 8 * 60 * 60 * 1_000_000_000) // 1_000_000, 1)]


def test_missing_funding_tail_uses_deterministic_one_anchor_pages(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    funding = pd.read_parquet(inputs.funding_source_path).iloc[0:0]
    funding.to_parquet(inputs.funding_source_path, index=False)
    anchors = [inputs.start_ns, inputs.start_ns + 8 * 60 * 60 * 1_000_000_000]
    calls: list[tuple[int, int, int]] = []

    def fetch(start_ms: int, end_ms: int, limit: int):
        calls.append((start_ms, end_ms, limit))
        selected = [anchor for anchor in anchors if start_ms <= anchor // 1_000_000 < end_ms][:limit]
        return [
            {"symbol": "BTCUSDT", "fundingTime": anchor // 1_000_000, "fundingRate": "0.0001"} for anchor in selected
        ]

    provenance = prepare_market_tail(
        inputs,
        fetch_perp_page=lambda *_: [],
        fetch_funding_page=fetch,
        page_limit=1,
    )

    assert len(calls) == 2
    assert [page["inserted_event_ns"] for page in provenance["rest_pages"]] == [[anchors[0]], [anchors[1]]]


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
    assert query["inserted_event_ns"] == [missing_ns]
    assert query["payload"] == [_rest_perp_row(missing_ns)]
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


def test_real_sized_288_bar_tail_gap_is_one_bounded_rest_page(tmp_path: Path) -> None:
    base = _inputs(tmp_path)
    start_ns = base.start_ns
    end_ns = start_ns + 576 * FIVE_MINUTES_NS
    available = list(range(start_ns, start_ns + 288 * FIVE_MINUTES_NS, FIVE_MINUTES_NS))
    pd.DataFrame([_perp_row(ts) for ts in available]).to_parquet(base.perp_source_path, index=False)
    funding_anchors = list(range(start_ns, end_ns, 8 * 60 * 60 * 1_000_000_000))
    pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * len(funding_anchors),
            "funding_time_ns": funding_anchors,
            "funding_rate": [0.0001] * len(funding_anchors),
        }
    ).to_parquet(base.funding_source_path, index=False)
    inputs = MarketTailInputs(
        perp_source_path=base.perp_source_path,
        funding_source_path=base.funding_source_path,
        output_root=tmp_path / "real-tail-derived",
        start_ns=start_ns,
        end_ns=end_ns,
        repository_root=Path.cwd(),
    )
    missing = list(range(start_ns + 288 * FIVE_MINUTES_NS, end_ns, FIVE_MINUTES_NS))
    calls: list[tuple[int, int, int]] = []

    def fetch(start_ms: int, end_ms: int, limit: int):
        calls.append((start_ms, end_ms, limit))
        return [_rest_perp_row(ts) for ts in missing]

    prepare_market_tail(inputs, fetch_perp_page=fetch, fetch_funding_page=lambda *_: [])

    assert calls == [(missing[0] // 1_000_000, end_ns // 1_000_000, 288)]
    assert len(pd.read_parquet(inputs.perp_output_path)) == 576


def test_perp_output_schema_and_dtypes_are_identical_with_or_without_repairs(tmp_path: Path) -> None:
    complete = _inputs(tmp_path / "complete")
    repaired = _inputs(tmp_path / "repaired")
    missing_ns = repaired.start_ns + 7 * FIVE_MINUTES_NS
    source = pd.read_parquet(repaired.perp_source_path)
    source[source["open_time_ns"] != missing_ns].to_parquet(repaired.perp_source_path, index=False)

    prepare_market_tail(complete, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])
    prepare_market_tail(
        repaired,
        fetch_perp_page=lambda *_: [_rest_perp_row(missing_ns)],
        fetch_funding_page=lambda *_: [],
    )

    complete_frame = pd.read_parquet(complete.perp_output_path)
    repaired_frame = pd.read_parquet(repaired.perp_output_path)
    assert tuple(complete_frame.columns) == PERP_COLUMNS
    assert tuple(repaired_frame.columns) == PERP_COLUMNS
    assert (
        complete_frame.dtypes.astype(str).to_dict()
        == repaired_frame.dtypes.astype(str).to_dict()
        == {
            "open_time_ns": "int64",
            "open": "float64",
            "high": "float64",
            "low": "float64",
            "close": "float64",
            "volume": "float64",
            "close_time_ns": "int64",
        }
    )
    assert (complete_frame["close_time_ns"] - complete_frame["open_time_ns"]).unique().tolist() == [
        FIVE_MINUTES_NS - 1_000_000
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
    assert provenance["page_limit"] == 1000
    assert provenance["sources"]["BINANCE_PERPETUAL_BTCUSDT_5M"]["contract"] == {
        "venue": "BINANCE",
        "instrument": "BTCUSDT",
        "market": "PERPETUAL",
        "interval": "5m",
    }
    assert tuple(pd.read_parquet(inputs.funding_output_path).columns) == FUNDING_COLUMNS
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


def test_cli_rejects_fixed_window_source_path_substitution_before_network(tmp_path: Path) -> None:
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
        "2026-08-24T00:00:00Z",
    ]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert "exact canonical BTCUSDT Binance path" in completed.stderr
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


def test_next_run_recovers_only_a_marked_interrupted_publication(tmp_path: Path, monkeypatch) -> None:
    inputs = _inputs(tmp_path)
    spot = inputs.output_root / "1d/data.parquet"
    spot.parent.mkdir(parents=True)
    spot.write_bytes(b"existing immutable spot")
    original_replace = market_tail.os.replace

    def interrupt_after_first_market_file(source: Path, destination: Path) -> None:
        if Path(destination) == inputs.funding_output_path.parent:
            raise KeyboardInterrupt("simulated process interruption")
        original_replace(source, destination)

    monkeypatch.setattr(market_tail.os, "replace", interrupt_after_first_market_file)
    with pytest.raises(KeyboardInterrupt, match="simulated process interruption"):
        prepare_market_tail(inputs, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])
    assert inputs.transaction_marker_path.exists()
    assert inputs.perp_output_path.exists()
    assert not inputs.provenance_path.exists()

    monkeypatch.setattr(market_tail.os, "replace", original_replace)
    prepare_market_tail(inputs, fetch_perp_page=lambda *_: [], fetch_funding_page=lambda *_: [])

    assert spot.read_bytes() == b"existing immutable spot"
    assert inputs.provenance_path.exists()
    assert not inputs.transaction_marker_path.exists()


def test_binance_request_retries_timeout_429_and_5xx_with_bounded_backoff() -> None:
    cli = _load_prepare_cli()
    request = cli.urllib.request.Request("https://fapi.binance.com/fapi/v1/klines")
    failures = [
        TimeoutError("timeout"),
        urllib.error.HTTPError(request.full_url, 429, "limited", {}, None),
        urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, None),
    ]
    sleeps: list[float] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self) -> bytes:
            return b"[]"

    def urlopen(*_, **__):
        if failures:
            raise failures.pop(0)
        return Response()

    payload = cli._request_json_with_retry(request, urlopen=urlopen, sleep=sleeps.append, max_attempts=4)

    assert payload == []
    assert sleeps == [0.25, 0.5, 1.0]


def test_binance_request_rejects_non_list_payload_without_retry() -> None:
    cli = _load_prepare_cli()
    request = cli.urllib.request.Request("https://fapi.binance.com/fapi/v1/fundingRate")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self) -> bytes:
            return b'{"code":-1}'

    with pytest.raises(MarketTailError, match="list"):
        cli._request_json_with_retry(request, urlopen=lambda *_args, **_kwargs: Response(), sleep=lambda _: None)


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("/fapi/v1/klines", [[1, 2]]),
        ("/fapi/v1/fundingRate", [{"symbol": "ETHUSDT", "fundingTime": 1, "fundingRate": "0.1"}]),
        ("/fapi/v1/fundingRate", ["not-an-object"]),
    ],
)
def test_binance_endpoint_payload_rejects_malformed_elements(endpoint: str, payload: list[object]) -> None:
    cli = _load_prepare_cli()

    with pytest.raises(MarketTailError, match="malformed"):
        cli._validate_endpoint_payload(endpoint, payload)
