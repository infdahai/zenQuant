import os
import time
import unittest

from psycopg import connect

from zenquant.infra import InMemoryJetStream, OnlineStateStore, OnlineStateStoreConfig
from zenquant.ingestor import DeribitOptionsIngestor


def _network_tests_enabled() -> bool:
    return os.getenv("ZENQUANT_RUN_DERIBIT_NETWORK_TESTS", "").strip().lower() in {"1", "true", "yes"}


class DeribitPostgreSQLTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dsn = os.getenv("ZENQUANT_TEST_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("ZENQUANT_TEST_PG_DSN not set")
        if not _network_tests_enabled():
            raise unittest.SkipTest(
                "Set ZENQUANT_RUN_DERIBIT_NETWORK_TESTS=1 to run live Deribit persistence validation"
            )
        cls.dsn = dsn
        with connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE deribit_risk_packs")

    async def test_deribit_pack_persists_to_postgresql(self) -> None:
        bus = InMemoryJetStream()
        store = OnlineStateStore(
            bus=bus,
            config=OnlineStateStoreConfig(dsn=self.dsn, apply_migrations=False),
        )
        store.start()
        self.addCleanup(store.stop)

        ingestor = DeribitOptionsIngestor(bus=bus)
        pack = await ingestor.poll_once()

        self.assertIsNotNone(pack, "Expected a live Deribit pack before checking persistence")
        time.sleep(1.0)

        with connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT symbol, atm_iv_30d, term_structure_slope, put_call_skew_25d,
                           oi_concentration, block_flow_imbalance, quality_flags
                    FROM deribit_risk_packs
                    ORDER BY ts_ns DESC
                    LIMIT 1
                    """
            )
            row = cur.fetchone()

        self.assertIsNotNone(row, "No Deribit pack was persisted")
        assert row is not None
        self.assertEqual(row[0], "BTC")
        self.assertGreater(row[1], 1.0)
        self.assertGreaterEqual(row[4], 0.0)
        self.assertLessEqual(row[4], 1.0)
        self.assertGreaterEqual(row[5], -1.0)
        self.assertLessEqual(row[5], 1.0)
        self.assertIn("FLOW_PROXY_VOLUME", row[6])


if __name__ == "__main__":
    unittest.main()
