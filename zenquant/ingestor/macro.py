"""Macro and sentiment signal ingestor.

Fetches daily-resolution signals from free public APIs:
  - Fear & Greed Index (alternative.me)
  - BTC Dominance + Stablecoin supply (CoinGecko /global)
  - Active Addresses (blockchain.com charts API)

All data is stored as Parquet for deterministic replay.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import requests
except ModuleNotFoundError:
    class _CompatResponse:
        def __init__(self, status_code: int, text: str) -> None:
            self.status_code = status_code
            self.text = text

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise HTTPError("", self.status_code, self.text, hdrs=None, fp=None)

        def json(self) -> Any:
            return json.loads(self.text)

    class _RequestsCompat:
        @staticmethod
        def get(
            url: str,
            params: dict[str, Any] | None = None,
            timeout: int = 10,
            headers: dict[str, str] | None = None,
        ) -> _CompatResponse:
            final_url = f"{url}?{urlencode(params)}" if params else url
            request = Request(final_url, headers=headers or {})
            try:
                with urlopen(request, timeout=timeout) as response:
                    status_code = getattr(response, "status", 200)
                    text = response.read().decode("utf-8")
                return _CompatResponse(status_code, text)
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore") if exc.fp is not None else str(exc)
                return _CompatResponse(exc.code, body)
            except URLError as exc:
                raise RuntimeError(str(exc)) from exc

    requests = _RequestsCompat()

REQUEST_TIMEOUT = 10
REQUEST_DELAY = 0.5
DEFAULT_BTC_ETF_FLOW_FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
DEFAULT_BITBO_ETF_FLOW_URL = "https://bitbo.io/treasuries/etf-flows/"
DEFAULT_COINGECKO_BTC_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
DEFAULT_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET with timeout and basic error handling."""
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT, headers=DEFAULT_HTTP_HEADERS)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Request failed {url}: {e}")
        return None


def _save_parquet(records: list[dict], output_path: Path) -> int:
    """Save records to Parquet, deduplicating by ts_ns."""
    if not records:
        return 0
    import pandas as pd

    df_new = pd.DataFrame(records)
    if output_path.exists():
        df_existing = pd.read_parquet(output_path)
        df = pd.concat([df_existing, df_new], ignore_index=True)
        df = df.drop_duplicates(subset=["ts_ns"]).sort_values("ts_ns")
    else:
        df = df_new
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    return len(records)


def _write_etf_flow_metadata(output_dir: Path, metadata: dict[str, Any]) -> None:
    metadata_path = output_dir / "btc_etf_netflow" / "metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def read_btc_etf_flow_metadata(output_dir: Path) -> dict[str, Any]:
    metadata_path = output_dir / "btc_etf_netflow" / "metadata.json"
    if not metadata_path.exists():
        return {
            "etf_flow_source": None,
            "etf_flow_source_confidence": "missing",
            "etf_flow_last_success_at": None,
        }
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {
        "etf_flow_source": payload.get("etf_flow_source"),
        "etf_flow_source_confidence": payload.get("etf_flow_source_confidence", "unknown"),
        "etf_flow_last_success_at": payload.get("etf_flow_last_success_at"),
    }


def _write_btc_mvrv_metadata(output_dir: Path, metadata: dict[str, Any]) -> None:
    metadata_path = output_dir / "btc_mvrv" / "metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def read_btc_mvrv_metadata(output_dir: Path) -> dict[str, Any]:
    metadata_path = output_dir / "btc_mvrv" / "metadata.json"
    if not metadata_path.exists():
        return {
            "btc_mvrv_source": None,
            "btc_mvrv_source_confidence": "missing",
            "btc_mvrv_last_success_at": None,
        }
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {
        "btc_mvrv_source": payload.get("btc_mvrv_source"),
        "btc_mvrv_source_confidence": payload.get("btc_mvrv_source_confidence", "unknown"),
        "btc_mvrv_last_success_at": payload.get("btc_mvrv_last_success_at"),
    }


