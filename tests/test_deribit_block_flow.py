import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from zenquant.data.deribit_block_flow import (
    DeribitBlockFlowRow,
    collect_deribit_block_flow_status,
    fetch_public_block_rfq_trades,
    fetch_public_option_trades,
    save_block_flow_rows,
    write_deribit_block_flow_status,
)
from zenquant.research.deribit_large_option_flow import (
    build_large_option_flow_records,
    filter_large_option_flow_rows,
)


class DeribitBlockFlowTests(unittest.TestCase):
    @patch("zenquant.data.deribit_block_flow._fetch_json")
    def test_public_option_trade_normalization(self, fetch_json) -> None:
        fetch_json.return_value = {
            "trades": [
                {
                    "trade_id": "48079289",
                    "timestamp": 1713446400000,
                    "instrument_name": "BTC-24APR26-72000-C",
                    "direction": "buy",
                    "price": 0.0525,
                    "amount": 3,
                    "index_price": 66930.31,
                    "mark_price": 0.05253883,
                    "iv": 45.91,
                    "block_trade_id": "154",
                    "block_trade_leg_count": 3,
                    "combo_id": "BTC-CS-24APR26-70000_72000",
                    "combo_trade_id": 99,
                }
            ]
        }

        rows = fetch_public_option_trades(
            currency="BTC",
            start_timestamp_ms=1713440000000,
            end_timestamp_ms=1713450000000,
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.trade_id, "48079289")
        self.assertEqual(row.option_type, "c")
        self.assertTrue(row.is_block_trade)
        self.assertTrue(row.is_combo_leg)
        self.assertEqual(row.leg_count, 3)

    @patch("zenquant.data.deribit_block_flow._fetch_json")
    def test_block_rfq_trades_expand_legs(self, fetch_json) -> None:
        fetch_json.return_value = {
            "block_rfqs": [
                {
                    "id": 6611,
                    "timestamp": 1739803305362,
                    "combo_id": "BTC-CS-28FEB25-100000_106000",
                    "legs": [
                        {
                            "price": 0.1,
                            "direction": "buy",
                            "instrument_name": "BTC-28FEB25-100000-C",
                            "ratio": 1,
                        },
                        {
                            "price": 0.05,
                            "direction": "sell",
                            "instrument_name": "BTC-28FEB25-106000-C",
                            "ratio": 1,
                        },
                    ],
                    "amount": 12.5,
                    "direction": "sell",
                    "mark_price": 0.010356754,
                    "index_prices": {"btc_usd": 96000},
                }
            ]
        }

        rows = fetch_public_block_rfq_trades(currency="BTC")

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.is_combo_leg for row in rows))
        self.assertTrue(all(row.is_block_trade for row in rows))
        self.assertEqual(rows[0].leg_count, 2)
        _, query = fetch_json.call_args.args
        self.assertEqual(query["count"], "25")

    def test_save_block_flow_rows_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = [
                DeribitBlockFlowRow(
                    ts_ns=1713446400000000000,
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
                ),
                DeribitBlockFlowRow(
                    ts_ns=1713446400000000000,
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
                ),
            ]
            save_block_flow_rows(payload, root)
            status_path = write_deribit_block_flow_status(root)
            status = collect_deribit_block_flow_status(root)
            frame = pd.read_parquet(root / "BTC" / "2024-04" / "data.parquet")

            self.assertTrue(status.enabled)
            self.assertTrue(status_path.exists())
            self.assertEqual(len(frame), 1)

    def test_large_option_flow_filter_respects_amount_notional_and_block_gate(self) -> None:
        rows = [
            DeribitBlockFlowRow(
                ts_ns=1713446400000000000,
                trade_id="small",
                block_trade_id="block-small",
                combo_id=None,
                combo_trade_id=None,
                instrument_name="BTC-24APR26-72000-C",
                option_type="c",
                expiry_ts_ms=1776988800000,
                strike=72000.0,
                direction="buy",
                amount=10.0,
                price=0.0525,
                index_price=70000.0,
                mark_price=0.05253883,
                iv=45.91,
                liquidity=None,
                is_block_trade=True,
                is_combo_leg=False,
                leg_count=1,
                source="public_option_trade",
            ),
            DeribitBlockFlowRow(
                ts_ns=1713446460000000000,
                trade_id="screen-large",
                block_trade_id=None,
                combo_id=None,
                combo_trade_id=None,
                instrument_name="BTC-24APR26-74000-C",
                option_type="c",
                expiry_ts_ms=1776988800000,
                strike=74000.0,
                direction="sell",
                amount=50.0,
                price=0.031,
                index_price=70000.0,
                mark_price=0.0312,
                iv=44.2,
                liquidity=None,
                is_block_trade=False,
                is_combo_leg=False,
                leg_count=1,
                source="public_option_trade",
            ),
            DeribitBlockFlowRow(
                ts_ns=1713446520000000000,
                trade_id="block-large",
                block_trade_id="block-1",
                combo_id="BTC-CS-24APR26-70000_72000",
                combo_trade_id="combo-1",
                instrument_name="BTC-24APR26-70000-C",
                option_type="c",
                expiry_ts_ms=1776988800000,
                strike=70000.0,
                direction="buy",
                amount=30.0,
                price=0.08,
                index_price=70000.0,
                mark_price=0.0801,
                iv=46.0,
                liquidity=None,
                is_block_trade=True,
                is_combo_leg=True,
                leg_count=2,
                source="public_option_trade",
            ),
        ]

        large_rows = filter_large_option_flow_rows(rows, min_amount=25.0, require_block_trade=True)
        self.assertEqual([row.trade_id for row in large_rows], ["block-large"])

        notional_rows = filter_large_option_flow_rows(
            rows,
            min_amount=25.0,
            min_underlying_notional_usd=3_000_000.0,
        )
        self.assertEqual([row.trade_id for row in notional_rows], ["screen-large"])

    def test_large_option_flow_records_are_latest_first_and_json_safe(self) -> None:
        rows = [
            DeribitBlockFlowRow(
                ts_ns=1713446400000000000,
                trade_id="older",
                block_trade_id=None,
                combo_id=None,
                combo_trade_id=None,
                instrument_name="BTC-24APR26-72000-C",
                option_type="c",
                expiry_ts_ms=1776988800000,
                strike=72000.0,
                direction="buy",
                amount=25.0,
                price=0.0525,
                index_price=70000.0,
                mark_price=0.05253883,
                iv=45.91,
                liquidity=None,
                is_block_trade=False,
                is_combo_leg=False,
                leg_count=1,
                source="public_option_trade",
            ),
            DeribitBlockFlowRow(
                ts_ns=1713446460000000000,
                trade_id="newer",
                block_trade_id="block-2",
                combo_id=None,
                combo_trade_id=None,
                instrument_name="BTC-24APR26-74000-P",
                option_type="p",
                expiry_ts_ms=1776988800000,
                strike=74000.0,
                direction="sell",
                amount=50.0,
                price=0.031,
                index_price=70000.0,
                mark_price=0.0312,
                iv=44.2,
                liquidity=None,
                is_block_trade=True,
                is_combo_leg=False,
                leg_count=1,
                source="public_option_trade",
            ),
        ]

        records = build_large_option_flow_records(rows, min_amount=25.0, limit=1)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["trade_id"], "newer")
        self.assertEqual(records[0]["expiry"], "2026-04-24")
        self.assertEqual(records[0]["underlying_notional_usd"], 3_500_000.0)
        self.assertEqual(records[0]["premium_usd"], 108_500.0)


if __name__ == "__main__":
    unittest.main()
