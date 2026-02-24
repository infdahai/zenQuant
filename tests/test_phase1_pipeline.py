import unittest

from zenquant.app import ZenQuantPipeline


class Phase1PipelineTests(unittest.TestCase):
    def test_pipeline_emits_phase1_artifacts(self) -> None:
        pipeline = ZenQuantPipeline(seed=11)
        result = pipeline.run(steps=420)

        self.assertGreater(result.total_indicator_packs, 0)
        self.assertGreater(result.total_targets, 0)
        self.assertGreater(result.total_reports, 0)
        self.assertGreater(result.filled_reports, 0)
        self.assertIn(result.latest_regime, {"TREND", "RANGE", "STRESS"})

    def test_pipeline_deterministic_for_same_seed(self) -> None:
        first = ZenQuantPipeline(seed=19).run(steps=380)
        second = ZenQuantPipeline(seed=19).run(steps=380)

        self.assertEqual(first.total_indicator_packs, second.total_indicator_packs)
        self.assertEqual(first.total_targets, second.total_targets)
        self.assertEqual(first.total_reports, second.total_reports)
        self.assertAlmostEqual(first.latest_zen_pulse, second.latest_zen_pulse, places=9)
        self.assertAlmostEqual(first.final_position_btc, second.final_position_btc, places=9)


if __name__ == "__main__":
    unittest.main()
