"""Train-only audit for large Deribit option-flow factors."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_TARGET_COLUMNS = {
    "forward_abs_return_4h",
    "forward_abs_return_1d",
    "trend_continuation_4h",
    "trend_continuation_1d",
}
_BASELINE_COLUMNS = {"price_return_24h_z", "realized_vol_24h_z"}


@dataclass(slots=True, frozen=True)
class DeribitLargeOptionFactorAudit:
    name: str
    target: str
    observations: int
    rank_ic_mean: float
    rolling_rank_ic_mean: float
    rank_ic_stability: float
    hit_rate: float
    ablation_delta: float
    max_abs_factor_correlation: float
    signal_sign: float
    blocked_by: str | None


def build_deribit_large_option_flow_audit(
    *,
    block_flow_root: Path,
    btc_4h_dir: Path,
    currency: str,
    train_start: str,
    train_end: str,
    min_amount: float,
    min_underlying_notional_usd: float | None,
    require_block_trade: bool,
    rolling_window: int,
    corr_threshold: float,
    min_train_observations: int,
    min_abs_rank_ic: float,
    min_abs_ablation_delta: float,
    feature_output_path: Path | None = None,
) -> dict[str, Any]:
    flow = load_deribit_block_flow_rows(block_flow_root, currency)
    prices = load_btc_4h_prices(btc_4h_dir)
    if flow.empty or prices.empty:
        return _blocked_payload(
            currency=currency,
            train_start=train_start,
            train_end=train_end,
            min_amount=min_amount,
            min_underlying_notional_usd=min_underlying_notional_usd,
            require_block_trade=require_block_trade,
            rolling_window=rolling_window,
            corr_threshold=corr_threshold,
            min_train_observations=min_train_observations,
            min_abs_rank_ic=min_abs_rank_ic,
            min_abs_ablation_delta=min_abs_ablation_delta,
            blocked_by="deribit_block_flow_or_btc_4h_missing",
            source_row_count=len(flow),
            price_bar_count=len(prices),
            features_path=str(feature_output_path) if feature_output_path else None,
        )

    features = build_large_option_flow_features(
        flow=flow,
        prices=prices,
        min_amount=min_amount,
        min_underlying_notional_usd=min_underlying_notional_usd,
        require_block_trade=require_block_trade,
        rolling_window=rolling_window,
    )
    if feature_output_path is not None and not features.empty:
        feature_output_path.parent.mkdir(parents=True, exist_ok=True)
        features_for_disk = features.copy()
        features_for_disk.attrs = {}
        features_for_disk.to_parquet(feature_output_path, index=False)

    aligned = _align_train_features(features=features, train_start=train_start, train_end=train_end)
    factor_names = _factor_names(aligned)
    if aligned.empty or not factor_names:
        return _blocked_payload(
            currency=currency,
            train_start=train_start,
            train_end=train_end,
            min_amount=min_amount,
            min_underlying_notional_usd=min_underlying_notional_usd,
            require_block_trade=require_block_trade,
            rolling_window=rolling_window,
            corr_threshold=corr_threshold,
            min_train_observations=min_train_observations,
            min_abs_rank_ic=min_abs_rank_ic,
            min_abs_ablation_delta=min_abs_ablation_delta,
            blocked_by="no_train_aligned_large_option_flow_factor",
            source_row_count=len(flow),
            selected_large_row_count=int(features.attrs.get("selected_large_row_count", 0)),
            price_bar_count=len(prices),
            feature_bar_count=len(features),
            features_path=str(feature_output_path) if feature_output_path else None,
            feature_coverage=_feature_coverage(features),
        )

    pairwise_spearman = _pairwise(aligned, factor_names, method="spearman")
    audits: list[DeribitLargeOptionFactorAudit] = []
    baseline = aligned[[column for column in _BASELINE_COLUMNS if column in aligned.columns]]
    for target in sorted(_TARGET_COLUMNS):
        for name in factor_names:
            frame = aligned[[name, target, *baseline.columns]].dropna()
            factor_values = frame[name].astype(float)
            target_values = frame[target].astype(float)
            rolling_ics = _rolling_rank_ic(factor_values, target_values, window=rolling_window)
            rank_ic = _rank_corr(factor_values, target_values)
            rolling_mean = float(np.mean(rolling_ics)) if rolling_ics else rank_ic
            rolling_std = float(np.std(rolling_ics)) if rolling_ics else 0.0
            stability = abs(rolling_mean) / rolling_std if rolling_std > 1e-9 else abs(rolling_mean)
            signal_sign = 1.0 if rolling_mean >= 0.0 else -1.0
            hit_rate = _signed_hit_rate(rolling_ics, signal_sign)
            residual_values = (
                _residualize(factor_values, baseline.loc[frame.index]) if not baseline.empty else factor_values
            )
            ablation_delta = _rank_corr(residual_values, target_values.loc[residual_values.index])
            max_abs_corr = max(
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
                DeribitLargeOptionFactorAudit(
                    name=name,
                    target=target,
                    observations=len(factor_values),
                    rank_ic_mean=round(float(rank_ic), 4),
                    rolling_rank_ic_mean=round(float(rolling_mean), 4),
                    rank_ic_stability=round(float(stability), 4),
                    hit_rate=round(float(hit_rate), 4),
                    ablation_delta=round(float(ablation_delta), 4),
                    max_abs_factor_correlation=round(float(max_abs_corr), 4),
                    signal_sign=signal_sign,
                    blocked_by=blocked_by,
                )
            )

    audits = _apply_factor_correlation_gate(audits, pairwise_spearman=pairwise_spearman, corr_threshold=corr_threshold)
    admissible = [audit for audit in audits if audit.blocked_by is None]
    blocked_by = None if admissible else "no_admissible_large_option_flow_factor"
    return {
        "schema_version": "deribit_large_option_flow_factor_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "pit_train_only": True,
        "currency": currency.upper(),
        "train_start": train_start,
        "train_end": train_end,
        "min_amount": min_amount,
        "min_underlying_notional_usd": min_underlying_notional_usd,
        "require_block_trade": require_block_trade,
        "rolling_window": rolling_window,
        "corr_threshold": corr_threshold,
        "min_train_observations": min_train_observations,
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_abs_ablation_delta": min_abs_ablation_delta,
        "features_path": str(feature_output_path) if feature_output_path else None,
        "feature_coverage": _feature_coverage(features),
        "summary": {
            "source_row_count": len(flow),
            "selected_large_row_count": int(features.attrs.get("selected_large_row_count", 0)),
            "price_bar_count": len(prices),
            "feature_bar_count": len(features),
            "train_aligned_observations": len(aligned),
            "factor_count": len(factor_names),
            "target_count": len(_TARGET_COLUMNS),
            "factor_target_audit_count": len(audits),
            "admissible_factor_target_count": len(admissible),
            "blocked_factor_target_count": len(audits) - len(admissible),
            "high_correlation_blocked_count": sum(
                1 for audit in audits if audit.blocked_by == "high_factor_correlation"
            ),
            "blocked_by": blocked_by,
            "next_action": _next_action(blocked_by),
        },
        "targets": sorted(_TARGET_COLUMNS),
        "factors": [asdict(audit) for audit in sorted(audits, key=_audit_sort_key, reverse=True)],
        "factor_spearman_matrix": pairwise_spearman,
    }


def load_deribit_block_flow_rows(root: Path, currency: str) -> pd.DataFrame:
    currency_root = root / currency.upper()
    if not currency_root.exists():
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for path in sorted(currency_root.glob("**/*.parquet")):
        if path.name == "data.parquet":
            frames.append(pd.read_parquet(path))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_btc_4h_prices(btc_4h_dir: Path) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in sorted(btc_4h_dir.glob("*.parquet"))]
    if not frames:
        frames = [_read_binance_csv(path) for path in sorted(btc_4h_dir.glob("*.csv"))]
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    ts_column = "close_time_ns" if "close_time_ns" in frame.columns else "ts_ns"
    if ts_column not in frame.columns:
        ts_column = "close_time" if "close_time" in frame.columns else ""
    if not ts_column or "close" not in frame.columns:
        return pd.DataFrame()
    prices = frame[[ts_column, "close"]].copy()
    prices["bar_close_ts"] = _timestamp_to_datetime(prices[ts_column])
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["bar_close_ts", "close"])
    prices = prices.sort_values("bar_close_ts").drop_duplicates(subset=["bar_close_ts"])
    prices["return_4h"] = prices["close"].pct_change()
    prices["price_return_24h"] = prices["close"].pct_change(6)
    prices["realized_vol_24h"] = prices["return_4h"].rolling(6, min_periods=4).std()
    prices["price_return_24h_z"] = _rolling_z(prices["price_return_24h"], 90)
    prices["realized_vol_24h_z"] = _rolling_z(prices["realized_vol_24h"], 90)
    prices["forward_return_4h"] = prices["close"].shift(-1) / prices["close"] - 1.0
    prices["forward_return_1d"] = prices["close"].shift(-6) / prices["close"] - 1.0
    prices["forward_abs_return_4h"] = prices["forward_return_4h"].abs()
    prices["forward_abs_return_1d"] = prices["forward_return_1d"].abs()
    trend_sign = np.sign(prices["price_return_24h"].fillna(0.0))
    prices["trend_continuation_4h"] = trend_sign * prices["forward_return_4h"]
    prices["trend_continuation_1d"] = trend_sign * prices["forward_return_1d"]
    columns = [
        "bar_close_ts",
        "close",
        "price_return_24h_z",
        "realized_vol_24h_z",
        "forward_abs_return_4h",
        "forward_abs_return_1d",
        "trend_continuation_4h",
        "trend_continuation_1d",
    ]
    return prices[columns].dropna(subset=["bar_close_ts", "close"])


def build_large_option_flow_features(
    *,
    flow: pd.DataFrame,
    prices: pd.DataFrame,
    min_amount: float,
    min_underlying_notional_usd: float | None,
    require_block_trade: bool,
    rolling_window: int,
) -> pd.DataFrame:
    flow = _clean_flow(flow)
    prices = prices.copy()
    if "bar_close_ts" in prices.columns:
        prices["bar_close_ts"] = _normalize_datetime_ns(prices["bar_close_ts"])
    if flow.empty or prices.empty:
        return pd.DataFrame()
    selected = flow[flow["amount_abs"] >= min_amount].copy()
    if require_block_trade:
        selected = selected[selected["is_block_trade"].astype(bool)]
    if min_underlying_notional_usd is not None:
        selected = selected[selected["underlying_notional_usd"] >= min_underlying_notional_usd]
    if selected.empty:
        features = _empty_feature_frame(prices, flow)
        features.attrs["selected_large_row_count"] = 0
        return features

    bars = prices[["bar_close_ts"]].sort_values("bar_close_ts").dropna().drop_duplicates()
    assigned = pd.merge_asof(
        selected.sort_values("flow_ts"),
        bars,
        left_on="flow_ts",
        right_on="bar_close_ts",
        direction="forward",
        tolerance=pd.Timedelta(hours=4, minutes=5),
    ).dropna(subset=["bar_close_ts"])
    if assigned.empty:
        features = _empty_feature_frame(prices, flow)
        features.attrs["selected_large_row_count"] = len(selected)
        return features

    grouped = assigned.groupby("bar_close_ts", sort=True).apply(_bar_features, include_groups=False).reset_index()
    coverage_segments = _coverage_segments(flow)
    feature_bars = _feature_bars_for_segments(bars, coverage_segments)
    features = feature_bars.merge(grouped, on="bar_close_ts", how="left")
    feature_columns = [column for column in features.columns if column != "bar_close_ts"]
    features[feature_columns] = features[feature_columns].fillna(0.0)
    features = _add_flow_ratios(features)
    features = _add_rolling_flow_features(features, window=6)
    features = _add_factor_zscores(features, rolling_window=rolling_window)
    features = features.merge(prices, on="bar_close_ts", how="left")
    features.attrs["selected_large_row_count"] = len(selected)
    features.attrs["coverage_segments"] = coverage_segments
    return features


def build_deribit_large_option_flow_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Deribit Large Option Flow Factor Audit",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- currency: `{payload['currency']}`",
        f"- train_start: `{payload['train_start']}`",
        f"- train_end: `{payload['train_end']}`",
        f"- research_only_non_promotable: `{payload['research_only_non_promotable']}`",
        f"- pit_train_only: `{payload['pit_train_only']}`",
        f"- min_amount: `{payload['min_amount']}`",
        f"- min_underlying_notional_usd: `{payload['min_underlying_notional_usd']}`",
        f"- require_block_trade: `{payload['require_block_trade']}`",
        f"- features_path: `{payload['features_path']}`",
        f"- source_row_count: `{summary['source_row_count']}`",
        f"- selected_large_row_count: `{summary['selected_large_row_count']}`",
        f"- train_aligned_observations: `{summary['train_aligned_observations']}`",
        f"- admissible_factor_target_count: `{summary['admissible_factor_target_count']}`",
        f"- blocked_by: `{summary['blocked_by']}`",
        f"- next_action: `{summary['next_action']}`",
        "",
        "## Feature Coverage",
        "",
    ]
    for key, value in payload.get("feature_coverage", {}).items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Top Factor-Target Audits", ""])
    for factor in payload["factors"][:20]:
        lines.append(
            f"- {factor['name']} -> {factor['target']}: rolling_rank_ic_mean=`{factor['rolling_rank_ic_mean']}`, "
            f"hit_rate=`{factor['hit_rate']}`, ablation_delta=`{factor['ablation_delta']}`, "
            f"max_abs_factor_correlation=`{factor['max_abs_factor_correlation']}`, "
            f"blocked_by=`{factor['blocked_by']}`"
        )
    return "\n".join(lines) + "\n"


def _clean_flow(flow: pd.DataFrame) -> pd.DataFrame:
    required = {"ts_ns", "instrument_name", "option_type", "amount", "price", "index_price", "expiry_ts_ms"}
    if not required.issubset(flow.columns):
        return pd.DataFrame()
    frame = flow.copy()
    for column in ("ts_ns", "expiry_ts_ms", "strike", "amount", "price", "index_price", "mark_price", "iv"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["flow_ts"] = pd.to_datetime(frame["ts_ns"], unit="ns", utc=True)
    frame["expiry_ts"] = pd.to_datetime(frame["expiry_ts_ms"], unit="ms", utc=True)
    frame["amount_abs"] = frame["amount"].abs()
    frame["option_side"] = (
        frame["option_type"].astype(str).str.lower().map({"c": "call", "call": "call", "p": "put", "put": "put"})
    )
    frame["direction_sign"] = (
        frame.get("direction", "").astype(str).str.lower().map({"buy": 1.0, "sell": -1.0}).fillna(0.0)
    )
    frame["is_block_trade"] = frame.get("is_block_trade", False).fillna(False).astype(bool)
    frame["is_combo_leg"] = frame.get("is_combo_leg", False).fillna(False).astype(bool)
    frame["leg_count"] = pd.to_numeric(frame.get("leg_count", 1), errors="coerce").fillna(1.0)
    frame["underlying_notional_usd"] = frame["amount_abs"] * frame["index_price"]
    frame["premium_usd"] = frame["amount_abs"] * frame["price"] * frame["index_price"]
    frame["dte"] = (frame["expiry_ts"] - frame["flow_ts"]).dt.total_seconds() / 86_400
    frame = frame.replace([math.inf, -math.inf], np.nan)
    return frame.dropna(subset=["flow_ts", "amount_abs", "option_side", "index_price"])


def _empty_feature_frame(prices: pd.DataFrame, flow: pd.DataFrame) -> pd.DataFrame:
    if flow.empty or "flow_ts" not in flow.columns:
        return pd.DataFrame()
    bars = prices[["bar_close_ts"]].sort_values("bar_close_ts")
    coverage_segments = _coverage_segments(flow)
    bars = _feature_bars_for_segments(bars, coverage_segments)
    if bars.empty:
        return pd.DataFrame()
    bars["large_row_count_4h"] = 0.0
    bars["large_contracts_4h"] = 0.0
    bars["large_premium_usd_4h"] = 0.0
    bars["large_underlying_notional_usd_4h"] = 0.0
    features = bars.merge(prices, on="bar_close_ts", how="left")
    features.attrs["coverage_segments"] = coverage_segments
    return features


def _bar_features(group: pd.DataFrame) -> pd.Series:
    call = group["option_side"] == "call"
    put = group["option_side"] == "put"
    near = group["dte"] <= 14.0
    far = group["dte"] >= 60.0
    amount = group["amount_abs"].astype(float)
    premium = group["premium_usd"].fillna(0.0).astype(float)
    notional = group["underlying_notional_usd"].fillna(0.0).astype(float)
    signed_amount = group["direction_sign"].astype(float) * amount
    signed_premium = group["direction_sign"].astype(float) * premium
    return pd.Series(
        {
            "large_row_count_4h": float(len(group)),
            "large_contracts_4h": float(amount.sum()),
            "large_premium_usd_4h": float(premium.sum()),
            "large_underlying_notional_usd_4h": float(notional.sum()),
            "large_call_contracts_4h": float(amount[call].sum()),
            "large_put_contracts_4h": float(amount[put].sum()),
            "large_call_premium_usd_4h": float(premium[call].sum()),
            "large_put_premium_usd_4h": float(premium[put].sum()),
            "large_block_rows_4h": float(group["is_block_trade"].sum()),
            "large_combo_rows_4h": float(group["is_combo_leg"].sum()),
            "large_near_contracts_4h": float(amount[near].sum()),
            "large_far_contracts_4h": float(amount[far].sum()),
            "large_signed_contracts_4h": float(signed_amount.sum()),
            "large_signed_premium_usd_4h": float(signed_premium.sum()),
            "large_avg_iv_4h": _weighted_average(group["iv"], amount),
            "large_call_avg_iv_4h": _weighted_average(group.loc[call, "iv"], amount[call]),
            "large_put_avg_iv_4h": _weighted_average(group.loc[put, "iv"], amount[put]),
        }
    )


def _add_flow_ratios(features: pd.DataFrame) -> pd.DataFrame:
    features = features.copy()
    features["large_call_put_contract_imbalance_4h"] = _safe_ratio(
        features["large_call_contracts_4h"] - features["large_put_contracts_4h"],
        features["large_call_contracts_4h"] + features["large_put_contracts_4h"],
    )
    features["large_call_put_premium_imbalance_4h"] = _safe_ratio(
        features["large_call_premium_usd_4h"] - features["large_put_premium_usd_4h"],
        features["large_call_premium_usd_4h"] + features["large_put_premium_usd_4h"],
    )
    features["large_block_row_share_4h"] = _safe_ratio(features["large_block_rows_4h"], features["large_row_count_4h"])
    features["large_combo_row_share_4h"] = _safe_ratio(features["large_combo_rows_4h"], features["large_row_count_4h"])
    features["large_near_contract_share_4h"] = _safe_ratio(
        features["large_near_contracts_4h"], features["large_contracts_4h"]
    )
    features["large_far_contract_share_4h"] = _safe_ratio(
        features["large_far_contracts_4h"], features["large_contracts_4h"]
    )
    features["large_signed_contract_pressure_4h"] = _safe_ratio(
        features["large_signed_contracts_4h"], features["large_contracts_4h"]
    )
    features["large_signed_premium_pressure_4h"] = _safe_ratio(
        features["large_signed_premium_usd_4h"], features["large_premium_usd_4h"]
    )
    features["large_call_put_iv_spread_4h"] = (
        features["large_call_avg_iv_4h"].replace(0.0, np.nan) - features["large_put_avg_iv_4h"].replace(0.0, np.nan)
    ).fillna(0.0)
    return features


def _add_rolling_flow_features(features: pd.DataFrame, *, window: int) -> pd.DataFrame:
    features = features.copy()
    sums = [
        "large_row_count",
        "large_contracts",
        "large_premium_usd",
        "large_underlying_notional_usd",
        "large_call_contracts",
        "large_put_contracts",
        "large_call_premium_usd",
        "large_put_premium_usd",
        "large_block_rows",
        "large_combo_rows",
        "large_near_contracts",
        "large_far_contracts",
        "large_signed_contracts",
        "large_signed_premium_usd",
    ]
    for base in sums:
        source = f"{base}_4h"
        if source in features.columns:
            features[f"{base}_24h"] = features[source].rolling(window, min_periods=1).sum()
    features["large_call_put_contract_imbalance_24h"] = _safe_ratio(
        features["large_call_contracts_24h"] - features["large_put_contracts_24h"],
        features["large_call_contracts_24h"] + features["large_put_contracts_24h"],
    )
    features["large_call_put_premium_imbalance_24h"] = _safe_ratio(
        features["large_call_premium_usd_24h"] - features["large_put_premium_usd_24h"],
        features["large_call_premium_usd_24h"] + features["large_put_premium_usd_24h"],
    )
    features["large_block_row_share_24h"] = _safe_ratio(
        features["large_block_rows_24h"], features["large_row_count_24h"]
    )
    features["large_combo_row_share_24h"] = _safe_ratio(
        features["large_combo_rows_24h"], features["large_row_count_24h"]
    )
    features["large_near_contract_share_24h"] = _safe_ratio(
        features["large_near_contracts_24h"], features["large_contracts_24h"]
    )
    features["large_far_contract_share_24h"] = _safe_ratio(
        features["large_far_contracts_24h"], features["large_contracts_24h"]
    )
    features["large_signed_contract_pressure_24h"] = _safe_ratio(
        features["large_signed_contracts_24h"], features["large_contracts_24h"]
    )
    features["large_signed_premium_pressure_24h"] = _safe_ratio(
        features["large_signed_premium_usd_24h"], features["large_premium_usd_24h"]
    )
    return features


def _add_factor_zscores(features: pd.DataFrame, *, rolling_window: int) -> pd.DataFrame:
    features = features.copy()
    for column in list(features.columns):
        if not column.startswith("large_"):
            continue
        if column.endswith("_z"):
            continue
        features[f"{column}_z"] = _rolling_z(features[column], rolling_window).fillna(0.0)
    return features


def _align_train_features(*, features: pd.DataFrame, train_start: str, train_end: str) -> pd.DataFrame:
    if features.empty:
        return pd.DataFrame()
    features = features.copy()
    features["bar_close_ts"] = _normalize_datetime_ns(features["bar_close_ts"])
    train_start_ts = _parse_utc(train_start)
    train_end_ts = _parse_utc(train_end)
    aligned = features[(features["bar_close_ts"] >= train_start_ts) & (features["bar_close_ts"] <= train_end_ts)]
    required = sorted(_TARGET_COLUMNS | _BASELINE_COLUMNS)
    return aligned.dropna(subset=[column for column in required if column in aligned.columns])


def _factor_names(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    excluded = {"bar_close_ts", "close"} | _TARGET_COLUMNS | _BASELINE_COLUMNS
    return [column for column in frame.columns if column.startswith("large_") and column not in excluded]


def _feature_coverage(features: pd.DataFrame) -> dict[str, Any]:
    if features.empty or "bar_close_ts" not in features.columns:
        return {"feature_start": None, "feature_end": None, "feature_bar_count": 0, "coverage_segment_count": 0}
    segments = features.attrs.get("coverage_segments") or []
    return {
        "feature_start": features["bar_close_ts"].min().isoformat(),
        "feature_end": features["bar_close_ts"].max().isoformat(),
        "feature_bar_count": len(features),
        "coverage_segment_count": len(segments),
        "coverage_segments": [{"start": start.isoformat(), "end": end.isoformat()} for start, end in segments[:20]],
    }


def _coverage_segments(flow: pd.DataFrame, *, max_gap_hours: float = 24.0) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if flow.empty or "flow_ts" not in flow.columns:
        return []
    times = flow["flow_ts"].dropna().sort_values().reset_index(drop=True)
    if times.empty:
        return []
    max_gap = pd.Timedelta(hours=max_gap_hours)
    segments: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    start = times.iloc[0]
    previous = times.iloc[0]
    for timestamp in times.iloc[1:]:
        if timestamp - previous > max_gap:
            segments.append((start.floor("4h"), previous.ceil("4h")))
            start = timestamp
        previous = timestamp
    segments.append((start.floor("4h"), previous.ceil("4h")))
    return segments


def _feature_bars_for_segments(
    bars: pd.DataFrame,
    segments: list[tuple[pd.Timestamp, pd.Timestamp]],
) -> pd.DataFrame:
    if not segments:
        return bars.iloc[0:0].copy()
    frames = [bars[(bars["bar_close_ts"] >= start) & (bars["bar_close_ts"] <= end)] for start, end in segments]
    if not frames:
        return bars.iloc[0:0].copy()
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["bar_close_ts"]).sort_values("bar_close_ts")


def _read_binance_csv(path: Path) -> pd.DataFrame:
    columns = [
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
    ]
    return pd.read_csv(path, names=columns, header=None)


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


def _normalize_datetime_ns(values: pd.Series) -> pd.Series:
    timestamps = pd.to_datetime(values, utc=True)
    return timestamps.astype("datetime64[ns, UTC]")


def _rolling_z(series: pd.Series, window: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([math.inf, -math.inf], np.nan).astype(float)
    min_periods = min(max(10, window // 3), max(1, window))
    mean = values.rolling(window, min_periods=min_periods).mean()
    std = values.rolling(window, min_periods=min_periods).std()
    return ((values - mean) / std).replace([math.inf, -math.inf], np.nan)


def _weighted_average(values: pd.Series, weights: pd.Series) -> float:
    frame = pd.DataFrame({"value": values, "weight": weights}).dropna()
    frame = frame[frame["weight"] > 0.0]
    if frame.empty:
        return 0.0
    return float((frame["value"] * frame["weight"]).sum() / frame["weight"].sum())


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0.0, np.nan)
    return (numerator / denominator).replace([math.inf, -math.inf], np.nan).fillna(0.0)


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


def _rolling_rank_ic(factor: pd.Series, target: pd.Series, *, window: int) -> list[float]:
    frame = pd.DataFrame({"factor": factor, "target": target}).dropna()
    if len(frame) <= window:
        return []
    return [
        _rank_corr(frame["factor"].iloc[idx - window : idx], frame["target"].iloc[idx - window : idx])
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


def _apply_factor_correlation_gate(
    audits: list[DeribitLargeOptionFactorAudit],
    *,
    pairwise_spearman: dict[str, dict[str, float]],
    corr_threshold: float,
) -> list[DeribitLargeOptionFactorAudit]:
    by_key = {(audit.name, audit.target): audit for audit in audits}
    blocked: set[tuple[str, str]] = set()
    names = sorted({audit.name for audit in audits})
    targets = sorted({audit.target for audit in audits})
    for target in targets:
        for idx, left in enumerate(names):
            for right in names[idx + 1 :]:
                corr = abs(pairwise_spearman[left][right])
                if corr < corr_threshold:
                    continue
                left_key = (left, target)
                right_key = (right, target)
                weaker = (
                    right_key if _audit_strength(by_key[left_key]) >= _audit_strength(by_key[right_key]) else left_key
                )
                blocked.add(weaker)

    updated = []
    for audit in audits:
        if (audit.name, audit.target) in blocked and audit.blocked_by is None:
            updated.append(
                DeribitLargeOptionFactorAudit(
                    name=audit.name,
                    target=audit.target,
                    observations=audit.observations,
                    rank_ic_mean=audit.rank_ic_mean,
                    rolling_rank_ic_mean=audit.rolling_rank_ic_mean,
                    rank_ic_stability=audit.rank_ic_stability,
                    hit_rate=audit.hit_rate,
                    ablation_delta=audit.ablation_delta,
                    max_abs_factor_correlation=audit.max_abs_factor_correlation,
                    signal_sign=audit.signal_sign,
                    blocked_by="high_factor_correlation",
                )
            )
        else:
            updated.append(audit)
    return updated


def _audit_strength(audit: DeribitLargeOptionFactorAudit) -> tuple[float, float, float]:
    return (abs(audit.rolling_rank_ic_mean), abs(audit.ablation_delta), audit.rank_ic_stability)


def _audit_sort_key(audit: DeribitLargeOptionFactorAudit) -> tuple[bool, float, float]:
    return (audit.blocked_by is None, abs(audit.rolling_rank_ic_mean), abs(audit.ablation_delta))


def _blocked_payload(
    *,
    currency: str,
    train_start: str,
    train_end: str,
    min_amount: float,
    min_underlying_notional_usd: float | None,
    require_block_trade: bool,
    rolling_window: int,
    corr_threshold: float,
    min_train_observations: int,
    min_abs_rank_ic: float,
    min_abs_ablation_delta: float,
    blocked_by: str,
    source_row_count: int,
    price_bar_count: int,
    selected_large_row_count: int = 0,
    feature_bar_count: int = 0,
    features_path: str | None = None,
    feature_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "deribit_large_option_flow_factor_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "pit_train_only": True,
        "currency": currency.upper(),
        "train_start": train_start,
        "train_end": train_end,
        "min_amount": min_amount,
        "min_underlying_notional_usd": min_underlying_notional_usd,
        "require_block_trade": require_block_trade,
        "rolling_window": rolling_window,
        "corr_threshold": corr_threshold,
        "min_train_observations": min_train_observations,
        "min_abs_rank_ic": min_abs_rank_ic,
        "min_abs_ablation_delta": min_abs_ablation_delta,
        "features_path": features_path,
        "feature_coverage": feature_coverage or {"feature_start": None, "feature_end": None, "feature_bar_count": 0},
        "summary": {
            "source_row_count": source_row_count,
            "selected_large_row_count": selected_large_row_count,
            "price_bar_count": price_bar_count,
            "feature_bar_count": feature_bar_count,
            "train_aligned_observations": 0,
            "factor_count": 0,
            "target_count": len(_TARGET_COLUMNS),
            "factor_target_audit_count": 0,
            "admissible_factor_target_count": 0,
            "blocked_factor_target_count": 0,
            "high_correlation_blocked_count": 0,
            "blocked_by": blocked_by,
            "next_action": _next_action(blocked_by),
        },
        "targets": sorted(_TARGET_COLUMNS),
        "factors": [],
        "factor_spearman_matrix": {},
    }


def _next_action(blocked_by: str | None) -> str:
    if blocked_by is None:
        return "promote_to_research_candidate_review_not_strategy_wiring"
    if blocked_by in {"deribit_block_flow_or_btc_4h_missing", "no_train_aligned_large_option_flow_factor"}:
        return "backfill_deribit_block_flow_before_factor_admission"
    if blocked_by == "no_admissible_large_option_flow_factor":
        return "keep_collecting_and_retest_before_strategy_wiring"
    return "investigate_large_option_flow_audit_blocker"
