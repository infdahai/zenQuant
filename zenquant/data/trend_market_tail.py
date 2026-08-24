"""Build an isolated, exact-window perpetual/funding corpus for trend research."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd

FIVE_MINUTES_NS: Final = 300_000_000_000
EIGHT_HOURS_NS: Final = 28_800_000_000_000
FUNDING_TOLERANCE_NS: Final = 60_000_000_000
PERP_SOURCE_ID: Final = "BINANCE_PERPETUAL_BTCUSDT_5M"
FUNDING_SOURCE_ID: Final = "BINANCE_PERPETUAL_BTCUSDT_FUNDING"

PageFetcher = Callable[[int, int, int], Sequence[object]]


class MarketTailError(ValueError):
    """Raised when the fixed market tail cannot be prepared exactly."""


@dataclass(frozen=True, slots=True)
class MarketTailInputs:
    """Explicit immutable inputs for one isolated market-tail publication."""

    perp_source_path: Path
    funding_source_path: Path
    output_root: Path
    start_ns: int
    end_ns: int
    repository_root: Path
    symbol: str = "BTCUSDT"

    def __post_init__(self) -> None:
        object.__setattr__(self, "perp_source_path", Path(self.perp_source_path))
        object.__setattr__(self, "funding_source_path", Path(self.funding_source_path))
        object.__setattr__(self, "output_root", Path(self.output_root))
        object.__setattr__(self, "repository_root", Path(self.repository_root).resolve())
        if self.symbol != "BTCUSDT":
            raise MarketTailError("only the frozen BTCUSDT research symbol is supported")
        if self.end_ns <= self.start_ns:
            raise MarketTailError("market-tail window must be increasing")
        if self.start_ns % EIGHT_HOURS_NS or self.end_ns % EIGHT_HOURS_NS:
            raise MarketTailError("market-tail window must align to 8h funding anchors")

    @property
    def perp_output_path(self) -> Path:
        return self.output_root / "5m/data.parquet"

    @property
    def funding_output_path(self) -> Path:
        return self.output_root / "funding/data.parquet"

    @property
    def provenance_path(self) -> Path:
        return self.output_root / "market_tail_provenance.json"


def prepare_market_tail(
    inputs: MarketTailInputs,
    *,
    fetch_perp_page: PageFetcher,
    fetch_funding_page: PageFetcher,
    page_limit: int = 1000,
) -> dict[str, Any]:
    """Prepare exact grids and atomically publish them with immutable provenance."""

    if page_limit <= 0:
        raise MarketTailError("page_limit must be positive")
    publication_targets = (inputs.perp_output_path.parent, inputs.funding_output_path.parent, inputs.provenance_path)
    existing_targets = [str(path) for path in publication_targets if path.exists()]
    if existing_targets:
        raise MarketTailError(f"immutable market-tail output already exists: {existing_targets}")
    source_before = {
        PERP_SOURCE_ID: _sha256_file(inputs.perp_source_path),
        FUNDING_SOURCE_ID: _sha256_file(inputs.funding_source_path),
    }
    rest_pages: list[dict[str, Any]] = []
    perp = _prepare_perp(inputs, fetch_perp_page, page_limit, rest_pages)
    funding = _prepare_funding(inputs, fetch_funding_page, page_limit, rest_pages)
    source_after = {
        PERP_SOURCE_ID: _sha256_file(inputs.perp_source_path),
        FUNDING_SOURCE_ID: _sha256_file(inputs.funding_source_path),
    }
    if source_before != source_after:
        raise MarketTailError("canonical source mutated during market-tail preparation")

    inputs.output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{inputs.output_root.name}.", dir=inputs.output_root.parent))
    try:
        perp_path = staging / "5m/data.parquet"
        funding_path = staging / "funding/data.parquet"
        perp_path.parent.mkdir(parents=True)
        funding_path.parent.mkdir(parents=True)
        perp.to_parquet(perp_path, index=False)
        funding.to_parquet(funding_path, index=False)
        _validate_perp_grid(perp, inputs.start_ns, inputs.end_ns)
        _validate_funding_grid(funding, inputs.start_ns, inputs.end_ns)
        sources = {
            PERP_SOURCE_ID: {
                "path": str(inputs.perp_source_path),
                "sha256": source_before[PERP_SOURCE_ID],
            },
            FUNDING_SOURCE_ID: {
                "path": str(inputs.funding_source_path),
                "sha256": source_before[FUNDING_SOURCE_ID],
            },
        }
        outputs = {
            PERP_SOURCE_ID: {
                "path": str(inputs.perp_output_path),
                "sha256": _sha256_file(perp_path),
                "row_count": len(perp),
            },
            FUNDING_SOURCE_ID: {
                "path": str(inputs.funding_output_path),
                "sha256": _sha256_file(funding_path),
                "row_count": len(funding),
            },
        }
        module_path = inputs.repository_root / "zenquant/data/trend_market_tail.py"
        provenance: dict[str, Any] = {
            "schema_version": "trend-market-tail-provenance.v1",
            "symbol": inputs.symbol,
            "window": [inputs.start_ns, inputs.end_ns],
            "sources": sources,
            "outputs": outputs,
            "rest_pages": rest_pages,
            "normalizer": {
                "callable": "zenquant.data.trend_market_tail.prepare_market_tail",
                "sha256": _sha256_file(module_path),
            },
            "repository": {"head": _repository_head(inputs.repository_root)},
        }
        (staging / "market_tail_provenance.json").write_text(_canonical_json(provenance), encoding="utf-8")
        inputs.output_root.mkdir(parents=True, exist_ok=True)
        published: list[Path] = []
        try:
            for source, target in (
                (staging / "5m", inputs.perp_output_path.parent),
                (staging / "funding", inputs.funding_output_path.parent),
                (staging / "market_tail_provenance.json", inputs.provenance_path),
            ):
                os.replace(source, target)
                published.append(target)
        except Exception:
            for target in reversed(published):
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            try:
                inputs.output_root.rmdir()
            except OSError:
                pass
            raise
        return provenance
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _prepare_perp(
    inputs: MarketTailInputs,
    fetch_page: PageFetcher,
    page_limit: int,
    rest_pages: list[dict[str, Any]],
) -> pd.DataFrame:
    try:
        frame = pd.read_parquet(inputs.perp_source_path)
    except (OSError, ValueError) as exc:
        raise MarketTailError(f"cannot read canonical perpetual source: {exc}") from exc
    required = {
        "open_time_ns",
        "close_time_ns",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    if not required.issubset(frame.columns):
        raise MarketTailError(f"canonical perpetual source missing columns: {sorted(required - set(frame.columns))}")
    frame = frame[(frame["open_time_ns"] >= inputs.start_ns) & (frame["open_time_ns"] < inputs.end_ns)].copy()
    expected = list(range(inputs.start_ns, inputs.end_ns, FIVE_MINUTES_NS))
    missing = sorted(set(expected).difference(int(value) for value in frame["open_time_ns"]))
    for chunk in _consecutive_chunks(missing, FIVE_MINUTES_NS, page_limit):
        start_ms = chunk[0] // 1_000_000
        end_ms = (chunk[-1] + FIVE_MINUTES_NS) // 1_000_000
        payload = list(fetch_page(start_ms, end_ms, len(chunk)))
        _record_page(rest_pages, PERP_SOURCE_ID, start_ms, end_ms, len(chunk), payload)
        try:
            rows = [_parse_perp_rest_row(item) for item in payload]
        except (IndexError, TypeError, ValueError) as exc:
            raise MarketTailError(f"invalid perpetual REST page: {exc}") from exc
        wanted = set(chunk)
        rows = [row for row in rows if int(row["open_time_ns"]) in wanted]
        frame = pd.concat((frame, pd.DataFrame(rows)), ignore_index=True)
    frame = frame.sort_values("open_time_ns", kind="stable").reset_index(drop=True)
    _validate_perp_grid(frame, inputs.start_ns, inputs.end_ns)
    return frame


def _prepare_funding(
    inputs: MarketTailInputs,
    fetch_page: PageFetcher,
    page_limit: int,
    rest_pages: list[dict[str, Any]],
) -> pd.DataFrame:
    try:
        raw = pd.read_parquet(inputs.funding_source_path)
    except (OSError, ValueError) as exc:
        raise MarketTailError(f"cannot read canonical funding source: {exc}") from exc
    if not {"funding_time_ns", "funding_rate"}.issubset(raw.columns):
        raise MarketTailError("canonical funding source missing funding_time_ns or funding_rate")
    rows = _normalize_funding_rows(raw.to_dict("records"), inputs.start_ns, inputs.end_ns)
    expected = list(range(inputs.start_ns, inputs.end_ns, EIGHT_HOURS_NS))
    missing = sorted(set(expected).difference(int(row["funding_time_ns"]) for row in rows))
    for chunk in _consecutive_chunks(missing, EIGHT_HOURS_NS, page_limit):
        start_ms = chunk[0] // 1_000_000
        end_ms = (chunk[-1] + EIGHT_HOURS_NS) // 1_000_000
        payload = list(fetch_page(start_ms, end_ms, len(chunk)))
        _record_page(rest_pages, FUNDING_SOURCE_ID, start_ms, end_ms, len(chunk), payload)
        try:
            rows.extend(_normalize_funding_rows(payload, inputs.start_ns, inputs.end_ns))
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketTailError(f"invalid funding REST page: {exc}") from exc
    frame = pd.DataFrame(rows).sort_values("funding_time_ns", kind="stable").reset_index(drop=True)
    _validate_funding_grid(frame, inputs.start_ns, inputs.end_ns)
    return frame


def _normalize_funding_rows(rows: Sequence[Mapping[str, Any]], start_ns: int, end_ns: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if "funding_time_ns" in row:
            raw_ns = int(row["funding_time_ns"])
        else:
            raw_ms = row.get("fundingTime", row.get("funding_time"))
            if raw_ms is None:
                raise ValueError("funding timestamp is missing")
            raw_ns = int(raw_ms) * 1_000_000
        anchor = raw_ns - raw_ns % EIGHT_HOURS_NS
        if not (start_ns <= anchor < end_ns):
            continue
        if not 0 <= raw_ns - anchor <= FUNDING_TOLERANCE_NS:
            raise MarketTailError("funding timestamp must be within 60 seconds after its 8h anchor")
        rate = float(row.get("funding_rate", row.get("fundingRate")))
        mark = float(row.get("mark_price", row.get("markPrice", 0.0)))
        normalized.append(
            {
                "symbol": str(row.get("symbol", "BTCUSDT")),
                "funding_time_ns": anchor,
                "raw_funding_time_ns": raw_ns,
                "funding_rate": rate,
                "mark_price": mark,
            }
        )
    return normalized


def _parse_perp_rest_row(item: object) -> dict[str, int | float]:
    if not isinstance(item, Sequence) or isinstance(item, str | bytes) or len(item) < 11:
        raise ValueError("row must be a Binance kline array")
    open_ms = int(item[0])
    close_ms = int(item[6])
    return {
        "open_time": open_ms,
        "open": float(item[1]),
        "high": float(item[2]),
        "low": float(item[3]),
        "close": float(item[4]),
        "volume": float(item[5]),
        "close_time": close_ms,
        "quote_volume": float(item[7]),
        "trade_count": int(item[8]),
        "taker_buy_base": float(item[9]),
        "taker_buy_quote": float(item[10]),
        "ignore": int(float(item[11])) if len(item) > 11 else 0,
        "open_time_ns": open_ms * 1_000_000,
        "close_time_ns": close_ms * 1_000_000,
    }


def _validate_perp_grid(frame: pd.DataFrame, start_ns: int, end_ns: int) -> None:
    required = ["open_time_ns", "close_time_ns", "open", "high", "low", "close", "volume"]
    if not set(required).issubset(frame.columns):
        raise MarketTailError("perpetual output lacks the required OHLCV contract")
    times = [int(value) for value in frame["open_time_ns"]]
    if times != list(range(start_ns, end_ns, FIVE_MINUTES_NS)):
        raise MarketTailError("perpetual output is not the exact 5m grid")
    numeric = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    for open_ns, cutoff_value, values in zip(
        frame["open_time_ns"], frame["close_time_ns"], numeric.itertuples(index=False, name=None), strict=True
    ):
        open_price, high, low, close, volume = (float(value) for value in values)
        cutoff = int(cutoff_value)
        open_time = int(open_ns)
        if cutoff not in {open_time + FIVE_MINUTES_NS - 1, open_time + FIVE_MINUTES_NS - 1_000_000}:
            raise MarketTailError("perpetual close cutoff is invalid")
        if (
            not all(math.isfinite(value) for value in (open_price, high, low, close, volume))
            or min(open_price, high, low, close) <= 0
            or volume < 0
            or high < max(open_price, close)
            or low > min(open_price, close)
            or high < low
        ):
            raise MarketTailError("perpetual OHLCV value contract failed")


def _validate_funding_grid(frame: pd.DataFrame, start_ns: int, end_ns: int) -> None:
    required = {"funding_time_ns", "raw_funding_time_ns", "funding_rate"}
    if not required.issubset(frame.columns):
        raise MarketTailError("funding output lacks canonical and raw timestamps")
    times = [int(value) for value in frame["funding_time_ns"]]
    if times != list(range(start_ns, end_ns, EIGHT_HOURS_NS)):
        raise MarketTailError("funding output must contain exactly one event per 8h anchor")
    for anchor, raw_ns, rate in zip(
        frame["funding_time_ns"], frame["raw_funding_time_ns"], frame["funding_rate"], strict=True
    ):
        if not 0 <= int(raw_ns) - int(anchor) <= FUNDING_TOLERANCE_NS:
            raise MarketTailError("funding timestamp must be within 60 seconds after its 8h anchor")
        if not math.isfinite(float(rate)):
            raise MarketTailError("funding rate must be finite")


def _consecutive_chunks(values: Sequence[int], interval_ns: int, limit: int) -> list[list[int]]:
    chunks: list[list[int]] = []
    for value in values:
        if not chunks or len(chunks[-1]) >= limit or value != chunks[-1][-1] + interval_ns:
            chunks.append([value])
        else:
            chunks[-1].append(value)
    return chunks


def _record_page(
    pages: list[dict[str, Any]],
    source_id: str,
    start_ms: int,
    end_ms: int,
    limit: int,
    payload: Sequence[object],
) -> None:
    endpoint = "/fapi/v1/klines" if source_id == PERP_SOURCE_ID else "/fapi/v1/fundingRate"
    params: dict[str, int | str] = {
        "symbol": "BTCUSDT",
        "startTime": start_ms,
        "endTime": end_ms - 1,
        "limit": limit,
    }
    if source_id == PERP_SOURCE_ID:
        params["interval"] = "5m"
    pages.append(
        {
            "source_id": source_id,
            "query": {"endpoint": endpoint, "params": params},
            "payload_sha256": hashlib.sha256(_canonical_json_value(payload).encode()).hexdigest(),
        }
    )


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return _canonical_json_value(payload)


def _canonical_json_value(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository_head(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MarketTailError("repository identity is unavailable") from exc
