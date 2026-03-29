"""Experiment tracker for AutoResearch campaigns.

Provides structured storage, deduplication, ranking, and comparison of
backtest experiment results.  All state is persisted as JSON files in a
campaign directory so that results survive process restarts and can be
inspected offline.

Usage:
    tracker = ExperimentTracker(campaign_dir=Path("results/campaigns/my_run"))
    tracker.record(params={...}, metrics={...}, tags=["sweep"])
    top = tracker.rank(metric="sharpe", top_n=5)
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Experiment:
    """One backtest experiment with its parameters and results."""

    experiment_id: str
    params: dict[str, Any]
    metrics: dict[str, float]
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0  # unix timestamp
    metadata: dict[str, Any] = field(default_factory=dict)


def params_hash(params: dict[str, Any]) -> str:
    """Deterministic hash of a parameter dict for deduplication."""
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


class ExperimentTracker:
    """Record, deduplicate, rank, and persist experiment results."""

    def __init__(self, campaign_dir: Path) -> None:
        self._dir = campaign_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._experiments: dict[str, Experiment] = {}
        self._dirty = False
        self._load()

    # ── public API ────────────────────────────────────────────

    def record(
        self,
        params: dict[str, Any],
        metrics: dict[str, float],
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Experiment:
        """Record an experiment.  Deduplicates by parameter hash."""
        exp_id = params_hash(params)
        exp = Experiment(
            experiment_id=exp_id,
            params=params,
            metrics=metrics,
            tags=tags or [],
            created_at=time.time(),
            metadata=metadata or {},
        )
        self._experiments[exp_id] = exp
        self._dirty = True
        return exp

    def rank(
        self,
        metric: str = "sharpe",
        *,
        top_n: int = 10,
        ascending: bool = False,
        tag_filter: str | None = None,
    ) -> list[Experiment]:
        """Return experiments ranked by *metric*."""
        candidates = list(self._experiments.values())
        if tag_filter is not None:
            candidates = [e for e in candidates if tag_filter in e.tags]
        candidates = [e for e in candidates if metric in e.metrics]
        candidates.sort(key=lambda e: e.metrics[metric], reverse=not ascending)
        return candidates[:top_n]

    def flush(self) -> None:
        """Write pending changes to disk."""
        if self._dirty:
            self._save()
            self._dirty = False

    def get(self, experiment_id: str) -> Experiment | None:
        return self._experiments.get(experiment_id)

    def all(self) -> list[Experiment]:
        return list(self._experiments.values())

    def count(self) -> int:
        return len(self._experiments)

    def best(self, metric: str = "sharpe") -> Experiment | None:
        ranked = self.rank(metric=metric, top_n=1)
        return ranked[0] if ranked else None

    def compare(
        self,
        ids: list[str],
        metrics: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Side-by-side comparison of specific experiments."""
        rows: list[dict[str, Any]] = []
        for exp_id in ids:
            exp = self._experiments.get(exp_id)
            if exp is None:
                continue
            row: dict[str, Any] = {"experiment_id": exp_id, **exp.params}
            for m in metrics or list(exp.metrics.keys()):
                row[m] = exp.metrics.get(m)
            rows.append(row)
        return rows

    # ── persistence ───────────────────────────────────────────

    def _save(self) -> None:
        path = self._dir / "experiments.json"
        data = [asdict(e) for e in self._experiments.values()]
        path.write_text(json.dumps(data, indent=2, default=str))

    def _load(self) -> None:
        path = self._dir / "experiments.json"
        if not path.exists():
            return
        raw = json.loads(path.read_text())
        for entry in raw:
            exp = Experiment(**entry)
            self._experiments[exp.experiment_id] = exp
