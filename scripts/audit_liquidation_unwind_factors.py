#!/usr/bin/env python3
"""Audit research-only liquidation/unwind phase factors on train data."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(slots=True, frozen=True)
class LiquidationUnwindFactorAudit:
    name: str
    hypothesis: str
    observations: int
    rank_ic_mean: float
    rolling_rank_ic_mean: float
    rank_ic_stability: float
    hit_rate: float
    ablation_delta: float
    max_abs_correlation: float
    max_abs_spearman: float
    signal_sign: float
    blocked_by: str | None


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit research-only liquidation/unwind phase factors")
    parser.add_argument("--btc-4h-dir", type=Path, default=Path("data/raw/binance/klines/BTCUSDT/4h"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/raw/binance/metrics/BTCUSDT"))
    parser.add_argument("--open-interest-dir", type=Path, default=Path("data/raw/binance/open_interest/BTCUSDT/4h"))
    parser.add_argument("--funding-dir", type=Path, default=Path("data/raw/binance/funding/BTCUSDT"))
    parser.add_argument("--liquidation-dir", type=Path, default=Path("data/raw/merged_liquidations/aggregated/BTCUSDT"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("results/research_swarm/liquidation_unwind_factor_audit")
    )
    parser.add_argument("--train-end", default="2023-12-31T23:59:59+00:00")
    parser.add_argument("--corr-threshold", type=float, default=0.75)
    parser.add_argument("--min-train-observations", type=int, default=500)
    parser.add_argument("--min-abs-rank-ic", type=float, default=0.01)
    parser.add_argument("--min-abs-ablation-delta", type=float, default=0.005)
    args = parser.parse_args()

    payload = build_liquidation_unwind_factor_audit(
        btc_4h_dir=args.btc_4h_dir,
        metrics_dir=args.metrics_dir,
        open_interest_dir=args.open_interest_dir,
        funding_dir=args.funding_dir,
        liquidation_dir=args.liquidation_dir,
        output_dir=args.output_dir,
        train_end=args.train_end,
        corr_threshold=args.corr_threshold,
        min_train_observations=args.min_train_observations,
        min_abs_rank_ic=args.min_abs_rank_ic,
        min_abs_ablation_delta=args.min_abs_ablation_delta,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "liquidation_unwind_factor_audit.json"
    md_path = args.output_dir / "liquidation_unwind_factor_audit.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path), "summary": payload["summary"]}, indent=2))


def build_liquidation_unwind_factor_audit(
    *,
    btc_4h_dir: Path,
    metrics_dir: Path,
    open_interest_dir: Path,
    funding_dir: Path,
    liquidation_dir: Path,
    output_dir: Path | None,
    train_end: str,
    corr_threshold: float,
    min_train_observations: int,
    min_abs_rank_ic: float,
    min_abs_ablation_delta: float,
) -> dict[str, Any]:
    prices = _load_btc_4h_prices(btc_4h_dir)
    metrics, metrics_lineage = _load_metrics(metrics_dir)
    open_interest, oi_lineage = _load_open_interest(open_interest_dir)
    funding, funding_lineage = _load_funding(funding_dir)
    liquidations, liquidation_lineage = _load_liquidations(liquidation_dir)

    if prices.empty or funding.empty or (metrics.empty and open_interest.empty):
        return _blocked_payload(
            train_end=train_end,
            blocked_by="price_funding_or_oi_missing",
            corr_threshold=corr_threshold,
            min_train_observations=min_train_observations,
            min_abs_rank_ic=min_abs_rank_ic,
            min_abs_ablation_delta=min_abs_ablation_delta,
            source_lineage={
                "btc_4h": _source_state(btc_4h_dir, not prices.empty),
                "metrics": metrics_lineage,
                "open_interest": oi_lineage,
                "funding": funding_lineage,
                "liquidation": liquidation_lineage,
            },
        )

    features = _build_features(
        prices=prices,
        metrics=metrics,
        open_interest=open_interest,
        funding=funding,
        liquidations=liquidations,
        liquidation_lineage=liquidation_lineage,
    )
    source_lineage = {
        "btc_4h": _source_state(btc_4h_dir, True),
        "metrics": metrics_lineage,
        "open_interest": oi_lineage,
        "funding": funding_lineage,
        "liquidation": liquidation_lineage,
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        features_path = output_dir / "liquidation_unwind_derived_features.parquet"
        features.to_parquet(features_path, index=False)
    else:
        features_path = None

    train_cutoff = pd.Timestamp(train_end)
    if train_cutoff.tzinfo is None:
        train_cutoff = train_cutoff.tz_localize(UTC)
    aligned = features[features["ts"] <= train_cutoff].copy()
    factor_names = [
        "crowded_long_unwind_risk",
        "crowded_short_squeeze",
        "oi_continuation_support",
        "taker_oi_exhaustion",
    ]
    aligned = aligned.dropna(subset=["forward_return_4h"])
    if aligned.empty or not any(aligned[name].notna().any() for name in factor_names):
        return _blocked_payload(
            train_end=train_end,
            blocked_by="no_train_aligned_liquidation_unwind_factor",
            corr_threshold=corr_threshold,
            min_train_observations=min_train_observations,
            min_abs_rank_ic=min_abs_rank_ic,
            min_abs_ablation_delta=min_abs_ablation_delta,
            source_lineage=source_lineage,
            features_path=str(features_path) if features_path else None,
        )

    pairwise_pearson = _pairwise(aligned, factor_names, method="pearson")
    pairwise_spearman = _pairwise(aligned, factor_names, method="spearman")
    baseline = aligned[["price_return_24h_z", "realized_vol_24h_z", "funding_z", "oi_growth_24h_z"]]
    audits: list[LiquidationUnwindFactorAudit] = []
    for name in factor_names:
        factor_frame = aligned[[name, "forward_return_4h"]].dropna()
        factor_values = factor_frame[name].astype(float)
        forward_returns = factor_frame["forward_return_4h"].astype(float)
        rolling_ics = _rolling_rank_ic(factor_values, forward_returns, window=120)
        rank_ic = _rank_corr(factor_values, forward_returns)
        rolling_mean = float(np.mean(rolling_ics)) if rolling_ics else rank_ic
        rolling_std = float(np.std(rolling_ics)) if rolling_ics else 0.0
        stability = abs(rolling_mean) / rolling_std if rolling_std > 1e-9 else abs(rolling_mean)
        signal_sign = 1.0 if rolling_mean >= 0.0 else -1.0
        hit_rate = _signed_hit_rate(rolling_ics, signal_sign)
        residual_values = _residualize(factor_values, baseline)
        ablation_delta = _rank_corr(residual_values, forward_returns)
        max_abs_corr = max((abs(pairwise_pearson[name][peer]) for peer in factor_names if peer != name), default=0.0)
        max_abs_spearman = max(
            (abs(pairwise_spearman[name][peer]) for peer in factor_names if peer != name),
            default=0.0,
        )
        blocked_by = None
        if len(factor_values) < min_train_observations:
            blocked_by = "insufficient_train_observations"
        elif abs(rolling_mean) < min_abs_rank_ic:
            blocked_by = "weak_abs_rank_ic"
        elif abs(ablation_delta) < min_abs_ablation_delta:
            blocked_by = "weak_ablation_delta"
        audits.append(
            LiquidationUnwindFactorAudit(
                name=name,
                hypothesis=_HYPOTHESES[name],
                observations=len(factor_values),
                rank_ic_mean=round(rank_ic, 4),
                rolling_rank_ic_mean=round(rolling_mean, 4),
                rank_ic_stability=round(stability, 4),
                hit_rate=round(hit_rate, 4),
                ablation_delta=round(float(ablation_delta), 4),
                max_abs_correlation=round(max_abs_corr, 4),
                max_abs_spearman=round(max_abs_spearman, 4),
                signal_sign=signal_sign,
                blocked_by=blocked_by,
            )
        )

    audits = _apply_correlation_gate(
        audits,
        pairwise_pearson=pairwise_pearson,
        pairwise_spearman=pairwise_spearman,
        corr_threshold=corr_threshold,
    )
    admissible = [audit for audit in audits if audit.blocked_by is None]
    return {
        "schema_version": "liquidation_unwind_factor_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "train_only": True,
        "train_end": train_end,
        "corr_threshold": corr_threshold,
        "min_train_observations": min_train_observations,
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_abs_ablation_delta": min_abs_ablation_delta,
        "features_path": str(features_path) if features_path else None,
        "source_lineage": source_lineage,
        "summary": {
            "factor_count": len(audits),
            "admissible_factor_count": len(admissible),
            "blocked_factor_count": len(audits) - len(admissible),
            "high_correlation_blocked_count": sum(
                1 for audit in audits if audit.blocked_by == "high_factor_correlation"
            ),
            "liquidation_lineage": liquidation_lineage["lineage"],
            "liquidation_cache_present": liquidation_lineage["cache_present"],
            "liquidation_proxy_used": liquidation_lineage["proxy_used"],
            "blocked_by": None if admissible else "no_admissible_liquidation_unwind_factor",
        },
        "factors": [asdict(audit) for audit in sorted(audits, key=_audit_sort_key, reverse=True)],
        "pairwise_correlation_matrix": pairwise_pearson,
        "pairwise_spearman_matrix": pairwise_spearman,
    }


_HYPOTHESES = {
    "crowded_long_unwind_risk": "Positive funding and OI expansion can mark long crowding that is vulnerable to forced unwind.",
    "crowded_short_squeeze": "Negative funding and OI expansion can mark short crowding that is vulnerable to squeeze.",
    "oi_continuation_support": "OI expansion in the direction of recent price movement can support continuation.",
    "taker_oi_exhaustion": "Aggressive taker pressure paired with OI expansion can mark late-phase exhaustion.",
}


def _load_btc_4h_prices(btc_4h_dir: Path) -> pd.DataFrame:
    frames = _read_parquet_frames(btc_4h_dir)
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    ts_column = "close_time_ns" if "close_time_ns" in frame.columns else "ts_ns"
    if ts_column not in frame.columns or "close" not in frame.columns:
        return pd.DataFrame()
    price = frame[
        [column for column in [ts_column, "close", "volume", "taker_buy_base"] if column in frame.columns]
    ].copy()
    price["ts"] = _normalize_ts(pd.to_datetime(price[ts_column], unit="ns", utc=True))
    price = price.sort_values("ts").drop_duplicates(subset=["ts"])
    price["close"] = pd.to_numeric(price["close"], errors="coerce")
    price["forward_return_4h"] = price["close"].pct_change().shift(-1)
    price["price_return_24h"] = price["close"].pct_change(6)
    price["realized_vol_24h"] = price["close"].pct_change().rolling(6, min_periods=4).std()
    price["price_return_24h_z"] = _rolling_z(price["price_return_24h"], 90)
    price["realized_vol_24h_z"] = _rolling_z(price["realized_vol_24h"], 90)
    if {"volume", "taker_buy_base"}.issubset(price.columns):
        volume = pd.to_numeric(price["volume"], errors="coerce").replace(0.0, np.nan)
        taker_buy = pd.to_numeric(price["taker_buy_base"], errors="coerce")
        taker_ratio = (taker_buy / (volume - taker_buy).clip(lower=1e-9)).replace([math.inf, -math.inf], np.nan)
        price["kline_taker_pressure_z"] = _rolling_z(_safe_log(taker_ratio), 90)
    else:
        price["kline_taker_pressure_z"] = np.nan
    return price[
        ["ts", "close", "forward_return_4h", "price_return_24h_z", "realized_vol_24h_z", "kline_taker_pressure_z"]
    ].dropna(subset=["close", "forward_return_4h"])


def _load_metrics(metrics_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames = _read_parquet_frames(metrics_dir, exclude_names={"derived_metrics_factors.parquet"})
    if not frames:
        return pd.DataFrame(), _source_state(metrics_dir, False, lineage="missing")
    frame = pd.concat(frames, ignore_index=True)
    ts = _coerce_time(frame, ["create_time", "timestamp", "time", "ts", "ts_ns"])
    if ts is None:
        return pd.DataFrame(), _source_state(metrics_dir, False, lineage="missing_timestamp")
    frame = frame.copy()
    frame["ts"] = ts
    optional_columns = [
        "sum_open_interest",
        "sum_open_interest_value",
        "count_toptrader_long_short_ratio",
        "sum_toptrader_long_short_ratio",
        "count_long_short_ratio",
        "sum_taker_long_short_vol_ratio",
    ]
    available = [column for column in optional_columns if column in frame.columns]
    if not available:
        return pd.DataFrame(), _source_state(metrics_dir, False, lineage="missing_metric_columns")
    for column in available:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame[["ts", *available]].sort_values("ts").dropna(subset=available, how="all"),
        _source_state(metrics_dir, True, lineage="binance_metrics_cache", rows=len(frame)),
    )


def _load_open_interest(open_interest_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames = _read_parquet_frames(open_interest_dir)
    if not frames:
        return pd.DataFrame(), _source_state(open_interest_dir, False, lineage="missing")
    frame = pd.concat(frames, ignore_index=True)
    ts = _coerce_time(frame, ["ts_ns", "timestamp", "time", "create_time"])
    if ts is None:
        return pd.DataFrame(), _source_state(open_interest_dir, False, lineage="missing_timestamp")
    oi_column = "open_interest" if "open_interest" in frame.columns else "sum_open_interest"
    if oi_column not in frame.columns:
        return pd.DataFrame(), _source_state(open_interest_dir, False, lineage="missing_open_interest")
    frame = frame.copy()
    frame["ts"] = ts
    frame["open_interest"] = pd.to_numeric(frame[oi_column], errors="coerce")
    return (
        frame[["ts", "open_interest"]].sort_values("ts").dropna(),
        _source_state(open_interest_dir, True, lineage="open_interest_cache", rows=len(frame)),
    )


def _load_funding(funding_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames = _read_parquet_frames(funding_dir)
    if not frames:
        return pd.DataFrame(), _source_state(funding_dir, False, lineage="missing")
    frame = pd.concat(frames, ignore_index=True)
    ts = _coerce_time(frame, ["funding_time_ns", "fundingTime", "funding_time", "time", "timestamp", "ts_ns"])
    if ts is None and "symbol" in frame.columns and pd.api.types.is_numeric_dtype(frame["symbol"]):
        # Some legacy converted funding caches carried the millisecond timestamp in the symbol column.
        ts = _timestamp_series(frame["symbol"])
    if ts is None or "funding_rate" not in frame.columns:
        return pd.DataFrame(), _source_state(funding_dir, False, lineage="missing_funding_columns")
    frame = frame.copy()
    frame["ts"] = ts
    frame["funding_rate"] = pd.to_numeric(frame["funding_rate"], errors="coerce")
    return (
        frame[["ts", "funding_rate"]].sort_values("ts").dropna(),
        _source_state(funding_dir, True, lineage="funding_cache", rows=len(frame)),
    )


def _load_liquidations(liquidation_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames = _read_parquet_frames(liquidation_dir)
    if not frames:
        return (
            pd.DataFrame(),
            {
                **_source_state(liquidation_dir, False, lineage="oi_funding_proxy_no_liquidation_cache"),
                "cache_present": False,
                "proxy_used": True,
            },
        )
    frame = pd.concat(frames, ignore_index=True)
    ts = _coerce_time(frame, ["bucket_end_ns", "close_time_ns", "ts_ns", "time", "timestamp"])
    if ts is None and "bucket_start_ns" in frame.columns:
        ts = _normalize_ts(
            pd.to_datetime(
                pd.to_numeric(frame["bucket_start_ns"], errors="coerce") + 4 * 3600 * 1_000_000_000,
                unit="ns",
                utc=True,
            )
        )
    required = {"long_liq_usd", "short_liq_usd"}
    if ts is None or not required.issubset(frame.columns):
        return (
            pd.DataFrame(),
            {
                **_source_state(liquidation_dir, False, lineage="oi_funding_proxy_liquidation_cache_unreadable"),
                "cache_present": True,
                "proxy_used": True,
            },
        )
    frame = frame.copy()
    frame["ts"] = ts
    frame["long_liq_usd"] = pd.to_numeric(frame["long_liq_usd"], errors="coerce").fillna(0.0)
    frame["short_liq_usd"] = pd.to_numeric(frame["short_liq_usd"], errors="coerce").fillna(0.0)
    if "total_liq_usd" in frame.columns:
        frame["total_liq_usd"] = pd.to_numeric(frame["total_liq_usd"], errors="coerce").fillna(0.0)
    else:
        frame["total_liq_usd"] = frame["long_liq_usd"] + frame["short_liq_usd"]
    return (
        frame[["ts", "long_liq_usd", "short_liq_usd", "total_liq_usd"]].sort_values("ts"),
        {
            **_source_state(liquidation_dir, True, lineage="liquidation_cache", rows=len(frame)),
            "cache_present": True,
            "proxy_used": False,
        },
    )


def _build_features(
    *,
    prices: pd.DataFrame,
    metrics: pd.DataFrame,
    open_interest: pd.DataFrame,
    funding: pd.DataFrame,
    liquidations: pd.DataFrame,
    liquidation_lineage: dict[str, Any],
) -> pd.DataFrame:
    aligned = prices.sort_values("ts").copy()
    if not metrics.empty:
        aligned = _asof_join(aligned, metrics, tolerance=pd.Timedelta(hours=8), suffix="metrics")
    if not open_interest.empty:
        aligned = _asof_join(aligned, open_interest, tolerance=pd.Timedelta(hours=8), suffix="oi")
    aligned = _asof_join(aligned, funding, tolerance=pd.Timedelta(hours=12), suffix="funding")
    if not liquidations.empty:
        aligned = _asof_join(aligned, liquidations, tolerance=pd.Timedelta(hours=8), suffix="liq")

    if "sum_open_interest" in aligned.columns:
        aligned["effective_open_interest"] = aligned["sum_open_interest"]
        oi_source = "binance_metrics_cache"
    else:
        aligned["effective_open_interest"] = np.nan
        oi_source = "missing"
    if "open_interest" in aligned.columns:
        aligned["effective_open_interest"] = aligned["effective_open_interest"].combine_first(aligned["open_interest"])
        oi_source = "open_interest_cache" if oi_source == "missing" else "binance_metrics_cache_with_open_interest_fill"
    aligned["effective_open_interest"] = pd.to_numeric(aligned["effective_open_interest"], errors="coerce")
    aligned["oi_growth_24h"] = aligned["effective_open_interest"].pct_change(6)
    aligned["oi_growth_24h_z"] = _rolling_z(aligned["oi_growth_24h"], 90)
    aligned["funding_z"] = _rolling_z(aligned["funding_rate"], 90)
    aligned["top_position_lsr_z"] = (
        _rolling_z(_safe_log(aligned["sum_toptrader_long_short_ratio"]), 90)
        if "sum_toptrader_long_short_ratio" in aligned.columns
        else 0.0
    )
    aligned["global_lsr_z"] = (
        _rolling_z(_safe_log(aligned["count_long_short_ratio"]), 90)
        if "count_long_short_ratio" in aligned.columns
        else 0.0
    )
    if "sum_taker_long_short_vol_ratio" in aligned.columns:
        aligned["taker_pressure_z"] = _rolling_z(_safe_log(aligned["sum_taker_long_short_vol_ratio"]), 90)
        taker_source = "binance_metrics_cache"
    else:
        aligned["taker_pressure_z"] = aligned["kline_taker_pressure_z"]
        taker_source = "kline_taker_buy_volume_proxy"

    if liquidation_lineage["proxy_used"]:
        aligned["long_liq_z"] = _factor_z(
            _positive(aligned["funding_z"])
            * _positive(aligned["oi_growth_24h_z"])
            * _positive(-aligned["price_return_24h_z"]),
            90,
        )
        aligned["short_liq_z"] = _factor_z(
            _positive(-aligned["funding_z"])
            * _positive(aligned["oi_growth_24h_z"])
            * _positive(aligned["price_return_24h_z"]),
            90,
        )
        aligned["total_liq_z"] = _factor_z(aligned["long_liq_z"].abs() + aligned["short_liq_z"].abs(), 90)
    else:
        aligned["long_liq_z"] = _factor_z(aligned["long_liq_usd"], 90)
        aligned["short_liq_z"] = _factor_z(aligned["short_liq_usd"], 90)
        aligned["total_liq_z"] = _factor_z(aligned["total_liq_usd"], 90)

    long_crowding = (
        _positive(aligned["funding_z"])
        + _positive(aligned["oi_growth_24h_z"])
        + _positive(aligned["top_position_lsr_z"])
        + _positive(aligned["global_lsr_z"])
        + _positive(aligned["long_liq_z"])
    ) / 5.0
    short_crowding = (
        _positive(-aligned["funding_z"])
        + _positive(aligned["oi_growth_24h_z"])
        + _positive(-aligned["top_position_lsr_z"])
        + _positive(-aligned["global_lsr_z"])
        + _positive(aligned["short_liq_z"])
    ) / 5.0
    aligned["crowded_long_unwind_risk"] = _factor_z(-long_crowding, 90)
    aligned["crowded_short_squeeze"] = _factor_z(short_crowding, 90)
    aligned["oi_continuation_support"] = _factor_z(
        aligned["price_return_24h_z"] * _positive(aligned["oi_growth_24h_z"]),
        90,
    )
    aligned["taker_oi_exhaustion"] = _factor_z(
        -aligned["taker_pressure_z"]
        * _positive(aligned["oi_growth_24h_z"])
        * (1.0 + _positive(aligned["realized_vol_24h_z"])),
        90,
    )
    aligned["oi_source"] = oi_source
    aligned["taker_source"] = taker_source
    aligned["liquidation_lineage"] = liquidation_lineage["lineage"]

    columns = [
        "ts",
        "forward_return_4h",
        "price_return_24h_z",
        "realized_vol_24h_z",
        "funding_z",
        "oi_growth_24h_z",
        "long_liq_z",
        "short_liq_z",
        "total_liq_z",
        "taker_pressure_z",
        "crowded_long_unwind_risk",
        "crowded_short_squeeze",
        "oi_continuation_support",
        "taker_oi_exhaustion",
        "oi_source",
        "taker_source",
        "liquidation_lineage",
    ]
    output = aligned[columns].copy()
    for column in output.columns:
        if column not in {"ts", "oi_source", "taker_source", "liquidation_lineage"}:
            output[column] = pd.to_numeric(output[column], errors="coerce").replace([math.inf, -math.inf], np.nan)
    return output


def _read_parquet_frames(root: Path, *, exclude_names: set[str] | None = None) -> list[pd.DataFrame]:
    exclude_names = exclude_names or set()
    if root.is_file() and root.suffix == ".parquet" and root.name not in exclude_names:
        paths = [root]
    elif root.exists():
        paths = [path for path in sorted(root.glob("**/*.parquet")) if path.name not in exclude_names]
    else:
        paths = []
    frames: list[pd.DataFrame] = []
    for path in paths:
        try:
            frames.append(pd.read_parquet(path))
        except Exception:
            continue
    return frames


def _coerce_time(frame: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    for column in candidates:
        if column not in frame.columns:
            continue
        if column.endswith("_ns"):
            return _normalize_ts(pd.to_datetime(pd.to_numeric(frame[column], errors="coerce"), unit="ns", utc=True))
        if pd.api.types.is_numeric_dtype(frame[column]):
            return _timestamp_series(frame[column])
        return _normalize_ts(pd.to_datetime(frame[column], utc=True, errors="coerce"))
    return None


def _timestamp_series(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    median = float(numeric.dropna().median()) if not numeric.dropna().empty else 0.0
    if median > 1e17:
        unit = "ns"
    elif median > 1e14:
        unit = "us"
    elif median > 1e11:
        unit = "ms"
    else:
        unit = "s"
    return _normalize_ts(pd.to_datetime(numeric, unit=unit, utc=True))


def _normalize_ts(series: pd.Series) -> pd.Series:
    return pd.Series(series, index=getattr(series, "index", None)).astype("datetime64[ns, UTC]")


def _asof_join(left: pd.DataFrame, right: pd.DataFrame, *, tolerance: pd.Timedelta, suffix: str) -> pd.DataFrame:
    right = right.sort_values("ts").drop_duplicates(subset=["ts"])
    return pd.merge_asof(
        left.sort_values("ts"),
        right,
        on="ts",
        direction="backward",
        tolerance=tolerance,
        suffixes=("", f"_{suffix}"),
    )


def _rolling_z(series: pd.Series | float, window: int) -> pd.Series:
    if not isinstance(series, pd.Series):
        return pd.Series(series)
    values = pd.to_numeric(series, errors="coerce").replace([math.inf, -math.inf], np.nan).astype(float)
    mean = values.rolling(window, min_periods=max(10, window // 3)).mean()
    std = values.rolling(window, min_periods=max(10, window // 3)).std()
    return ((values - mean) / std).replace([math.inf, -math.inf], np.nan)


def _factor_z(series: pd.Series, window: int) -> pd.Series:
    return _rolling_z(series, window).fillna(0.0)


def _safe_log(series: pd.Series) -> pd.Series:
    return pd.Series(np.log(pd.to_numeric(series, errors="coerce").clip(lower=1e-9)), index=series.index)


def _positive(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").clip(lower=0.0)


def _pairwise(frame: pd.DataFrame, names: list[str], *, method: str) -> dict[str, dict[str, float]]:
    matrix = {name: {} for name in names}
    for left in names:
        for right in names:
            if left == right:
                matrix[left][right] = 1.0
            elif method == "spearman":
                matrix[left][right] = round(_finite_corr(frame[left].rank(), frame[right].rank()), 4)
            else:
                matrix[left][right] = round(_finite_corr(frame[left], frame[right]), 4)
    return matrix


def _finite_corr(left: pd.Series, right: pd.Series) -> float:
    frame = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(frame) < 2 or frame["left"].nunique() < 2 or frame["right"].nunique() < 2:
        return 0.0
    value = float(frame["left"].corr(frame["right"]))
    return value if math.isfinite(value) else 0.0


def _rank_corr(left: pd.Series, right: pd.Series) -> float:
    frame = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(frame) < 10:
        return 0.0
    return _finite_corr(frame["left"].rank(), frame["right"].rank())


def _rolling_rank_ic(factor: pd.Series, forward_returns: pd.Series, *, window: int) -> list[float]:
    frame = pd.DataFrame({"factor": factor, "forward_return": forward_returns}).dropna()
    if len(frame) <= window:
        return []
    return [
        _rank_corr(frame["factor"].iloc[idx - window : idx], frame["forward_return"].iloc[idx - window : idx])
        for idx in range(window, len(frame) + 1)
    ]


def _signed_hit_rate(values: list[float], signal_sign: float) -> float:
    if not values:
        return 0.5
    return sum(1 for value in values if value * signal_sign > 0.0) / len(values)


def _residualize(factor: pd.Series, baseline: pd.DataFrame) -> pd.Series:
    frame = pd.concat([factor.rename("factor"), baseline], axis=1).dropna()
    if frame.empty:
        return pd.Series(dtype=float)
    y = frame["factor"].to_numpy(dtype=float)
    x = frame[[column for column in frame.columns if column != "factor"]].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    return pd.Series(y - x @ beta, index=frame.index)


def _apply_correlation_gate(
    audits: list[LiquidationUnwindFactorAudit],
    *,
    pairwise_pearson: dict[str, dict[str, float]],
    pairwise_spearman: dict[str, dict[str, float]],
    corr_threshold: float,
) -> list[LiquidationUnwindFactorAudit]:
    by_name = {audit.name: audit for audit in audits}
    blocked: set[str] = set()
    names = sorted(by_name)
    for idx, left in enumerate(names):
        for right in names[idx + 1 :]:
            corr = max(abs(pairwise_pearson[left][right]), abs(pairwise_spearman[left][right]))
            if corr < corr_threshold:
                continue
            weaker = right if _audit_strength(by_name[left]) >= _audit_strength(by_name[right]) else left
            blocked.add(weaker)
    updated = []
    for audit in audits:
        if audit.name in blocked and audit.blocked_by is None:
            updated.append(
                LiquidationUnwindFactorAudit(
                    name=audit.name,
                    hypothesis=audit.hypothesis,
                    observations=audit.observations,
                    rank_ic_mean=audit.rank_ic_mean,
                    rolling_rank_ic_mean=audit.rolling_rank_ic_mean,
                    rank_ic_stability=audit.rank_ic_stability,
                    hit_rate=audit.hit_rate,
                    ablation_delta=audit.ablation_delta,
                    max_abs_correlation=audit.max_abs_correlation,
                    max_abs_spearman=audit.max_abs_spearman,
                    signal_sign=audit.signal_sign,
                    blocked_by="high_factor_correlation",
                )
            )
        else:
            updated.append(audit)
    return updated


def _audit_strength(audit: LiquidationUnwindFactorAudit) -> tuple[float, float, float]:
    return (abs(audit.rolling_rank_ic_mean), abs(audit.ablation_delta), audit.rank_ic_stability)


def _audit_sort_key(audit: LiquidationUnwindFactorAudit) -> tuple[bool, float, float]:
    return (audit.blocked_by is None, abs(audit.rolling_rank_ic_mean), abs(audit.ablation_delta))


def _source_state(
    path: Path, available: bool, *, lineage: str | None = None, rows: int | None = None
) -> dict[str, Any]:
    return {
        "path": str(path),
        "available": available,
        "lineage": lineage or ("cache" if available else "missing"),
        "rows": rows,
    }


def _blocked_payload(
    *,
    train_end: str,
    blocked_by: str,
    corr_threshold: float,
    min_train_observations: int,
    min_abs_rank_ic: float,
    min_abs_ablation_delta: float,
    source_lineage: dict[str, Any],
    features_path: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "liquidation_unwind_factor_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "train_only": True,
        "train_end": train_end,
        "corr_threshold": corr_threshold,
        "min_train_observations": min_train_observations,
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_abs_ablation_delta": min_abs_ablation_delta,
        "features_path": features_path,
        "source_lineage": source_lineage,
        "summary": {
            "factor_count": 0,
            "admissible_factor_count": 0,
            "blocked_factor_count": 0,
            "high_correlation_blocked_count": 0,
            "liquidation_lineage": (source_lineage.get("liquidation") or {}).get("lineage"),
            "liquidation_cache_present": (source_lineage.get("liquidation") or {}).get("cache_present", False),
            "liquidation_proxy_used": (source_lineage.get("liquidation") or {}).get("proxy_used", False),
            "blocked_by": blocked_by,
        },
        "factors": [],
        "pairwise_correlation_matrix": {},
        "pairwise_spearman_matrix": {},
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Liquidation/Unwind Factor Audit",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- train_end: `{payload['train_end']}`",
        f"- train_only: `{payload['train_only']}`",
        f"- research_only_non_promotable: `{payload['research_only_non_promotable']}`",
        f"- features_path: `{payload['features_path']}`",
        f"- liquidation_lineage: `{payload['summary']['liquidation_lineage']}`",
        f"- liquidation_proxy_used: `{payload['summary']['liquidation_proxy_used']}`",
        f"- admissible_factor_count: `{payload['summary']['admissible_factor_count']}`",
        f"- blocked_by: `{payload['summary']['blocked_by']}`",
        "",
        "## Factors",
        "",
    ]
    for factor in payload["factors"]:
        lines.append(
            f"- {factor['name']}: rolling_rank_ic_mean=`{factor['rolling_rank_ic_mean']}`, "
            f"rank_ic_stability=`{factor['rank_ic_stability']}`, hit_rate=`{factor['hit_rate']}`, "
            f"ablation_delta=`{factor['ablation_delta']}`, max_abs_correlation=`{factor['max_abs_correlation']}`, "
            f"max_abs_spearman=`{factor['max_abs_spearman']}`, signal_sign=`{factor['signal_sign']}`, "
            f"blocked_by=`{factor['blocked_by']}`"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
