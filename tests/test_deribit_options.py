import unittest
from unittest.mock import patch

from zenquant.domain import DeribitRiskPack
from zenquant.infra import InMemoryJetStream
from zenquant.ingestor.deribit_options import (
    DeribitOptionsIngestor,
    DeribitOptionsIngestorConfig,
)


def _summary(instrument_name: str, mark_iv: float, open_interest: float, volume_usd: float) -> dict[str, object]:
    return {
        "instrument_name": instrument_name,
        "open_interest": open_interest,
        "mark_iv": mark_iv,
        "volume_usd": volume_usd,
        "underlying_price": 100_000.0,
    }


def _instrument(
    instrument_name: str,
    *,
    expiry_ms: int,
    strike: float,
    option_type: str,
) -> dict[str, object]:
    return {
        "instrument_name": instrument_name,
        "expiration_timestamp": expiry_ms,
        "strike": strike,
        "option_type": option_type,
    }


class DeribitOptionsIngestorTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_once_publishes_risk_pack(self) -> None:
        now_ms = 1_700_000_000_000
        expiries = {
            "near": now_ms + 7 * 86_400_000,
            "atm": now_ms + 30 * 86_400_000,
            "far": now_ms + 90 * 86_400_000,
        }
        summaries = [
            _summary("BTC-NEAR-100000-C", 60.0, 200.0, 90_000.0),
            _summary("BTC-NEAR-100000-P", 62.0, 180.0, 70_000.0),
            _summary("BTC-ATM-100000-C", 65.0, 300.0, 120_000.0),
            _summary("BTC-ATM-100000-P", 67.0, 320.0, 80_000.0),
            _summary("BTC-ATM-90000-P", 72.0, 600.0, 45_000.0),
            _summary("BTC-ATM-110000-C", 58.0, 500.0, 75_000.0),
            _summary("BTC-FAR-100000-C", 70.0, 150.0, 40_000.0),
            _summary("BTC-FAR-100000-P", 72.0, 140.0, 30_000.0),
        ]
        instruments = [
            _instrument("BTC-NEAR-100000-C", expiry_ms=expiries["near"], strike=100_000.0, option_type="call"),
            _instrument("BTC-NEAR-100000-P", expiry_ms=expiries["near"], strike=100_000.0, option_type="put"),
            _instrument("BTC-ATM-100000-C", expiry_ms=expiries["atm"], strike=100_000.0, option_type="call"),
            _instrument("BTC-ATM-100000-P", expiry_ms=expiries["atm"], strike=100_000.0, option_type="put"),
            _instrument("BTC-ATM-90000-P", expiry_ms=expiries["atm"], strike=90_000.0, option_type="put"),
            _instrument("BTC-ATM-110000-C", expiry_ms=expiries["atm"], strike=110_000.0, option_type="call"),
            _instrument("BTC-FAR-100000-C", expiry_ms=expiries["far"], strike=100_000.0, option_type="call"),
            _instrument("BTC-FAR-100000-P", expiry_ms=expiries["far"], strike=100_000.0, option_type="put"),
        ]

        bus = InMemoryJetStream()
        ingestor = DeribitOptionsIngestor(
            bus=bus,
            config=DeribitOptionsIngestorConfig(currency="BTC", poll_interval_s=9999.0),
        )
        published: list[DeribitRiskPack] = []
        bus.subscribe(
            "risk.deribit.>",
            lambda _subject, payload: published.append(payload)
            if isinstance(payload, DeribitRiskPack)
            else None,
        )

        responses = {
            "get_book_summary_by_currency": summaries,
            "get_instruments": instruments,
            "get_index_price": {"index_price": 100_000.0},
        }

        def fake_fetch_json(url: str, *, timeout_s: float, query: dict[str, str]):
            for key, payload in responses.items():
                if key in url:
                    return payload
            raise AssertionError(url)

        with patch("zenquant.ingestor.deribit_options._fetch_json", side_effect=fake_fetch_json), patch(
            "zenquant.ingestor.deribit_options.time.time",
            return_value=now_ms / 1_000,
        ):
            pack = await ingestor.poll_once()

        self.assertIsNotNone(pack)
        assert pack is not None
        self.assertEqual(pack.symbol, "BTC")
        self.assertAlmostEqual(pack.atm_iv_30d, 66.0323, places=4)
        self.assertAlmostEqual(pack.term_structure_slope, 0.141169, places=6)
        self.assertAlmostEqual(pack.put_call_skew_25d, 14.0, places=4)
        self.assertAlmostEqual(pack.oi_concentration, 0.594142, places=6)
        self.assertAlmostEqual(pack.block_flow_imbalance, 0.181818, places=6)
        self.assertIn("FLOW_PROXY_VOLUME", pack.quality_flags)
        self.assertEqual(len(published), 1)

    async def test_poll_once_returns_none_on_empty_surface(self) -> None:
        bus = InMemoryJetStream()
        ingestor = DeribitOptionsIngestor(bus=bus)

        def fake_fetch_json(url: str, *, timeout_s: float, query: dict[str, str]):
            if "get_index_price" in url:
                return {"index_price": 100_000.0}
            return []

        with patch("zenquant.ingestor.deribit_options._fetch_json", side_effect=fake_fetch_json):
            pack = await ingestor.poll_once()

        self.assertIsNone(pack)
        self.assertEqual(ingestor.snapshot()["published"], 0)


if __name__ == "__main__":
    unittest.main()
