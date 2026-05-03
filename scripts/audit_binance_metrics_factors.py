#!/usr/bin/env python3
"""Build and audit Binance futures metrics/OI factors for BTC research."""

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
class BinanceMetricsFactorAudit:
    name: str
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
    parser = argparse.ArgumentParser(description="Audit research-only Binance metrics/OI factors")
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/raw/binance/metrics/BTCUSDT"))
    parser.add_argument("--btc-4h-dir", type=Path, default=Path("data/raw/binance/klines/BTCUSDT/4h"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/research_swarm/binance_metrics_factor_audit"))
    parser.add_argument("--train-end", default="2023-12-31T23:59:59+00:00")
    parser.add_argument("--corr-threshold", type=float, default=0.75)
    parser.add_argument("--min-abs-rank-ic", type=float, default=0.01)
    parser.add_argument("--min-abs-ablation-delta", type=float, default=0.005)
    args = parser.parse_args()

    payload = build_binance_metrics_factor_audit(
        metrics_dir=args.metrics_dir,
        btc_4h_dir=args.btc_4h_dir,
        train_end=args.train_end,
        corr_threshold=args.corr_threshold,
        min_abs_rank_ic=args.min_abs_rank_ic,
        min_abs_ablation_delta=args.min_abs_ablation_delta,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "binance_metrics_factor_audit.json"
    md_path = args.output_dir / "binance_metrics_factor_audit.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path), "summary": payload["summary"]}, indent=2))


def build_binance_metrics_factor_audit(
    *,
    metrics_dir: Path,
    btc_4h_dir: Path,
    train_end: str,
    corr_threshold: float,
    min_abs_rank_ic: float,
    min_abs_ablation_delta: float,
) -> dict[str, Any]:
    metrics = _load_metrics(metrics_dir)
    prices = _load_btc_4h_prices(btc_4h_dir)
    if metrics.empty or prices.empty:
        return _blocked_payload(
            train_end=train_end,
            blocked_by="binance_metrics_or_btc_4h_missing",
            corr_threshold=corr_threshold,
            min_abs_rank_ic=min_abs_rank_ic,
            min_abs_ablation_delta=min_abs_ablation_delta,
        )

    features = _build_features(metrics, prices)
    features_path = metrics_dir / "derived_metrics_factors.parquet"
    features.to_parquet(features_path, index=False)

    train_cutoff = pd.Timestamp(train_end)
    if train_cutoff.tzinfo is None:
        train_cutoff = train_cutoff.tz_localize(UTC)
    aligned = features[features["ts"] <= train_cutoff].copy().dropna()
    factor_names = [
        column
        for column in aligned.columns
        if column
        not in {
            "ts",
            "forward_return_4h",
            "price_return_24h_z",
            "realized_vol_24h_z",
        }
    ]
    if aligned.empty or not factor_names:
        return _blocked_payload(
            train_end=train_end,
            blocked_by="no_train_aligned_binance_metrics_factor",
            corr_threshold=corr_threshold,
            min_abs_rank_ic=min_abs_rank_ic,
            min_abs_ablation_delta=min_abs_ablation_delta,
            features_path=str(features_path),
        )

    pairwise_pearson = _pairwise(aligned, factor_names, method="pearson")
    pairwise_spearman = _pairwise(aligned, factor_names, method="spearman")
    baseline = aligned[["price_return_24h_z", "realized_vol_24h_z"]]
    audits: list[BinanceMetricsFactorAudit] = []
    for name in factor_names:
        factor_values = aligned[name].astype(float)
        forward_returns = aligned["forward_return_4h"].astype(float)
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
        if len(factor_values) < 500:
            blocked_by = "insufficient_train_observations"
        elif abs(rolling_mean) < min_abs_rank_ic:
            blocked_by = "weak_abs_rank_ic"
        elif abs(ablation_delta) < min_abs_ablation_delta:
            blocked_by = "weak_ablation_delta"
        audits.append(
            BinanceMetricsFactorAudit(
                name=name,
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
        "schema_version": "binance_metrics_factor_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "train_end": train_end,
        "corr_threshold": corr_threshold,
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_abs_ablation_delta": min_abs_ablation_delta,
        "features_path": str(features_path),
        "summary": {
            "factor_count": len(audits),
            "admissible_factor_count": len(admissible),
            "blocked_factor_count": len(audits) - len(admissible),
            "high_correlation_blocked_count": sum(
                1 for audit in audits if audit.blocked_by == "high_factor_correlation"
            ),
            "blocked_by": None if admissible else "no_admissible_binance_metrics_factor",
        },
        "factors": [asdict(audit) for audit in sorted(audits, key=_audit_sort_key, reverse=True)],
        "pairwise_correlation_matrix": pairwise_pearson,
        "pairwise_spearman_matrix": pairwise_spearman,
    }


def _load_metrics(metrics_dir: Path) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in sorted(metrics_dir.glob("20*.parquet"))]
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    frame["ts"] = pd.to_datetime(frame["create_time"], utc=True).astype("datetime64[ns, UTC]")
    numeric_columns = [
        "sum_open_interest",
        "sum_open_interest_value",
        "count_toptrader_long_short_ratio",
        "sum_toptrader_long_short_ratio",
        "count_long_short_ratio",
        "sum_taker_long_short_vol_ratio",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("ts").dropna(subset=numeric_columns)


def _load_btc_4h_prices(btc_4h_dir: Path) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in sorted(btc_4h_dir.glob("*.parquet"))]
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    ts_column = "close_time_ns" if "close_time_ns" in frame.columns else "ts_ns"
    price = frame[[ts_column, "close"]].copy()
    price["bar_close_ts"] = pd.to_datetime(price[ts_column], unit="ns", utc=True)
    price = price.sort_values("bar_close_ts").drop_duplicates(subset=["bar_close_ts"])
    price["close"] = pd.to_numeric(price["close"], errors="coerce")
    price["forward_return_4h"] = price["close"].pct_change().shift(-1)
    price["price_return_24h"] = price["close"].pct_change(6)
    price["realized_vol_24h"] = price["close"].pct_change().rolling(6, min_periods=4).std()
    price["price_return_24h_z"] = _rolling_z(price["price_return_24h"], 90)
    price["realized_vol_24h_z"] = _rolling_z(price["realized_vol_24h"], 90)
    return price[["bar_close_ts", "close", "forward_return_4h", "price_return_24h_z", "realized_vol_24h_z"]].dropna()


def _build_features(metrics: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    metrics = metrics.copy()
    metrics["bucket"] = metrics["ts"].dt.floor("4h")
    four_hour = metrics.sort_values("ts").groupby("bucket", as_index=False).tail(1).sort_values("ts")
    four_hour["oi_growth_24h"] = four_hour["sum_open_interest"].pct_change(6)
    four_hour["oi_value_growth_24h"] = four_hour["sum_open_interest_value"].pct_change(6)
    four_hour["oi_growth_24h_z"] = _rolling_z(four_hour["oi_growth_24h"], 90)
    four_hour["oi_value_growth_24h_z"] = _rolling_z(four_hour["oi_value_growth_24h"], 90)
    four_hour["global_account_lsr_z"] = _rolling_z(_safe_log(four_hour["count_long_short_ratio"]), 90)
    four_hour["top_trader_position_lsr_z"] = _rolling_z(_safe_log(four_hour["sum_toptrader_long_short_ratio"]), 90)
    four_hour["top_trader_account_lsr_z"] = _rolling_z(_safe_log(four_hour["count_toptrader_long_short_ratio"]), 90)
    four_hour["taker_pressure_z"] = _rolling_z(_safe_log(four_hour["sum_taker_long_short_vol_ratio"]), 90)

    aligned = pd.merge_asof(
        four_hour.sort_values("ts"),
        prices.sort_values("bar_close_ts"),
        left_on="ts",
        right_on="bar_close_ts",
        direction="forward",
        tolerance=pd.Timedelta(minutes=10),
    )
    aligned["oi_price_divergence_24h"] = aligned["oi_growth_24h_z"] * -aligned["price_return_24h_z"]
    aligned["crowding_unwind_pressure"] = aligned["top_trader_position_lsr_z"] * -aligned["price_return_24h_z"]
    aligned["leverage_crowding_composite"] = (
        aligned["oi_growth_24h_z"] + aligned["top_trader_position_lsr_z"] + aligned["global_account_lsr_z"]
    ) / 3.0
    aligned["oi_expansion_price_continuation"] = _rolling_z(
        aligned["oi_value_growth_24h_z"] * aligned["price_return_24h_z"], 90
    )
    aligned["top_account_global_divergence"] = _rolling_z(
        aligned["top_trader_account_lsr_z"] - aligned["global_account_lsr_z"], 90
    )
    aligned["taker_oi_exhaustion"] = _rolling_z(aligned["taker_pressure_z"] * aligned["oi_value_growth_24h_z"], 90)
    aligned["crowded_unwind_vol_adjusted"] = _rolling_z(
        aligned["crowding_unwind_pressure"] / (1.0 + aligned["realized_vol_24h_z"].abs()), 90
    )
    feature_columns = [
        "ts",
        "forward_return_4h",
        "price_return_24h_z",
        "realized_vol_24h_z",
        "oi_growth_24h_z",
        "oi_value_growth_24h_z",
        "global_account_lsr_z",
        "top_trader_position_lsr_z",
        "top_trader_account_lsr_z",
        "taker_pressure_z",
        "oi_price_divergence_24h",
        "crowding_unwind_pressure",
        "leverage_crowding_composite",
        "oi_expansion_price_continuation",
        "top_account_global_divergence",
        "taker_oi_exhaustion",
        "crowded_unwind_vol_adjusted",
    ]
    output = aligned[feature_columns].copy()
    for column in output.columns:
        if column != "ts":
            output[column] = pd.to_numeric(output[column], errors="coerce").replace([math.inf, -math.inf], pd.NA)
    return output.dropna()


def _rolling_z(series: pd.Series, window: int) -> pd.Series:
    series = pd.to_numeric(series, errors="coerce").replace([math.inf, -math.inf], np.nan).astype(float)
    mean = series.rolling(window, min_periods=max(10, window // 3)).mean()
    std = series.rolling(window, min_periods=max(10, window // 3)).std()
    return ((series - mean) / std).replace([math.inf, -math.inf], np.nan)


def _safe_log(series: pd.Series) -> pd.Series:
    return pd.Series(np.log(pd.to_numeric(series, errors="coerce").clip(lower=1e-9)), index=series.index)


def _pairwise(frame: pd.DataFrame, names: list[str], *, method: str) -> dict[str, dict[str, float]]:
    matrix = {name: {} for name in names}
    for left in names:
        for right in names:
            if left == right:
                matrix[left][right] = 1.0
            elif method == "spearman":
                matrix[left][right] = round(float(frame[left].rank().corr(frame[right].rank())), 4)
            else:
                matrix[left][right] = round(float(frame[left].corr(frame[right])), 4)
    return matrix


def _rank_corr(left: pd.Series, right: pd.Series) -> float:
    frame = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(frame) < 10:
        return 0.0
    return float(frame["left"].rank().corr(frame["right"].rank()))


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
    audits: list[BinanceMetricsFactorAudit],
    *,
    pairwise_pearson: dict[str, dict[str, float]],
    pairwise_spearman: dict[str, dict[str, float]],
    corr_threshold: float,
) -> list[BinanceMetricsFactorAudit]:
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
                BinanceMetricsFactorAudit(
                    name=audit.name,
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


def _audit_strength(audit: BinanceMetricsFactorAudit) -> tuple[float, float, float]:
    return (abs(audit.rolling_rank_ic_mean), abs(audit.ablation_delta), audit.rank_ic_stability)


def _audit_sort_key(audit: BinanceMetricsFactorAudit) -> tuple[bool, float, float]:
    return (audit.blocked_by is None, abs(audit.rolling_rank_ic_mean), abs(audit.ablation_delta))


def _blocked_payload(
    *,
    train_end: str,
    blocked_by: str,
    corr_threshold: float,
    min_abs_rank_ic: float,
    min_abs_ablation_delta: float,
    features_path: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "binance_metrics_factor_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "train_end": train_end,
        "corr_threshold": corr_threshold,
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_abs_ablation_delta": min_abs_ablation_delta,
        "features_path": features_path,
        "summary": {
            "factor_count": 0,
            "admissible_factor_count": 0,
            "blocked_factor_count": 0,
            "high_correlation_blocked_count": 0,
            "blocked_by": blocked_by,
        },
        "factors": [],
        "pairwise_correlation_matrix": {},
        "pairwise_spearman_matrix": {},
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Binance Metrics/OI Factor Audit",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- train_end: `{payload['train_end']}`",
        f"- research_only_non_promotable: `{payload['research_only_non_promotable']}`",
        f"- features_path: `{payload['features_path']}`",
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
