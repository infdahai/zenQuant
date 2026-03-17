import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


OPTIONS_CHAIN = _load_module("download_deribit_options_chain_module", "scripts/download_deribit_options_chain.py")


class DownloadDeribitOptionsChainTests(unittest.TestCase):
    def test_fetch_option_chain_normalizes_rows(self) -> None:
        instruments = [
            {
                "instrument_name": "BTC-28MAR26-100000-C",
                "expiration_timestamp": 1_775_000_000_000,
                "strike": 100_000.0,
                "option_type": "call",
                "base_currency": "BTC",
                "settlement_period": "month",
            },
            {
                "instrument_name": "BTC-28MAR26-100000-P",
                "expiration_timestamp": 1_775_000_000_000,
                "strike": 100_000.0,
                "option_type": "put",
                "base_currency": "BTC",
                "settlement_period": "month",
            },
        ]
        summaries = [
            {
                "instrument_name": "BTC-28MAR26-100000-C",
                "underlying_price": 98_000.0,
                "open_interest": 120.0,
                "volume_usd": 50_000.0,
                "mark_iv": 64.0,
                "bid_price": 0.12,
                "ask_price": 0.14,
            },
            {
                "instrument_name": "BTC-28MAR26-100000-P",
                "underlying_price": 98_000.0,
                "open_interest": 140.0,
                "volume_usd": 60_000.0,
                "mark_iv": 67.0,
                "bid_price": 0.13,
                "ask_price": 0.16,
            },
        ]

        def fake_fetch(endpoint: str, query: dict[str, str]):
            if endpoint == "get_instruments":
                return instruments
            if endpoint == "get_book_summary_by_currency":
                return summaries
            raise AssertionError(endpoint)

        with patch.object(OPTIONS_CHAIN, "_fetch_json", side_effect=fake_fetch):
            rows = OPTIONS_CHAIN.fetch_option_chain("BTC")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].currency, "BTC")
        self.assertEqual(rows[0].option_type, "call")
        self.assertAlmostEqual(rows[0].mid_price or 0.0, 0.13, places=6)

    def test_save_option_chain_writes_json_and_parquet(self) -> None:
        rows = [
            OPTIONS_CHAIN.OptionChainRow(
                snapshot_ts_ns=1_700_000_000_000_000_000,
                currency="BTC",
                instrument_name="BTC-28MAR26-100000-C",
                expiry_ts_ms=1_775_000_000_000,
                expiry_date="2026-03-28",
                strike=100_000.0,
                option_type="call",
                underlying_price=98_000.0,
                open_interest=120.0,
                volume_usd=50_000.0,
                mark_iv=64.0,
                bid_price=0.12,
                ask_price=0.14,
                mid_price=0.13,
                underlying_index="BTC",
                settlement_period="month",
            )
        ]

        with tempfile.TemporaryDirectory() as tmp:
            json_path, parquet_path = OPTIONS_CHAIN.save_option_chain(rows, Path(tmp))
            self.assertTrue(json_path.exists())
            self.assertTrue(parquet_path.exists())


if __name__ == "__main__":
    unittest.main()
