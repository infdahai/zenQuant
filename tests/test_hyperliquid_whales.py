import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.data.hyperliquid_whales import (
    HyperliquidWhaleAccount,
    collect_and_write_hyperliquid_whales,
    collect_hyperliquid_whale_positions,
    load_whale_watchlist,
    parse_account_spec,
)
from zenquant.historical import read_parquet_rows

ADDRESS_1 = "0x1111111111111111111111111111111111111111"
ADDRESS_2 = "0x2222222222222222222222222222222222222222"


def _state_payload() -> dict:
    return {
        "assetPositions": [
            {
                "position": {
                    "coin": "BTC",
                    "szi": "2.0",
                    "entryPx": "70000",
                    "positionValue": "140000",
                    "unrealizedPnl": "1000",
                    "returnOnEquity": "0.01",
                    "marginUsed": "7000",
                    "liquidationPx": "45000",
                    "leverage": {"type": "cross", "value": 20},
                    "maxTradeSzs": ["10", "9"],
                }
            },
            {
                "position": {
                    "coin": "ETH",
                    "szi": "-10.0",
                    "entryPx": "3000",
                    "positionValue": "30000",
                    "unrealizedPnl": "-100",
                    "returnOnEquity": "-0.003",
                    "marginUsed": "1500",
                    "liquidationPx": "3500",
                    "leverage": {"type": "isolated", "value": 10},
                    "maxTradeSzs": ["100", "100"],
                }
            },
        ],
        "marginSummary": {
            "accountValue": "250000",
            "totalMarginUsed": "8500",
            "totalNtlPos": "170000",
            "totalRawUsd": "241500",
        },
        "withdrawable": "200000",
    }


class HyperliquidWhaleTests(unittest.TestCase):
    def test_load_watchlist_skips_disabled_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlist.toml"
            path.write_text(
                f"""
[[accounts]]
name = "enabled"
address = "{ADDRESS_1.upper()}"

[[accounts]]
name = "disabled"
address = "{ADDRESS_2}"
enabled = false
""",
                encoding="utf-8",
            )

            accounts = load_whale_watchlist(path)

        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].name, "enabled")
        self.assertEqual(accounts[0].address, ADDRESS_1)

    def test_parse_account_spec_accepts_named_and_plain_address(self) -> None:
        named = parse_account_spec(f"alpha={ADDRESS_1}")
        plain = parse_account_spec(ADDRESS_2)

        self.assertEqual(named.name, "alpha")
        self.assertEqual(named.address, ADDRESS_1)
        self.assertEqual(plain.name, "0x22222222")
        self.assertEqual(plain.address, ADDRESS_2)

    def test_collect_positions_normalizes_account_and_filters_position_value(self) -> None:
        account = HyperliquidWhaleAccount(name="alpha", address=ADDRESS_1)
        with patch("zenquant.data.hyperliquid_whales._post_info", return_value=_state_payload()):
            accounts, positions, errors = collect_hyperliquid_whale_positions(
                [account],
                min_abs_position_value_usd=100_000.0,
                observed_at_ns=1_714_000_000_000_000_000,
            )

        self.assertEqual(errors, [])
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].account_value_usd, 250000.0)
        self.assertEqual(accounts[0].position_count, 2)
        self.assertEqual(accounts[0].gross_position_value_usd, 170000.0)
        self.assertEqual(accounts[0].net_position_value_usd, 110000.0)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].coin, "BTC")
        self.assertEqual(positions[0].side, "LONG")
        self.assertEqual(positions[0].signed_position_value_usd, 140000.0)
        self.assertEqual(positions[0].leverage_type, "cross")

    def test_collect_and_write_snapshots_writes_status_and_parquet(self) -> None:
        account = HyperliquidWhaleAccount(name="alpha", address=ADDRESS_1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "whales"
            with patch("zenquant.data.hyperliquid_whales._post_info", return_value=_state_payload()):
                status = collect_and_write_hyperliquid_whales(
                    accounts=[account],
                    root=root,
                    min_abs_position_value_usd=0.0,
                    coins={"BTC"},
                )

            status_path = root / "_collector_status.json"
            saved = json.loads(status_path.read_text(encoding="utf-8"))
            account_rows = read_parquet_rows(str(root / "accounts" / "*" / "*.parquet"))
            position_rows = read_parquet_rows(str(root / "positions" / "*" / "*.parquet"))

        self.assertIsNone(status["blocked_by"])
        self.assertEqual(saved["account_count"], 1)
        self.assertEqual(saved["position_count"], 1)
        self.assertEqual(len(account_rows), 1)
        self.assertEqual(len(position_rows), 1)
        self.assertEqual(position_rows[0]["coin"], "BTC")


if __name__ == "__main__":
    unittest.main()
