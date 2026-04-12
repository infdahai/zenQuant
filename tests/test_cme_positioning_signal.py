import unittest

from zenquant.plugins.signals.cme_positioning_state_signal import compute


class CmePositioningStateSignalTests(unittest.TestCase):
    def test_crowded_long_state_haircuts_confidence(self) -> None:
        result = compute(
            {
                "cme_lev_money_net_z": 1.8,
                "cme_total_oi_z": 1.5,
            },
            {},
            {},
        )
        self.assertLess(result.value, 0.0)
        self.assertIn("CME_CROWDED_LONG", result.reason_codes)

    def test_institutional_support_is_positive_but_small(self) -> None:
        result = compute(
            {
                "cme_asset_mgr_net_z": 0.9,
                "cme_dealer_net_z": -0.8,
            },
            {},
            {},
        )
        self.assertGreater(result.value, 0.0)
        self.assertLessEqual(result.value, 0.2)
        self.assertIn("CME_INSTITUTIONAL_SUPPORT", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
