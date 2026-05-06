import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from zenquant.app.forward_data_collect import (
    ForwardCollectionConfig,
    ForwardHealthConfig,
    evaluate_health,
    run_collection,
)
from zenquant.data.deribit_block_flow import DeribitBlockFlowRow, save_block_flow_rows, write_deribit_block_flow_status
from zenquant.data.deribit_status import write_deribit_status
from zenquant.data.liquidation_pipeline import LiquidationBucket, write_aggregated_liquidations


def _ts(year: int, month: int, day: int, hour: int) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp() * 1_000_000_000)


class ForwardDataCollectTests(unittest.TestCase):
    def test_run_collection_writes_ordered_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ForwardCollectionConfig(output_dir=root / "results")
            with (
                patch("zenquant.app.forward_data_collect._collect_deribit", return_value={"ok": True}),
                patch("zenquant.app.forward_data_collect._collect_deribit_block_flow", return_value={"ok": True}),
                patch("zenquant.app.forward_data_collect._collect_hyperliquid", return_value={"ok": True}),
                patch("zenquant.app.forward_data_collect._collect_hyperliquid_whales", return_value={"ok": True}),
                patch("zenquant.app.forward_data_collect._merge_liquidations", return_value={"ok": True}),
                patch("zenquant.app.forward_data_collect._refresh_readiness", return_value={"ok": True}),
            ):
                payload = run_collection(config)

            self.assertTrue(payload["ok"])
            self.assertEqual(
                [step["step"] for step in payload["steps"]],
                [
                    "deribit_collect",
                    "deribit_block_flow_collect",
                    "hyperliquid_collect",
                    "hyperliquid_whales_collect",
                    "merge_liquidation",
                    "readiness_refresh",
                ],
            )
            summary_path = root / "results" / "forward_collection_latest.json"
            self.assertTrue(summary_path.exists())

    def test_health_reports_missing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = evaluate_health(
                ForwardHealthConfig(
                    deribit_root=root / "deribit",
                    hyperliquid_root=root / "hyper",
                    merged_root=root / "merged",
                    output_dir=root / "results",
                )
            )

        self.assertFalse(payload["ok"])
        self.assertIn("readiness_missing", payload["failures"])
        self.assertIn("hyperliquid_status_missing", payload["failures"])
        self.assertIn("merged_liquidation_missing", payload["failures"])

    def test_health_passes_when_status_files_are_fresh(self) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deribit_root = root / "deribit"
            block_flow_root = root / "block_flow"
            snapshot_dir = deribit_root / "BTC" / now.strftime("%Y-%m")
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            (snapshot_dir / f"options_chain_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet").write_text(
                "ok", encoding="utf-8"
            )
            write_deribit_status(deribit_root)
            save_block_flow_rows(
                [
                    DeribitBlockFlowRow(
                        ts_ns=int(now.timestamp() * 1_000_000_000),
                        trade_id="t1",
                        block_trade_id=None,
                        combo_id=None,
                        combo_trade_id=None,
                        instrument_name="BTC-24APR26-72000-C",
                        option_type="c",
                        expiry_ts_ms=1776988800000,
                        strike=72000.0,
                        direction="buy",
                        amount=3.0,
                        price=0.0525,
                        index_price=66930.31,
                        mark_price=0.05253883,
                        iv=45.91,
                        liquidity=None,
                        is_block_trade=False,
                        is_combo_leg=False,
                        leg_count=1,
                        source="public_option_trade",
                    )
                ],
                block_flow_root,
            )
            write_deribit_block_flow_status(block_flow_root)

            hyper_root = root / "hyper"
            hyper_root.mkdir(parents=True, exist_ok=True)
            (hyper_root / "_collector_status.json").write_text(
                json.dumps(
                    {
                        "last_success_ts": now.isoformat(),
                        "recent_trades": 10,
                        "proxy_buckets": 1,
                        "blocked_by": None,
                    }
                ),
                encoding="utf-8",
            )

            merged_root = root / "merged"
            write_aggregated_liquidations(
                merged_root,
                [
                    LiquidationBucket(
                        bucket_start_ns=_ts(2026, 4, 1, 0),
                        bucket_end_ns=_ts(2026, 4, 1, 4),
                        symbol="BTCUSDT",
                        long_liq_count=1,
                        short_liq_count=0,
                        long_liq_usd=100_000.0,
                        short_liq_usd=0.0,
                        total_liq_usd=100_000.0,
                        max_single_liq_usd=100_000.0,
                        liq_imbalance=1.0,
                        source="hyperliquid_public",
                    )
                ],
            )

            results_dir = root / "results"
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "data_readiness.json").write_text(json.dumps({"overall_blockers": []}), encoding="utf-8")

            payload = evaluate_health(
                ForwardHealthConfig(
                    deribit_root=deribit_root,
                    deribit_block_flow_root=block_flow_root,
                    hyperliquid_root=hyper_root,
                    merged_root=merged_root,
                    output_dir=results_dir,
                    max_staleness_hours=2.0,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["failures"], [])

    def test_health_only_alerts_after_persistent_readiness_blockers(self) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deribit_root = root / "deribit"
            block_flow_root = root / "block_flow"
            snapshot_dir = deribit_root / "BTC" / now.strftime("%Y-%m")
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            (snapshot_dir / f"options_chain_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet").write_text(
                "ok", encoding="utf-8"
            )
            write_deribit_status(deribit_root)
            save_block_flow_rows(
                [
                    DeribitBlockFlowRow(
                        ts_ns=int(now.timestamp() * 1_000_000_000),
                        trade_id="t1",
                        block_trade_id=None,
                        combo_id=None,
                        combo_trade_id=None,
                        instrument_name="BTC-24APR26-72000-C",
                        option_type="c",
                        expiry_ts_ms=1776988800000,
                        strike=72000.0,
                        direction="buy",
                        amount=3.0,
                        price=0.0525,
                        index_price=66930.31,
                        mark_price=0.05253883,
                        iv=45.91,
                        liquidity=None,
                        is_block_trade=False,
                        is_combo_leg=False,
                        leg_count=1,
                        source="public_option_trade",
                    )
                ],
                block_flow_root,
            )
            write_deribit_block_flow_status(block_flow_root)

            hyper_root = root / "hyper"
            hyper_root.mkdir(parents=True, exist_ok=True)
            (hyper_root / "_collector_status.json").write_text(
                json.dumps(
                    {
                        "last_success_ts": now.isoformat(),
                        "recent_trades": 10,
                        "proxy_buckets": 1,
                        "blocked_by": None,
                    }
                ),
                encoding="utf-8",
            )

            merged_root = root / "merged"
            write_aggregated_liquidations(
                merged_root,
                [
                    LiquidationBucket(
                        bucket_start_ns=_ts(2026, 4, 1, 0),
                        bucket_end_ns=_ts(2026, 4, 1, 4),
                        symbol="BTCUSDT",
                        long_liq_count=1,
                        short_liq_count=0,
                        long_liq_usd=100_000.0,
                        short_liq_usd=0.0,
                        total_liq_usd=100_000.0,
                        max_single_liq_usd=100_000.0,
                        liq_imbalance=1.0,
                        source="hyperliquid_public",
                    )
                ],
            )

            results_dir = root / "results"
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "data_readiness.json").write_text(
                json.dumps({"overall_blockers": ["deribit_sample_window_incomplete"]}),
                encoding="utf-8",
            )

            first = evaluate_health(
                ForwardHealthConfig(
                    deribit_root=deribit_root,
                    deribit_block_flow_root=block_flow_root,
                    hyperliquid_root=hyper_root,
                    merged_root=merged_root,
                    output_dir=results_dir,
                    max_staleness_hours=2.0,
                )
            )
            second = evaluate_health(
                ForwardHealthConfig(
                    deribit_root=deribit_root,
                    hyperliquid_root=hyper_root,
                    merged_root=merged_root,
                    output_dir=results_dir,
                    max_staleness_hours=2.0,
                )
            )

        self.assertTrue(first["ok"])
        self.assertEqual(first["readiness_blocked_cycles"], 1)
        self.assertFalse(second["ok"])
        self.assertIn("readiness_blocked_persistent", second["failures"])


if __name__ == "__main__":
    unittest.main()
