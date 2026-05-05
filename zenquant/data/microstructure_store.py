"""Point-in-time microstructure sidecar store for risk-only lookups."""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

FEATURE_COLUMNS = (
    "book_imbalance",
    "depth_skew",
    "spread_bps",
    "microprice_dev_bps",
    "aggressive_flow_imbalance",
    "trade_intensity",
    "volatility_burst",
    "liquidity_vacuum_score",
    "toxicity_proxy",
)

RISK_OVERLAY_FEATURE_MAP = {
    "ms_liquidity_vacuum_score": "liquidity_vacuum_score",
    "ms_toxicity_proxy": "toxicity_proxy",
    "ms_trade_intensity": "trade_intensity",
    "ms_spread_bps": "spread_bps",
    "ms_book_imbalance": "book_imbalance",
    "ms_microprice_dev_bps": "microprice_dev_bps",
}


@dataclass(slots=True)
class MicrostructureFeatureStoreConfig:
    data_dir: Path = field(default_factory=lambda: Path("data/raw/microstructure_sidecar/bybit/BTCUSDT/1000ms"))
    max_staleness_ns: int | None = None


class MicrostructureFeatureStore:
    """Point-in-time lookup for bar-aligned sidecar feature summaries."""

    def __init__(self, config: MicrostructureFeatureStoreConfig | None = None) -> None:
        self._config = config or MicrostructureFeatureStoreConfig()
        self._cutoffs: list[int] = []
        self._rows: list[dict[str, float]] = []
        self._metadata: dict[str, Any] = {}

    @property
    def loaded(self) -> bool:
        return bool(self._cutoffs)

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    def load(self) -> None:
        parquet_path = self._config.data_dir / "features.parquet"
        metadata_path = self._config.data_dir / "metadata.json"
        if metadata_path.exists():
            self._metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not parquet_path.exists():
            return
        frame = pd.read_parquet(parquet_path)
        if frame.empty or "cutoff_time_ns" not in frame.columns:
            return
        frame = frame.sort_values("cutoff_time_ns", kind="stable")
        self._cutoffs = [int(value) for value in frame["cutoff_time_ns"].tolist()]
        self._rows = []
        for row in frame.to_dict("records"):
            payload = {key: float(row.get(key, 0.0) or 0.0) for key in FEATURE_COLUMNS}
            self._rows.append(payload)

    def lookup(self, cutoff_time_ns: int) -> dict[str, float]:
        if not self._cutoffs:
            return {key: 0.0 for key in FEATURE_COLUMNS}
        idx = bisect.bisect_right(self._cutoffs, cutoff_time_ns) - 1
        if idx < 0:
            return {key: 0.0 for key in FEATURE_COLUMNS}
        return dict(self._rows[idx])

    def lookup_risk_overlay_values(self, cutoff_time_ns: int) -> dict[str, float | bool]:
        values: dict[str, float | bool] = {
            "ms_sidecar_ready": False,
            "ms_sidecar_stale": True,
            **{key: 0.0 for key in RISK_OVERLAY_FEATURE_MAP},
        }
        if not self._cutoffs:
            return values

        idx = bisect.bisect_right(self._cutoffs, cutoff_time_ns) - 1
        if idx < 0:
            return values

        source_cutoff_ns = self._cutoffs[idx]
        max_staleness_ns = self._config.max_staleness_ns
        stale = False
        if max_staleness_ns is not None:
            stale = (cutoff_time_ns - source_cutoff_ns) > max_staleness_ns
        if stale:
            return values

        row = self._rows[idx]
        values.update(
            {
                target_key: float(row.get(source_key, 0.0) or 0.0)
                for target_key, source_key in RISK_OVERLAY_FEATURE_MAP.items()
            }
        )
        values["ms_sidecar_ready"] = bool(self._metadata.get("pit_safe", True))
        values["ms_sidecar_stale"] = False
        return values
