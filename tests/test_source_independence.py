import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from zenquant.data.deribit_block_flow import DeribitBlockFlowRow, save_block_flow_rows
from zenquant.research.source_independence import (
    build_deribit_block_flow_independence_audit,
    build_mvrv_independence_audit,
    load_source_independence_audit,
    write_source_independence_audit,
)


def _ts(day_offset: int) -> int:
    base = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day_offset)
    return int(base.timestamp() * 1_000_000_000)


class SourceIndependenceTests(unittest.TestCase):
    def test_mvrv_independence_audit_writes_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            macro_root = root / "macro"
            data_root = root / "binance"
            output_dir = root / "signal_combination"
            (macro_root / "btc_mvrv").mkdir(parents=True, exist_ok=True)
            (macro_root / "btc_etf_netflow").mkdir(parents=True, exist_ok=True)
            (macro_root / "gold").mkdir(parents=True, exist_ok=True)
            (data_root / "klines" / "BTCUSDT" / "1d").mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                {"ts_ns": [_ts(i) for i in range(90)], "value": [1.0 + i * 0.01 for i in range(90)]}
            ).to_parquet(
                macro_root / "btc_mvrv" / "data.parquet",
                index=False,
            )
            pd.DataFrame({"ts_ns": [_ts(i) for i in range(90)], "value": [10.0 + i for i in range(90)]}).to_parquet(
                macro_root / "btc_etf_netflow" / "data.parquet",
                index=False,
            )
            pd.DataFrame(
                {"ts_ns": [_ts(i) for i in range(90)], "value": [180.0 + i * 0.1 for i in range(90)]}
            ).to_parquet(
                macro_root / "gold" / "data.parquet",
                index=False,
            )
            pd.DataFrame(
                {
                    "close_time_ns": [_ts(i) for i in range(90)],
                    "close": [30000.0 + i * 100 for i in range(90)],
                }
            ).to_parquet(data_root / "klines" / "BTCUSDT" / "1d" / "data.parquet", index=False)

            audit = build_mvrv_independence_audit(symbol="BTCUSDT", macro_root=macro_root, data_root=data_root)
            payload = write_source_independence_audit(audit, output_dir)
            loaded = load_source_independence_audit("btc_mvrv", review_dir=output_dir)

        self.assertIn("json_path", payload)
        self.assertTrue(loaded["use_for_research_only"])
        self.assertIn("max_abs_correlation", loaded)

    def test_deribit_block_flow_independence_defaults_research_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            macro_root = root / "macro"
            data_root = root / "binance"
            flow_root = root / "deribit_block_flow"
            (macro_root / "btc_mvrv").mkdir(parents=True, exist_ok=True)
            (macro_root / "btc_etf_netflow").mkdir(parents=True, exist_ok=True)
            (data_root / "klines" / "BTCUSDT" / "1d").mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {"ts_ns": [_ts(i) for i in range(30)], "value": [1.2 + i * 0.01 for i in range(30)]}
            ).to_parquet(
                macro_root / "btc_mvrv" / "data.parquet",
                index=False,
            )
            pd.DataFrame({"ts_ns": [_ts(i) for i in range(30)], "value": [5.0 + i for i in range(30)]}).to_parquet(
                macro_root / "btc_etf_netflow" / "data.parquet",
                index=False,
            )
            pd.DataFrame(
                {
                    "close_time_ns": [_ts(i) for i in range(30)],
                    "close": [40000.0 + i * 50 for i in range(30)],
                }
            ).to_parquet(data_root / "klines" / "BTCUSDT" / "1d" / "data.parquet", index=False)
            save_block_flow_rows(
                [
                    DeribitBlockFlowRow(
                        ts_ns=_ts(i),
                        trade_id=f"t{i}",
                        block_trade_id=f"b{i}",
                        combo_id=None,
                        combo_trade_id=None,
                        instrument_name="BTC-25SEP26-72000-C",
                        option_type="call",
                        expiry_ts_ms=None,
                        strike=72000.0,
                        direction="buy",
                        amount=10.0 + i,
                        price=0.05,
                        index_price=40000.0 + i * 100,
                        mark_price=0.04,
                        iv=0.5,
                        liquidity="T",
                        is_block_trade=True,
                        is_combo_leg=False,
                        leg_count=1,
                        source="public_option_trade",
                    )
                    for i in range(30)
                ],
                flow_root,
            )

            audit = build_deribit_block_flow_independence_audit(
                symbol="BTCUSDT",
                deribit_block_flow_root=flow_root,
                macro_root=macro_root,
                data_root=data_root,
            )

        self.assertTrue(audit.use_for_research_only)
        self.assertFalse(audit.allowed_in_state_features)


if __name__ == "__main__":
    unittest.main()
