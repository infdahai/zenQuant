import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from zenquant.research.microstructure import (
    MicrostructureFeatureStore,
    MicrostructureFeatureStoreConfig,
    build_microstructure_admission_review,
    build_microstructure_dataset_contract,
    build_microstructure_feature_baseline_review,
    build_microstructure_preprocessing_review,
    write_microstructure_sidecar_features,
)


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RESEARCH_CLI = _load_module("research_microstructure_module", "scripts/research_microstructure.py")
PREPARE_BYBIT_CLI = _load_module(
    "prepare_bybit_microstructure_module",
    "scripts/prepare_bybit_microstructure_sample.py",
)


def _snapshot_row(ts_ns: int, mid: float, *, bid_size: float, ask_size: float, sequence: int) -> dict[str, object]:
    half_spread = 0.5
    bid = mid - half_spread
    ask = mid + half_spread
    return {
        "ts_ns": ts_ns,
        "venue": "bybit",
        "symbol": "BTCUSDT",
        "sequence": sequence,
        "bid_prices": json.dumps([bid, bid - 1.0]),
        "bid_sizes": json.dumps([bid_size, bid_size * 0.8]),
        "ask_prices": json.dumps([ask, ask + 1.0]),
        "ask_sizes": json.dumps([ask_size, ask_size * 0.8]),
    }


def _trade_row(ts_ns: int, price: float, side: str, size: float) -> dict[str, object]:
    return {
        "ts_ns": ts_ns,
        "venue": "bybit",
        "symbol": "BTCUSDT",
        "price": price,
        "size": size,
        "side": side,
    }


def _build_dataset(root: Path) -> None:
    snapshots: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    start = 1_700_000_000_000_000_000
    mid = 50_000.0
    for idx in range(24):
        ts_ns = start + idx * 1_000_000_000
        drift = 4.0 if idx < 12 else -3.0
        mid += drift
        bid_size = 12.0 + idx if idx < 12 else 34.0 - idx
        ask_size = 34.0 - idx if idx < 12 else 12.0 + idx
        snapshots.append(_snapshot_row(ts_ns, mid, bid_size=bid_size, ask_size=ask_size, sequence=idx))
        trade_side = "buy" if drift > 0 else "sell"
        trades.append(_trade_row(ts_ns + 100_000_000, mid, trade_side, 1.0 + (idx % 3)))

    pd.DataFrame(snapshots).to_parquet(root / "snapshots.parquet", index=False)
    pd.DataFrame(trades).to_parquet(root / "trades.parquet", index=False)