def _parse_btc_etf_flow_rows(rows: list[dict[str, str]]) -> list[dict[str, float | int]]:
    parsed: list[dict[str, float | int]] = []
    for row in rows:
        lower = {str(k).strip().lower(): v for k, v in row.items()}
        date_value = lower.get("date") or lower.get("day")
        if not date_value:
            continue
        try:
            date_part = str(date_value).strip().split(" ")[0]
            ts_ns = int(datetime.fromisoformat(date_part).replace(tzinfo=UTC).timestamp() * 1_000_000_000)
        except Exception:
            continue

        netflow_raw = (
            lower.get("netflow")
            or lower.get("net_flow")
            or lower.get("net flow")
            or lower.get("net_inflow")
            or lower.get("net inflow")
        )
        if netflow_raw is None:
            total = 0.0
            found_component = False
            for key, value in lower.items():
                if key in {"date", "day", "total", "netflow", "net_flow", "net flow"}:
                    continue
                if not value:
                    continue
                try:
                    total += float(str(value).replace(",", ""))
                    found_component = True
                except ValueError:
                    continue
            if not found_component:
                continue
            netflow = total
        else:
            try:
                netflow = float(str(netflow_raw).replace(",", ""))
            except ValueError:
                continue

        parsed.append({"ts_ns": ts_ns, "value": round(netflow * 1_000_000.0, 2)})
    return parsed


def _parse_btc_mvrv_rows(rows: list[dict[str, str]]) -> list[dict[str, float | int]]:
    parsed: list[dict[str, float | int]] = []
    for row in rows:
        lower = {str(k).strip().lower(): v for k, v in row.items()}
        date_value = lower.get("date") or lower.get("day") or lower.get("timestamp") or lower.get("time")
        if not date_value:
            continue
        ts_ns: int | None = None
        date_text = str(date_value).strip()
        try:
            if date_text.isdigit():
                raw = int(date_text)
                ts_ns = raw * 1_000_000_000 if raw < 10_000_000_000 else raw * 1_000_000
            else:
                date_part = date_text.split(" ")[0]
                ts_ns = int(datetime.fromisoformat(date_part).replace(tzinfo=UTC).timestamp() * 1_000_000_000)
        except Exception:
            continue
        mvrv_raw = (
            lower.get("mvrv")
            or lower.get("mvrv_ratio")
            or lower.get("value")
            or lower.get("mv_rv")
            or lower.get("mvrv zscore")
        )
        if mvrv_raw is None:
            continue
        parsed_value = _safe_float(mvrv_raw)
        if parsed_value is None:
            continue
        parsed.append({"ts_ns": ts_ns, "value": round(parsed_value, 6)})
    parsed.sort(key=lambda item: int(item["ts_ns"]))
    deduped: list[dict[str, float | int]] = []
    seen: set[int] = set()
    for item in parsed:
        ts_ns = int(item["ts_ns"])
        if ts_ns in seen:
            continue
        seen.add(ts_ns)
        deduped.append(item)
    return deduped


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "—", "–", "nan", "None", "null"}:
        return None
    text = text.replace("$", "").replace(",", "").replace("%", "").replace("(", "-").replace(")", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


class _SimpleHtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"th", "td"} and self._current_row is not None:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(unescape("".join(self._current_cell)).strip())
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None and self._current_table is not None:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


def _parse_farside_html_table(html: str) -> list[dict[str, float | int]]:
    if "Just a moment" in html or "Enable JavaScript and cookies to continue" in html:
        return []
    parser = _SimpleHtmlTableParser()
    parser.feed(html)
    for table in parser.tables:
        if not table:
            continue
        header = [cell.strip().lower() for cell in table[0]]
        if "date" not in header:
            continue
        date_idx = header.index("date")
        total_idx = next((idx for idx, cell in enumerate(header) if cell in {"total", "net flow", "netflow"}), None)
        if total_idx is None:
            continue
        rows: list[dict[str, str]] = []
        for row in table[1:]:
            if date_idx >= len(row) or total_idx >= len(row):
                continue
            rows.append({"date": row[date_idx], "netflow": row[total_idx]})
        parsed = _parse_btc_etf_flow_rows(rows)
        if parsed:
            return parsed
    return []


