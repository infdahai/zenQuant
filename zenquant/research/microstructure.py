"""Research-only microstructure sidecar helpers.

This module keeps raw L2 / trade-tick research outside the flagship
bar-close strategy path. It standardizes a minimal dataset contract,
preprocessing review, feature generation, and a simple linear baseline
without promoting any feature into authoritative strategy truth.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from zenquant.data.microstructure_store import (
    FEATURE_COLUMNS,
    MicrostructureFeatureStore,
    MicrostructureFeatureStoreConfig,
)

DEFAULT_ALLOWED_USES = ["execution_modifier", "risk_overlay_input"]
REQUIRED_INPUT_FILES = ("snapshots.parquet", "trades.parquet")


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _artifact_id(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _parse_levels(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, list):
        return [float(item) for item in value if item is not None]
    if isinstance(value, tuple):
        return [float(item) for item in value if item is not None]
    if isinstance(value, np.ndarray):
        return [float(item) for item in value.tolist() if item is not None]
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [float(item) for item in parsed if item is not None]
    return []


def _normalize_snapshots(
    data_root: Path,
    *,
    venue: str,
    symbol: str,
    depth: int,
) -> pd.DataFrame:
    frame = _read_parquet(data_root / "snapshots.parquet")
    if frame.empty:
        return pd.DataFrame()
    if "venue" in frame.columns:
        frame = frame.loc[frame["venue"].astype(str) == venue].copy()
    if "symbol" in frame.columns:
        frame = frame.loc[frame["symbol"].astype(str) == symbol].copy()
    if frame.empty or "ts_ns" not in frame.columns:
        return pd.DataFrame()

    normalized = frame.copy()
    for column in ("bid_prices", "bid_sizes", "ask_prices", "ask_sizes"):
        normalized[column] = normalized[column].map(_parse_levels)

    normalized["sequence"] = pd.to_numeric(normalized.get("sequence"), errors="coerce")
    normalized["ts_ns"] = pd.to_numeric(normalized["ts_ns"], errors="coerce")
    normalized = normalized.loc[normalized["ts_ns"].notna()].copy()
    normalized = normalized.sort_values(["ts_ns", "sequence"], kind="stable")

    bid_prices = normalized["bid_prices"].map(lambda levels: levels[:depth])
    ask_prices = normalized["ask_prices"].map(lambda levels: levels[:depth])
    bid_sizes = normalized["bid_sizes"].map(lambda levels: levels[:depth])
    ask_sizes = normalized["ask_sizes"].map(lambda levels: levels[:depth])

    normalized["best_bid"] = bid_prices.map(lambda levels: float(levels[0]) if levels else math.nan)
    normalized["best_ask"] = ask_prices.map(lambda levels: float(levels[0]) if levels else math.nan)
    normalized["bid_depth"] = bid_sizes.map(lambda levels: float(sum(levels)))
    normalized["ask_depth"] = ask_sizes.map(lambda levels: float(sum(levels)))
    normalized["weighted_bid_price"] = pd.Series(
        [
            float(np.average(prices, weights=sizes)) if prices and sum(sizes) > 0 else math.nan
            for prices, sizes in zip(bid_prices, bid_sizes, strict=False)
        ]
    )
    normalized["weighted_ask_price"] = pd.Series(
        [
            float(np.average(prices, weights=sizes)) if prices and sum(sizes) > 0 else math.nan
            for prices, sizes in zip(ask_prices, ask_sizes, strict=False)
        ]
    )

    normalized["mid_price"] = (normalized["best_bid"] + normalized["best_ask"]) / 2.0
    normalized["spread_bps"] = np.where(
        normalized["mid_price"] > 0,
        (normalized["best_ask"] - normalized["best_bid"]) / normalized["mid_price"] * 10_000.0,
        math.nan,
    )
    normalized["book_imbalance"] = np.where(
        (normalized["bid_depth"] + normalized["ask_depth"]) > 0,
        (normalized["bid_depth"] - normalized["ask_depth"]) / (normalized["bid_depth"] + normalized["ask_depth"]),
        0.0,
    )
    normalized["depth_skew"] = np.where(
        normalized["mid_price"] > 0,
        (normalized["weighted_ask_price"] - normalized["weighted_bid_price"]) / normalized["mid_price"] * 10_000.0,
        0.0,
    )
    top_bid_size = bid_sizes.map(lambda levels: float(levels[0]) if levels else 0.0)
    top_ask_size = ask_sizes.map(lambda levels: float(levels[0]) if levels else 0.0)
    normalized["microprice"] = np.where(
        (top_bid_size + top_ask_size) > 0,
        (normalized["best_ask"] * top_bid_size + normalized["best_bid"] * top_ask_size) / (top_bid_size + top_ask_size),
        normalized["mid_price"],
    )
    normalized["microprice_dev_bps"] = np.where(
        normalized["mid_price"] > 0,
        (normalized["microprice"] - normalized["mid_price"]) / normalized["mid_price"] * 10_000.0,
        0.0,
    )
    return normalized


def _normalize_trades(data_root: Path, *, venue: str, symbol: str) -> pd.DataFrame:
    frame = _read_parquet(data_root / "trades.parquet")
    if frame.empty:
        return pd.DataFrame(columns=["ts_ns", "price", "size", "side_sign"])
    if "venue" in frame.columns:
        frame = frame.loc[frame["venue"].astype(str) == venue].copy()
    if "symbol" in frame.columns:
        frame = frame.loc[frame["symbol"].astype(str) == symbol].copy()
    if frame.empty or "ts_ns" not in frame.columns:
        return pd.DataFrame(columns=["ts_ns", "price", "size", "side_sign"])
    frame["ts_ns"] = pd.to_numeric(frame["ts_ns"], errors="coerce")
    frame["price"] = pd.to_numeric(frame.get("price"), errors="coerce")
    frame["size"] = pd.to_numeric(frame.get("size"), errors="coerce").fillna(0.0)
    frame["side_sign"] = (
        frame.get("side", pd.Series(dtype="object")).astype(str).str.lower().map({"buy": 1.0, "sell": -1.0}).fillna(0.0)
    )
    frame = frame.loc[frame["ts_ns"].notna() & frame["price"].notna()].copy()
    return frame.sort_values("ts_ns", kind="stable")


def build_microstructure_dataset_contract(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
) -> dict[str, Any]:
    data_root = data_root.resolve()
    snapshots = _normalize_snapshots(data_root, venue=venue, symbol=symbol, depth=depth)
    trades = _normalize_trades(data_root, venue=venue, symbol=symbol)
    missing_files = [name for name in REQUIRED_INPUT_FILES if not (data_root / name).exists()]

    window_start_ns = None if snapshots.empty else int(snapshots["ts_ns"].min())
    window_end_ns = None if snapshots.empty else int(snapshots["ts_ns"].max())
    snapshot_delta_mode = "snapshot_only"
    if "is_delta" in snapshots.columns and bool(snapshots["is_delta"].astype(bool).any()):
        snapshot_delta_mode = "mixed_snapshot_delta"

    payload = {
        "generated_at": _ts(),
        "source_type": "microstructure_sidecar_dataset",
        "dataset_name": f"{venue.lower()}_{symbol.lower()}_l2_sidecar",
        "data_root": str(data_root),
        "venue": venue,
        "symbol": symbol,
        "depth": depth,
        "sampling_cadence_ms": cadence_ms,
        "required_files": list(REQUIRED_INPUT_FILES),
        "missing_files": missing_files,
        "snapshot_delta_mode": snapshot_delta_mode,
        "ready": len(missing_files) == 0 and not snapshots.empty,
        "window_start_ns": window_start_ns,
        "window_end_ns": window_end_ns,
        "row_counts": {
            "snapshots": len(snapshots),
            "trades": len(trades),
        },
        "allowed_uses": list(DEFAULT_ALLOWED_USES),
        "research_only": True,
        "execution_modifier_only": True,
        "promote_truth": False,
        "pit_safe_required": True,
        "blocked_by": []
        if len(missing_files) == 0 and not snapshots.empty
        else ([f"missing:{name}" for name in missing_files] + ([] if not snapshots.empty else ["snapshots_missing"])),
    }
    payload["artifact_id"] = _artifact_id(payload)
    return payload


def write_microstructure_dataset_contract(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
    output_dir: Path,
) -> dict[str, Any]:
    payload = build_microstructure_dataset_contract(
        data_root=data_root,
        venue=venue,
        symbol=symbol,
        depth=depth,
        cadence_ms=cadence_ms,
    )
    json_path = output_dir / "dataset_contract.json"
    md_path = output_dir / "dataset_contract.md"
    _write_json(json_path, payload)
    md_lines = [
        "# Microstructure Dataset Contract",
        "",
        f"- artifact_id: `{payload['artifact_id']}`",
        f"- venue: `{payload['venue']}`",
        f"- symbol: `{payload['symbol']}`",
        f"- depth: `{payload['depth']}`",
        f"- sampling_cadence_ms: `{payload['sampling_cadence_ms']}`",
        f"- snapshot_delta_mode: `{payload['snapshot_delta_mode']}`",
        f"- ready: `{payload['ready']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
        "",
        "## Row Counts",
        "",
        f"- snapshots: `{payload['row_counts']['snapshots']}`",
        f"- trades: `{payload['row_counts']['trades']}`",
        "",
        "## Allowed Uses",
        "",
    ]
    md_lines.extend(f"- `{item}`" for item in payload["allowed_uses"])
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": payload["artifact_id"]}


def build_microstructure_preprocessing_review(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
) -> dict[str, Any]:
    snapshots = _normalize_snapshots(data_root, venue=venue, symbol=symbol, depth=depth)
    trades = _normalize_trades(data_root, venue=venue, symbol=symbol)
    if snapshots.empty:
        payload = {
            "generated_at": _ts(),
            "ready": False,
            "coverage_ratio": 0.0,
            "duplicate_snapshot_rows": 0,
            "reordered_snapshot_rows": 0,
            "gap_count": 0,
            "stale_snapshot_rows": 0,
            "crossed_book_rows": 0,
            "trade_side_missing_rows": len(trades.loc[trades["side_sign"] == 0.0]) if not trades.empty else 0,
            "preprocessing_version": "v1_normalize_clip_sort",
            "blocked_by": ["snapshots_missing"],
        }
        payload["artifact_id"] = _artifact_id(payload)
        return payload

    cadence_ns = cadence_ms * 1_000_000
    raw_snapshot_ts = pd.to_numeric(
        _read_parquet(data_root / "snapshots.parquet").get("ts_ns"), errors="coerce"
    ).dropna()
    duplicate_snapshot_rows = int(raw_snapshot_ts.duplicated().sum()) if not raw_snapshot_ts.empty else 0
    reordered_snapshot_rows = int((raw_snapshot_ts.diff() < 0).fillna(False).sum()) if not raw_snapshot_ts.empty else 0
    gaps = snapshots["ts_ns"].diff().fillna(0)
    gap_count = int((gaps > cadence_ns * 3).sum())
    stale_snapshot_rows = int((gaps > cadence_ns * 5).sum())
    crossed_book_rows = int((snapshots["best_ask"] <= snapshots["best_bid"]).fillna(False).sum())
    coverage_ratio = 1.0
    if len(snapshots) > 1:
        expected_rows = max(1, int((int(snapshots["ts_ns"].max()) - int(snapshots["ts_ns"].min())) / cadence_ns) + 1)
        coverage_ratio = min(1.0, float(len(snapshots)) / float(expected_rows))

    blocked_by: list[str] = []
    if coverage_ratio < 0.80:
        blocked_by.append("low_snapshot_coverage")
    if crossed_book_rows > 0:
        blocked_by.append("crossed_book_rows_present")
    if stale_snapshot_rows > 0:
        blocked_by.append("stale_snapshot_gaps_present")

    payload = {
        "generated_at": _ts(),
        "ready": len(blocked_by) == 0,
        "coverage_ratio": round(coverage_ratio, 4),
        "duplicate_snapshot_rows": duplicate_snapshot_rows,
        "reordered_snapshot_rows": reordered_snapshot_rows,
        "gap_count": gap_count,
        "stale_snapshot_rows": stale_snapshot_rows,
        "crossed_book_rows": crossed_book_rows,
        "trade_side_missing_rows": len(trades.loc[trades["side_sign"] == 0.0]) if not trades.empty else 0,
        "preprocessing_version": "v1_normalize_clip_sort",
        "blocked_by": blocked_by,
    }
    payload["artifact_id"] = _artifact_id(payload)
    return payload


def write_microstructure_preprocessing_review(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
    output_dir: Path,
) -> dict[str, Any]:
    payload = build_microstructure_preprocessing_review(
        data_root=data_root,
        venue=venue,
        symbol=symbol,
        depth=depth,
        cadence_ms=cadence_ms,
    )
    json_path = output_dir / "preprocessing_review.json"
    md_path = output_dir / "preprocessing_review.md"
    _write_json(json_path, payload)
    md_lines = [
        "# Microstructure Preprocessing Review",
        "",
        f"- artifact_id: `{payload['artifact_id']}`",
        f"- ready: `{payload['ready']}`",
        f"- coverage_ratio: `{payload['coverage_ratio']}`",
        f"- duplicate_snapshot_rows: `{payload['duplicate_snapshot_rows']}`",
        f"- reordered_snapshot_rows: `{payload['reordered_snapshot_rows']}`",
        f"- gap_count: `{payload['gap_count']}`",
        f"- stale_snapshot_rows: `{payload['stale_snapshot_rows']}`",
        f"- crossed_book_rows: `{payload['crossed_book_rows']}`",
        f"- trade_side_missing_rows: `{payload['trade_side_missing_rows']}`",
        f"- preprocessing_version: `{payload['preprocessing_version']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
    ]
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": payload["artifact_id"]}


def _aggregate_windows(
    snapshots: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    cadence_ms: int,
) -> pd.DataFrame:
    if snapshots.empty:
        return pd.DataFrame(columns=["cutoff_time_ns", *FEATURE_COLUMNS, "mid_price"])
    cadence_ns = cadence_ms * 1_000_000
    frame = snapshots.copy()
    frame["window_start_ns"] = (frame["ts_ns"] // cadence_ns) * cadence_ns

    grouped = frame.groupby("window_start_ns", sort=True)
    feature_rows: list[dict[str, Any]] = []
    for window_start_ns, group in grouped:
        last_row = group.iloc[-1]
        trade_slice = trades.loc[
            (trades["ts_ns"] >= window_start_ns) & (trades["ts_ns"] < window_start_ns + cadence_ns)
        ].copy()
        aggressive_flow = (
            float((trade_slice["size"] * trade_slice["side_sign"]).sum()) if not trade_slice.empty else 0.0
        )
        trade_volume = float(trade_slice["size"].sum()) if not trade_slice.empty else 0.0
        flow_imbalance = aggressive_flow / trade_volume if trade_volume > 0 else 0.0
        feature_rows.append(
            {
                "cutoff_time_ns": int(window_start_ns + cadence_ns),
                "mid_price": float(last_row["mid_price"]),
                "book_imbalance": float(group["book_imbalance"].mean()),
                "depth_skew": float(group["depth_skew"].mean()),
                "spread_bps": float(group["spread_bps"].mean()),
                "microprice_dev_bps": float(group["microprice_dev_bps"].mean()),
                "aggressive_flow_imbalance": float(flow_imbalance),
                "trade_intensity": float(len(trade_slice)),
            }
        )

    windows = pd.DataFrame(feature_rows).sort_values("cutoff_time_ns", kind="stable")
    if windows.empty:
        return pd.DataFrame(columns=["cutoff_time_ns", *FEATURE_COLUMNS, "mid_price"])

    returns_bps = np.log(windows["mid_price"].replace(0.0, np.nan)).diff().fillna(0.0) * 10_000.0
    rolling_std = returns_bps.rolling(window=5, min_periods=2).std().fillna(0.0)
    median_depth = (windows["book_imbalance"].abs() + 1.0).rolling(window=5, min_periods=1).median().replace(0.0, 1.0)

    windows["volatility_burst"] = np.where(rolling_std > 0, returns_bps.abs() / rolling_std.replace(0.0, np.nan), 0.0)
    windows["volatility_burst"] = windows["volatility_burst"].replace([np.inf, -np.inf], 0.0).fillna(0.0)
    windows["liquidity_vacuum_score"] = windows["spread_bps"].clip(lower=0.0) * (
        1.0 - windows["book_imbalance"].abs().clip(upper=0.99)
    )
    windows["toxicity_proxy"] = windows["spread_bps"].clip(lower=0.0) * windows["aggressive_flow_imbalance"].abs()

    return windows[
        [
            "cutoff_time_ns",
            "mid_price",
            *FEATURE_COLUMNS,
        ]
    ].reset_index(drop=True)


def build_microstructure_feature_frame(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
) -> pd.DataFrame:
    snapshots = _normalize_snapshots(data_root, venue=venue, symbol=symbol, depth=depth)
    trades = _normalize_trades(data_root, venue=venue, symbol=symbol)
    return _aggregate_windows(snapshots, trades, cadence_ms=cadence_ms)


def write_microstructure_sidecar_features(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
    feature_output_root: Path,
) -> dict[str, Any]:
    frame = build_microstructure_feature_frame(
        data_root=data_root,
        venue=venue,
        symbol=symbol,
        depth=depth,
        cadence_ms=cadence_ms,
    )
    output_dir = feature_output_root / venue.lower() / symbol / f"{cadence_ms}ms"
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / "features.parquet"
    metadata_path = output_dir / "metadata.json"
    frame.to_parquet(parquet_path, index=False)
    metadata = {
        "generated_at": _ts(),
        "venue": venue,
        "symbol": symbol,
        "depth": depth,
        "sampling_cadence_ms": cadence_ms,
        "research_only": True,
        "execution_modifier_only": True,
        "promote_truth": False,
        "pit_safe": True,
        "row_count": len(frame),
        "feature_columns": list(FEATURE_COLUMNS),
    }
    _write_json(metadata_path, metadata)
    return {"parquet_path": str(parquet_path), "metadata_path": str(metadata_path), "row_count": len(frame)}


def _chronological_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    split_idx = max(1, int(len(frame) * 0.8))
    split_idx = min(split_idx, len(frame) - 1)
    return frame.iloc[:split_idx].copy(), frame.iloc[split_idx:].copy()


def _standardize(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    mean = train.mean(axis=0)
    std = train.std(axis=0).replace(0.0, 1.0)
    train_scaled = ((train - mean) / std).fillna(0.0).to_numpy(dtype=float)
    test_scaled = ((test - mean) / std).fillna(0.0).to_numpy(dtype=float)
    return train_scaled, test_scaled


def _f1_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    tp = float(np.sum((y_true == 1) & (y_pred == 1)))
    fp = float(np.sum((y_true == 0) & (y_pred == 1)))
    fn = float(np.sum((y_true == 1) & (y_pred == 0)))
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if precision + recall == 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _evaluate_linear_baseline(
    frame: pd.DataFrame, *, variant_name: str, horizon_ms: int, cadence_ms: int
) -> dict[str, Any]:
    cadence_ns = cadence_ms * 1_000_000
    horizon_steps = max(1, round(horizon_ms * 1_000_000 / cadence_ns))
    base = frame.copy()
    future_mid = base["mid_price"].shift(-horizon_steps)
    future_return_bps = np.where(
        (base["mid_price"] > 0) & future_mid.notna(),
        np.log(future_mid / base["mid_price"]) * 10_000.0,
        np.nan,
    )
    base["future_return_bps"] = future_return_bps
    base["label"] = np.where(base["future_return_bps"] > 0.0, 1, 0)
    base = base.loc[base["future_return_bps"].notna()].copy()

    if len(base) < 8:
        return {
            "variant": variant_name,
            "model_family": "linear_correlation_baseline",
            "rows": len(base),
            "train_rows": 0,
            "test_rows": 0,
            "f1": 0.0,
            "accuracy": 0.0,
            "blocked_by": ["insufficient_labeled_windows"],
        }

    train, test = _chronological_split(base)
    if train.empty or test.empty:
        return {
            "variant": variant_name,
            "model_family": "linear_correlation_baseline",
            "rows": len(base),
            "train_rows": len(train),
            "test_rows": len(test),
            "f1": 0.0,
            "accuracy": 0.0,
            "blocked_by": ["split_too_small"],
        }

    x_train_df = train.loc[:, FEATURE_COLUMNS]
    x_test_df = test.loc[:, FEATURE_COLUMNS]
    x_train, x_test = _standardize(x_train_df, x_test_df)
    y_train = train["label"].to_numpy(dtype=float) * 2.0 - 1.0
    y_test = test["label"].to_numpy(dtype=int)

    weights, *_ = np.linalg.lstsq(x_train, y_train, rcond=None)
    scores = x_test @ weights
    y_pred = (scores > 0.0).astype(int)

    accuracy = float(np.mean(y_pred == y_test)) if len(y_test) > 0 else 0.0
    f1 = _f1_score(y_test, y_pred)
    return {
        "variant": variant_name,
        "model_family": "linear_correlation_baseline",
        "rows": len(base),
        "train_rows": len(train),
        "test_rows": len(test),
        "f1": round(f1, 4),
        "accuracy": round(accuracy, 4),
        "avg_abs_score": round(float(np.mean(np.abs(scores))) if len(scores) else 0.0, 4),
        "blocked_by": [],
    }


def _clip_variant(frame: pd.DataFrame) -> pd.DataFrame:
    clipped = frame.copy()
    for column in FEATURE_COLUMNS:
        series = clipped[column]
        median = float(series.median()) if len(series) else 0.0
        mad = float((series - median).abs().median()) if len(series) else 0.0
        bound = max(1e-6, 5.0 * mad)
        clipped[column] = series.clip(lower=median - bound, upper=median + bound)
    return clipped


def _smoothed_variant(frame: pd.DataFrame) -> pd.DataFrame:
    smoothed = frame.copy()
    for column in FEATURE_COLUMNS:
        smoothed[column] = smoothed[column].rolling(window=5, min_periods=1).median()
    return smoothed


def build_microstructure_feature_baseline_review(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
    horizon_ms: int,
) -> dict[str, Any]:
    frame = build_microstructure_feature_frame(
        data_root=data_root,
        venue=venue,
        symbol=symbol,
        depth=depth,
        cadence_ms=cadence_ms,
    )
    if frame.empty:
        payload = {
            "generated_at": _ts(),
            "recommended_variant": None,
            "horizon_ms": horizon_ms,
            "cadence_ms": cadence_ms,
            "model_policy": "simple_linear_only_v1",
            "variants": [],
            "blocked_by": ["empty_feature_frame"],
        }
        payload["artifact_id"] = _artifact_id(payload)
        return payload

    variants = {
        "raw": frame,
        "clipped": _clip_variant(frame),
        "smoothed": _smoothed_variant(frame),
    }
    reviews = [
        _evaluate_linear_baseline(variant_frame, variant_name=name, horizon_ms=horizon_ms, cadence_ms=cadence_ms)
        for name, variant_frame in variants.items()
    ]
    best = max(reviews, key=lambda item: (float(item.get("f1", 0.0)), float(item.get("accuracy", 0.0))))
    blocked_by = [] if float(best.get("f1", 0.0)) >= 0.50 else ["weak_short_horizon_signal"]
    payload = {
        "generated_at": _ts(),
        "recommended_variant": best["variant"],
        "horizon_ms": horizon_ms,
        "cadence_ms": cadence_ms,
        "model_policy": "simple_linear_only_v1",
        "variants": reviews,
        "blocked_by": blocked_by,
    }
    payload["artifact_id"] = _artifact_id(payload)
    return payload


def write_microstructure_feature_baseline_review(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
    horizon_ms: int,
    output_dir: Path,
) -> dict[str, Any]:
    payload = build_microstructure_feature_baseline_review(
        data_root=data_root,
        venue=venue,
        symbol=symbol,
        depth=depth,
        cadence_ms=cadence_ms,
        horizon_ms=horizon_ms,
    )
    json_path = output_dir / "feature_baseline_review.json"
    md_path = output_dir / "feature_baseline_review.md"
    _write_json(json_path, payload)
    md_lines = [
        "# Microstructure Feature Baseline Review",
        "",
        f"- artifact_id: `{payload['artifact_id']}`",
        f"- recommended_variant: `{payload['recommended_variant']}`",
        f"- model_policy: `{payload['model_policy']}`",
        f"- cadence_ms: `{payload['cadence_ms']}`",
        f"- horizon_ms: `{payload['horizon_ms']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
        "",
        "## Variants",
        "",
    ]
    for item in payload["variants"]:
        md_lines.extend(
            [
                f"### {item['variant']}",
                "",
                f"- model_family: `{item['model_family']}`",
                f"- rows: `{item['rows']}`",
                f"- train_rows: `{item['train_rows']}`",
                f"- test_rows: `{item['test_rows']}`",
                f"- f1: `{item['f1']}`",
                f"- accuracy: `{item['accuracy']}`",
                f"- blocked_by: `{item['blocked_by']}`",
                "",
            ]
        )
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": payload["artifact_id"]}


def build_microstructure_admission_review(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
    horizon_ms: int,
) -> dict[str, Any]:
    dataset = build_microstructure_dataset_contract(
        data_root=data_root,
        venue=venue,
        symbol=symbol,
        depth=depth,
        cadence_ms=cadence_ms,
    )
    preprocessing = build_microstructure_preprocessing_review(
        data_root=data_root,
        venue=venue,
        symbol=symbol,
        depth=depth,
        cadence_ms=cadence_ms,
    )
    baseline = build_microstructure_feature_baseline_review(
        data_root=data_root,
        venue=venue,
        symbol=symbol,
        depth=depth,
        cadence_ms=cadence_ms,
        horizon_ms=horizon_ms,
    )
    blocked_by = (
        list(dataset.get("blocked_by", []))
        + list(preprocessing.get("blocked_by", []))
        + list(baseline.get("blocked_by", []))
    )
    payload = {
        "generated_at": _ts(),
        "source_name": "microstructure_sidecar",
        "venue": venue,
        "symbol": symbol,
        "depth": depth,
        "sampling_cadence_ms": cadence_ms,
        "horizon_ms": horizon_ms,
        "research_only": True,
        "execution_modifier_only": True,
        "promote_truth": False,
        "pit_safe": bool(dataset.get("ready")) and bool(preprocessing.get("ready")),
        "allowed_uses": list(DEFAULT_ALLOWED_USES),
        "recommended_variant": baseline.get("recommended_variant"),
        "research_ready": len(blocked_by) == 0,
        "blocked_by": blocked_by,
        "next_action": (
            "retain_sidecar_research_only"
            if len(blocked_by) == 0
            else "improve_dataset_quality_before_mainline_integration"
        ),
        "decision_policy": {
            "allow_direction_vote": False,
            "allow_execution_modifier": True,
            "allow_risk_overlay_input": True,
            "allow_promote_truth": False,
        },
    }
    payload["artifact_id"] = _artifact_id(payload)
    return payload


def write_microstructure_admission_review(
    *,
    data_root: Path,
    venue: str,
    symbol: str,
    depth: int,
    cadence_ms: int,
    horizon_ms: int,
    output_dir: Path,
) -> dict[str, Any]:
    payload = build_microstructure_admission_review(
        data_root=data_root,
        venue=venue,
        symbol=symbol,
        depth=depth,
        cadence_ms=cadence_ms,
        horizon_ms=horizon_ms,
    )
    json_path = output_dir / "admission_review.json"
    md_path = output_dir / "admission_review.md"
    _write_json(json_path, payload)
    md_lines = [
        "# Microstructure Admission Review",
        "",
        f"- artifact_id: `{payload['artifact_id']}`",
        f"- source_name: `{payload['source_name']}`",
        f"- venue: `{payload['venue']}`",
        f"- symbol: `{payload['symbol']}`",
        f"- depth: `{payload['depth']}`",
        f"- sampling_cadence_ms: `{payload['sampling_cadence_ms']}`",
        f"- horizon_ms: `{payload['horizon_ms']}`",
        f"- research_only: `{payload['research_only']}`",
        f"- execution_modifier_only: `{payload['execution_modifier_only']}`",
        f"- promote_truth: `{payload['promote_truth']}`",
        f"- pit_safe: `{payload['pit_safe']}`",
        f"- recommended_variant: `{payload['recommended_variant']}`",
        f"- research_ready: `{payload['research_ready']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
        f"- next_action: `{payload['next_action']}`",
        "",
        "## Allowed Uses",
        "",
    ]
    md_lines.extend(f"- `{item}`" for item in payload["allowed_uses"])
    md_lines.extend(
        [
            "",
            "## Decision Policy",
            "",
            f"- allow_direction_vote: `{payload['decision_policy']['allow_direction_vote']}`",
            f"- allow_execution_modifier: `{payload['decision_policy']['allow_execution_modifier']}`",
            f"- allow_risk_overlay_input: `{payload['decision_policy']['allow_risk_overlay_input']}`",
            f"- allow_promote_truth: `{payload['decision_policy']['allow_promote_truth']}`",
        ]
    )
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": payload["artifact_id"]}


__all__ = [
    "FEATURE_COLUMNS",
    "MicrostructureFeatureStore",
    "MicrostructureFeatureStoreConfig",
    "build_microstructure_admission_review",
    "build_microstructure_dataset_contract",
    "build_microstructure_feature_baseline_review",
    "build_microstructure_preprocessing_review",
    "write_microstructure_admission_review",
    "write_microstructure_dataset_contract",
    "write_microstructure_feature_baseline_review",
    "write_microstructure_preprocessing_review",
    "write_microstructure_sidecar_features",
]
