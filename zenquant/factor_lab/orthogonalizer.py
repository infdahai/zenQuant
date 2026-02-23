from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from zenquant.factor_lab.models import FactorObservation
from zenquant.factor_lab.stats import pearson_corr


@dataclass(slots=True)
class OrthogonalizerConfig:
    correlation_threshold: float = 0.8
    observation_window: int = 256


class FactorOrthogonalizer:
    def __init__(self, config: OrthogonalizerConfig | None = None) -> None:
        self._config = config or OrthogonalizerConfig()
        self._factor_series: dict[tuple[str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.observation_window)
        )

    def ingest(self, obs: FactorObservation) -> float:
        key = (obs.symbol, obs.factor_id)
        self._factor_series[key].append(obs.factor_value)
        max_corr = 0.0
        for (symbol, factor_id), series in self._factor_series.items():
            if symbol != obs.symbol or factor_id == obs.factor_id:
                continue
            corr = abs(pearson_corr(list(self._factor_series[key]), list(series)))
            if corr > max_corr:
                max_corr = corr
        if max_corr <= self._config.correlation_threshold:
            return 1.0
        headroom = max(1e-12, 1.0 - self._config.correlation_threshold)
        penalty = 1.0 - ((max_corr - self._config.correlation_threshold) / headroom)
        return max(0.1, min(1.0, penalty))

