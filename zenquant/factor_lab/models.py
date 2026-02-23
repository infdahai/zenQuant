from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FactorObservation:
    symbol: str
    factor_id: str
    timestamp_ns: int
    factor_value: float
    forward_return: float


@dataclass(slots=True)
class FactorScore:
    symbol: str
    factor_id: str
    ic_mean: float
    ir: float
    sample_size: int
    timestamp_ns: int


@dataclass(slots=True)
class FactorWeight:
    symbol: str
    factor_id: str
    weight: float
    ic_mean: float
    orthogonal_penalty: float
    portfolio_sigma: float
    timestamp_ns: int

