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
PERP_COLUMNS: Final = (
    "open_time_ns",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_ns",
)
FUNDING_COLUMNS: Final = (
    "symbol",
    "funding_time_ns",
    "raw_funding_time_ns",
    "funding_rate",
    "mark_price",
)
SOURCE_CONTRACTS: Final = {
    PERP_SOURCE_ID: {
        "venue": "BINANCE",
        "instrument": "BTCUSDT",
        "market": "PERPETUAL",
        "interval": "5m",
    },
    FUNDING_SOURCE_ID: {
        "venue": "BINANCE",
        "instrument": "BTCUSDT",
        "market": "PERPETUAL_FUNDING",
        "interval": "8h",
    },
}

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

    @property
    def transaction_marker_path(self) -> Path:
        return self.output_root / ".market_tail_transaction.json"


def prepare_market_tail(
    inputs: MarketTailInputs,
    *,
    fetch_perp_page: PageFetcher,
    fetch_funding_page: PageFetcher,
    page_limit: int = 1000,
) -> dict[str, Any]:
    """Prepare exact grids and atomically publish them with immutable provenance."""

    if not 0 < page_limit <= 1000:
        raise MarketTailError("page_limit must be in [1, 1000]")
    for source_id, source_path in (
        (PERP_SOURCE_ID, inputs.perp_source_path),
        (FUNDING_SOURCE_ID, inputs.funding_source_path),
    ):
        if source_path.is_symlink():
            raise MarketTailError(f"canonical source file may not be a symlink: {source_id}")
    _recover_orphaned_publication(inputs)
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
                "resolved_path": str(inputs.perp_source_path.resolve()),
                "sha256": source_before[PERP_SOURCE_ID],
                "contract": SOURCE_CONTRACTS[PERP_SOURCE_ID],
            },
            FUNDING_SOURCE_ID: {
                "path": str(inputs.funding_source_path),
                "resolved_path": str(inputs.funding_source_path.resolve()),
                "sha256": source_before[FUNDING_SOURCE_ID],
                "contract": SOURCE_CONTRACTS[FUNDING_SOURCE_ID],
            },
        }
        outputs = {
            PERP_SOURCE_ID: {
                "path": str(inputs.perp_output_path),
                "sha256": _sha256_file(perp_path),
                "row_count": len(perp),
                "expected_row_count": (inputs.end_ns - inputs.start_ns) // FIVE_MINUTES_NS,
                "columns": list(PERP_COLUMNS),
            },
            FUNDING_SOURCE_ID: {
                "path": str(inputs.funding_output_path),
                "sha256": _sha256_file(funding_path),
                "row_count": len(funding),
                "expected_row_count": (inputs.end_ns - inputs.start_ns) // EIGHT_HOURS_NS,
                "columns": list(FUNDING_COLUMNS),
            },
        }
        module_path = inputs.repository_root / "zenquant/data/trend_market_tail.py"
        provenance: dict[str, Any] = {
            "schema_version": "trend-market-tail-provenance.v1",
            "symbol": inputs.symbol,
            "window": [inputs.start_ns, inputs.end_ns],
            "page_limit": page_limit,
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
        marker = _transaction_marker(inputs, staging, outputs)
        (staging / ".market_tail_transaction.json").write_text(_canonical_json(marker), encoding="utf-8")
        inputs.output_root.mkdir(parents=True, exist_ok=True)
        published: list[Path] = []
        try:
            for source, target in (
                (staging / ".market_tail_transaction.json", inputs.transaction_marker_path),
                (staging / "5m", inputs.perp_output_path.parent),
                (staging / "funding", inputs.funding_output_path.parent),
                (staging / "market_tail_provenance.json", inputs.provenance_path),
            ):
                os.replace(source, target)
                published.append(target)
            inputs.transaction_marker_path.unlink()
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


def _transaction_marker(
    inputs: MarketTailInputs, staging: Path, outputs: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": "trend-market-tail-transaction.v1",
        "window": [inputs.start_ns, inputs.end_ns],
        "staging_path": str(staging.resolve()),
        "targets": {
            "5m/data.parquet": outputs[PERP_SOURCE_ID]["sha256"],
            "funding/data.parquet": outputs[FUNDING_SOURCE_ID]["sha256"],
        },
    }


def _recover_orphaned_publication(inputs: MarketTailInputs) -> None:
    marker_path = inputs.transaction_marker_path
    if not marker_path.exists():
        return
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker.get("schema_version") != "trend-market-tail-transaction.v1":
            raise ValueError("transaction schema mismatch")
        if marker.get("window") != [inputs.start_ns, inputs.end_ns]:
            raise ValueError("transaction window mismatch")
        expected_targets = {
            "5m/data.parquet": inputs.perp_output_path,
            "funding/data.parquet": inputs.funding_output_path,
        }
        targets = marker.get("targets")
        if not isinstance(targets, dict) or set(targets) != set(expected_targets):
            raise ValueError("transaction targets mismatch")
        if inputs.provenance_path.exists():
            marker_path.unlink()
            return
        for relative, target in expected_targets.items():
            if target.parent.exists():
                if not target.is_file() or set(target.parent.iterdir()) != {target}:
                    raise ValueError(f"orphan target directory is unsafe: {relative}")
                if targets[relative] != _sha256_file(target):
                    raise ValueError(f"orphan target digest mismatch: {relative}")
        for target in expected_targets.values():
            if target.parent.exists():
                shutil.rmtree(target.parent)
        staging = Path(str(marker.get("staging_path"))).resolve()
        parent = inputs.output_root.parent.resolve()
        if staging.parent != parent or not staging.name.startswith(f".{inputs.output_root.name}."):
            raise ValueError("transaction staging path mismatch")
        if staging.exists():
            shutil.rmtree(staging)
        marker_path.unlink()
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MarketTailError(f"unsafe orphaned market-tail publication: {exc}") from exc


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
    required = set(PERP_COLUMNS)
    if not required.issubset(frame.columns):
        raise MarketTailError(f"canonical perpetual source missing columns: {sorted(required - set(frame.columns))}")
    frame = frame[(frame["open_time_ns"] >= inputs.start_ns) & (frame["open_time_ns"] < inputs.end_ns)].copy()
    _validate_perp_rows(frame)
    frame = _project_perp(frame)
    expected = list(range(inputs.start_ns, inputs.end_ns, FIVE_MINUTES_NS))
    missing = sorted(set(expected).difference(int(value) for value in frame["open_time_ns"]))
    repair_rows: list[dict[str, int | float]] = []
    for chunk in _consecutive_chunks(missing, FIVE_MINUTES_NS, page_limit):
        start_ms = chunk[0] // 1_000_000
        end_ms = (chunk[-1] + FIVE_MINUTES_NS) // 1_000_000
        payload = list(fetch_page(start_ms, end_ms, len(chunk)))
        try:
            rows = [_parse_perp_rest_row(item) for item in payload]
        except (IndexError, TypeError, ValueError) as exc:
            raise MarketTailError(f"invalid perpetual REST page: {exc}") from exc
        inserted = [int(row["open_time_ns"]) for row in rows]
        if inserted != chunk:
            raise MarketTailError("perpetual REST page does not exactly match the missing grid")
        _record_page(rest_pages, PERP_SOURCE_ID, start_ms, end_ms, len(chunk), payload, inserted)
        repair_rows.extend(rows)
    if repair_rows:
        frame = pd.concat((frame, _project_perp(pd.DataFrame(repair_rows))), ignore_index=True)
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
    repair_rows: list[dict[str, Any]] = []
    for chunk in _consecutive_chunks(missing, EIGHT_HOURS_NS, page_limit):
        start_ms = chunk[0] // 1_000_000
        end_ms = (chunk[-1] + EIGHT_HOURS_NS) // 1_000_000
        payload = list(fetch_page(start_ms, end_ms, len(chunk)))
        try:
            page_rows = _normalize_funding_rows(payload, inputs.start_ns, inputs.end_ns)
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketTailError(f"invalid funding REST page: {exc}") from exc
        inserted = [int(row["funding_time_ns"]) for row in page_rows]
        if inserted != chunk:
            raise MarketTailError("funding REST page does not exactly match the missing grid")
        _record_page(rest_pages, FUNDING_SOURCE_ID, start_ms, end_ms, len(chunk), payload, inserted)
        repair_rows.extend(page_rows)
    rows.extend(repair_rows)
    frame = pd.DataFrame(rows).sort_values("funding_time_ns", kind="stable").reset_index(drop=True)
    frame = _project_funding(frame)
    _validate_funding_grid(frame, inputs.start_ns, inputs.end_ns)
    return frame


def _normalize_funding_rows(rows: Sequence[Mapping[str, Any]], start_ns: int, end_ns: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise MarketTailError("funding REST page element must be an object")
        if row.get("symbol") != "BTCUSDT":
            raise MarketTailError("funding row must identify BTCUSDT")
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


def _project_perp(frame: pd.DataFrame) -> pd.DataFrame:
    projected = frame.loc[:, PERP_COLUMNS].copy()
    projected["open_time_ns"] = pd.to_numeric(projected["open_time_ns"], errors="raise").astype("int64")
    for column in ("open", "high", "low", "close", "volume"):
        projected[column] = pd.to_numeric(projected[column], errors="raise").astype("float64")
    projected["close_time_ns"] = projected["open_time_ns"] + FIVE_MINUTES_NS - 1_000_000
    return projected


def _project_funding(frame: pd.DataFrame) -> pd.DataFrame:
    projected = frame.loc[:, FUNDING_COLUMNS].copy()
    projected["symbol"] = projected["symbol"].astype("string")
    projected["funding_time_ns"] = pd.to_numeric(projected["funding_time_ns"], errors="raise").astype("int64")
    projected["raw_funding_time_ns"] = pd.to_numeric(projected["raw_funding_time_ns"], errors="raise").astype("int64")
    projected["funding_rate"] = pd.to_numeric(projected["funding_rate"], errors="raise").astype("float64")
    projected["mark_price"] = pd.to_numeric(projected["mark_price"], errors="raise").astype("float64")
    return projected


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


def replay_market_tail_provenance(
    payload: Mapping[str, Any],
    inputs: MarketTailInputs,
) -> dict[str, pd.DataFrame]:
    """Rebuild both derived frames solely from bound sources and stored REST pages."""

    if payload.get("schema_version") != "trend-market-tail-provenance.v1":
        raise MarketTailError("provenance schema mismatch")
    if payload.get("symbol") != "BTCUSDT" or payload.get("window") != [inputs.start_ns, inputs.end_ns]:
        raise MarketTailError("provenance instrument or window mismatch")
    page_limit = payload.get("page_limit")
    if not isinstance(page_limit, int) or not 0 < page_limit <= 1000:
        raise MarketTailError("provenance page_limit is invalid")
    sources = payload.get("sources")
    if not isinstance(sources, Mapping) or set(sources) != {PERP_SOURCE_ID, FUNDING_SOURCE_ID}:
        raise MarketTailError("provenance source set mismatch")
    for source_id, source_path in (
        (PERP_SOURCE_ID, inputs.perp_source_path),
        (FUNDING_SOURCE_ID, inputs.funding_source_path),
    ):
        envelope = sources[source_id]
        if not isinstance(envelope, Mapping):
            raise MarketTailError(f"invalid source envelope: {source_id}")
        if source_path.is_symlink():
            raise MarketTailError(f"canonical source file may not be a symlink: {source_id}")
        if envelope.get("path") != str(source_path) or envelope.get("resolved_path") != str(source_path.resolve()):
            raise MarketTailError(f"canonical source path mismatch: {source_id}")
        if envelope.get("sha256") != _sha256_file(source_path):
            raise MarketTailError(f"canonical source digest mismatch: {source_id}")
        if envelope.get("contract") != SOURCE_CONTRACTS[source_id]:
            raise MarketTailError(f"canonical source contract mismatch: {source_id}")
    repository = payload.get("repository")
    if not isinstance(repository, Mapping) or repository.get("head") != _repository_head(inputs.repository_root):
        raise MarketTailError("provenance repository HEAD mismatch")

    pages = payload.get("rest_pages")
    if not isinstance(pages, list):
        raise MarketTailError("provenance REST page ledger is missing")
    page_index = 0

    perp = pd.read_parquet(inputs.perp_source_path)
    if not set(PERP_COLUMNS).issubset(perp.columns):
        raise MarketTailError("canonical perpetual source schema mismatch")
    perp = perp[(perp["open_time_ns"] >= inputs.start_ns) & (perp["open_time_ns"] < inputs.end_ns)].copy()
    _validate_perp_rows(perp)
    perp = _project_perp(perp)
    expected_perp = list(range(inputs.start_ns, inputs.end_ns, FIVE_MINUTES_NS))
    missing_perp = sorted(set(expected_perp).difference(int(value) for value in perp["open_time_ns"]))
    perp_repairs: list[dict[str, int | float]] = []
    for chunk in _consecutive_chunks(missing_perp, FIVE_MINUTES_NS, page_limit):
        page = _checked_page(pages, page_index, PERP_SOURCE_ID, chunk, FIVE_MINUTES_NS)
        page_index += 1
        rows = [_parse_perp_rest_row(item) for item in page["payload"]]
        if [int(row["open_time_ns"]) for row in rows] != chunk:
            raise MarketTailError("perpetual REST payload does not match missing events")
        perp_repairs.extend(rows)
    if perp_repairs:
        perp = pd.concat((perp, _project_perp(pd.DataFrame(perp_repairs))), ignore_index=True)
    perp = perp.sort_values("open_time_ns", kind="stable").reset_index(drop=True)
    _validate_perp_grid(perp, inputs.start_ns, inputs.end_ns)

    funding_raw = pd.read_parquet(inputs.funding_source_path)
    if not {"symbol", "funding_time_ns", "funding_rate"}.issubset(funding_raw.columns):
        raise MarketTailError("canonical funding source schema mismatch")
    funding_rows = _normalize_funding_rows(funding_raw.to_dict("records"), inputs.start_ns, inputs.end_ns)
    expected_funding = list(range(inputs.start_ns, inputs.end_ns, EIGHT_HOURS_NS))
    missing_funding = sorted(set(expected_funding).difference(int(row["funding_time_ns"]) for row in funding_rows))
    for chunk in _consecutive_chunks(missing_funding, EIGHT_HOURS_NS, page_limit):
        page = _checked_page(pages, page_index, FUNDING_SOURCE_ID, chunk, EIGHT_HOURS_NS)
        page_index += 1
        rows = _normalize_funding_rows(page["payload"], inputs.start_ns, inputs.end_ns)
        if [int(row["funding_time_ns"]) for row in rows] != chunk:
            raise MarketTailError("funding REST payload does not match missing events")
        funding_rows.extend(rows)
    if page_index != len(pages):
        raise MarketTailError("provenance contains unexpected REST pages")
    funding = (
        _project_funding(pd.DataFrame(funding_rows))
        .sort_values("funding_time_ns", kind="stable")
        .reset_index(drop=True)
    )
    _validate_funding_grid(funding, inputs.start_ns, inputs.end_ns)
    return {PERP_SOURCE_ID: perp, FUNDING_SOURCE_ID: funding}


def _checked_page(
    pages: Sequence[object],
    index: int,
    source_id: str,
    chunk: Sequence[int],
    interval_ns: int,
) -> Mapping[str, Any]:
    if index >= len(pages) or not isinstance(pages[index], Mapping):
        raise MarketTailError(f"missing provenance REST page for {source_id}")
    page = pages[index]
    start_ms = chunk[0] // 1_000_000
    end_ms = (chunk[-1] + interval_ns) // 1_000_000
    endpoint = "/fapi/v1/klines" if source_id == PERP_SOURCE_ID else "/fapi/v1/fundingRate"
    params: dict[str, int | str] = {
        "symbol": "BTCUSDT",
        "startTime": start_ms,
        "endTime": end_ms - 1,
        "limit": len(chunk),
    }
    if source_id == PERP_SOURCE_ID:
        params["interval"] = "5m"
    payload = page.get("payload")
    if (
        page.get("source_id") != source_id
        or page.get("query") != {"endpoint": endpoint, "params": params}
        or page.get("inserted_event_ns") != list(chunk)
        or not isinstance(payload, list)
        or page.get("payload_sha256") != hashlib.sha256(_canonical_json_value(payload).encode()).hexdigest()
    ):
        raise MarketTailError(f"provenance REST page mismatch for {source_id}")
    return page


def _validate_perp_grid(frame: pd.DataFrame, start_ns: int, end_ns: int) -> None:
    if tuple(frame.columns) != PERP_COLUMNS:
        raise MarketTailError("perpetual output schema is not canonical")
    expected_dtypes = {
        "open_time_ns": "int64",
        "open": "float64",
        "high": "float64",
        "low": "float64",
        "close": "float64",
        "volume": "float64",
        "close_time_ns": "int64",
    }
    if frame.dtypes.astype(str).to_dict() != expected_dtypes:
        raise MarketTailError("perpetual output dtypes are not canonical")
    times = [int(value) for value in frame["open_time_ns"]]
    if times != list(range(start_ns, end_ns, FIVE_MINUTES_NS)):
        raise MarketTailError("perpetual output is not the exact 5m grid")
    _validate_perp_rows(frame)


def _validate_perp_rows(frame: pd.DataFrame) -> None:
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
    if tuple(frame.columns) != FUNDING_COLUMNS:
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
    if set(frame["symbol"].astype(str)) != {"BTCUSDT"}:
        raise MarketTailError("funding output must identify BTCUSDT")


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
    inserted_event_ns: Sequence[int],
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
            "inserted_event_ns": list(inserted_event_ns),
            "payload": list(payload),
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
