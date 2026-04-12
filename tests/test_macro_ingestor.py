import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from zenquant.ingestor.macro import fetch_btc_etf_netflow, read_btc_etf_flow_metadata


class BtcEtfFlowIngestorTests(unittest.TestCase):
    def test_local_csv_has_priority_and_writes_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            csv_path = output_dir / "sample.csv"
            csv_path.write_text(
                "date,netflow\n2026-04-01,100\n2026-04-02,-50\n",
                encoding="utf-8",
            )

            saved = fetch_btc_etf_netflow(output_dir, csv_path=csv_path)

            self.assertEqual(saved, 2)
            metadata = read_btc_etf_flow_metadata(output_dir)
            self.assertEqual(metadata["etf_flow_source"], "local_csv")
            self.assertEqual(metadata["etf_flow_source_confidence"], "high")
            self.assertIsNotNone(metadata["etf_flow_last_success_at"])

    @patch("zenquant.ingestor.macro.requests.get")
    def test_farside_html_is_used_as_fallback(self, mock_get: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)

            def _build_response(*_args, **_kwargs):
                response = Mock()
                response.raise_for_status.return_value = None
                return response

            first = _build_response()
            first.text = """
            <html><body>
            <table>
              <tr><th>Date</th><th>IBIT</th><th>Total</th></tr>
              <tr><td>2026-04-01</td><td>10</td><td>100</td></tr>
              <tr><td>2026-04-02</td><td>-5</td><td>-50</td></tr>
            </table>
            </body></html>
            """
            with (
                patch.dict(
                    "os.environ",
                    {"ZENQUANT_BINANCE_BTC_1D_PATH": str(output_dir / "missing.parquet")},
                    clear=False,
                ),
                patch("zenquant.ingestor.macro._fetch_coingecko_daily_close_map", return_value={}),
                patch("zenquant.ingestor.macro._fetch_btc_etf_from_bitbo", return_value=[]),
            ):
                mock_get.return_value = first
                saved = fetch_btc_etf_netflow(output_dir)

            self.assertEqual(saved, 2)
            metadata = read_btc_etf_flow_metadata(output_dir)
            self.assertEqual(metadata["etf_flow_source"], "farside_html")
            self.assertEqual(metadata["etf_flow_source_confidence"], "medium")

    @patch("zenquant.ingestor.macro.requests.get")
    def test_bitbo_fallback_reconstructs_usd_flow(self, mock_get: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            import pyarrow as pa
            import pyarrow.parquet as pq

            kline_path = output_dir / "binance_1d.parquet"
            table = pa.Table.from_pylist(
                [
                    {"open_time_ns": 1775433600000000000, "close": 100_000.0},  # 2026-04-06
                    {"open_time_ns": 1775606400000000000, "close": 101_000.0},  # 2026-04-08
                ]
            )
            pq.write_table(table, kline_path)
            bitbo_response = Mock()
            bitbo_response.raise_for_status.return_value = None
            bitbo_response.text = """
            <html><body>
            <table>
              <tr><th>Date</th><th>IBIT</th><th>Totals</th></tr>
              <tr><td>Apr 08, 2026</td><td>41.0</td><td>-180.0</td></tr>
              <tr><td>Apr 06, 2026</td><td>41.0</td><td>250.0</td></tr>
            </table>
            </body></html>
            """
            mock_get.return_value = bitbo_response

            with patch.dict(
                "os.environ",
                {"ZENQUANT_BINANCE_BTC_1D_PATH": str(kline_path)},
                clear=False,
            ), patch(
                "zenquant.ingestor.macro._fetch_coingecko_daily_close_map",
                return_value={},
            ):
                saved = fetch_btc_etf_netflow(output_dir)

            self.assertEqual(saved, 2)
            metadata = read_btc_etf_flow_metadata(output_dir)
            self.assertEqual(metadata["etf_flow_source"], "bitbo_etf_flows")
            self.assertEqual(metadata["etf_flow_source_confidence"], "medium")

    @patch("zenquant.ingestor.macro.requests.get")
    def test_failed_sources_write_missing_metadata(self, mock_get: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            mock_get.side_effect = RuntimeError("network down")

            with patch.dict(
                "os.environ",
                {"ZENQUANT_BINANCE_BTC_1D_PATH": str(output_dir / "missing.parquet")},
                clear=False,
            ):
                saved = fetch_btc_etf_netflow(output_dir)

            self.assertEqual(saved, 0)
            metadata_path = output_dir / "btc_etf_netflow" / "metadata.json"
            self.assertTrue(metadata_path.exists())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["etf_flow_source_confidence"], "missing")


if __name__ == "__main__":
    unittest.main()
