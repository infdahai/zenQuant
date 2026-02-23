import unittest

from zenquant.app import ZenQuantPipeline


class PipelineTests(unittest.TestCase):
    def test_pipeline_emits_reports(self) -> None:
        pipeline = ZenQuantPipeline(seed=11)
        result = pipeline.run(steps=220, start_price=30_000.0)
        self.assertGreater(result.total_signals, 0)
        self.assertGreater(result.total_reports, 0)
        self.assertGreater(result.filled_reports, 0)
        self.assertGreater(result.total_factor_weights, 0)


if __name__ == "__main__":
    unittest.main()
