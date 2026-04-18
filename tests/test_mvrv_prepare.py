import tempfile
import unittest
from pathlib import Path

from zenquant.domain import Timeframe
from zenquant.historical.storage import HistoricalKlineRow, write_parquet_rows
from zenquant.ingestor.mvrv_prepare import (
    build_canonical_mvrv_rows_from_realized_price,
    normalize_mvrv_csv_rows,
)


class MvrvPrepareTests(unittest.TestCase):
    def test_build_canonical_rows_from_realized_price_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spot_path = Path(tmp) / "spot_1d.parquet"
            start_ns = 1_704_067_200_000_000_000
            write_parquet_rows(
                spot_path,
                [
                    HistoricalKlineRow(
                        symbol="BTCUSDT",
                        timeframe=Timeframe.TF_1D.value,
                        open_time_ns=start_ns - Timeframe.TF_1D.seconds * 1_000_000_000 + 1_000_000,
                        close_time_ns=start_ns,
                        open=40_000.0,
                        high=41_000.0,
                        low=39_000.0,
                        close=40_500.0,
                        volume=1.0,
                        trade_count=1,
                    )
                ],
            )
            rows = build_canonical_mvrv_rows_from_realized_price(
                spot_1d_path=spot_path,
                realized_price_payload=[[1_704_067_200_000, 27_000.0]],
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2024-01-01")
        self.assertAlmostEqual(float(rows[0]["mvrv"]), 1.5, places=6)

    def test_normalize_raw_csv_rows_to_canonical_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_csv = Path(tmp) / "raw.csv"
            raw_csv.write_text(
                "\n".join(
                    [
                        "Date,MVRV Ratio",
                        "2024-01-01,1.23",
                        "2024-01-02,1.45",
                    ]
                ),
                encoding="utf-8",
            )
            rows = normalize_mvrv_csv_rows(raw_csv)

        self.assertEqual(rows[0], {"date": "2024-01-01", "mvrv": "1.230000"})
        self.assertEqual(rows[1], {"date": "2024-01-02", "mvrv": "1.450000"})

