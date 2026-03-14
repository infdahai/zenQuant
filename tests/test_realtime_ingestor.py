import unittest

from zenquant.ingestor.realtime import (
    _parse_depth_side,
    _to_binance_symbol,
    _to_okx_inst_id,
)


class RealTimeIngestorTests(unittest.TestCase):
    def test_symbol_mappings_cover_supported_perps(self) -> None:
        self.assertEqual(_to_binance_symbol("BTCUSDT_PERP"), "BTCUSDT")
        self.assertEqual(_to_binance_symbol("ETHUSDT_PERP"), "ETHUSDT")
        self.assertIsNone(_to_binance_symbol("SOLUSDT_PERP"))

        self.assertEqual(_to_okx_inst_id("BTCUSDT_PERP"), "BTC-USDT-SWAP")
        self.assertEqual(_to_okx_inst_id("ETHUSDT_PERP"), "ETH-USDT-SWAP")
        self.assertIsNone(_to_okx_inst_id("SOLUSDT_PERP"))

    def test_parse_depth_side_filters_invalid_rows(self) -> None:
        parsed = _parse_depth_side(
            [
                ["100.0", "1.5"],
                ["0", "2.0"],
                ["101.0", "-1.0"],
                ["102.0", "3.0", "ignored"],
                "bad-row",
            ],
            levels=5,
        )

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].price, 100.0)
        self.assertEqual(parsed[0].quantity, 1.5)
        self.assertEqual(parsed[1].price, 102.0)
        self.assertEqual(parsed[1].quantity, 3.0)


if __name__ == "__main__":
    unittest.main()
