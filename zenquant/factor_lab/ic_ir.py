from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from zenquant.factor_lab.models import FactorObservation, FactorScore
from zenquant.factor_lab.stats import mean, pearson_corr, stddev


@dataclass(slots=True)
class RollingICEvaluatorConfig:
    observation_window: int = 256
    ic_window: int = 128
    min_samples: int = 24


class RollingICEvaluator:
    def __init__(self, config: RollingICEvaluatorConfig | None = None) -> None:
        self._config = config or RollingICEvaluatorConfig()
        self._observations: dict[tuple[str, str], deque[FactorObservation]] = defaultdict(
            lambda: deque(maxlen=self._config.observation_window)
        )
        self._ic_history: dict[tuple[str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.ic_window)
        )

    def add_observation(self, obs: FactorObservation) -> FactorScore | None:
        key = (obs.symbol, obs.factor_id)
        series = self._observations[key]
        series.append(obs)
        if len(series) < self._config.min_samples:
            return None

        factor_values = [point.factor_value for point in series]
        forward_returns = [point.forward_return for point in series]
        ic = pearson_corr(factor_values, forward_returns)
        self._ic_history[key].append(ic)
        ic_values = list(self._ic_history[key])
        ic_mean = mean(ic_values)
        sigma = stddev(ic_values)
        ir = ic_mean / sigma if sigma > 1e-12 else 0.0

        return FactorScore(
            symbol=obs.symbol,
            factor_id=obs.factor_id,
            ic_mean=ic_mean,
            ir=ir,
            sample_size=len(series),
            timestamp_ns=obs.timestamp_ns,
        )

