import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
