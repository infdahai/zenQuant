#!/usr/bin/env python3
"""Generate the synthetic downloader reference without third-party dependencies."""

import dataclasses
import importlib.util
import io
import json
import sys
import types
import zipfile
from datetime import UTC, datetime
from pathlib import Path

root = Path(__file__).resolve().parents[1]
fixture = root / "internal/history/testdata"
# Load the actual downloader while replacing its optional Parquet dependencies.
package = types.ModuleType("zenquant.historical")
package.__path__ = [str(root / "zenquant/historical")]
sys.modules["zenquant.historical"] = package
storage = types.ModuleType("zenquant.historical.storage")


@dataclasses.dataclass
class HistoricalKlineRow:
    symbol: str
    timeframe: str
    open_time_ns: int
    close_time_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int


@dataclasses.dataclass
class HistoricalFundingRow:
    symbol: str
    funding_time_ns: int
    funding_rate: float


storage.HistoricalKlineRow = HistoricalKlineRow
storage.HistoricalFundingRow = HistoricalFundingRow
for name in ("HistoricalDatasetLayout", "HistoricalOpenInterestRow", "write_parquet_rows"):
    setattr(storage, name, object)
sys.modules[storage.__name__] = storage
spec = importlib.util.spec_from_file_location("zenquant.historical.binance", root / "zenquant/historical/binance.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
archive = io.BytesIO()
with zipfile.ZipFile(archive, "w") as z:
    z.writestr("klines.csv", (fixture / "klines.csv").read_bytes())
module._fetch_bytes = lambda *args, **kwargs: archive.getvalue()
downloader = object.__new__(module.BinanceHistoricalDownloader)
downloader._config = module.BinanceHistoricalSyncConfig()
rows = downloader._download_kline_month(symbol="BTCUSDT", timeframe="4h", year=2024, month=1)
(fixture / "python_klines.json").write_text(json.dumps([dataclasses.asdict(row) for row in rows], indent=2) + "\n")
# Stub the transport: this exercises the downloader conversion without network access.
payload = json.loads((fixture / "funding.json").read_text())
module._fetch_json = lambda *args, **kwargs: payload
rows = downloader._download_funding(
    symbol="BTCUSDT", start_dt=datetime(2024, 1, 1, tzinfo=UTC), end_dt=datetime(2024, 1, 2, tzinfo=UTC)
)
(fixture / "python_funding.json").write_text(json.dumps([dataclasses.asdict(row) for row in rows], indent=2) + "\n")
