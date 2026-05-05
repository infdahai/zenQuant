import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from zenquant.research.deribit_history_backfill import (
    backfill_history_option_trades,
    fetch_history_option_trades_paginated,
    row_to_dict,
)


class DeribitHistoryBackfillTests(unittest.TestCase):
    @patch("zenquant.research.deribit_history_backfill._fetch_history_json")
    def test_fetch_history_option_trades_paginates_and_normalizes(self, fetch_json) -> None:
        fetch_json.side_effect = [
            {
                "trades": [
                    _trade("t1", 1_690_848_000_000, "BTC-2AUG23-29250-P", 2.5),
                    _trade("t2", 1_690_848_000_100, "BTC-2AUG23-29250-C", 0.1),
                ],
                "has_more": True,
            },
            {
                "trades": [_trade("t3", 1_690_848_000_200, "BTC-2AUG23-30000-C", 4.0)],
                "has_more": False,
            },
        ]

        rows, requests_used = fetch_history_option_trades_paginated(
            currency="BTC",
            start_timestamp_ms=1_690_848_000_000,
            end_timestamp_ms=1_690_934_400_000,
            count=2,
        )

        self.assertEqual(requests_used, 2)
        self.assertEqual([row.trade_id for row in rows], ["t1", "t2", "t3"])
        self.assertEqual(rows[0].option_type, "p")
        _, second_query = fetch_json.call_args_list[1].args
        self.assertEqual(second_query["start_timestamp"], "1690848000101")

    @patch("zenquant.research.deribit_history_backfill._fetch_history_json")
    def test_backfill_history_option_trades_writes_monthly_partition(self, fetch_json) -> None:
        fetch_json.return_value = {
            "trades": [_trade("t1", 1_690_848_000_000, "BTC-2AUG23-29250-P", 2.5)],
            "has_more": False,
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = backfill_history_option_trades(
                currency="BTC",
                start_timestamp_ms=1_690_848_000_000,
                end_timestamp_ms=1_690_934_400_000,
                output_root=root,
                chunk_hours=24.0,
                count=10_000,
            )
            frame = pd.read_parquet(root / "BTC" / "2023-08" / "data.parquet")

        self.assertEqual(payload["rows_written"], 1)
        self.assertTrue(payload["completed"])
        self.assertEqual(payload["completed_until_timestamp_ms"], 1_690_934_400_000)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["trade_id"], "t1")

    def test_row_to_dict_is_json_safe_shape(self) -> None:
        rows, _ = fetch_history_option_trades_paginated_from_payload(
            {
                "trades": [_trade("t1", 1_690_848_000_000, "BTC-2AUG23-29250-P", 2.5)],
                "has_more": False,
            }
        )

        payload = row_to_dict(rows[0])

        self.assertEqual(payload["instrument_name"], "BTC-2AUG23-29250-P")
        self.assertEqual(payload["source"], "public_option_trade")


def fetch_history_option_trades_paginated_from_payload(payload):
    with patch("zenquant.research.deribit_history_backfill._fetch_history_json", return_value=payload):
        return fetch_history_option_trades_paginated(
            currency="BTC",
            start_timestamp_ms=1_690_848_000_000,
            end_timestamp_ms=1_690_934_400_000,
        )


def _trade(trade_id: str, timestamp: int, instrument_name: str, amount: float) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "timestamp": timestamp,
        "instrument_name": instrument_name,
        "direction": "sell",
        "price": 0.0065,
        "amount": amount,
        "index_price": 29_226.93,
        "mark_price": 0.00699128,
        "iv": 25.83,
    }


if __name__ == "__main__":
    unittest.main()
