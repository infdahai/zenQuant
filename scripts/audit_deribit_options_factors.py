#!/usr/bin/env python3
"""Audit research-only Deribit options regime factors on the train split."""

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
class DeribitOptionsFactorAudit:
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
    parser = argparse.ArgumentParser(description="Audit research-only Deribit options regime factors")
    parser.add_argument("--options-dir", type=Path, default=Path("data/raw/deribit_options"))
    parser.add_argument("--btc-4h-dir", type=Path, default=Path("data/raw/binance/klines/BTCUSDT/4h"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/research_swarm/deribit_options_factor_audit"))
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--train-start", default="2020-01-01T00:00:00+00:00")
    parser.add_argument("--train-end", default="2023-12-31T23:59:59+00:00")
    parser.add_argument("--max-feature-age-hours", type=float, default=24.0)
    parser.add_argument("--rolling-window", type=int, default=120)
    parser.add_argument("--corr-threshold", type=float, default=0.75)
    parser.add_argument("--min-train-observations", type=int, default=500)
    parser.add_argument("--min-abs-rank-ic", type=float, default=0.01)
    parser.add_argument("--min-abs-ablation-delta", type=float, default=0.005)
    args = parser.parse_args()

    payload = build_deribit_options_factor_audit(
        options_dir=args.options_dir,
        btc_4h_dir=args.btc_4h_dir,
        currency=args.currency,
        train_start=args.train_start,
        train_end=args.train_end,
        max_feature_age_hours=args.max_feature_age_hours,
        rolling_window=args.rolling_window,
        corr_threshold=args.corr_threshold,
        min_train_observations=args.min_train_observations,
        min_abs_rank_ic=args.min_abs_rank_ic,
        min_abs_ablation_delta=args.min_abs_ablation_delta,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "deribit_options_factor_audit.json"
    md_path = args.output_dir / "deribit_options_factor_audit.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path), "summary": payload["summary"]}, indent=2))


def build_deribit_options_factor_audit(
    *,
    options_dir: Path,
    btc_4h_dir: Path,
    currency: str,
    train_start: str,
    train_end: str,
    max_feature_age_hours: float,
    rolling_window: int,
    corr_threshold: float,
    min_train_observations: int,
    min_abs_rank_ic: float,
    min_abs_ablation_delta: float,
) -> dict[str, Any]:
    options = _load_deribit_options_cache(options_dir, currency)
    prices = _load_btc_4h_prices(btc_4h_dir)
    if options.empty or prices.empty:
        return _blocked_payload(
            currency=currency,
            train_start=train_start,
            train_end=train_end,
            max_feature_age_hours=max_feature_age_hours,
            rolling_window=rolling_window,
            corr_threshold=corr_threshold,
            min_train_observations=min_train_observations,
            min_abs_rank_ic=min_abs_rank_ic,
            min_abs_ablation_delta=min_abs_ablation_delta,
            blocked_by="deribit_options_cache_or_btc_4h_missing",
        )

    features = _build_snapshot_features(options, rolling_window=rolling_window)
    features_path = options_dir / currency.upper() / "derived_options_regime_factors.parquet"
    if not features.empty:
        features_path.parent.mkdir(parents=True, exist_ok=True)
        features.to_parquet(features_path, index=False)
    if features.empty:
        return _blocked_payload(
            currency=currency,
            train_start=train_start,
            train_end=train_end,
            max_feature_age_hours=max_feature_age_hours,
            rolling_window=rolling_window,
            corr_threshold=corr_threshold,
            min_train_observations=min_train_observations,
            min_abs_rank_ic=min_abs_rank_ic,
            min_abs_ablation_delta=min_abs_ablation_delta,
            blocked_by="no_usable_deribit_options_snapshot",
            features_path=str(features_path),
        )

    aligned = _align_pit_train_features(
        features=features,
        prices=prices,
        train_start=train_start,
        train_end=train_end,
        max_feature_age_hours=max_feature_age_hours,
    )
    factor_names = [
        column
        for column in aligned.columns
        if column
        not in {
            "bar_close_ts",
            "option_snapshot_ts",
            "forward_return_4h",
            "price_return_24h_z",
            "realized_vol_24h_z",
        }
    ]
    if aligned.empty or not factor_names:
        return _blocked_payload(
            currency=currency,
            train_start=train_start,
            train_end=train_end,
            max_feature_age_hours=max_feature_age_hours,
            rolling_window=rolling_window,
            corr_threshold=corr_threshold,
            min_train_observations=min_train_observations,
            min_abs_rank_ic=min_abs_rank_ic,
            min_abs_ablation_delta=min_abs_ablation_delta,
            blocked_by="no_train_aligned_deribit_options_factor",
            features_path=str(features_path),
        )

    pairwise_pearson = _pairwise(aligned, factor_names, method="pearson")
    pairwise_spearman = _pairwise(aligned, factor_names, method="spearman")
    baseline = aligned[["price_return_24h_z", "realized_vol_24h_z"]]
    audits: list[DeribitOptionsFactorAudit] = []
    for name in factor_names:
        frame = aligned[[name, "forward_return_4h", "price_return_24h_z", "realized_vol_24h_z"]].dropna()
        factor_values = frame[name].astype(float)
        forward_returns = frame["forward_return_4h"].astype(float)
        rolling_ics = _rolling_rank_ic(factor_values, forward_returns, window=rolling_window)
        rank_ic = _rank_corr(factor_values, forward_returns)
        rolling_mean = float(np.mean(rolling_ics)) if rolling_ics else rank_ic
        rolling_std = float(np.std(rolling_ics)) if rolling_ics else 0.0
        stability = abs(rolling_mean) / rolling_std if rolling_std > 1e-9 else abs(rolling_mean)
        signal_sign = 1.0 if rolling_mean >= 0.0 else -1.0
        hit_rate = _signed_hit_rate(rolling_ics, signal_sign)
        residual_values = _residualize(factor_values, baseline.loc[frame.index])
        ablation_delta = _rank_corr(residual_values, forward_returns.loc[residual_values.index])
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
            DeribitOptionsFactorAudit(
                name=name,
                observations=len(factor_values),
                rank_ic_mean=round(float(rank_ic), 4),
                rolling_rank_ic_mean=round(float(rolling_mean), 4),
                rank_ic_stability=round(float(stability), 4),
                hit_rate=round(float(hit_rate), 4),
                ablation_delta=round(float(ablation_delta), 4),
                max_abs_correlation=round(float(max_abs_corr), 4),
                max_abs_spearman=round(float(max_abs_spearman), 4),
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
        "schema_version": "deribit_options_factor_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "pit_train_only": True,
        "currency": currency.upper(),
        "train_start": train_start,
        "train_end": train_end,
        "max_feature_age_hours": max_feature_age_hours,
        "rolling_window": rolling_window,
        "corr_threshold": corr_threshold,
        "min_train_observations": min_train_observations,
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_abs_ablation_delta": min_abs_ablation_delta,
        "features_path": str(features_path),
        "summary": {
            "snapshot_count": int(features["option_snapshot_ts"].nunique()),
            "train_aligned_observations": len(aligned),
            "factor_count": len(audits),
            "admissible_factor_count": len(admissible),
            "blocked_factor_count": len(audits) - len(admissible),
            "high_correlation_blocked_count": sum(
                1 for audit in audits if audit.blocked_by == "high_factor_correlation"
            ),
            "blocked_by": None if admissible else "no_admissible_deribit_options_factor",
        },
        "factors": [asdict(audit) for audit in sorted(audits, key=_audit_sort_key, reverse=True)],
        "pairwise_correlation_matrix": pairwise_pearson,
        "pairwise_spearman_matrix": pairwise_spearman,
    }


def _load_deribit_options_cache(options_dir: Path, currency: str) -> pd.DataFrame:
    currency_dir = options_dir / currency.upper()
    if not currency_dir.exists():
        return pd.DataFrame()
    frames = [pd.read_parquet(path) for path in sorted(currency_dir.glob("**/*.parquet"))]
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    required = {
        "snapshot_ts_ns",
        "expiry_ts_ms",
        "strike",
        "option_type",
        "underlying_price",
        "open_interest",
        "volume_usd",
        "mark_iv",
    }
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    frame = frame.copy()
    for column in (
        "snapshot_ts_ns",
        "expiry_ts_ms",
        "strike",
        "underlying_price",
        "open_interest",
        "volume_usd",
        "mark_iv",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["option_type"] = frame["option_type"].astype(str).str.lower()
    frame = frame.replace([math.inf, -math.inf], np.nan)
    return frame.dropna(subset=["snapshot_ts_ns", "expiry_ts_ms", "strike", "underlying_price", "mark_iv"])


def _load_btc_4h_prices(btc_4h_dir: Path) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in sorted(btc_4h_dir.glob("*.parquet"))]
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    ts_column = "close_time_ns" if "close_time_ns" in frame.columns else "ts_ns"
    if ts_column not in frame.columns or "close" not in frame.columns:
        return pd.DataFrame()
    price = frame[[ts_column, "close"]].copy()
    price["bar_close_ts"] = _timestamp_to_datetime(price[ts_column])
    price = price.sort_values("bar_close_ts").drop_duplicates(subset=["bar_close_ts"])
    price["close"] = pd.to_numeric(price["close"], errors="coerce")
    price["forward_return_4h"] = price["close"].pct_change().shift(-1)
    price["price_return_24h"] = price["close"].pct_change(6)
    price["realized_vol_24h"] = price["close"].pct_change().rolling(6, min_periods=4).std()
    price["price_return_24h_z"] = _rolling_z(price["price_return_24h"], 90)
    price["realized_vol_24h_z"] = _rolling_z(price["realized_vol_24h"], 90)
    return price[["bar_close_ts", "forward_return_4h", "price_return_24h_z", "realized_vol_24h_z"]].dropna()


def _build_snapshot_features(options: pd.DataFrame, *, rolling_window: int) -> pd.DataFrame:
    rows: list[dict[str, float | pd.Timestamp]] = []
    for snapshot_ts_ns, snapshot in options.groupby("snapshot_ts_ns", sort=True):
        features = _snapshot_to_features(snapshot)
        if not features:
            continue
        features["option_snapshot_ts"] = pd.to_datetime(int(snapshot_ts_ns), unit="ns", utc=True)
        rows.append(features)
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows).sort_values("option_snapshot_ts").reset_index(drop=True)
    for column in list(frame.columns):
        if column == "option_snapshot_ts":
            continue
        frame[column] = pd.to_numeric(frame[column], errors="coerce").replace([math.inf, -math.inf], np.nan)

    z_columns = {
        "iv_level_30d": "iv_level_30d_z",
        "iv_term_structure_slope": "iv_term_structure_slope_z",
        "put_call_moneyness_skew_proxy": "put_call_moneyness_skew_proxy_z",
        "oi_concentration": "oi_concentration_z",
    }
    for source, target in z_columns.items():
        if source in frame.columns:
            frame[target] = _rolling_z(frame[source], rolling_window)
    return frame


def _snapshot_to_features(snapshot: pd.DataFrame) -> dict[str, float]:
    snapshot = snapshot.copy()
    snapshot["dte"] = (snapshot["expiry_ts_ms"] / 1000 - snapshot["snapshot_ts_ns"] / 1_000_000_000) / 86_400
    spot = float(snapshot["underlying_price"].dropna().median() or 0.0)
    if spot <= 0:
        return {}

    atm_expiry = _pick_expiry(snapshot, target_days=30, min_days=7, max_days=90)
    near_expiry = _pick_expiry(snapshot, target_days=7, min_days=3, max_days=30)
    far_expiry = _pick_expiry(snapshot, target_days=90, min_days=45, max_days=365)
    if atm_expiry is None:
        return {}

    atm_iv = _weighted_atm_iv(snapshot[snapshot["expiry_ts_ms"] == atm_expiry], spot=spot, limit=20)
    skew = _put_call_skew(snapshot[snapshot["expiry_ts_ms"] == atm_expiry], spot=spot)
    near_iv = (
        _weighted_atm_iv(snapshot[snapshot["expiry_ts_ms"] == near_expiry], spot=spot, limit=10)
        if near_expiry
        else None
    )
    far_iv = (
        _weighted_atm_iv(snapshot[snapshot["expiry_ts_ms"] == far_expiry], spot=spot, limit=10) if far_expiry else None
    )
    term_slope = (far_iv - near_iv) / far_iv if near_iv is not None and far_iv is not None and far_iv > 1e-9 else None

    total_oi = float(snapshot["open_interest"].clip(lower=0.0).sum())
    call_oi = float(snapshot.loc[snapshot["option_type"] == "call", "open_interest"].clip(lower=0.0).sum())
    put_oi = float(snapshot.loc[snapshot["option_type"] == "put", "open_interest"].clip(lower=0.0).sum())
    total_volume = float(snapshot["volume_usd"].clip(lower=0.0).sum())
    call_volume = float(snapshot.loc[snapshot["option_type"] == "call", "volume_usd"].clip(lower=0.0).sum())
    put_volume = float(snapshot.loc[snapshot["option_type"] == "put", "volume_usd"].clip(lower=0.0).sum())
    top_oi = snapshot["open_interest"].clip(lower=0.0).sort_values(ascending=False).head(5).sum()

    return {
        "iv_level_30d": float(atm_iv) if atm_iv is not None else np.nan,
        "iv_term_structure_slope": float(term_slope) if term_slope is not None else np.nan,
        "put_call_moneyness_skew_proxy": float(skew) if skew is not None else np.nan,
        "oi_concentration": float(top_oi / total_oi) if total_oi > 0 else np.nan,
        "call_put_oi_imbalance": float((call_oi - put_oi) / total_oi) if total_oi > 0 else np.nan,
        "call_put_volume_imbalance": float((call_volume - put_volume) / total_volume) if total_volume > 0 else np.nan,
    }


def _pick_expiry(snapshot: pd.DataFrame, *, target_days: float, min_days: float, max_days: float) -> int | None:
    eligible = snapshot[(snapshot["dte"] >= min_days) & (snapshot["dte"] <= max_days)]
    if eligible.empty:
        return None
    expiry_dtes = eligible.groupby("expiry_ts_ms")["dte"].median()
    return int(min(expiry_dtes.index, key=lambda expiry: abs(float(expiry_dtes.loc[expiry]) - target_days)))


def _weighted_atm_iv(options: pd.DataFrame, *, spot: float, limit: int) -> float | None:
    if options.empty:
        return None
    options = options.copy()
    options["dist_to_atm"] = (options["strike"] - spot).abs()
    atm = options.nsmallest(limit, "dist_to_atm")
    valid = atm[(atm["open_interest"] > 0.0) & atm["mark_iv"].notna()]
    total_oi = float(valid["open_interest"].sum())
    if total_oi <= 0:
        return None
    return float((valid["mark_iv"] * valid["open_interest"]).sum() / total_oi)


def _put_call_skew(options: pd.DataFrame, *, spot: float) -> float | None:
    puts = options[options["option_type"] == "put"].copy()
    calls = options[options["option_type"] == "call"].copy()
    if puts.empty or calls.empty:
        return None
    puts["dist"] = (puts["strike"] - spot * 0.9).abs()
    calls["dist"] = (calls["strike"] - spot * 1.1).abs()
    put_iv = puts.nsmallest(1, "dist").iloc[0]["mark_iv"]
    call_iv = calls.nsmallest(1, "dist").iloc[0]["mark_iv"]
    if pd.isna(put_iv) or pd.isna(call_iv):
        return None
    return float(put_iv - call_iv)


def _align_pit_train_features(
    *,
    features: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
    max_feature_age_hours: float,
) -> pd.DataFrame:
    aligned = pd.merge_asof(
        prices.sort_values("bar_close_ts"),
        features.sort_values("option_snapshot_ts"),
        left_on="bar_close_ts",
        right_on="option_snapshot_ts",
        direction="backward",
        tolerance=pd.Timedelta(hours=max_feature_age_hours),
    )
    train_start_ts = _parse_utc(train_start)
    train_end_ts = _parse_utc(train_end)
    aligned = aligned[(aligned["bar_close_ts"] >= train_start_ts) & (aligned["bar_close_ts"] <= train_end_ts)]
    return aligned.dropna(subset=["option_snapshot_ts", "forward_return_4h"])


def _timestamp_to_datetime(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    max_value = float(numeric.max() or 0.0)
    if max_value >= 1_000_000_000_000_000_000:
        unit = "ns"
    elif max_value >= 1_000_000_000_000_000:
        unit = "us"
    elif max_value >= 1_000_000_000_000:
        unit = "ms"
    else:
        unit = "s"
    return pd.to_datetime(numeric, unit=unit, utc=True)


def _parse_utc(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(UTC)
    return timestamp.tz_convert(UTC)


def _rolling_z(series: pd.Series, window: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([math.inf, -math.inf], np.nan).astype(float)
    min_periods = min(max(10, window // 3), max(1, window))
    mean = values.rolling(window, min_periods=min_periods).mean()
    std = values.rolling(window, min_periods=min_periods).std()
    return ((values - mean) / std).replace([math.inf, -math.inf], np.nan)


def _pairwise(frame: pd.DataFrame, names: list[str], *, method: str) -> dict[str, dict[str, float]]:
    matrix = {name: {} for name in names}
    for left in names:
        for right in names:
            if left == right:
                matrix[left][right] = 1.0
                continue
            pair = frame[[left, right]].dropna()
            if len(pair) < 10:
                matrix[left][right] = 0.0
            elif method == "spearman":
                matrix[left][right] = round(float(pair[left].rank().corr(pair[right].rank())), 4)
            else:
                matrix[left][right] = round(float(pair[left].corr(pair[right])), 4)
    return matrix


def _rank_corr(left: pd.Series, right: pd.Series) -> float:
    frame = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(frame) < 10:
        return 0.0
    value = frame["left"].rank().corr(frame["right"].rank())
    return 0.0 if pd.isna(value) else float(value)


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
    audits: list[DeribitOptionsFactorAudit],
    *,
    pairwise_pearson: dict[str, dict[str, float]],
    pairwise_spearman: dict[str, dict[str, float]],
    corr_threshold: float,
) -> list[DeribitOptionsFactorAudit]:
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
                DeribitOptionsFactorAudit(
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


def _audit_strength(audit: DeribitOptionsFactorAudit) -> tuple[float, float, float]:
    return (abs(audit.rolling_rank_ic_mean), abs(audit.ablation_delta), audit.rank_ic_stability)


def _audit_sort_key(audit: DeribitOptionsFactorAudit) -> tuple[bool, float, float]:
    return (audit.blocked_by is None, abs(audit.rolling_rank_ic_mean), abs(audit.ablation_delta))


def _blocked_payload(
    *,
    currency: str,
    train_start: str,
    train_end: str,
    max_feature_age_hours: float,
    rolling_window: int,
    corr_threshold: float,
    min_train_observations: int,
    min_abs_rank_ic: float,
    min_abs_ablation_delta: float,
    blocked_by: str,
    features_path: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "deribit_options_factor_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "pit_train_only": True,
        "currency": currency.upper(),
        "train_start": train_start,
        "train_end": train_end,
        "max_feature_age_hours": max_feature_age_hours,
        "rolling_window": rolling_window,
        "corr_threshold": corr_threshold,
        "min_train_observations": min_train_observations,
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_abs_ablation_delta": min_abs_ablation_delta,
        "features_path": features_path,
        "summary": {
            "snapshot_count": 0,
            "train_aligned_observations": 0,
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
        "# Deribit Options Factor Audit",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- currency: `{payload['currency']}`",
        f"- train_start: `{payload['train_start']}`",
        f"- train_end: `{payload['train_end']}`",
        f"- research_only_non_promotable: `{payload['research_only_non_promotable']}`",
        f"- pit_train_only: `{payload['pit_train_only']}`",
        f"- features_path: `{payload['features_path']}`",
        f"- train_aligned_observations: `{payload['summary']['train_aligned_observations']}`",
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
