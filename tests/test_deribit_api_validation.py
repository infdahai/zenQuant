import asyncio
import os
import unittest
from unittest.mock import patch

from zenquant.infra import InMemoryJetStream
from zenquant.ingestor import DeribitOptionsIngestor, DeribitOptionsIngestorConfig


def _network_tests_enabled() -> bool:
    return os.getenv("ZENQUANT_RUN_DERIBIT_NETWORK_TESTS", "").strip().lower() in {"1", "true", "yes"}


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


def _valid_deribit_responses(now_ms: int) -> dict[str, object]:
    expiries = {
        "near": now_ms + 7 * 86_400_000,
        "atm": now_ms + 30 * 86_400_000,
        "far": now_ms + 90 * 86_400_000,
    }
    return {
        "get_book_summary_by_currency": [
            _summary("BTC-NEAR-100000-C", 60.0, 200.0, 90_000.0),
            _summary("BTC-NEAR-100000-P", 62.0, 180.0, 70_000.0),
            _summary("BTC-ATM-100000-C", 65.0, 300.0, 120_000.0),
            _summary("BTC-ATM-100000-P", 67.0, 320.0, 80_000.0),
            _summary("BTC-ATM-90000-P", 72.0, 600.0, 45_000.0),
            _summary("BTC-ATM-110000-C", 58.0, 500.0, 75_000.0),
            _summary("BTC-FAR-100000-C", 70.0, 150.0, 40_000.0),
            _summary("BTC-FAR-100000-P", 72.0, 140.0, 30_000.0),
        ],
        "get_instruments": [
            _instrument("BTC-NEAR-100000-C", expiry_ms=expiries["near"], strike=100_000.0, option_type="call"),
            _instrument("BTC-NEAR-100000-P", expiry_ms=expiries["near"], strike=100_000.0, option_type="put"),
            _instrument("BTC-ATM-100000-C", expiry_ms=expiries["atm"], strike=100_000.0, option_type="call"),
            _instrument("BTC-ATM-100000-P", expiry_ms=expiries["atm"], strike=100_000.0, option_type="put"),
            _instrument("BTC-ATM-90000-P", expiry_ms=expiries["atm"], strike=90_000.0, option_type="put"),
            _instrument("BTC-ATM-110000-C", expiry_ms=expiries["atm"], strike=110_000.0, option_type="call"),
            _instrument("BTC-FAR-100000-C", expiry_ms=expiries["far"], strike=100_000.0, option_type="call"),
            _instrument("BTC-FAR-100000-P", expiry_ms=expiries["far"], strike=100_000.0, option_type="put"),
        ],
        "get_index_price": {"index_price": 100_000.0},
    }


class DeribitValidationContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_loop_records_fail_open_error_state(self) -> None:
        bus = InMemoryJetStream()
        ingestor = DeribitOptionsIngestor(
            bus=bus,
            config=DeribitOptionsIngestorConfig(currency="BTC", poll_interval_s=0.01),
        )

        with patch(
            "zenquant.ingestor.deribit_options._fetch_json", side_effect=RuntimeError("temporary deribit outage")
        ):
            await ingestor.start()
            await asyncio.sleep(0.03)
            await ingestor.stop()

        snapshot = ingestor.snapshot()
        self.assertGreaterEqual(snapshot["polls"], 1)
        self.assertEqual(snapshot["published"], 0)
        self.assertIn("temporary deribit outage", snapshot["last_error"])
        self.assertEqual(snapshot["last_success_ns"], 0)

    async def test_background_loop_recovers_and_clears_last_error(self) -> None:
        now_ms = 1_700_000_000_000
        responses = _valid_deribit_responses(now_ms)
        bus = InMemoryJetStream()
        ingestor = DeribitOptionsIngestor(
            bus=bus,
            config=DeribitOptionsIngestorConfig(currency="BTC", poll_interval_s=0.01),
        )

        failed = {"done": False}

        def fake_fetch_json(url: str, *, timeout_s: float, query: dict[str, str]):
            if not failed["done"]:
                failed["done"] = True
                raise RuntimeError("temporary deribit outage")
            for key, payload in responses.items():
                if key in url:
                    return payload
            raise AssertionError(url)

        with (
            patch("zenquant.ingestor.deribit_options._fetch_json", side_effect=fake_fetch_json),
            patch(
                "zenquant.ingestor.deribit_options.time.time",
                return_value=now_ms / 1_000,
            ),
        ):
            await ingestor.start()
            await asyncio.sleep(0.05)
            await ingestor.stop()

        snapshot = ingestor.snapshot()
        self.assertGreaterEqual(snapshot["polls"], 2)
        self.assertGreaterEqual(snapshot["published"], 1)
        self.assertEqual(snapshot["last_error"], "")
        self.assertGreater(snapshot["last_success_ns"], 0)


class DeribitAPIValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not _network_tests_enabled():
            raise unittest.SkipTest("Set ZENQUANT_RUN_DERIBIT_NETWORK_TESTS=1 to run live Deribit validation")

    async def test_api_availability_and_metric_ranges(self) -> None:
        bus = InMemoryJetStream()
        ingestor = DeribitOptionsIngestor(
            bus=bus,
            config=DeribitOptionsIngestorConfig(currency="BTC", poll_interval_s=1.0),
        )

        published = []
        bus.subscribe("risk.deribit.>", lambda _subject, payload: published.append(payload))

        pack = await ingestor.poll_once()

        self.assertIsNotNone(pack, "Deribit API returned no pack")
        assert pack is not None
        self.assertEqual(pack.symbol, "BTC")
        self.assertGreater(pack.atm_iv_30d, 1.0, "ATM IV should be positive and reported in percent points")
        self.assertLess(pack.atm_iv_30d, 400.0, "ATM IV appears implausibly high")
        self.assertGreaterEqual(pack.oi_concentration, 0.0)
        self.assertLessEqual(pack.oi_concentration, 1.0)
        self.assertGreaterEqual(pack.block_flow_imbalance, -1.0)
        self.assertLessEqual(pack.block_flow_imbalance, 1.0)
        self.assertIn("FLOW_PROXY_VOLUME", pack.quality_flags)
        self.assertEqual(len(published), 1, "poll_once should publish exactly one pack")

    async def test_poll_once_produces_fresh_timestamps(self) -> None:
        bus = InMemoryJetStream()
        ingestor = DeribitOptionsIngestor(bus=bus)

        first = await ingestor.poll_once()
        second = await ingestor.poll_once()

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        self.assertNotEqual(first.ts_ns, second.ts_ns)
        self.assertIn("FLOW_PROXY_VOLUME", second.quality_flags)

    async def test_metric_definition_guardrails(self) -> None:
        bus = InMemoryJetStream()
        ingestor = DeribitOptionsIngestor(bus=bus)

        pack = await ingestor.poll_once()

        self.assertIsNotNone(pack)
        assert pack is not None
        self.assertIn("FLOW_PROXY_VOLUME", pack.quality_flags)
        self.assertIsInstance(pack.term_structure_slope, float)
        self.assertIsInstance(pack.put_call_skew_25d, float)


if __name__ == "__main__":
    unittest.main()
