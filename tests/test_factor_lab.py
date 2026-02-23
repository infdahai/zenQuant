import unittest

from zenquant.factor_lab.engine import FactorLabEngine, FactorLabEngineConfig
from zenquant.factor_lab.models import FactorObservation
from zenquant.factor_lab.orthogonalizer import FactorOrthogonalizer, OrthogonalizerConfig


class FactorLabTests(unittest.TestCase):
    def test_ic_ir_produces_score_and_weight(self) -> None:
        engine = FactorLabEngine(
            FactorLabEngineConfig(
                base_weight=1.0,
                min_samples=6,
                observation_window=64,
                ic_window=32,
                correlation_threshold=0.8,
            )
        )
        final_score = None
        final_weight = None
        for i in range(12):
            value = float(i + 1)
            obs = FactorObservation(
                symbol="BTC_USDT",
                factor_id="MOM",
                timestamp_ns=i + 1,
                factor_value=value,
                forward_return=value * 0.01,
            )
            final_score, final_weight = engine.evaluate(obs, portfolio_sigma=0.02)

        self.assertIsNotNone(final_score)
        self.assertIsNotNone(final_weight)
        assert final_score is not None
        assert final_weight is not None
        self.assertGreater(final_score.ic_mean, 0.95)
        self.assertGreater(final_weight.weight, 0.0)

    def test_orthogonalizer_penalizes_high_correlation(self) -> None:
        orth = FactorOrthogonalizer(
            OrthogonalizerConfig(correlation_threshold=0.8, observation_window=64)
        )
        penalties = []
        for i in range(1, 10):
            orth.ingest(
                FactorObservation(
                    symbol="BTC_USDT",
                    factor_id="FA",
                    timestamp_ns=i,
                    factor_value=float(i),
                    forward_return=0.0,
                )
            )
            penalty = orth.ingest(
                FactorObservation(
                    symbol="BTC_USDT",
                    factor_id="FB",
                    timestamp_ns=100 + i,
                    factor_value=float(i),
                    forward_return=0.0,
                )
            )
            penalties.append(penalty)
        self.assertLess(penalties[-1], 1.0)


if __name__ == "__main__":
    unittest.main()

