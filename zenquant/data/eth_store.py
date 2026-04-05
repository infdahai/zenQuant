"""ETH price store for cross-asset analysis (Transfer Entropy).

Loads ETH 4H klines and provides returns for TE(ETH→BTC) computation.
Point-in-time correct: only returns data before cutoff.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class EthStoreConfig:
    eth_kline_path: Path = field(default_factory=lambda: Path("data/raw/binance/klines/ETHUSDT/4h"))


@dataclass(slots=True, frozen=True)
class EthSnapshot:
    """ETH price and returns at a point in time."""

    price: float
    return_4h: float  # Latest 4H return
    te_eth_to_btc: float  # Transfer entropy score (higher = ETH leading BTC)


class EthDataStore:
    """Load ETH 4H klines for cross-asset Transfer Entropy computation."""

    def __init__(self, config: EthStoreConfig | None = None) -> None:
        self._config = config or EthStoreConfig()
        self._timestamps: list[int] = []
        self._closes: list[float] = []
        self._returns: list[float] = []

    @property
    def loaded(self) -> bool:
        return len(self._timestamps) > 0

    def load(self) -> None:
        parquet_path = self._config.eth_kline_path / "data.parquet"
        if not parquet_path.exists():
            return

        import pyarrow.parquet as pq

        df = pq.read_table(parquet_path).to_pandas()
        if df.empty or "close" not in df.columns:
            return

        df = df.sort_values("close_time_ns")
        for _, row in df.iterrows():
            ts = int(row["close_time_ns"])
            close = float(row["close"])
            self._timestamps.append(ts)
            self._closes.append(close)

        # Compute returns
        self._returns = [0.0]
        for i in range(1, len(self._closes)):
            if self._closes[i - 1] > 0:
                self._returns.append(
                    (self._closes[i] - self._closes[i - 1]) / self._closes[i - 1]
                )
            else:
                self._returns.append(0.0)

    def get_returns_before(self, cutoff_time_ns: int, window: int = 120) -> list[float]:
        """Get ETH returns up to cutoff (point-in-time correct)."""
        if not self._timestamps:
            return []

        idx = bisect.bisect_right(self._timestamps, cutoff_time_ns)
        if idx < 2:
            return []

        start = max(0, idx - window)
        return self._returns[start:idx]

    def get_price(self, cutoff_time_ns: int) -> float:
        """Get ETH price at cutoff."""
        if not self._timestamps:
            return 0.0
        idx = bisect.bisect_right(self._timestamps, cutoff_time_ns) - 1
        return self._closes[idx] if idx >= 0 else 0.0
