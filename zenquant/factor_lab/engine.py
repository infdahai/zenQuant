from __future__ import annotations

from dataclasses import dataclass

from zenquant.factor_lab.ic_ir import RollingICEvaluator, RollingICEvaluatorConfig
from zenquant.factor_lab.models import FactorObservation, FactorScore, FactorWeight
from zenquant.factor_lab.orthogonalizer import FactorOrthogonalizer, OrthogonalizerConfig


@dataclass(slots=True)
class FactorLabEngineConfig:
    base_weight: float = 1.0
    portfolio_sigma_floor: float = 1e-4
    correlation_threshold: float = 0.8
    observation_window: int = 256
    ic_window: int = 128
    min_samples: int = 24


class FactorLabEngine:
    def __init__(self, config: FactorLabEngineConfig | None = None) -> None:
        self._config = config or FactorLabEngineConfig()
        self._ic = RollingICEvaluator(
            RollingICEvaluatorConfig(
                observation_window=self._config.observation_window,
                ic_window=self._config.ic_window,
                min_samples=self._config.min_samples,
            )
        )
        self._orthogonalizer = FactorOrthogonalizer(
            OrthogonalizerConfig(
                correlation_threshold=self._config.correlation_threshold,
                observation_window=self._config.observation_window,
            )
        )

    def evaluate(self, obs: FactorObservation, portfolio_sigma: float) -> tuple[FactorScore | None, FactorWeight | None]:
        penalty = self._orthogonalizer.ingest(obs)
        score = self._ic.add_observation(obs)
        if score is None:
            return None, None
        sigma = max(abs(portfolio_sigma), self._config.portfolio_sigma_floor)
        weight = self._config.base_weight * ((score.ic_mean * penalty) / sigma)
        return score, FactorWeight(
            symbol=obs.symbol,
            factor_id=obs.factor_id,
            weight=weight,
            ic_mean=score.ic_mean,
            orthogonal_penalty=penalty,
            portfolio_sigma=sigma,
            timestamp_ns=obs.timestamp_ns,
        )

