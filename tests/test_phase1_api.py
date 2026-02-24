import unittest

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:  # pragma: no cover - optional dependency in CI
    TestClient = None

from zenquant.app.server import create_app


@unittest.skipUnless(TestClient is not None, "fastapi is not installed")
class Phase1APITests(unittest.TestCase):
    def test_indicator_latest_and_history_endpoints(self) -> None:
        with TestClient(create_app()) as client:
            latest_resp = client.get(
                "/api/indicators/latest",
                params={"symbol": "BTCUSDT_PERP", "tf": "4h"},
            )
            self.assertEqual(latest_resp.status_code, 200)
            latest = latest_resp.json()
            self.assertEqual(latest["symbol"], "BTCUSDT_PERP")
            self.assertEqual(latest["tf"], "4h")
            self.assertIn("zen_pulse", latest["values"])
            self.assertIn("indicator_version", latest)

            history_resp = client.get(
                "/api/indicators/history",
                params={
                    "symbol": "BTCUSDT_PERP",
                    "tf": "4h",
                    "from": latest["cutoff_time"],
                    "to": latest["cutoff_time"],
                },
            )
            self.assertEqual(history_resp.status_code, 200)
            history = history_resp.json()
            self.assertGreaterEqual(len(history), 1)
            self.assertEqual(history[-1]["symbol"], "BTCUSDT_PERP")

            state_resp = client.get(
                "/api/state",
                params={"symbol": "BTCUSDT_PERP", "tf": "4h"},
            )
            self.assertEqual(state_resp.status_code, 200)
            state = state_resp.json()
            self.assertEqual(state["symbol"], "BTCUSDT_PERP")
            self.assertIn("latest_indicator", state)


if __name__ == "__main__":
    unittest.main()
