import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zenquant.data.deribit_block_flow import DeribitBlockFlowRow, save_block_flow_rows, write_deribit_block_flow_status
from zenquant.data.deribit_status import write_deribit_status
from zenquant.data.liquidation_pipeline import (
    LiquidationEvent,
    aggregate_liquidation_events,
    write_aggregated_liquidations,
)


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


REVIEW = _load_module("review_hyperliquid_admission_module", "scripts/review_hyperliquid_admission.py")
MVRV_REVIEW = _load_module("review_mvrv_admission_module", "scripts/review_mvrv_admission.py")
BLOCK_FLOW_REVIEW = _load_module("review_deribit_block_flow_admission_module", "scripts/review_deribit_block_flow_admission.py")


def _ts(year: int, month: int, day: int, hour: int) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp() * 1_000_000_000)


class DataSourceReviewTests(unittest.TestCase):
    def test_hyperliquid_review_emits_secondary_not_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binance_root = root / "binance"
            merged_root = root / "merged"
            hyperliquid_root = root / "hyperliquid"
            deribit_root = root / "deribit_options"
            output_dir = root / "review"

            # Simulate merged Hyperliquid buckets only; Binance still absent.
            merged_buckets = aggregate_liquidation_events(
                [
                    LiquidationEvent(
                        ts_ns=_ts(2026, 4, 1, 0),
                        symbol="BTCUSDT",
                        side="SELL",
                        price=80000.0,
                        qty=2.0,
                        usd_value=160000.0,
                        source="hyperliquid_public",
                        trade_time_ms=_ts(2026, 4, 1, 0) // 1_000_000,
                    )
                ]
            )
            write_aggregated_liquidations(merged_root, merged_buckets)
            (hyperliquid_root / "_collector_status.json").parent.mkdir(parents=True, exist_ok=True)
            (hyperliquid_root / "_collector_status.json").write_text("{}", encoding="utf-8")

            recent = datetime.now(UTC)
            target = deribit_root / "BTC" / recent.strftime("%Y-%m")
            target.mkdir(parents=True, exist_ok=True)
            for index in range(3):
                ts = recent - timedelta(hours=index * 4)
                (target / f"options_chain_{ts.strftime('%Y%m%dT%H%M%SZ')}.parquet").write_text("ok", encoding="utf-8")
            write_deribit_status(deribit_root)

            with tempfile.TemporaryDirectory() as _:
                argv = [
                    "review_hyperliquid_admission.py",
                    "--symbol",
                    "BTCUSDT",
                    "--binance-root",
                    str(binance_root),
                    "--merged-root",
                    str(merged_root),
                    "--hyperliquid-root",
                    str(hyperliquid_root),
                    "--deribit-root",
                    str(deribit_root),
                    "--output-dir",
                    str(output_dir),
                ]
                with patch.object(sys, "argv", argv):
                    REVIEW.main()

            payload = json.loads((output_dir / "hyperliquid_admission_review.json").read_text(encoding="utf-8"))

        self.assertTrue(payload["admit_as_secondary"])
        self.assertFalse(payload["admit_as_primary"])
        self.assertEqual(payload["source_tiers"]["binance_liquidation"], "tier1_primary")
        self.assertEqual(payload["source_tiers"]["hyperliquid_public_proxy"], "tier2_supplemental")
        self.assertTrue(payload["recommendation"]["collect_continue"])
        self.assertTrue(payload["recommendation"]["exclude_from_promote_truth"])
        self.assertEqual(payload["tier_decision"], "tier2_research_only_feature")
        self.assertEqual(payload["time_granularity"], "4h")

    def test_mvrv_review_emits_daily_research_only_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            macro_root = root / "macro"
            output_dir = root / "review"
            (macro_root / "btc_mvrv").mkdir(parents=True, exist_ok=True)
            (macro_root / "btc_mvrv" / "data.parquet").write_text("placeholder", encoding="utf-8")
            (macro_root / "btc_mvrv" / "metadata.json").write_text(
                json.dumps(
                    {
                        "btc_mvrv_source": "local_csv",
                        "btc_mvrv_source_confidence": "high",
                        "btc_mvrv_last_success_at": "2026-04-18T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            argv = [
                "review_mvrv_admission.py",
                "--symbol",
                "BTCUSDT",
                "--macro-root",
                str(macro_root),
                "--output-dir",
                str(output_dir),
            ]
            with patch.object(sys, "argv", argv):
                MVRV_REVIEW.main()

            payload = json.loads((output_dir / "mvrv_admission_review.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["source_name"], "btc_mvrv")
        self.assertEqual(payload["time_granularity"], "1d")
        self.assertEqual(payload["tier_decision"], "tier2_research_only_feature")
        self.assertTrue(payload["use_for_research_only"])
        self.assertTrue(payload["exclude_from_promote_truth"])

    def test_deribit_block_flow_review_emits_research_only_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flow_root = root / "deribit_block_flow"
            output_dir = root / "review"
            now_ns = int(datetime.now(UTC).timestamp() * 1_000_000_000)
            save_block_flow_rows(
                [
                    DeribitBlockFlowRow(
                        ts_ns=now_ns,
                        trade_id="t1",
                        block_trade_id="b1",
                        combo_id=None,
                        combo_trade_id=None,
                        instrument_name="BTC-25SEP26-72000-C",
                        option_type="call",
                        expiry_ts_ms=None,
                        strike=72000.0,
                        direction="buy",
                        amount=1000.0,
                        price=0.05,
                        index_price=70000.0,
                        mark_price=0.04,
                        iv=0.45,
                        liquidity="T",
                        is_block_trade=True,
                        is_combo_leg=False,
                        leg_count=1,
                        source="public_option_trade",
                    )
                ],
                flow_root,
            )
            write_deribit_block_flow_status(flow_root)

            argv = [
                "review_deribit_block_flow_admission.py",
                "--symbol",
                "BTCUSDT",
                "--deribit-block-flow-root",
                str(flow_root),
                "--output-dir",
                str(output_dir),
            ]
            with patch.object(sys, "argv", argv):
                BLOCK_FLOW_REVIEW.main()

            payload = json.loads((output_dir / "deribit_block_flow_admission_review.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["source_name"], "deribit_block_flow")
        self.assertEqual(payload["time_granularity"], "1d")
        self.assertTrue(payload["use_for_research_only"])
        self.assertTrue(payload["exclude_from_promote_truth"])


if __name__ == "__main__":
    unittest.main()
