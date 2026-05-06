"""Research-only 5m-to-4h representation probe.

This module intentionally does not load a Time Series Foundation Model.
It builds a deterministic PCA proxy over 5m bar sequences so the repo can
test whether a TSFM-shaped feature layer is worth the integration cost.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
)

MANUAL_FEATURE_COLUMNS = (
    "realized_vol_5m",
    "intra_bar_consistency",
    "intra_bar_max_dd",
    "volume_front_ratio",
    "realized_skew",
    "taker_buy_ratio",
    "trade_intensity_log",
    "range_bps",
)

LABEL_NAMES = (
    "next_4h_direction_up",
    "next_4h_abs_move_top30",
    "next_4h_range_top30",
    "next_4h_downside_tail_worst30",
)

DEFAULT_ALLOWED_USES = ("risk_overlay_input", "execution_modifier")
DEFAULT_FORBIDDEN_USES = ("direction_vote", "promoted_truth", "direct_strategy_entry")


@dataclass(slots=True, frozen=True)
class BarTSFMProbeConfig:
    """Configuration for a deterministic 5m sequence representation probe."""

    kline_path: Path = Path("data/raw/binance/klines/BTCUSDT/5m/data.parquet")
    output_dir: Path = Path("results/bar_tsfm_probe")
    symbol: str = "BTCUSDT"
    source_timeframe: str = "5m"
    target_timeframe: str = "4h"
    source_bars_per_target: int = 48
    train_start: str = "2020-01-01"
    train_end: str = "2023-12-31"
    dev_start: str = "2024-01-01"
    dev_end: str = "2025-12-31"
    pca_components: int = 12
    include_chronos2: bool = False
    chronos2_model_id: str = "amazon/chronos-2"
    chronos2_device_map: str = "cpu"
    chronos2_batch_size: int = 256
    chronos2_embedding_components: int = 12
    ridge_alpha: float = 1.0
    top_quantile: float = 0.70
    downside_quantile: float = 0.30
    min_monthly_windows: int = 24


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_5m_klines(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix == ".parquet":
        frame = pd.read_parquet(path)
    else:
        frame = pd.read_csv(path, names=KLINE_COLUMNS)
    required = {"open_time", "open", "high", "low", "close", "volume", "trade_count", "taker_buy_base"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing_kline_columns:{','.join(missing)}")
    frame = frame.copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.loc[frame["open_time"].notna()].copy()
    frame = frame.sort_values("open_time", kind="stable")
    frame = frame.drop_duplicates("open_time", keep="last")
    return frame.reset_index(drop=True)


def _split_bounds(config: BarTSFMProbeConfig) -> dict[str, pd.Timestamp]:
    return {
        "train_start": pd.Timestamp(config.train_start, tz=UTC),
        "train_end": pd.Timestamp(config.train_end, tz=UTC) + pd.Timedelta(days=1),
        "dev_start": pd.Timestamp(config.dev_start, tz=UTC),
        "dev_end": pd.Timestamp(config.dev_end, tz=UTC) + pd.Timedelta(days=1),
    }


def _safe_log_ratio(numerator: float, denominator: float) -> float:
    if numerator <= 0.0 or denominator <= 0.0:
        return 0.0
    return math.log(numerator / denominator)


def _manual_features(group: pd.DataFrame) -> dict[str, float]:
    closes = group["close"].to_numpy(dtype=float)
    volumes = group["volume"].to_numpy(dtype=float)
    trade_count = group["trade_count"].to_numpy(dtype=float)
    taker_buy = group["taker_buy_base"].to_numpy(dtype=float)
    highs = group["high"].to_numpy(dtype=float)
    lows = group["low"].to_numpy(dtype=float)
    opens = group["open"].to_numpy(dtype=float)

    returns = np.diff(np.log(np.where(closes > 0.0, closes, np.nan)))
    returns = returns[np.isfinite(returns)]
    sum_sq = float(np.sum(returns * returns)) if len(returns) else 0.0
    realized_vol = math.sqrt(sum_sq)

    bar_direction = 1.0 if closes[-1] >= closes[0] else -1.0
    consistency = 1.0
    if len(returns):
        consistency = float(np.mean((returns >= 0.0) == (bar_direction >= 0.0)))

    peak = closes[0]
    max_dd = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak > 0.0:
            max_dd = max(max_dd, (peak - close) / peak)

    half = max(1, len(volumes) // 2)
    total_volume = float(np.sum(volumes))
    volume_front_ratio = float(np.sum(volumes[:half]) / total_volume) if total_volume > 0.0 else 0.5

    realized_skew = 0.0
    if len(returns) > 2:
        centered = returns - float(np.mean(returns))
        std = float(np.std(returns))
        if std > 1e-12:
            realized_skew = float(np.mean(centered**3) / (std**3))

    taker_buy_ratio = float(np.sum(taker_buy) / total_volume) if total_volume > 0.0 else 0.5
    range_bps = 0.0
    if float(opens[0]) > 0.0:
        range_bps = _safe_log_ratio(float(np.max(highs)), float(np.min(lows))) * 10_000.0

    return {
        "realized_vol_5m": round(realized_vol, 8),
        "intra_bar_consistency": round(consistency, 6),
        "intra_bar_max_dd": round(max_dd, 8),
        "volume_front_ratio": round(volume_front_ratio, 6),
        "realized_skew": round(realized_skew, 6),
        "taker_buy_ratio": round(taker_buy_ratio, 6),
        "trade_intensity_log": round(math.log1p(float(np.sum(trade_count))), 6),
        "range_bps": round(range_bps, 6),
    }


def _sequence_vector(group: pd.DataFrame) -> np.ndarray:
    close = group["close"].to_numpy(dtype=float)
    open_ = group["open"].to_numpy(dtype=float)
    high = group["high"].to_numpy(dtype=float)
    low = group["low"].to_numpy(dtype=float)
    volume = group["volume"].to_numpy(dtype=float)
    taker_buy = group["taker_buy_base"].to_numpy(dtype=float)
    trade_count = group["trade_count"].to_numpy(dtype=float)

    previous_close = np.roll(close, 1)
    previous_close[0] = open_[0]
    returns = np.array([_safe_log_ratio(c, p) for c, p in zip(close, previous_close, strict=False)])
    body = np.array([_safe_log_ratio(c, o) for c, o in zip(close, open_, strict=False)])
    spread_range = np.array(
        [_safe_log_ratio(high_value, low_value) for high_value, low_value in zip(high, low, strict=False)]
    )
    volume_log = np.log1p(np.maximum(volume, 0.0))
    volume_log = volume_log - float(np.mean(volume_log))
    taker_ratio = np.divide(taker_buy, volume, out=np.full_like(taker_buy, 0.5), where=volume > 0.0) - 0.5
    trade_log = np.log1p(np.maximum(trade_count, 0.0))
    trade_log = trade_log - float(np.mean(trade_log))
    return np.concatenate([returns, body, spread_range, volume_log, taker_ratio, trade_log]).astype(float)


def build_probe_frame(config: BarTSFMProbeConfig) -> pd.DataFrame:
    """Build 4h-aligned rows from 5m kline sequences."""
    frame = _read_5m_klines(config.kline_path)
    bounds = _split_bounds(config)
    max_source_time = int(bounds["dev_end"].timestamp() * 1_000)
    frame = frame.loc[frame["open_time"] < max_source_time].copy()

    target_ms = config.source_bars_per_target * 5 * 60 * 1_000
    frame["target_window_start_ms"] = (frame["open_time"] // target_ms) * target_ms

    rows: list[dict[str, Any]] = []
    sequence_vectors: list[np.ndarray] = []
    grouped = frame.groupby("target_window_start_ms", sort=True)
    for window_start_ms, group in grouped:
        group = group.sort_values("open_time", kind="stable")
        if len(group) != config.source_bars_per_target:
            continue
        if int(group["open_time"].iloc[0]) != int(window_start_ms):
            continue
        cutoff_ms = int(window_start_ms + target_ms)
        cutoff = pd.to_datetime(cutoff_ms, unit="ms", utc=True)
        manual = _manual_features(group)
        rows.append(
            {
                "window_start_ms": int(window_start_ms),
                "cutoff_time_ms": cutoff_ms,
                "cutoff_time": cutoff,
                "open": float(group["open"].iloc[0]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group["close"].iloc[-1]),
                **manual,
            }
        )
        sequence_vectors.append(_sequence_vector(group))

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values("cutoff_time_ms", kind="stable").reset_index(drop=True)
    vectors = np.vstack(sequence_vectors)
    sequence_columns = [f"seq_{idx:03d}" for idx in range(vectors.shape[1])]
    out = pd.concat([out, pd.DataFrame(vectors, columns=sequence_columns)], axis=1)

    out["next_close"] = out["close"].shift(-1)
    out["next_high"] = out["high"].shift(-1)
    out["next_low"] = out["low"].shift(-1)
    out["future_return_bps"] = np.log(out["next_close"] / out["close"]) * 10_000.0
    out["future_abs_return_bps"] = out["future_return_bps"].abs()
    out["future_range_bps"] = np.log(out["next_high"] / out["next_low"]) * 10_000.0
    out["future_downside_bps"] = np.log(out["next_low"] / out["close"]) * 10_000.0
    out = out.loc[out["future_return_bps"].notna()].copy()

    bounds = _split_bounds(config)
    out["split"] = "unused"
    out.loc[(out["cutoff_time"] >= bounds["train_start"]) & (out["cutoff_time"] < bounds["train_end"]), "split"] = (
        "train"
    )
    out.loc[(out["cutoff_time"] >= bounds["dev_start"]) & (out["cutoff_time"] < bounds["dev_end"]), "split"] = "dev"
    return out.reset_index(drop=True)


def _standardize(train: np.ndarray, other: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mean = np.mean(train, axis=0)
    std = np.std(train, axis=0)
    std = np.where(std > 1e-12, std, 1.0)
    return (train - mean) / std, (other - mean) / std, mean, std


def _fit_pca_proxy(train_x: np.ndarray, all_x: np.ndarray, n_components: int) -> np.ndarray:
    train_scaled, all_scaled, _, _ = _standardize(train_x, all_x)
    _, _, vt = np.linalg.svd(train_scaled, full_matrices=False)
    k = max(1, min(n_components, vt.shape[0]))
    return all_scaled @ vt[:k].T


def _sequence_inputs(frame: pd.DataFrame, config: BarTSFMProbeConfig) -> np.ndarray:
    seq_cols = [column for column in frame.columns if column.startswith("seq_")]
    expected = config.source_bars_per_target * 6
    if len(seq_cols) != expected:
        raise ValueError(f"unexpected_sequence_width:{len(seq_cols)}")
    values = frame.loc[:, seq_cols].to_numpy(dtype=np.float32)
    return values.reshape(len(frame), 6, config.source_bars_per_target)


def _chronos2_embedding_pca(frame: pd.DataFrame, config: BarTSFMProbeConfig) -> tuple[np.ndarray, dict[str, Any]]:
    try:
        from chronos import BaseChronosPipeline
    except ImportError as exc:
        raise RuntimeError("chronos_forecasting_unavailable") from exc

    pipeline = BaseChronosPipeline.from_pretrained(
        config.chronos2_model_id,
        device_map=config.chronos2_device_map,
    )
    inputs = _sequence_inputs(frame, config)
    embeddings, _loc_scale = pipeline.embed(
        inputs,
        batch_size=config.chronos2_batch_size,
        context_length=config.source_bars_per_target,
    )
    pooled_rows: list[np.ndarray] = []
    for embedding in embeddings:
        arr = embedding.detach().cpu().numpy()
        if arr.ndim != 3:
            raise RuntimeError(f"unexpected_chronos_embedding_shape:{arr.shape}")
        usable = arr[:, :-1, :] if arr.shape[1] > 1 else arr
        pooled_rows.append(usable.mean(axis=(0, 1)))
    pooled = np.vstack(pooled_rows)
    train_mask = frame["split"].to_numpy() == "train"
    reduced = _fit_pca_proxy(
        pooled[train_mask],
        pooled,
        n_components=config.chronos2_embedding_components,
    )
    metadata = {
        "chronos2_model_id": config.chronos2_model_id,
        "chronos2_device_map": config.chronos2_device_map,
        "chronos2_batch_size": config.chronos2_batch_size,
        "chronos2_raw_embedding_dim": int(pooled.shape[1]),
        "chronos2_embedding_components": int(reduced.shape[1]),
        "chronos2_pooling": "mean_variates_and_encoder_tokens_excluding_masked_output",
    }
    return reduced, metadata


def _f1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    tp = float(np.sum((y_true == 1) & (y_pred == 1)))
    fp = float(np.sum((y_true == 0) & (y_pred == 1)))
    fn = float(np.sum((y_true == 1) & (y_pred == 0)))
    precision = tp / (tp + fp) if tp + fp > 0.0 else 0.0
    recall = tp / (tp + fn) if tp + fn > 0.0 else 0.0
    return 0.0 if precision + recall == 0.0 else 2.0 * precision * recall / (precision + recall)


def _classification_metrics(y_true: np.ndarray, y_pred: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    tp = float(np.sum((y_true == 1) & (y_pred == 1)))
    fp = float(np.sum((y_true == 0) & (y_pred == 1)))
    fn = float(np.sum((y_true == 1) & (y_pred == 0)))
    precision = tp / (tp + fp) if tp + fp > 0.0 else 0.0
    recall = tp / (tp + fn) if tp + fn > 0.0 else 0.0
    accuracy = float(np.mean(y_true == y_pred)) if len(y_true) else 0.0
    return {
        "f1": round(_f1(y_true, y_pred), 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "accuracy": round(accuracy, 4),
        "positive_rate": round(float(np.mean(y_pred)) if len(y_pred) else 0.0, 4),
        "avg_abs_score": round(float(np.mean(np.abs(scores))) if len(scores) else 0.0, 4),
    }


def _fit_ridge_scores(
    train_x: np.ndarray,
    train_y: np.ndarray,
    eval_x: np.ndarray,
    *,
    alpha: float,
) -> tuple[np.ndarray, np.ndarray]:
    train_scaled, eval_scaled, _, _ = _standardize(train_x, eval_x)
    train_design = np.column_stack([np.ones(len(train_scaled)), train_scaled])
    eval_design = np.column_stack([np.ones(len(eval_scaled)), eval_scaled])
    y_signed = train_y.astype(float) * 2.0 - 1.0
    penalty = np.eye(train_design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    weights = np.linalg.solve(train_design.T @ train_design + penalty, train_design.T @ y_signed)
    return train_design @ weights, eval_design @ weights


def _best_train_threshold(scores: np.ndarray, y_true: np.ndarray) -> tuple[float, float]:
    if len(scores) == 0:
        return 0.0, 0.0
    candidates = np.unique(np.quantile(scores, np.linspace(0.05, 0.95, 37)))
    best_threshold = 0.0
    best_f1 = -1.0
    for threshold in candidates:
        y_pred = (scores >= threshold).astype(int)
        score = _f1(y_true, y_pred)
        if score > best_f1:
            best_f1 = score
            best_threshold = float(threshold)
    return best_threshold, max(0.0, best_f1)


def _monthly_stability(
    dev_frame: pd.DataFrame,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    min_monthly_windows: int,
) -> dict[str, Any]:
    if dev_frame.empty:
        return {"monthly_f1_mean": 0.0, "monthly_f1_std": 0.0, "monthly_f1_min": 0.0, "months": 0}
    tmp = pd.DataFrame(
        {
            "month": dev_frame["cutoff_time"].dt.strftime("%Y-%m").to_numpy(),
            "y_true": y_true,
            "y_pred": y_pred,
        }
    )
    values: list[float] = []
    for _, group in tmp.groupby("month", sort=True):
        if len(group) < min_monthly_windows:
            continue
        values.append(_f1(group["y_true"].to_numpy(dtype=int), group["y_pred"].to_numpy(dtype=int)))
    if not values:
        return {"monthly_f1_mean": 0.0, "monthly_f1_std": 0.0, "monthly_f1_min": 0.0, "months": 0}
    arr = np.array(values, dtype=float)
    return {
        "monthly_f1_mean": round(float(np.mean(arr)), 4),
        "monthly_f1_std": round(float(np.std(arr)), 4),
        "monthly_f1_min": round(float(np.min(arr)), 4),
        "months": len(arr),
    }


def _label_thresholds(train: pd.DataFrame, config: BarTSFMProbeConfig) -> dict[str, float]:
    return {
        "next_4h_abs_move_top30": float(train["future_abs_return_bps"].quantile(config.top_quantile)),
        "next_4h_range_top30": float(train["future_range_bps"].quantile(config.top_quantile)),
        "next_4h_downside_tail_worst30": float(train["future_downside_bps"].quantile(config.downside_quantile)),
    }


def _labels(frame: pd.DataFrame, thresholds: dict[str, float]) -> dict[str, np.ndarray]:
    return {
        "next_4h_direction_up": (frame["future_return_bps"].to_numpy(dtype=float) > 0.0).astype(int),
        "next_4h_abs_move_top30": (
            frame["future_abs_return_bps"].to_numpy(dtype=float) >= thresholds["next_4h_abs_move_top30"]
        ).astype(int),
        "next_4h_range_top30": (
            frame["future_range_bps"].to_numpy(dtype=float) >= thresholds["next_4h_range_top30"]
        ).astype(int),
        "next_4h_downside_tail_worst30": (
            frame["future_downside_bps"].to_numpy(dtype=float) <= thresholds["next_4h_downside_tail_worst30"]
        ).astype(int),
    }


def _evaluate_variant(
    *,
    name: str,
    train_x: np.ndarray,
    dev_x: np.ndarray,
    train_y: np.ndarray,
    dev_y: np.ndarray,
    dev_frame: pd.DataFrame,
    config: BarTSFMProbeConfig,
) -> dict[str, Any]:
    train_scores, dev_scores = _fit_ridge_scores(train_x, train_y, dev_x, alpha=config.ridge_alpha)
    threshold, train_f1 = _best_train_threshold(train_scores, train_y)
    train_pred = (train_scores >= threshold).astype(int)
    dev_pred = (dev_scores >= threshold).astype(int)
    return {
        "variant": name,
        "model_family": "ridge_linear_threshold_train_only",
        "train_threshold": round(threshold, 6),
        "train": {
            **_classification_metrics(train_y, train_pred, train_scores),
            "selected_f1": round(train_f1, 4),
            "label_positive_rate": round(float(np.mean(train_y)) if len(train_y) else 0.0, 4),
        },
        "dev": {
            **_classification_metrics(dev_y, dev_pred, dev_scores),
            "label_positive_rate": round(float(np.mean(dev_y)) if len(dev_y) else 0.0, 4),
            **_monthly_stability(dev_frame, dev_y, dev_pred, min_monthly_windows=config.min_monthly_windows),
        },
    }


def run_bar_tsfm_probe(config: BarTSFMProbeConfig | None = None) -> dict[str, Any]:
    """Run the train/dev-only representation probe and return a report payload."""
    config = config or BarTSFMProbeConfig()
    frame = build_probe_frame(config)
    if frame.empty:
        payload = {
            "generated_at": _ts(),
            "source_type": "bar_tsfm_readiness_probe",
            "ready": False,
            "blocked_by": ["empty_probe_frame"],
        }
        return payload

    train = frame.loc[frame["split"] == "train"].copy()
    dev = frame.loc[frame["split"] == "dev"].copy()
    if train.empty or dev.empty:
        return {
            "generated_at": _ts(),
            "source_type": "bar_tsfm_readiness_probe",
            "ready": False,
            "rows": {"total": len(frame), "train": len(train), "dev": len(dev)},
            "blocked_by": ["train_or_dev_split_empty"],
        }

    seq_cols = [column for column in frame.columns if column.startswith("seq_")]
    manual_cols = list(MANUAL_FEATURE_COLUMNS)
    thresholds = _label_thresholds(train, config)
    all_pca = _fit_pca_proxy(
        train.loc[:, seq_cols].to_numpy(dtype=float),
        frame.loc[:, seq_cols].to_numpy(dtype=float),
        n_components=config.pca_components,
    )
    pca_cols = [f"pca_{idx:02d}" for idx in range(all_pca.shape[1])]
    for idx, column in enumerate(pca_cols):
        frame[column] = all_pca[:, idx]
    train = frame.loc[frame["split"] == "train"].copy()
    dev = frame.loc[frame["split"] == "dev"].copy()

    train_labels = _labels(train, thresholds)
    dev_labels = _labels(dev, thresholds)
    variants = {
        "manual_hf_features": manual_cols,
        "sequence_pca_proxy": pca_cols,
        "manual_plus_sequence_pca": [*manual_cols, *pca_cols],
    }
    tsfm_embedding_status: dict[str, Any] = {
        "requested": bool(config.include_chronos2),
        "available": False,
        "blocked_by": [],
    }
    if config.include_chronos2:
        try:
            chronos_pca, chronos_metadata = _chronos2_embedding_pca(frame, config)
        except Exception as exc:
            tsfm_embedding_status["blocked_by"] = [str(exc)]
        else:
            chronos_cols = [f"chronos2_pca_{idx:02d}" for idx in range(chronos_pca.shape[1])]
            frame = pd.concat([frame, pd.DataFrame(chronos_pca, columns=chronos_cols)], axis=1)
            train = frame.loc[frame["split"] == "train"].copy()
            dev = frame.loc[frame["split"] == "dev"].copy()
            variants["chronos2_embedding_pca"] = chronos_cols
            variants["manual_plus_chronos2_embedding_pca"] = [*manual_cols, *chronos_cols]
            tsfm_embedding_status.update({"available": True, **chronos_metadata})

    label_reviews: list[dict[str, Any]] = []
    for label_name in LABEL_NAMES:
        reviews = [
            _evaluate_variant(
                name=variant_name,
                train_x=train.loc[:, columns].to_numpy(dtype=float),
                dev_x=dev.loc[:, columns].to_numpy(dtype=float),
                train_y=train_labels[label_name],
                dev_y=dev_labels[label_name],
                dev_frame=dev,
                config=config,
            )
            for variant_name, columns in variants.items()
        ]
        best_dev = max(
            reviews,
            key=lambda item: (
                float(item["dev"]["f1"]),
                -float(item["dev"]["monthly_f1_std"]),
                float(item["dev"]["accuracy"]),
            ),
        )
        manual = next(item for item in reviews if item["variant"] == "manual_hf_features")
        label_reviews.append(
            {
                "label": label_name,
                "best_dev_variant": best_dev["variant"],
                "manual_dev_f1": manual["dev"]["f1"],
                "best_dev_f1": best_dev["dev"]["f1"],
                "best_monthly_f1_std": best_dev["dev"]["monthly_f1_std"],
                "variants": reviews,
            }
        )

    risk_labels = {"next_4h_abs_move_top30", "next_4h_range_top30", "next_4h_downside_tail_worst30"}
    risk_improvements = [
        float(item["best_dev_f1"]) - float(item["manual_dev_f1"])
        for item in label_reviews
        if item["label"] in risk_labels and item["best_dev_variant"] != "manual_hf_features"
    ]
    true_tsfm_risk_improvements = [
        float(item["best_dev_f1"]) - float(item["manual_dev_f1"])
        for item in label_reviews
        if item["label"] in risk_labels
        and str(item["best_dev_variant"]).startswith(("chronos2_", "manual_plus_chronos2_"))
    ]
    blocked_by: list[str] = ["dev_is_diagnostic_only", "no_test_split_access", "direction_vote_not_approved"]
    if not bool(tsfm_embedding_status.get("available")):
        blocked_by.append("not_true_tsfm_embedding")
        if tsfm_embedding_status.get("requested"):
            blocked_by.extend(f"chronos2:{item}" for item in tsfm_embedding_status.get("blocked_by", []))
    if not risk_improvements or max(risk_improvements) < 0.03:
        blocked_by.append("no_material_risk_label_improvement_over_manual_hf")
    if bool(tsfm_embedding_status.get("available")) and (
        not true_tsfm_risk_improvements or max(true_tsfm_risk_improvements) < 0.03
    ):
        blocked_by.append("no_material_true_tsfm_risk_label_improvement_over_manual_hf")

    material_true_tsfm_blocked = "no_material_true_tsfm_risk_label_improvement_over_manual_hf" in blocked_by
    payload = {
        "generated_at": _ts(),
        "source_type": "bar_tsfm_readiness_probe",
        "symbol": config.symbol,
        "source_timeframe": config.source_timeframe,
        "target_timeframe": config.target_timeframe,
        "embedding_policy": (
            "chronos2_encoder_embedding_plus_sequence_pca_proxy"
            if bool(tsfm_embedding_status.get("available"))
            else "sequence_pca_proxy_not_true_tsfm"
        ),
        "model_policy": "pca_proxy_plus_ridge_linear_train_threshold",
        "tsfm_embedding_status": tsfm_embedding_status,
        "split_policy": {
            "train": f"{config.train_start}..{config.train_end}",
            "dev": f"{config.dev_start}..{config.dev_end}",
            "test_accessed": False,
        },
        "rows": {
            "total_full_4h_windows": len(frame),
            "train": len(train),
            "dev": len(dev),
            "unused": int((frame["split"] == "unused").sum()),
        },
        "feature_policy": {
            "manual_feature_columns": manual_cols,
            "sequence_vector_columns": len(seq_cols),
            "pca_components": len(pca_cols),
        },
        "label_thresholds_train_only": {key: round(value, 6) for key, value in thresholds.items()},
        "label_reviews": label_reviews,
        "allowed_uses": list(DEFAULT_ALLOWED_USES),
        "forbidden_uses": list(DEFAULT_FORBIDDEN_USES),
        "research_ready": "no_material_risk_label_improvement_over_manual_hf" not in blocked_by,
        "integration_ready": bool(tsfm_embedding_status.get("available")) and not material_true_tsfm_blocked,
        "blocked_by": blocked_by,
        "next_action": (
            "evaluate_risk_overlay_threshold_ablation_train_dev_only"
            if bool(tsfm_embedding_status.get("available")) and not material_true_tsfm_blocked
            else "do_not_integrate_chronos2_embedding_without_new_label_or_pooling_evidence"
            if bool(tsfm_embedding_status.get("available"))
            else "run_true_tsfm_embedding_ablation_train_dev_only"
            if "not_true_tsfm_embedding" in blocked_by
            and "no_material_risk_label_improvement_over_manual_hf" not in blocked_by
            else "keep_5m_sequence_layer_as_research_backlog_until_true_tsfm_ablation"
        ),
    }
    return payload


def write_bar_tsfm_probe_report(config: BarTSFMProbeConfig | None = None) -> dict[str, str]:
    config = config or BarTSFMProbeConfig()
    payload = run_bar_tsfm_probe(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = config.output_dir / "bar_tsfm_probe.json"
    md_path = config.output_dir / "bar_tsfm_probe.md"
    _write_json(json_path, payload)
    md_path.write_text(_markdown_report(payload), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path)}


def _markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Bar TSFM Readiness Probe",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- source_type: `{payload.get('source_type')}`",
        f"- embedding_policy: `{payload.get('embedding_policy')}`",
        f"- model_policy: `{payload.get('model_policy')}`",
        f"- research_ready: `{payload.get('research_ready')}`",
        f"- integration_ready: `{payload.get('integration_ready')}`",
        f"- blocked_by: `{payload.get('blocked_by')}`",
        f"- next_action: `{payload.get('next_action')}`",
        f"- tsfm_embedding_status: `{payload.get('tsfm_embedding_status')}`",
        "",
        "## Split",
        "",
        f"- train: `{payload.get('split_policy', {}).get('train')}`",
        f"- dev: `{payload.get('split_policy', {}).get('dev')}`",
        f"- test_accessed: `{payload.get('split_policy', {}).get('test_accessed')}`",
        "",
        "## Rows",
        "",
    ]
    rows = payload.get("rows", {})
    for key in ("total_full_4h_windows", "train", "dev", "unused"):
        lines.append(f"- {key}: `{rows.get(key)}`")

    lines.extend(["", "## Label Reviews", ""])
    for item in payload.get("label_reviews", []):
        lines.extend(
            [
                f"### {item['label']}",
                "",
                f"- best_dev_variant: `{item['best_dev_variant']}`",
                f"- manual_dev_f1: `{item['manual_dev_f1']}`",
                f"- best_dev_f1: `{item['best_dev_f1']}`",
                f"- best_monthly_f1_std: `{item['best_monthly_f1_std']}`",
                "",
                "| variant | train_f1 | dev_f1 | dev_precision | dev_recall | monthly_f1_mean | monthly_f1_std |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for variant in item["variants"]:
            train = variant["train"]
            dev = variant["dev"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        variant["variant"],
                        str(train["f1"]),
                        str(dev["f1"]),
                        str(dev["precision"]),
                        str(dev["recall"]),
                        str(dev["monthly_f1_mean"]),
                        str(dev["monthly_f1_std"]),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines.extend(["## Boundary", ""])
    if payload.get("tsfm_embedding_status", {}).get("available"):
        lines.append("- Chronos-2 encoder embeddings were computed and reduced with train-fitted PCA.")
    else:
        lines.append("- This is a deterministic PCA proxy, not a true TSFM embedding.")
    lines.extend(
        [
            "- Dev is diagnostic only; no test split evidence is used.",
            "- Allowed use is limited to risk overlay or future execution modifier research.",
            "- Direction vote and promoted truth remain out of scope.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "BarTSFMProbeConfig",
    "build_probe_frame",
    "run_bar_tsfm_probe",
    "write_bar_tsfm_probe_report",
]
