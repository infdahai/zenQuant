"""Research-only source-level independence audit helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from zenquant.historical import read_parquet_rows


@dataclass(slots=True, frozen=True)
class SourceIndependenceAudit:
    generated_at: str
    source_name: str
    symbol: str
    role: str
    time_granularity: str
    observations: int
    pairwise_correlations: dict[str, float]
    max_abs_correlation: float
    rolling_rank_ic_mean: float
    rolling_rank_ic_stability: float
    ablation_ready: bool
    ablation_delta: float | None
    allowed_in_state_features: bool
    use_for_research_only: bool
    blocked_by: str | None
    next_action: str


def _pearson_corr(left: list[float], right: list[float]) -> float:
    if len(left) < 5 or len(right) < 5:
        return 0.0
    series = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(series) < 5:
        return 0.0
    return float(series["left"].corr(series["right"]))


def _rank_corr(left: list[float], right: list[float]) -> float:
    if len(left) < 5 or len(right) < 5:
        return 0.0
    series = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(series) < 5:
        return 0.0
    return float(series["left"].rank().corr(series["right"].rank()))


def _rolling_rank_ic(values: list[float], forward_returns: list[float], *, window: int = 60) -> tuple[float, float]:
    aligned = min(len(values), len(forward_returns))
    if aligned <= window:
        return 0.0, 0.0
    ics: list[float] = []
    for idx in range(window, aligned + 1):
        corr = _rank_corr(values[idx - window : idx], forward_returns[idx - window : idx])
        ics.append(corr)
    if not ics:
        return 0.0, 0.0
    mean_ic = sum(ics) / len(ics)
    std_ic = (sum((value - mean_ic) ** 2 for value in ics) / len(ics)) ** 0.5
    stability = abs(mean_ic) / std_ic if std_ic > 1e-9 else abs(mean_ic)
    return round(mean_ic, 4), round(stability, 4)


def _load_daily_series(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "value"])
    frame = pd.read_parquet(path)
    if "ts_ns" not in frame.columns or "value" not in frame.columns:
        return pd.DataFrame(columns=["date", "value"])
    frame = frame[["ts_ns", "value"]].copy()
    frame["date"] = pd.to_datetime(frame["ts_ns"], unit="ns", utc=True).dt.floor("D")
    return frame.groupby("date", as_index=False)["value"].last()


def _load_btc_daily_returns(data_root: Path, symbol: str) -> pd.DataFrame:
    candidates = [
        data_root / "klines" / symbol / "1d" / "data.parquet",
        Path("data/raw/binance_spot") / "klines" / symbol / "1d" / "data.parquet",
    ]
    for path in candidates:
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        ts_column = "close_time_ns" if "close_time_ns" in frame.columns else "ts_ns"
        close_column = "close" if "close" in frame.columns else "value"
        daily = frame[[ts_column, close_column]].copy()
        daily["date"] = pd.to_datetime(daily[ts_column], unit="ns", utc=True).dt.floor("D")
        daily = daily.groupby("date", as_index=False)[close_column].last().rename(columns={close_column: "close"})
        daily["forward_return_1d"] = daily["close"].pct_change().shift(-1)
        return daily[["date", "close", "forward_return_1d"]].dropna()
    return pd.DataFrame(columns=["date", "close", "forward_return_1d"])


def _load_deribit_block_flow_daily(root: Path, currency: str) -> pd.DataFrame:
    rows = read_parquet_rows(str(root / currency.upper() / "**" / "*.parquet"), order_by="ts_ns ASC")
    if not rows:
        return pd.DataFrame(columns=["date", "value"])
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["date", "value"])
    frame["date"] = pd.to_datetime(frame["ts_ns"], unit="ns", utc=True).dt.floor("D")
    frame["notional"] = frame["amount"].astype(float) * frame["index_price"].fillna(0.0).astype(float)
    frame["signed_notional"] = frame.apply(
        lambda row: row["notional"] if row.get("option_type") == "call" else -row["notional"],
        axis=1,
    )
    daily = frame.groupby("date", as_index=False)["signed_notional"].sum().rename(columns={"signed_notional": "value"})
    return daily


def _build_audit(
    *,
    source_name: str,
    symbol: str,
    role: str,
    time_granularity: str,
    series_frame: pd.DataFrame,
    comparison_frames: dict[str, pd.DataFrame],
    returns_frame: pd.DataFrame,
    allowed_default: bool,
    use_for_research_only: bool,
    blocked_by: str | None,
    next_action: str,
) -> SourceIndependenceAudit:
    returns_frame = returns_frame.copy()
    series_frame = series_frame.copy()
    returns_frame["date"] = pd.to_datetime(returns_frame["date"], utc=True)
    series_frame["date"] = pd.to_datetime(series_frame["date"], utc=True)
    normalized_comparisons: dict[str, pd.DataFrame] = {}
    for name, frame in comparison_frames.items():
        normalized = frame.copy()
        normalized["date"] = pd.to_datetime(normalized.get("date"), utc=True)
        normalized_comparisons[name] = normalized

    if series_frame.empty or returns_frame.empty:
        return SourceIndependenceAudit(
            generated_at=datetime.now(UTC).isoformat(),
            source_name=source_name,
            symbol=symbol,
            role=role,
            time_granularity=time_granularity,
            observations=0,
            pairwise_correlations={},
            max_abs_correlation=0.0,
            rolling_rank_ic_mean=0.0,
            rolling_rank_ic_stability=0.0,
            ablation_ready=False,
            ablation_delta=None,
            allowed_in_state_features=False,
            use_for_research_only=use_for_research_only,
            blocked_by=blocked_by or "source_series_unavailable",
            next_action=next_action,
        )

    aligned = returns_frame.merge(series_frame, on="date", how="inner").dropna()
    pairwise: dict[str, float] = {}
    for name, frame in normalized_comparisons.items():
        merged = aligned.merge(frame, on="date", how="inner", suffixes=("", f"_{name}")).dropna()
        pairwise[name] = round(_pearson_corr(merged["value"].tolist(), merged[f"value_{name}"].tolist()), 4)

    rolling_rank_ic_mean, rolling_rank_ic_stability = _rolling_rank_ic(
        aligned["value"].tolist(),
        aligned["forward_return_1d"].tolist(),
    )
    max_abs_correlation = max((abs(value) for value in pairwise.values()), default=0.0)
    allowed_in_state_features = allowed_default and max_abs_correlation < 0.75
    if max_abs_correlation >= 0.75:
        blocked_by = blocked_by or "high_role_correlation"

    return SourceIndependenceAudit(
        generated_at=datetime.now(UTC).isoformat(),
        source_name=source_name,
        symbol=symbol,
        role=role,
        time_granularity=time_granularity,
        observations=len(aligned),
        pairwise_correlations=pairwise,
        max_abs_correlation=round(max_abs_correlation, 4),
        rolling_rank_ic_mean=rolling_rank_ic_mean,
        rolling_rank_ic_stability=rolling_rank_ic_stability,
        ablation_ready=False,
        ablation_delta=None,
        allowed_in_state_features=allowed_in_state_features,
        use_for_research_only=use_for_research_only,
        blocked_by=blocked_by,
        next_action=next_action,
    )


def build_mvrv_independence_audit(
    *,
    symbol: str,
    macro_root: Path = Path("data/raw/macro"),
    data_root: Path = Path("data/raw/binance"),
) -> SourceIndependenceAudit:
    returns_frame = _load_btc_daily_returns(data_root, symbol)
    mvrv_frame = _load_daily_series(macro_root / "btc_mvrv" / "data.parquet")
    comparisons = {
        "btc_etf_netflow": _load_daily_series(macro_root / "btc_etf_netflow" / "data.parquet"),
        "ndx": _load_daily_series(macro_root / "ndx" / "data.parquet"),
        "gold": _load_daily_series(macro_root / "gold" / "data.parquet"),
    }
    return _build_audit(
        source_name="btc_mvrv",
        symbol=symbol,
        role="state",
        time_granularity="1d",
        series_frame=mvrv_frame,
        comparison_frames=comparisons,
        returns_frame=returns_frame,
        allowed_default=True,
        use_for_research_only=True,
        blocked_by=None if not mvrv_frame.empty else "btc_mvrv_missing",
        next_action="run_ablation_after_mvrv_integration",
    )


def build_deribit_block_flow_independence_audit(
    *,
    symbol: str,
    deribit_block_flow_root: Path = Path("data/raw/deribit_block_flow"),
    macro_root: Path = Path("data/raw/macro"),
    data_root: Path = Path("data/raw/binance"),
) -> SourceIndependenceAudit:
    returns_frame = _load_btc_daily_returns(data_root, symbol)
    block_flow_frame = _load_deribit_block_flow_daily(deribit_block_flow_root, "BTC")
    comparisons = {
        "btc_mvrv": _load_daily_series(macro_root / "btc_mvrv" / "data.parquet"),
        "btc_etf_netflow": _load_daily_series(macro_root / "btc_etf_netflow" / "data.parquet"),
    }
    return _build_audit(
        source_name="deribit_block_flow",
        symbol=symbol,
        role="state",
        time_granularity="1d",
        series_frame=block_flow_frame,
        comparison_frames=comparisons,
        returns_frame=returns_frame,
        allowed_default=False,
        use_for_research_only=True,
        blocked_by=None if not block_flow_frame.empty else "deribit_block_flow_missing",
        next_action="aggregate_block_flow_features_and_run_research_only_ablation",
    )


def write_source_independence_audit(audit: SourceIndependenceAudit, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{audit.source_name}_independence_audit.json"
    md_path = output_dir / f"{audit.source_name}_independence_audit.md"
    json_path.write_text(json.dumps(asdict(audit), indent=2), encoding="utf-8")
    lines = [
        f"# {audit.source_name} Independence Audit",
        "",
        f"- generated_at: `{audit.generated_at}`",
        f"- role: `{audit.role}`",
        f"- time_granularity: `{audit.time_granularity}`",
        f"- observations: `{audit.observations}`",
        f"- max_abs_correlation: `{audit.max_abs_correlation}`",
        f"- rolling_rank_ic_mean: `{audit.rolling_rank_ic_mean}`",
        f"- rolling_rank_ic_stability: `{audit.rolling_rank_ic_stability}`",
        f"- ablation_ready: `{audit.ablation_ready}`",
        f"- allowed_in_state_features: `{audit.allowed_in_state_features}`",
        f"- use_for_research_only: `{audit.use_for_research_only}`",
        f"- blocked_by: `{audit.blocked_by}`",
        f"- next_action: `{audit.next_action}`",
        "",
        "## Pairwise Correlations",
        "",
    ]
    if audit.pairwise_correlations:
        lines.extend(f"- {name}: `{value}`" for name, value in sorted(audit.pairwise_correlations.items()))
    else:
        lines.append("- `none`")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "allowed_in_state_features": audit.allowed_in_state_features,
        "blocked_by": audit.blocked_by,
    }


def load_source_independence_audit(
    source_name: str,
    *,
    review_dir: Path = Path("results/signal_combination"),
) -> dict[str, Any]:
    path = review_dir / f"{source_name}_independence_audit.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