class MicrostructureResearchTests(unittest.TestCase):
    def test_dataset_contract_and_preprocessing_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _build_dataset(data_root)

            contract = build_microstructure_dataset_contract(
                data_root=data_root,
                venue="bybit",
                symbol="BTCUSDT",
                depth=20,
                cadence_ms=1000,
            )
            review = build_microstructure_preprocessing_review(
                data_root=data_root,
                venue="bybit",
                symbol="BTCUSDT",
                depth=20,
                cadence_ms=1000,
            )

        self.assertTrue(contract["ready"])
        self.assertEqual(contract["snapshot_delta_mode"], "snapshot_only")
        self.assertEqual(contract["row_counts"]["snapshots"], 24)
        self.assertTrue(review["ready"])
        self.assertEqual(review["duplicate_snapshot_rows"], 0)
        self.assertEqual(review["crossed_book_rows"], 0)

    def test_feature_store_lookup_is_point_in_time_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "raw"
            feature_root = Path(tmp) / "sidecar"
            data_root.mkdir(parents=True, exist_ok=True)
            _build_dataset(data_root)

            write_microstructure_sidecar_features(
                data_root=data_root,
                venue="bybit",
                symbol="BTCUSDT",
                depth=20,
                cadence_ms=1000,
                feature_output_root=feature_root,
            )

            store = MicrostructureFeatureStore(
                MicrostructureFeatureStoreConfig(data_dir=feature_root / "bybit" / "BTCUSDT" / "1000ms")
            )
            store.load()

            early = store.lookup(1_700_000_000_000_000_000)
            late = store.lookup(1_700_000_010_000_000_000)

        self.assertTrue(store.loaded)
        self.assertEqual(set(early.keys()), set(late.keys()))
        self.assertNotEqual(early["book_imbalance"], late["book_imbalance"])

    def test_feature_store_exposes_risk_overlay_values_and_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "raw"
            feature_root = Path(tmp) / "sidecar"
            data_root.mkdir(parents=True, exist_ok=True)
            _build_dataset(data_root)

            write_microstructure_sidecar_features(
                data_root=data_root,
                venue="bybit",
                symbol="BTCUSDT",
                depth=20,
                cadence_ms=1000,
                feature_output_root=feature_root,
            )

            store = MicrostructureFeatureStore(
                MicrostructureFeatureStoreConfig(
                    data_dir=feature_root / "bybit" / "BTCUSDT" / "1000ms",
                    max_staleness_ns=2_000_000_000,
                )
            )
            store.load()

            ready = store.lookup_risk_overlay_values(1_700_000_010_000_000_000)
            stale = store.lookup_risk_overlay_values(1_700_000_050_000_000_000)

        self.assertTrue(bool(ready["ms_sidecar_ready"]))
        self.assertFalse(bool(ready["ms_sidecar_stale"]))
        self.assertIn("ms_toxicity_proxy", ready)
        self.assertFalse(bool(stale["ms_sidecar_ready"]))
        self.assertTrue(bool(stale["ms_sidecar_stale"]))
        self.assertEqual(float(stale["ms_toxicity_proxy"]), 0.0)

    def test_feature_baseline_and_admission_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _build_dataset(data_root)

            baseline = build_microstructure_feature_baseline_review(
                data_root=data_root,
                venue="bybit",
                symbol="BTCUSDT",
                depth=20,
                cadence_ms=1000,
                horizon_ms=1000,
            )
            admission = build_microstructure_admission_review(
                data_root=data_root,
                venue="bybit",
                symbol="BTCUSDT",
                depth=20,
                cadence_ms=1000,
                horizon_ms=1000,
            )

        self.assertEqual(len(baseline["variants"]), 3)
        self.assertIn(baseline["recommended_variant"], {"raw", "clipped", "smoothed"})
        self.assertTrue(admission["research_only"])
        self.assertTrue(admission["execution_modifier_only"])
        self.assertFalse(admission["promote_truth"])
        self.assertFalse(admission["decision_policy"]["allow_direction_vote"])

    def test_research_cli_writes_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = root / "raw"
            output_dir = root / "results"
            feature_root = root / "sidecar"
            data_root.mkdir(parents=True, exist_ok=True)
            _build_dataset(data_root)

            argv = [
                "research_microstructure.py",
                "--data-root",
                str(data_root),
                "--output-dir",
                str(output_dir),
                "--feature-output-root",
                str(feature_root),
                "--venue",
                "bybit",
                "--symbol",
                "BTCUSDT",
                "--depth",
                "20",
                "--cadence-ms",
                "1000",
                "--horizon-ms",
                "1000",
            ]
            with patch.object(sys, "argv", argv):
                RESEARCH_CLI.main()

            expected = [
                output_dir / "dataset_contract.json",
                output_dir / "preprocessing_review.json",
                output_dir / "feature_baseline_review.json",
                output_dir / "admission_review.json",
                feature_root / "bybit" / "BTCUSDT" / "1000ms" / "features.parquet",
            ]
            for path in expected:
                self.assertTrue(path.exists(), msg=str(path))

    def test_prepare_bybit_cli_normalizes_live_sample_payloads(self) -> None:
        orderbook_payload = {
            "time": 1_700_000_000_500,
            "result": {
                "ts": "1700000000500",
                "u": 12345,
                "b": [["50000", "1.5"], ["49999", "1.0"]],
                "a": [["50001", "1.2"], ["50002", "0.9"]],
            },
        }
        trade_payload = {
            "result": {
                "list": [
                    {"T": 1_700_000_000_450, "p": "50000.5", "v": "0.25", "S": "Buy", "i": "t1"},
                    {"T": 1_700_000_000_460, "p": "50000.0", "v": "0.40", "S": "Sell", "i": "t2"},
                ]
            }
        }

        def _fake_get_json(*args, **_kwargs):
            path = str(args[1]) if len(args) > 1 else ""
            if "orderbook" in path:
                return orderbook_payload
            return trade_payload

        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "bybit_sample"
            argv = [
                "prepare_bybit_microstructure_sample.py",
                "--output-root",
                str(output_root),
                "--samples",
                "2",
                "--poll-interval-ms",
                "1",
            ]
            with patch.object(sys, "argv", argv), patch.object(
                PREPARE_BYBIT_CLI,
                "_get_json",
                side_effect=_fake_get_json,
            ):
                PREPARE_BYBIT_CLI.main()

            snapshots = pd.read_parquet(output_root / "snapshots.parquet")
            trades = pd.read_parquet(output_root / "trades.parquet")

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(len(trades), 2)
        self.assertEqual(str(snapshots.iloc[0]["venue"]), "bybit")
        self.assertEqual(str(trades.iloc[0]["symbol"]), "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
