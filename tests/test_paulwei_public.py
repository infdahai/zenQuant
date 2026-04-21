from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from zenquant.research.comparison import public_archive_variant_specs
from zenquant.research.paulwei_public import (
    build_ahr999_alignment,
    build_behavior_profile,
    build_paulwei_dataset_contract,
    ingest_paulwei_public_dataset,
    write_baseline_mapping,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    frame = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _write_spot_daily(path: Path, days: int = 260) -> None:
    start = pd.Timestamp("2022-01-01T00:00:00Z")
    rows = []
    for i in range(days):
        ts = start + pd.Timedelta(days=i)
        close = 20_000 + i * 50
        rows.append(
            {
                "open": close - 10,
                "high": close + 25,
                "low": close - 25,
                "close": close,
                "close_time_ns": int((ts + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)).value),
                "open_time_ns": int(ts.value),
            }
        )
    frame = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _write_mvrv(path: Path, days: int = 260) -> None:
    start = pd.Timestamp("2022-01-01T00:00:00Z")
    rows = []
    for i in range(days):
        ts = start + pd.Timedelta(days=i)
        rows.append({"ts_ns": int(ts.value), "value": 0.8 + i * 0.002})
    frame = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _build_public_source(root: Path) -> None:
    _write_csv(
        root / "api-v1-execution-tradeHistory.csv",
        [
            {
                "timestamp": "2022-02-01T00:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Buy",
                "lastQty": 100,
                "lastPx": 40000,
                "foreignNotional": -100,
                "orderID": "o1",
                "execID": "e1",
            },
            {
                "timestamp": "2022-02-01T04:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Buy",
                "lastQty": 50,
                "lastPx": 40500,
                "foreignNotional": -50,
                "orderID": "o2",
                "execID": "e2",
            },
            {
                "timestamp": "2022-02-01T08:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Sell",
                "lastQty": 30,
                "lastPx": 41000,
                "foreignNotional": 30,
                "orderID": "o3",
                "execID": "e3",
            },
            {
                "timestamp": "2022-02-02T00:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Sell",
                "lastQty": 120,
                "lastPx": 42000,
                "foreignNotional": 120,
                "orderID": "o4",
                "execID": "e4",
            },
            {
                "timestamp": "2022-02-02T06:00:00Z",
                "execType": "Trade",
                "symbol": "XBTUSD",
                "side": "Buy",
                "lastQty": 40,
                "lastPx": 41500,
                "foreignNotional": -40,
                "orderID": "o5",
                "execID": "e5",
            },
        ],
    )
    _write_csv(root / "api-v1-order.csv", [{"timestamp": "2022-02-01T00:00:00Z", "symbol": "XBTUSD"}])
    _write_csv(root / "api-v1-user-walletHistory.csv", [{"timestamp": "2022-02-01T00:00:00Z", "walletBalance": 1}])
    _write_csv(root / "api-v1-position.snapshot.csv", [{"timestamp": "2022-02-03T00:00:00Z", "currentQty": 0}])
    _write_csv(root / "api-v1-user-wallet.snapshot-all.csv", [{"timestamp": "2022-02-03T00:00:00Z", "currency": "XBt", "amount": 1}])
    _write_csv(root / "api-v1-user-margin.snapshot-all.csv", [{"timestamp": "2022-02-03T00:00:00Z", "currency": "XBt", "walletBalance": 1}])
    _write_csv(
        root / "derived-equity-curve.csv",
        [
            {"timestamp": "2022-02-01T00:00:00Z", "adjustedWealthXBT": 1.0},
            {"timestamp": "2022-02-02T00:00:00Z", "adjustedWealthXBT": 0.85},
            {"timestamp": "2022-02-03T00:00:00Z", "adjustedWealthXBT": 1.1},
        ],
    )
    (root / "manifest.json").write_text(json.dumps({"generated_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")


class PaulWeiPublicResearchTests(unittest.TestCase):
    def test_ingest_and_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            output = Path(tmp) / "output"
            results = Path(tmp) / "results"
            source.mkdir(parents=True, exist_ok=True)
            _build_public_source(source)

            ingest_paulwei_public_dataset(source_root=source, output_root=output)
            contract = build_paulwei_dataset_contract(data_root=output)

            self.assertTrue(contract["ready"])
            self.assertEqual(contract["source_type"], "external_public_account_archive")
            self.assertEqual(contract["venue"], "bitmex")
            self.assertFalse(contract["admit_as_primary_truth"])
            self.assertEqual(contract["missing_files"], [])

    def test_behavior_profile_and_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "paulwei"
            spot_root = Path(tmp) / "spot"
            output = Path(tmp) / "results"
            _build_public_source(data_root)
            _write_spot_daily(spot_root / "klines" / "BTCUSDT" / "1d" / "data.parquet")

            profile, episodes = build_behavior_profile(data_root=data_root, spot_root=spot_root)
            mapping_info = write_baseline_mapping(behavior_profile=profile, output_dir=output)
            mapping = json.loads((output / "baseline_mapping.json").read_text(encoding="utf-8"))

            self.assertGreaterEqual(len(episodes), 2)
            self.assertIn("btc_notional_share", profile)
            self.assertIn("drawdown_profile", profile)
            self.assertEqual(mapping["mapping_confidence"], "medium")
            self.assertIn("derived_execution_rules", mapping)
            self.assertTrue(Path(mapping_info["json_path"]).exists())

    def test_ahr999_alignment_and_public_archive_variant_specs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "paulwei"
            spot_root = Path(tmp) / "spot"
            macro_root = Path(tmp) / "macro"
            output = Path(tmp) / "results"
            _build_public_source(data_root)
            _write_spot_daily(spot_root / "klines" / "BTCUSDT" / "1d" / "data.parquet")
            _write_mvrv(macro_root / "btc_mvrv" / "data.parquet")

            profile, _ = build_behavior_profile(data_root=data_root, spot_root=spot_root)
            write_baseline_mapping(behavior_profile=profile, output_dir=output)
            alignment = build_ahr999_alignment(data_root=data_root, spot_root=spot_root, macro_root=macro_root)
            variants = public_archive_variant_specs(mapping_path=output / "baseline_mapping.json")

            self.assertTrue(alignment["ready"])
            self.assertIn("correlation_to_mvrv", alignment)
            self.assertEqual(variants[-1].name, "paulwei_public_archive_baseline")
            self.assertEqual(len(variants), 3)
