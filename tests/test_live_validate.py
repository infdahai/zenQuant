import unittest
from unittest.mock import patch

from zenquant.app.live_validate import LiveValidationConfig, run_validation


def _state_payload(*, writes: int = 0, connected: bool = False) -> dict:
    return {
        "runtime_status": {
            "bootstrap_phase": "ready",
            "market_connected": True,
            "last_market_event_ns": 1_000_000_000,
            "database": {
                "enabled": True,
                "connected": connected,
                "writes": writes,
                "dropped": 0,
            },
        },
        "latest_indicator": {"cutoff_time_ns": 1_000_000_000},
        "market_state": {"regime": "TREND"},
        "active_playbook": {"playbook_id": "BULL_CONTINUATION"},
        "latest_sizing": {"final_leverage": 1.2},
        "latest_target": {"target_qty": 1.0},
        "directional_bias": {"direction": 1.0},
    }


class LiveValidateTests(unittest.TestCase):
    def test_run_validation_reports_success(self) -> None:
        responses = {
            "healthz": {"status": "ok", "mode": "live"},
            "tf=4h": _state_payload(writes=4, connected=True),
            "tf=1d": _state_payload(writes=4, connected=True),
        }

        def fake_fetch(url: str):
            for key, payload in responses.items():
                if key in url:
                    return payload
            raise AssertionError(url)

        with patch("zenquant.app.live_validate._fetch_json", side_effect=fake_fetch), patch(
            "zenquant.app.live_validate.time.time_ns",
            return_value=1_000_000_000,
        ):
            summary = run_validation(LiveValidationConfig(require_db=True))

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["failures"], [])

    def test_run_validation_flags_missing_database_writes(self) -> None:
        responses = {
            "healthz": {"status": "ok", "mode": "live"},
            "tf=4h": _state_payload(writes=0, connected=False),
            "tf=1d": _state_payload(writes=0, connected=False),
        }

        def fake_fetch(url: str):
            for key, payload in responses.items():
                if key in url:
                    return payload
            raise AssertionError(url)

        with patch("zenquant.app.live_validate._fetch_json", side_effect=fake_fetch), patch(
            "zenquant.app.live_validate.time.time_ns",
            return_value=1_000_000_000,
        ):
            summary = run_validation(LiveValidationConfig(require_db=True))

        self.assertFalse(summary["ok"])
        self.assertIn("database_not_connected", summary["failures"])
        self.assertIn("database_no_writes", summary["failures"])


if __name__ == "__main__":
    unittest.main()