def _load_binance_daily_close_map(parquet_path: Path) -> dict[int, float]:
    if not parquet_path.exists():
        return {}
    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path, columns=["open_time_ns", "close"])
    timestamps = table.column("open_time_ns").to_pylist()
    closes = table.column("close").to_pylist()
    return {int(ts): float(close) for ts, close in zip(timestamps, closes, strict=False)}


def _fetch_coingecko_daily_close_map(days: int = 120) -> dict[int, float]:
    payload = _get(
        DEFAULT_COINGECKO_BTC_MARKET_CHART_URL,
        {"vs_currency": "usd", "days": days, "interval": "daily"},
    )
    if not isinstance(payload, dict):
        return {}
    prices = payload.get("prices")
    if not isinstance(prices, list):
        return {}
    mapping: dict[int, float] = {}
    for item in prices:
        if not isinstance(item, list) or len(item) < 2:
            continue
        try:
            ts_ms = int(item[0])
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            mapping[int(dt.timestamp() * 1_000_000_000)] = float(item[1])
        except Exception:
            continue
    return mapping


def _parse_bitbo_etf_flows_html(
    html: str,
    *,
    binance_daily_close: dict[int, float],
) -> list[dict[str, float | int]]:
    parser = _SimpleHtmlTableParser()
    parser.feed(html)
    for table in parser.tables:
        if not table:
            continue
        header = [cell.strip() for cell in table[0]]
        lower_header = [cell.lower() for cell in header]
        if "date" not in lower_header or "totals" not in lower_header:
            continue
        date_idx = lower_header.index("date")
        total_idx = lower_header.index("totals")
        parsed: list[dict[str, float | int]] = []
        for row in table[1:]:
            if date_idx >= len(row) or total_idx >= len(row):
                continue
            try:
                dt = datetime.strptime(row[date_idx].strip(), "%b %d, %Y").replace(tzinfo=UTC)
            except ValueError:
                continue
            btc_flow = _safe_float(row[total_idx])
            if btc_flow is None:
                continue
            ts_ns = int(dt.timestamp() * 1_000_000_000)
            close = binance_daily_close.get(ts_ns)
            if close is None:
                continue
            parsed.append({"ts_ns": ts_ns, "value": round(btc_flow * close, 2)})
        if parsed:
            return sorted(parsed, key=lambda item: int(item["ts_ns"]))
    return []


