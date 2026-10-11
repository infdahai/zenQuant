import tempfile
import unittest
from pathlib import Path

from zenquant.domain import Timeframe
from zenquant.historical.storage import HistoricalKlineRow, write_parquet_rows
from zenquant.research.data_coverage_audit import build_data_coverage_audit, write_data_coverage_audit


def _write_kline(path: Path, timeframe: Timeframe, start_ns: int) -> None:
    write_parquet_rows(
        path,
        [
            HistoricalKlineRow(
                symbol="BTCUSDT",
                timeframe=timeframe.value,
                open_time_ns=start_ns,
                close_time_ns=start_ns + timeframe.seconds * 1_000_000_000,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1.0,
                trade_count=10,
            )
        ],
    )


class DataCoverageAuditTests(unittest.TestCase):
    def test_audit_reports_parquet_csv_and_training_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "raw"
            _write_kline(
                root / "binance" / "klines" / "BTCUSDT" / "4h" / "data.parquet",
                Timeframe.TF_4H,
                1_577_836_800_000_000_000,
            )
            csv_dir = root / "binance" / "klines" / "BTCUSDT" / "4h"
            csv_dir.mkdir(parents=True, exist_ok=True)
            (csv_dir / "BTCUSDT-4h-2026-05-03.csv").write_text("header\n", encoding="utf-8")
            _write_kline(
                root / "binance_spot" / "klines" / "BTCUSDT" / "4h" / "data.parquet",
                Timeframe.TF_4H,
                1_503_003_600_000_000_000,
            )

            payload = build_data_coverage_audit(data_root=root, symbol="BTCUSDT")

        sources = {source["source_id"]: source for source in payload["sources"]}
        futures_4h = sources["binance_futures_klines_4h"]
        spot_4h = sources["binance_spot_klines_4h"]
        bitmex = sources["bitmex_xbtusd_2017_2020_replay_candidate"]

        self.assertEqual(futures_4h["training_role"], "formal_price_truth")
        self.assertTrue(futures_4h["training_integration"]["formal_train_enabled"])
        self.assertEqual(sum(item["file_count"] for item in futures_4h["csv"]), 1)
        self.assertEqual(futures_4h["combined_coverage"]["end_at"], "2026-05-04T00:00:00+00:00")
        self.assertEqual(spot_4h["training_role"], "context_only_not_execution_truth")
        self.assertFalse(spot_4h["training_integration"]["formal_train_enabled"])
        self.assertIn("spot_perp_truth_boundary", spot_4h["training_integration"]["blocked_by"])
        self.assertEqual(bitmex["training_integration"]["integration_status"], "missing")
        self.assertIn("local_data_missing", bitmex["training_integration"]["blocked_by"])

    def test_write_audit_creates_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_data_coverage_audit(data_root=root / "raw", symbol="BTCUSDT")
            result = write_data_coverage_audit(payload, output_dir=root / "out")

            json_path = Path(result["json_path"])
            md_path = Path(result["md_path"])
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("source_count", result)


if __name__ == "__main__":
    unittest.main()
