#!/usr/bin/env python3
"""Build and audit CoinMetrics community on-chain second-order BTC factors."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(slots=True, frozen=True)
class OnchainFactorAudit:
    name: str
    observations: int
    rank_ic_mean: float
    rank_ic_stability: float
    hit_rate: float
    max_abs_correlation: float
    blocked_by: str | None


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit research-only CoinMetrics on-chain factors")
    parser.add_argument(
        "--coinmetrics-path", type=Path, default=Path("data/raw/coinmetrics/community/BTC/data.parquet")
    )
    parser.add_argument("--btc-daily-path", type=Path, default=Path("data/raw/binance/klines/BTCUSDT/1d/data.parquet"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/research_swarm/onchain_factor_audit"))
    parser.add_argument("--train-end", default="2023-12-31")
    parser.add_argument("--corr-threshold", type=float, default=0.75)
    parser.add_argument("--min-rank-ic", type=float, default=0.01)
    args = parser.parse_args()

    result = build_coinmetrics_factor_audit(
        coinmetrics_path=args.coinmetrics_path,
        btc_daily_path=args.btc_daily_path,
        train_end=args.train_end,
        corr_threshold=args.corr_threshold,
        min_rank_ic=args.min_rank_ic,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "coinmetrics_onchain_factor_audit.json"
    md_path = args.output_dir / "coinmetrics_onchain_factor_audit.md"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path), "summary": result["summary"]}, indent=2))


def build_coinmetrics_factor_audit(
    *,
    coinmetrics_path: Path,
    btc_daily_path: Path,
    train_end: str,
    corr_threshold: float,
    min_rank_ic: float,
) -> dict[str, Any]:
    coinmetrics = _load_coinmetrics(coinmetrics_path)
    returns = _load_forward_returns(btc_daily_path)
    if coinmetrics.empty or returns.empty:
        blocked_by = "coinmetrics_or_btc_daily_missing"
        return {
            "schema_version": "coinmetrics_onchain_factor_audit.v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "research_only_non_promotable": True,
            "train_end": train_end,
            "min_rank_ic": min_rank_ic,
            "features_path": None,
            "summary": {
                "factor_count": 0,
                "admissible_factor_count": 0,
                "blocked_by": blocked_by,
            },
            "factors": [],
            "pairwise_correlation_matrix": {},
        }

    all_features = _build_features(coinmetrics)
    train_cutoff = pd.Timestamp(train_end, tz=UTC) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    features = all_features[all_features["date"] <= train_cutoff].copy()
    aligned = features.merge(returns, on="date", how="inner").dropna()
    factor_names = [name for name in aligned.columns if name not in {"date", "forward_return_1d"}]
    pairwise = _pairwise(aligned, factor_names)
    audits = []
    for name in factor_names:
        values = aligned[name].astype(float).tolist()
        forward_returns = aligned["forward_return_1d"].astype(float).tolist()
        rank_ic_mean, stability, hit_rate = _rolling_rank_ic(values, forward_returns)
        max_abs_corr = max((abs(pairwise[name][peer]) for peer in factor_names if peer != name), default=0.0)
        blocked_by = None
        if len(values) < 365:
            blocked_by = "insufficient_train_observations"
        elif max_abs_corr >= corr_threshold:
            blocked_by = "high_factor_correlation"
        elif rank_ic_mean < min_rank_ic:
            blocked_by = "weak_rank_ic"
        audits.append(
            OnchainFactorAudit(
                name=name,
                observations=len(values),
                rank_ic_mean=round(rank_ic_mean, 4),
                rank_ic_stability=round(stability, 4),
                hit_rate=round(hit_rate, 4),
                max_abs_correlation=round(max_abs_corr, 4),
                blocked_by=blocked_by,
            )
        )

    features_path = coinmetrics_path.parent / "derived_onchain_factors.parquet"
    all_features.to_parquet(features_path, index=False)
    admissible = [audit for audit in audits if audit.blocked_by is None]
    return {
        "schema_version": "coinmetrics_onchain_factor_audit.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only_non_promotable": True,
        "train_end": train_end,
        "min_rank_ic": min_rank_ic,
        "features_path": str(features_path),
        "summary": {
            "factor_count": len(audits),
            "admissible_factor_count": len(admissible),
            "blocked_factor_count": len(audits) - len(admissible),
            "blocked_by": None if admissible else "no_admissible_onchain_factor",
        },
        "factors": [asdict(audit) for audit in sorted(audits, key=lambda item: item.rank_ic_mean, reverse=True)],
        "pairwise_correlation_matrix": pairwise,
    }


def _load_coinmetrics(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    if "ts_ns" not in frame.columns:
        return pd.DataFrame()
    frame = frame.copy()
    frame["date"] = _timestamp_to_datetime(frame["ts_ns"]).dt.floor("D")
    for column in ("AdrActCnt", "TxCnt", "SplyCur"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    return frame[["date", "AdrActCnt", "TxCnt", "SplyCur"]].dropna(how="all", subset=["AdrActCnt", "TxCnt", "SplyCur"])


def _load_forward_returns(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "forward_return_1d"])
    frame = pd.read_parquet(path)
    ts_column = "close_time_ns" if "close_time_ns" in frame.columns else "ts_ns"
    daily = frame[[ts_column, "close"]].copy()
    daily["date"] = _timestamp_to_datetime(daily[ts_column]).dt.floor("D")
    daily = daily.groupby("date", as_index=False)["close"].last()
    daily["forward_return_1d"] = pd.to_numeric(daily["close"], errors="coerce").pct_change().shift(-1)
    return daily[["date", "forward_return_1d"]].dropna()


def _build_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame[["date"]].copy()
    active = pd.to_numeric(frame["AdrActCnt"], errors="coerce")
    tx = pd.to_numeric(frame["TxCnt"], errors="coerce")
    supply = pd.to_numeric(frame["SplyCur"], errors="coerce")
    output["active_address_momentum_14d"] = active.pct_change(14)
    output["tx_count_velocity_14d"] = tx.pct_change(14)
    output["supply_activity_regime"] = ((active / supply).replace([math.inf, -math.inf], pd.NA)).pct_change(30)
    for column in output.columns:
        if column == "date":
            continue
        output[column] = pd.to_numeric(output[column], errors="coerce").replace([math.inf, -math.inf], pd.NA)
    return output.dropna()


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


def _pairwise(frame: pd.DataFrame, names: list[str]) -> dict[str, dict[str, float]]:
    matrix = {name: {} for name in names}
    for left in names:
        for right in names:
            matrix[left][right] = 1.0 if left == right else round(float(frame[left].corr(frame[right])), 4)
    return matrix


def _rank_corr(left: list[float], right: list[float]) -> float:
    if len(left) < 5 or len(right) < 5:
        return 0.0
    series = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(series) < 5:
        return 0.0
    return float(series["left"].rank().corr(series["right"].rank()))


def _rolling_rank_ic(values: list[float], forward_returns: list[float], window: int = 60) -> tuple[float, float, float]:
    aligned = min(len(values), len(forward_returns))
    if aligned <= window:
        return 0.0, 0.0, 0.5
    ics = [
        _rank_corr(values[idx - window : idx], forward_returns[idx - window : idx]) for idx in range(window, aligned)
    ]
    if not ics:
        return 0.0, 0.0, 0.5
    mean_ic = sum(ics) / len(ics)
    std_ic = (sum((value - mean_ic) ** 2 for value in ics) / len(ics)) ** 0.5
    stability = abs(mean_ic) / std_ic if std_ic > 1e-9 else abs(mean_ic)
    hit_rate = sum(1 for value in ics if value > 0) / len(ics)
    return mean_ic, stability, hit_rate


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# CoinMetrics On-Chain Factor Audit",
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
            f"- {factor['name']}: rank_ic_mean=`{factor['rank_ic_mean']}`, "
            f"stability=`{factor['rank_ic_stability']}`, hit_rate=`{factor['hit_rate']}`, "
            f"max_abs_correlation=`{factor['max_abs_correlation']}`, blocked_by=`{factor['blocked_by']}`"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
