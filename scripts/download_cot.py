#!/usr/bin/env python3
"""Download CFTC Commitments of Traders (COT) data for Bitcoin futures.

The COT report provides weekly institutional positioning data from CME.
This is the only free source of institutional vs retail positioning.

Data source: CFTC Disaggregated Futures Reports
  - Released every Friday for the prior Tuesday's positions
  - Bitcoin CME futures appear as "BITCOIN-MICRO" or "BITCOIN" in contract name

Usage:
    uv run python scripts/download_cot.py
    uv run python scripts/download_cot.py --years 3
"""

from __future__ import annotations

import argparse
import csv
import io
import ssl
import sys
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# CFTC Traders in Financial Futures (Bitcoin is classified as financial, not commodity)
# Current year (updated weekly)
CFTC_YEAR_URL = "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"

OUTPUT_DIR = Path("data/raw/cftc")
BITCOIN_KEYWORDS = ("BITCOIN", "MICRO BITCOIN")


def download_cot_year(year: int, output_dir: Path) -> list[dict]:
    """Download COT data for one year and extract Bitcoin rows."""
    url = CFTC_YEAR_URL.format(year=year)

    try:
        print(f"  Downloading COT {year}...")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        request = urllib.request.Request(url, headers={"User-Agent": "zenquant/1.0"})
        with urllib.request.urlopen(request, timeout=60, context=ctx) as response:
            data = response.read()
    except Exception as e:
        print(f"  Failed to download {year}: {e}")
        return []

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".txt")]
            if not csv_names:
                return []

            content = zf.read(csv_names[0]).decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  Failed to extract {year}: {e}")
        return []

    return _parse_cot_csv(content)


def _parse_cot_csv(content: str) -> list[dict]:
    """Parse CFTC disaggregated COT CSV and extract Bitcoin rows."""
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(content))

    for row in reader:
        market_name = (row.get("Market_and_Exchange_Names", "") or "").upper()

        # Filter for Bitcoin CME futures
        if not any(kw in market_name for kw in BITCOIN_KEYWORDS):
            continue

        try:
            # Parse report date
            report_date_str = row.get("Report_Date_as_YYYY-MM-DD", "") or row.get("As_of_Date_In_Form_YYMMDD", "")
            if not report_date_str:
                continue

            if len(report_date_str) == 6:  # YYMMDD format
                report_date = datetime.strptime(report_date_str, "%y%m%d").replace(tzinfo=UTC)
            else:
                report_date = datetime.strptime(report_date_str.strip(), "%Y-%m-%d").replace(tzinfo=UTC)

            report_date_ns = int(report_date.timestamp() * 1_000_000_000)

            # Extract positioning data (Disaggregated format)
            asset_mgr_long = _safe_int(row.get("Asset_Mgr_Positions_Long_All", 0))
            asset_mgr_short = _safe_int(row.get("Asset_Mgr_Positions_Short_All", 0))
            lev_long = _safe_int(row.get("Lev_Money_Positions_Long_All", 0))
            lev_short = _safe_int(row.get("Lev_Money_Positions_Short_All", 0))
            dealer_long = _safe_int(row.get("Dealer_Positions_Long_All", 0))
            dealer_short = _safe_int(row.get("Dealer_Positions_Short_All", 0))
            total_oi = _safe_int(row.get("Open_Interest_All", 0))

            rows.append({
                "report_date_ns": report_date_ns,
                "market_name": market_name.strip(),
                "asset_mgr_long": asset_mgr_long,
                "asset_mgr_short": asset_mgr_short,
                "asset_mgr_net": asset_mgr_long - asset_mgr_short,
                "leveraged_long": lev_long,
                "leveraged_short": lev_short,
                "leveraged_net": lev_long - lev_short,
                "dealer_long": dealer_long,
                "dealer_short": dealer_short,
                "dealer_net": dealer_long - dealer_short,
                "total_oi": total_oi,
            })
        except (ValueError, KeyError):
            continue

    return rows


def _safe_int(val: object) -> int:
    try:
        s = str(val).strip().replace(",", "")
        return int(s) if s else 0
    except (ValueError, TypeError):
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Download CFTC COT Bitcoin positioning data")
    parser.add_argument("--years", type=int, default=5, help="How many years back to download")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    current_year = datetime.now(UTC).year
    start_year = current_year - args.years + 1

    print("CFTC COT Downloader — Bitcoin CME Futures")
    print(f"Years: {start_year}-{current_year}")
    print("=" * 50)

    all_rows: list[dict] = []
    for year in range(start_year, current_year + 1):
        year_rows = download_cot_year(year, args.output)
        all_rows.extend(year_rows)
        print(f"  {year}: {len(year_rows)} Bitcoin records")

    if not all_rows:
        print("\nNo Bitcoin COT data found.")
        sys.exit(1)

    # Deduplicate by report_date_ns (same date can appear in overlapping downloads)
    seen: set[int] = set()
    unique_rows: list[dict] = []
    for row in sorted(all_rows, key=lambda r: r["report_date_ns"]):
        if row["report_date_ns"] not in seen:
            seen.add(row["report_date_ns"])
            unique_rows.append(row)

    # Write parquet
    out_path = args.output / "cot_btc.parquet"
    table = pa.Table.from_pylist(unique_rows)
    pq.write_table(table, out_path)

    print(f"\nWritten {len(unique_rows)} weekly records to {out_path}")

    # Summary
    if unique_rows:
        first = datetime.fromtimestamp(unique_rows[0]["report_date_ns"] / 1e9, tz=UTC)
        last = datetime.fromtimestamp(unique_rows[-1]["report_date_ns"] / 1e9, tz=UTC)
        print(f"Date range: {first.date()} → {last.date()}")
        latest = unique_rows[-1]
        print(f"Latest: Asset Mgr net={latest['asset_mgr_net']:+d}, Leveraged net={latest['leveraged_net']:+d}")


if __name__ == "__main__":
    main()
