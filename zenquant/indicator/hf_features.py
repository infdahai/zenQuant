"""High-frequency feature extraction from 5min bars.

Computes 4H-level features from 5min intra-bar data, following
academic best practices (Liu, Patton & Sheppard 2015).

Each 4H bar contains 48 × 5min bars. Features extracted:
  1. realized_vol_hf: Realized volatility (sum of squared returns)
  2. intra_bar_consistency: Direction quality (% of 5min bars same direction)
  3. intra_bar_max_dd: Maximum drawdown within the 4H bar
  4. volume_front_ratio: Front-half vs back-half volume ratio
  5. realized_skew: Third moment of 5min returns (tail risk)

Usage:
    store = HFFeatureStore(Path("data/raw/binance/klines/BTCUSDT/5m"))
    store.load()
    features = store.lookup(bar_end_ns)
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True, frozen=True)
class HFFeatures:
    """High-frequency features aggregated from 5min to 4H level."""

    realized_vol_hf: float  # Annualized RV from 5min returns
    intra_bar_consistency: float  # 0.0 (chop) to 1.0 (perfect trend)
    intra_bar_max_dd: float  # Max drawdown within the 4H bar
    volume_front_ratio: float  # Front-half volume / total volume
    realized_skew: float  # Skewness of 5min returns
    n_bars: int  # Number of 5min bars available


@dataclass(slots=True)
class HFFeatureStoreConfig:
    """Configuration for the high-frequency feature store."""

    data_dir: Path = field(default_factory=lambda: Path("data/raw/binance/klines/BTCUSDT/5m"))
    bars_per_4h: int = 48  # 5min bars per 4H period
    annualizer: float = math.sqrt(365 * 24 * 12)  # 5min bars per year


class HFFeatureStore:
    """Load 5min parquet and provide pre-computed 4H features via point-in-time lookup."""

    def __init__(self, config: HFFeatureStoreConfig | None = None) -> None:
        self._config = config or HFFeatureStoreConfig()
        self._bar_end_ns: list[int] = []  # 4H bar end timestamps
        self._features: list[HFFeatures] = []  # Pre-computed features per 4H bar

    @property
    def loaded(self) -> bool:
        return len(self._bar_end_ns) > 0

    def load(self) -> None:
        """Load 5min parquet, aggregate to 4H features."""
        parquet_path = self._config.data_dir / "data.parquet"
        if not parquet_path.exists():
            return

        import pyarrow.parquet as pq

        table = pq.read_table(parquet_path)
        df = table.to_pandas()
        if df.empty or "close" not in df.columns:
            return

        # Sort by time
        df = df.sort_values("open_time")
        closes = df["close"].values
        volumes = df["volume"].values if "volume" in df.columns else [1.0] * len(df)
        open_times = df["open_time"].values  # milliseconds

        # Convert to nanoseconds
        open_times_ns = open_times * 1_000_000

        # Group into 4H blocks (48 × 5min bars)
        n = len(closes)
        block_size = self._config.bars_per_4h

        for start in range(0, n - block_size + 1, block_size):
            end = start + block_size
            block_closes = closes[start:end]
            block_volumes = volumes[start:end]

            # 4H bar end timestamp
            bar_end_ns = int(open_times_ns[end - 1]) + 5 * 60 * 1_000_000_000  # +5min

            features = self._compute_features(block_closes, block_volumes)
            self._bar_end_ns.append(bar_end_ns)
            self._features.append(features)

    def lookup(self, cutoff_time_ns: int) -> HFFeatures:
        """Return features for the 4H bar ending at or before cutoff."""
        if not self._bar_end_ns:
            return HFFeatures(
                realized_vol_hf=0.0, intra_bar_consistency=1.0,
                intra_bar_max_dd=0.0, volume_front_ratio=0.5,
                realized_skew=0.0, n_bars=0,
            )

        idx = bisect.bisect_right(self._bar_end_ns, cutoff_time_ns) - 1
        if idx < 0:
            return HFFeatures(
                realized_vol_hf=0.0, intra_bar_consistency=1.0,
                intra_bar_max_dd=0.0, volume_front_ratio=0.5,
                realized_skew=0.0, n_bars=0,
            )
        return self._features[idx]

    def _compute_features(self, closes: list, volumes: list) -> HFFeatures:
        """Compute all features from a block of 5min closes."""
        n = len(closes)
        if n < 2:
            return HFFeatures(0.0, 1.0, 0.0, 0.5, 0.0, n)

        # 5min log returns
        returns = []
        for i in range(1, n):
            if closes[i - 1] > 0 and closes[i] > 0:
                returns.append(math.log(closes[i] / closes[i - 1]))

        if not returns:
            return HFFeatures(0.0, 1.0, 0.0, 0.5, 0.0, n)

        # 1. Realized Volatility (annualized)
        sum_sq = sum(r * r for r in returns)
        rv = math.sqrt(sum_sq) * self._config.annualizer

        # 2. Intra-bar Consistency: direction alignment
        bar_direction = 1 if closes[-1] > closes[0] else -1
        same_dir = sum(1 for r in returns if (r > 0) == (bar_direction > 0))
        consistency = same_dir / len(returns)

        # 3. Intra-bar Max Drawdown
        peak = closes[0]
        max_dd = 0.0
        for c in closes:
            if c > peak:
                peak = c
            dd = (peak - c) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # 4. Volume Front Ratio
        half = n // 2
        front_vol = sum(float(v) for v in volumes[:half])
        total_vol = sum(float(v) for v in volumes)
        front_ratio = front_vol / total_vol if total_vol > 0 else 0.5

        # 5. Realized Skewness (third moment / rv^1.5)
        if len(returns) > 2 and rv > 1e-9:
            mean_r = sum(returns) / len(returns)
            m3 = sum((r - mean_r) ** 3 for r in returns) / len(returns)
            std_r = math.sqrt(sum_sq / len(returns))
            skew = m3 / (std_r ** 3) if std_r > 1e-12 else 0.0
        else:
            skew = 0.0

        return HFFeatures(
            realized_vol_hf=round(rv, 6),
            intra_bar_consistency=round(consistency, 4),
            intra_bar_max_dd=round(max_dd, 6),
            volume_front_ratio=round(front_ratio, 4),
            realized_skew=round(skew, 4),
            n_bars=n,
        )
