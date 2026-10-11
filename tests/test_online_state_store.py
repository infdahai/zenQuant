import unittest
from unittest.mock import patch

from zenquant.domain import DeribitRiskPack
from zenquant.infra import InMemoryJetStream, OnlineStateStore, OnlineStateStoreConfig


class _FakeCursor:
    def __init__(self) -> None:
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql, params=()) -> None:
        self.executed.append((sql, params))


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self._cursor


class OnlineStateStoreTests(unittest.TestCase):
    def test_deribit_persistence_uses_explicit_moneyness_proxy_column(self) -> None:
        store = OnlineStateStore(
            bus=InMemoryJetStream(),
            config=OnlineStateStoreConfig(dsn="postgresql://example", apply_migrations=False),
        )
        store._on_deribit(
            "risk.deribit.BTC",
            DeribitRiskPack(
                symbol="BTC",
                ts_ns=1,
                atm_iv_30d=0.6,
                term_structure_slope=0.1,
                put_call_moneyness_skew_proxy=0.05,
                oi_concentration=0.2,
                block_flow_imbalance=0.0,
            ),
        )
        sql, params = store._queue.get_nowait()
        self.assertIn("put_call_moneyness_skew_proxy", sql)
        self.assertNotIn("put_call_skew_25d", sql)
        self.assertEqual(params[4], 0.05)

    def test_run_retries_after_connection_failure(self) -> None:
        bus = InMemoryJetStream()
        store = OnlineStateStore(
            bus=bus,
            config=OnlineStateStoreConfig(
                dsn="postgresql://example",
                apply_migrations=False,
                reconnect_backoff_s=0.001,
                max_reconnect_backoff_s=0.001,
            ),
        )
        cursor = _FakeCursor()
        attempts = {"count": 0}

        def fake_connect(*args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("db unavailable")
            return _FakeConnection(cursor)

        store._enqueue("SELECT 1", ())
        store._enqueue("__STOP__", ())

        with (
            patch("zenquant.infra.db.connect", side_effect=fake_connect),
            patch("zenquant.infra.db.time.sleep", return_value=None),
        ):
            store._run()

        snapshot = store.snapshot()
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(store._writes, 1)
        self.assertFalse(snapshot["connected"])
        self.assertEqual(cursor.executed, [("SELECT 1", ())])


if __name__ == "__main__":
    unittest.main()
