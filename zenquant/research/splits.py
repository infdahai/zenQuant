"""Canonical dataset split contract for clean tuning campaigns."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime


@dataclass(slots=True, frozen=True)
class SplitWindow:
    name: str
    start_ns: int
    end_ns: int | None


@dataclass(slots=True, frozen=True)
class DatasetSplitSpec:
    version: str
    asset: str
    train: SplitWindow
    dev: SplitWindow
    test: SplitWindow


def _ns(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1_000_000_000)


def default_btc_split_spec() -> DatasetSplitSpec:
    return DatasetSplitSpec(
        version="btc_temporal_v2_train_selection_only",
        asset="BTC",
        train=SplitWindow("train", _ns(2020, 1, 1), _ns(2024, 1, 1)),
        dev=SplitWindow("dev", _ns(2024, 1, 1), _ns(2026, 1, 1)),
        test=SplitWindow("test", _ns(2026, 1, 1), None),
    )


def split_summary_json(spec: DatasetSplitSpec) -> dict[str, object]:
    return {
        "version": spec.version,
        "asset": spec.asset,
        "selection_policy": "train_only_search_dev_diagnostics_test_acceptance",
        "train": asdict(spec.train),
        "dev": asdict(spec.dev),
        "test": asdict(spec.test),
        "windows": {
            "train": asdict(spec.train),
            "dev": asdict(spec.dev),
            "test": asdict(spec.test),
        },
    }


def period_within_split(start_ns: int, end_ns: int, split_window: SplitWindow) -> bool:
    split_end = split_window.end_ns if split_window.end_ns is not None else 2**63 - 1
    return start_ns >= split_window.start_ns and end_ns <= split_end