def _fetch_btc_etf_from_farside_html(source_url: str) -> list[dict[str, float | int]]:
    response = requests.get(source_url, headers=DEFAULT_HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return _parse_farside_html_table(response.text)


def _fetch_btc_etf_from_bitbo(source_url: str, *, binance_daily_close: dict[int, float]) -> list[dict[str, float | int]]:
    response = requests.get(source_url, headers=DEFAULT_HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return _parse_bitbo_etf_flows_html(response.text, binance_daily_close=binance_daily_close)


def fetch_btc_etf_netflow(output_dir: Path, csv_path: Path | None = None, csv_url: str | None = None) -> int:
    """Fetch or import BTC spot ETF aggregate daily net flow.

    Values are stored in USD under ``btc_etf_netflow/data.parquet``.
    Source priority:
      1. local CSV
      2. Bitbo ETF flows
      3. Farside HTML fallback
    """
    source_path = csv_path or (
        Path(os.environ["ZENQUANT_BTC_ETF_FLOW_CSV"]) if os.getenv("ZENQUANT_BTC_ETF_FLOW_CSV") else None
    )
    bitbo_url = os.getenv("ZENQUANT_BTC_ETF_FLOW_BITBO_URL") or DEFAULT_BITBO_ETF_FLOW_URL
    farside_url = csv_url or os.getenv("ZENQUANT_BTC_ETF_FLOW_CSV_URL") or os.getenv("ZENQUANT_BTC_ETF_FLOW_FARSIDE_URL") or DEFAULT_BTC_ETF_FLOW_FARSIDE_URL
    local_daily_close = _load_binance_daily_close_map(
        Path(os.getenv("ZENQUANT_BINANCE_BTC_1D_PATH", "data/raw/binance/klines/BTCUSDT/1d/data.parquet"))
    )
    records: list[dict[str, float | int]] = []
    metadata = {
        "etf_flow_source": None,
        "etf_flow_source_confidence": "missing",
        "etf_flow_last_success_at": None,
        "etf_flow_source_detail": None,
    }
    if source_path is not None and source_path.exists():
        rows = list(csv.DictReader(source_path.read_text(encoding="utf-8").splitlines()))
        records = _parse_btc_etf_flow_rows(rows)
        metadata.update(
            {
                "etf_flow_source": "local_csv",
                "etf_flow_source_confidence": "high" if records else "missing",
                "etf_flow_source_detail": str(source_path),
            }
        )
    if not records:
        try:
            daily_close_map = dict(_fetch_coingecko_daily_close_map())
            daily_close_map.update(local_daily_close)
            records = _fetch_btc_etf_from_bitbo(bitbo_url, binance_daily_close=daily_close_map)
            metadata.update(
                {
                    "etf_flow_source": "bitbo_etf_flows",
                    "etf_flow_source_confidence": "medium" if records else "missing",
                    "etf_flow_source_detail": bitbo_url,
                }
            )
        except Exception as e:
            print(f"  ETF flow Bitbo fallback failed {bitbo_url}: {e}")
    if not records:
        try:
            records = _fetch_btc_etf_from_farside_html(farside_url)
            metadata.update(
                {
                    "etf_flow_source": "farside_html",
                    "etf_flow_source_confidence": "medium" if records else "missing",
                    "etf_flow_source_detail": farside_url,
                }
            )
        except Exception as e:
            print(f"  ETF flow Farside fallback failed {farside_url}: {e}")

    if not records:
        _write_etf_flow_metadata(output_dir, metadata)
        print("  No valid BTC ETF flow records")
        return 0

    output_path = output_dir / "btc_etf_netflow" / "data.parquet"
    saved = _save_parquet(records, output_path)
    metadata["etf_flow_last_success_at"] = datetime.now(UTC).isoformat()
    _write_etf_flow_metadata(output_dir, metadata)
    print(f"  Saved {saved} records to {output_path}")
    return saved


def import_btc_etf_netflow(output_dir: Path, csv_path: Path) -> int:
    return fetch_btc_etf_netflow(output_dir, csv_path=csv_path)


def fetch_btc_mvrv(output_dir: Path, csv_path: Path | None = None, csv_url: str | None = None) -> int:
    """Fetch or import BTC MVRV daily history.

    Source priority:
      1. local CSV
      2. remote CSV URL
    """
    source_path = csv_path or (Path(os.environ["ZENQUANT_BTC_MVRV_CSV"]) if os.getenv("ZENQUANT_BTC_MVRV_CSV") else None)
    source_url = csv_url or os.getenv("ZENQUANT_BTC_MVRV_CSV_URL")
    metadata = {
        "btc_mvrv_source": None,
        "btc_mvrv_source_confidence": "missing",
        "btc_mvrv_last_success_at": None,
        "btc_mvrv_source_detail": None,
    }
    rows: list[dict[str, float | int]] = []
    if source_path is not None and source_path.exists():
        rows = _parse_btc_mvrv_rows(list(csv.DictReader(source_path.read_text(encoding="utf-8").splitlines())))
        metadata.update(
            {
                "btc_mvrv_source": "local_csv",
                "btc_mvrv_source_confidence": "high" if rows else "missing",
                "btc_mvrv_source_detail": str(source_path),
            }
        )
    if not rows and source_url:
        try:
            response = requests.get(source_url, timeout=REQUEST_TIMEOUT, headers=DEFAULT_HTTP_HEADERS)
            response.raise_for_status()
            rows = _parse_btc_mvrv_rows(list(csv.DictReader(response.text.splitlines())))
            metadata.update(
                {
                    "btc_mvrv_source": "remote_csv",
                    "btc_mvrv_source_confidence": "medium" if rows else "missing",
                    "btc_mvrv_source_detail": source_url,
                }
            )
        except Exception as exc:
            print(f"  BTC MVRV fetch failed {source_url}: {exc}")
    if not rows:
        _write_btc_mvrv_metadata(output_dir, metadata)
        print("  No valid BTC MVRV records")
        return 0
    output_path = output_dir / "btc_mvrv" / "data.parquet"
    saved = _save_parquet(rows, output_path)
    metadata["btc_mvrv_last_success_at"] = datetime.now(UTC).isoformat()
    _write_btc_mvrv_metadata(output_dir, metadata)
    print(f"  Saved {saved} records to {output_path}")
    return saved


def import_btc_mvrv(output_dir: Path, csv_path: Path) -> int:
    return fetch_btc_mvrv(output_dir, csv_path=csv_path)


def fetch_fear_greed(output_dir: Path, limit: int = 365) -> int:
    """Fetch Fear & Greed Index from alternative.me.

    Free, no API key required. History up to 2018.
    Value: 0 (Extreme Fear) to 100 (Extreme Greed).
    Contrarian signal: extreme fear = historically bullish.
    """
    print(f"Fetching Fear & Greed Index ({limit} days)...")
    data = _get("https://api.alternative.me/fng/", {"limit": limit, "format": "json"})
    if not data or "data" not in data:
        print("  No data returned")
        return 0

    records = []
    for row in data["data"]:
        ts_s = int(row["timestamp"])
        records.append(
            {
                "ts_ns": ts_s * 1_000_000_000,
                "value": int(row["value"]),
                "classification": row["value_classification"],
            }
        )

    output_path = output_dir / "fear_greed" / "data.parquet"
    saved = _save_parquet(records, output_path)
    print(f"  Saved {saved} records to {output_path}")
    return saved


def fetch_btc_dominance(output_dir: Path) -> int:
    """Fetch BTC dominance and total market cap from CoinGecko /global.

    Free, no API key required for basic usage.
    BTC.D high = BTC season; BTC.D falling = alt season.
    """
    print("Fetching BTC dominance (CoinGecko /global)...")
    data = _get("https://api.coingecko.com/api/v3/global")
    if not data or "data" not in data:
        print("  No data returned")
        return 0

    d = data["data"]
    ts_ns = int(datetime.now(UTC).timestamp() * 1_000_000_000)

    records = [
        {
            "ts_ns": ts_ns,
            "btc_dominance_pct": float(d.get("market_cap_percentage", {}).get("btc", 0.0)),
            "eth_dominance_pct": float(d.get("market_cap_percentage", {}).get("eth", 0.0)),
            "total_market_cap_usd": float(d.get("total_market_cap", {}).get("usd", 0.0)),
            "total_volume_24h_usd": float(d.get("total_volume", {}).get("usd", 0.0)),
            "stablecoin_market_cap_usd": float(d.get("total_market_cap", {}).get("usdt", 0.0)),
            "active_cryptocurrencies": int(d.get("active_cryptocurrencies", 0)),
        }
    ]

    output_path = output_dir / "btc_dominance" / "data.parquet"
    saved = _save_parquet(records, output_path)
    print(f"  Saved {saved} records to {output_path}")
    return saved


def fetch_active_addresses(output_dir: Path, days: int = 365) -> int:
    """Fetch Bitcoin active addresses from blockchain.com charts API.

    Free, no API key required.
    Validated signal: Liu & Tsyvinski (JF 2022) -- network effect predictor.
    """
    print(f"Fetching active addresses (blockchain.com, {days} days)...")
    data = _get(
        "https://api.blockchain.info/charts/n-unique-addresses",
        {"timespan": f"{days}days", "format": "json", "cors": "true"},
    )
    if not data or "values" not in data:
        print("  No data returned")
        return 0

    records = []
    for row in data["values"]:
        records.append(
            {
                "ts_ns": int(row["x"]) * 1_000_000_000,
                "active_addresses": int(row["y"]),
            }
        )

    output_path = output_dir / "active_addresses" / "data.parquet"
    saved = _save_parquet(records, output_path)
    print(f"  Saved {saved} records to {output_path}")
    return saved


def fetch_transaction_count(output_dir: Path, days: int = 365) -> int:
    """Fetch Bitcoin daily transaction count from blockchain.com."""
    print(f"Fetching transaction count (blockchain.com, {days} days)...")
    data = _get(
        "https://api.blockchain.info/charts/n-transactions",
        {"timespan": f"{days}days", "format": "json", "cors": "true"},
    )
    if not data or "values" not in data:
        print("  No data returned")
        return 0

    records = []
    for row in data["values"]:
        records.append(
            {
                "ts_ns": int(row["x"]) * 1_000_000_000,
                "tx_count": int(row["y"]),
            }
        )

    output_path = output_dir / "tx_count" / "data.parquet"
    saved = _save_parquet(records, output_path)
    print(f"  Saved {saved} records to {output_path}")
    return saved


def _fetch_yfinance(
    ticker: str,
    name: str,
    output_dir: Path,
    period: str = "2y",
) -> int:
    """Fetch daily close from Yahoo Finance via yfinance.

    Requires ``yfinance`` (optional dependency group ``macro``).
    """
    try:
        import yfinance as yf
    except ImportError:
        print(f"  yfinance not installed — skipping {name}. Install with: uv pip install 'zenquant[macro]'")
        return 0

    print(f"Fetching {name} ({ticker}, {period})...")
    try:
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    except Exception as e:
        print(f"  yfinance download failed for {ticker}: {e}")
        return 0

    if data.empty:
        print(f"  No data returned for {ticker}")
        return 0

    import pandas as pd

    # Flatten MultiIndex columns if present (yfinance can return MultiIndex with auto_adjust=True)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    records: list[dict] = []
    for ts, row in data.iterrows():
        ts_val = pd.Timestamp(ts)
        ts_ns = int(ts_val.timestamp() * 1_000_000_000)
        # Safely extract Close value (handles potential Series objects)
        close_val = row["Close"]
        if isinstance(close_val, pd.Series):
            close_val = close_val.iloc[0]
        if pd.notna(close_val):
            records.append({"ts_ns": ts_ns, "value": float(close_val)})

    output_path = output_dir / name / "data.parquet"
    saved = _save_parquet(records, output_path)
    print(f"  Saved {saved} records to {output_path}")
    return saved


def fetch_vix(output_dir: Path, period: str = "2y") -> int:
    """Fetch CBOE VIX index daily close.

    VIX measures 30-day implied volatility of S&P 500 options.
    High VIX (>30) = market fear, historically correlated with crypto sell-offs.
    """
    return _fetch_yfinance("^VIX", "vix", output_dir, period)


def fetch_dxy(output_dir: Path, period: str = "2y") -> int:
    """Fetch US Dollar Index (DXY) daily close.

    DXY measures USD strength against a basket of 6 currencies.
    Strong dollar (rising DXY) typically bearish for risk assets including crypto.
    """
    return _fetch_yfinance("DX-Y.NYB", "dxy", output_dir, period)


def fetch_treasury_10y(output_dir: Path, period: str = "2y") -> int:
    """Fetch 10-Year US Treasury Yield daily close.

    Rising yields = tighter financial conditions, risk-off.
    Sharp yield moves signal macro regime shifts.
    """
    return _fetch_yfinance("^TNX", "treasury_10y", output_dir, period)


def run_all(output_dir: Path = Path("data/raw/macro"), days: int = 365) -> None:
    """Fetch all macro signals. Safe to run daily as a cron job."""
    print(f"Fetching macro signals \u2192 {output_dir}")
    print("=" * 60)

    total = 0
    total += fetch_fear_greed(output_dir, limit=days)
    time.sleep(REQUEST_DELAY)

    total += fetch_btc_dominance(output_dir)
    time.sleep(REQUEST_DELAY)

    total += fetch_active_addresses(output_dir, days=days)
    time.sleep(REQUEST_DELAY)
    total += fetch_btc_etf_netflow(output_dir)
    time.sleep(REQUEST_DELAY)

    total += fetch_transaction_count(output_dir, days=days)
    time.sleep(REQUEST_DELAY)

    # Traditional macro indicators (require yfinance)
    total += fetch_vix(output_dir)
    time.sleep(REQUEST_DELAY)

    total += fetch_dxy(output_dir)
    time.sleep(REQUEST_DELAY)

    total += fetch_treasury_10y(output_dir)

    print()
    print(f"Done. Total records: {total:,}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch macro signals")
    parser.add_argument("--output", type=Path, default=Path("data/raw/macro"))
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    run_all(args.output, args.days)
