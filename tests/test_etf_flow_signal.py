import unittest

from zenquant.plugins.signals.etf_flow_state_signal import compute


class EtfFlowStateSignalTests(unittest.TestCase):
    def test_outflow_stress_reduces_state_confidence(self) -> None:
        result = compute(
            {
                "btc_etf_netflow_z": -1.8,
                "btc_etf_flow_accel": -1_500_000_000.0,
                "btc_etf_negative_streak": 4,
            },
            {},
            {},
        )
        self.assertLess(result.value, 0.0)
        self.assertGreater(result.confidence, 0.4)
        self.assertIn("ETF_OUTFLOW_STRESS", result.reason_codes)

    def test_inflow_support_is_positive_but_not_directional_vote(self) -> None:
        result = compute(
            {
                "btc_etf_netflow_z": 1.2,
                "btc_etf_flow_accel": 2_000_000_000.0,
                "btc_etf_positive_streak": 3,
            },
            {},
            {},
        )
        self.assertGreater(result.value, 0.0)
        self.assertLessEqual(result.value, 0.45)
        self.assertIn("ETF_INFLOW_SUPPORT", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
