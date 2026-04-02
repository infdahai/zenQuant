"""Macro and sentiment signal ingestor.

Fetches daily-resolution signals from free public APIs:
  - Fear & Greed Index (alternative.me)
  - BTC Dominance + Stablecoin supply (CoinGecko /global)
  - Active Addresses (blockchain.com charts API)

All data is stored as Parquet for deterministic replay.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

REQUEST_TIMEOUT = 10
REQUEST_DELAY = 0.5


def _get(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET with timeout and basic error handling."""
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
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
