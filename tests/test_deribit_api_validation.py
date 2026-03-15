import os
import unittest

from zenquant.ingestor import DeribitOptionsIngestor, DeribitOptionsIngestorConfig
from zenquant.infra import InMemoryJetStream


def _network_tests_enabled() -> bool:
    return os.getenv("ZENQUANT_RUN_DERIBIT_NETWORK_TESTS", "").strip().lower() in {"1", "true", "yes"}


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
