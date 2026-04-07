import unittest

from zenquant.research.comparison import liquidation_research_variant_specs


class LiquidationResearchVariantTests(unittest.TestCase):
    def test_liquidation_research_variants_cover_four_tracks(self) -> None:
        variants = liquidation_research_variant_specs()
        self.assertEqual(
            [variant.name for variant in variants],
            ["paulwei_baseline", "execution_plus", "strategy_plus", "full_variant"],
        )

    def test_liquidation_variant_toggles_match_variant_boundaries(self) -> None:
        variants = {variant.name: variant for variant in liquidation_research_variant_specs()}
        self.assertFalse(variants["paulwei_baseline"].liquidation_data_enabled)
        self.assertFalse(variants["paulwei_baseline"].state_liquidation_overlay_enabled)
        self.assertTrue(variants["execution_plus"].liquidation_data_enabled)
        self.assertFalse(variants["execution_plus"].state_liquidation_overlay_enabled)
        self.assertTrue(variants["strategy_plus"].liquidation_data_enabled)
        self.assertTrue(variants["strategy_plus"].state_liquidation_overlay_enabled)
        self.assertTrue(variants["full_variant"].liquidation_data_enabled)
        self.assertTrue(variants["full_variant"].state_liquidation_overlay_enabled)


if __name__ == "__main__":
    unittest.main()
