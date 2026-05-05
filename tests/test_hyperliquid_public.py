import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.data.hyperliquid_public import (
    build_hyperliquid_proxy_history,
    fetch_candle_history,
    fetch_candle_snapshot,
    fetch_funding_history,
    fetch_funding_history_paginated,
    fetch_recent_trades,
    recent_trades_to_buckets,
    write_hyperliquid_raw,
)


class HyperliquidPublicTests(unittest.TestCase):
    def test_fetch_recent_trades_normalizes_shape(self) -> None:
        payload = [
            {"coin": "BTC", "side": "B", "px": "70000", "sz": "0.5", "time": 1711929600000, "tid": 1, "hash": "0x1"}
        ]
        with patch("zenquant.data.hyperliquid_public._post_info", return_value=payload):
            rows = fetch_recent_trades("BTC")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].symbol, "BTCUSDT")
        self.assertEqual(rows[0].side, "BUY")
        self.assertEqual(rows[0].notional_usd, 35000.0)

    def test_fetch_candle_and_funding_history_parse_payloads(self) -> None:
        candle_payload = [
            {
                "t": 1711929600000,
                "T": 1711943999999,
                "s": "BTC",
                "i": "4h",
                "o": "70000",
                "h": "70500",
                "l": "69000",
                "c": "69500",
                "v": "1000",
                "n": 10,
            }
        ]
        funding_payload = [{"coin": "BTC", "fundingRate": "0.0001", "premium": "0.001", "time": 1711929600000}]
        with (
            patch("zenquant.data.hyperliquid_public._post_info", side_effect=[candle_payload, funding_payload]),
        ):
            candles = fetch_candle_snapshot("BTC", "4h", 1711929600000, 1711944000000)
            funding = fetch_funding_history("BTC", 1711929600000, 1711944000000)

        self.assertEqual(candles[0].symbol, "BTCUSDT")
        self.assertEqual(candles[0].interval, "4h")
        self.assertEqual(funding[0].symbol, "BTCUSDT")

    def test_fetch_history_chunks_and_deduplicates_rows(self) -> None:
        candle_payloads = [
            [
                {
                    "t": 1711929600000,
                    "T": 1711943999999,
                    "s": "BTC",
                    "i": "4h",
                    "o": "70000",
                    "h": "70500",
                    "l": "69000",
                    "c": "69500",
                    "v": "1000",
                    "n": 10,
                }
            ],
            [
                {
                    "t": 1711944000000,
                    "T": 1711958399999,
                    "s": "BTC",
                    "i": "4h",
                    "o": "69500",
                    "h": "70000",
                    "l": "69000",
                    "c": "69900",
                    "v": "900",
                    "n": 11,
                }
            ],
        ]
        with patch("zenquant.data.hyperliquid_public._post_info", side_effect=candle_payloads):
            candles = fetch_candle_history(
                "BTC",
                "4h",
                1711929600000,
                1711958400000,
                chunk_ms=4 * 3600 * 1000,
            )

        self.assertEqual([row.start_ms for row in candles], [1711929600000, 1711944000000])

        funding_payloads = [
            [{"coin": "BTC", "fundingRate": "0.0001", "premium": "0.001", "time": 1711929600000}],
            [{"coin": "BTC", "fundingRate": "0.0002", "premium": "0.002", "time": 1711944000000}],
        ]
        with patch("zenquant.data.hyperliquid_public._post_info", side_effect=funding_payloads):
            funding = fetch_funding_history_paginated(
                "BTC",
                1711929600000,
                1711958400000,
                chunk_ms=4 * 3600 * 1000,
            )

        self.assertEqual([row.ts_ms for row in funding], [1711929600000, 1711944000000])

    def test_build_proxy_history_marks_short_backfill_as_blocked(self) -> None:
        candle_payload = [
            {
                "t": 1711929600000,
                "T": 1711943999999,
                "s": "BTC",
                "i": "4h",
                "o": "70000",
                "h": "70500",
                "l": "69000",
                "c": "69500",
                "v": "1000",
                "n": 10,
            }
        ]
        funding_payload = [{"coin": "BTC", "fundingRate": "0.0001", "premium": "0.001", "time": 1711929600000}]
        with patch("zenquant.data.hyperliquid_public._post_info", side_effect=[candle_payload, funding_payload]):
            candles, funding, buckets, status = build_hyperliquid_proxy_history(
                coin="BTC",
                start_ms=1711929600000,
                end_ms=1711944000000,
            )

        self.assertEqual(len(candles), 1)
        self.assertEqual(len(funding), 1)
        self.assertEqual(len(buckets), 1)
        self.assertEqual(status.blocked_by, "hyperliquid_public_history_limit")

    def test_recent_trades_to_buckets_filters_small_notional(self) -> None:
        payload = [
            {"coin": "BTC", "side": "B", "px": "70000", "sz": "0.0001", "time": 1711929600000, "tid": 1, "hash": "0x1"},
            {"coin": "BTC", "side": "A", "px": "70000", "sz": "1.0", "time": 1711929600000, "tid": 2, "hash": "0x2"},
        ]
        with patch("zenquant.data.hyperliquid_public._post_info", return_value=payload):
            trades = fetch_recent_trades("BTC")
        buckets = recent_trades_to_buckets(trades)
        self.assertEqual(len(buckets), 1)
        self.assertAlmostEqual(buckets[0].total_liq_usd, 70000.0, places=2)

    def test_write_hyperliquid_raw_creates_expected_files(self) -> None:
        candle_payload = [
            {
                "t": 1711929600000,
                "T": 1711943999999,
                "s": "BTC",
                "i": "4h",
                "o": "70000",
                "h": "70500",
                "l": "69000",
                "c": "69500",
                "v": "1000",
                "n": 10,
            }
        ]
        with patch("zenquant.data.hyperliquid_public._post_info", return_value=candle_payload):
            candles = fetch_candle_snapshot("BTC", "4h", 1711929600000, 1711944000000)
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_hyperliquid_raw(Path(tmp), candles=candles)
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].exists())


if __name__ == "__main__":
    unittest.main()
