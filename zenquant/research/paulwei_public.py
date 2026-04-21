"""Research helpers for the public PaulWei / BitMEX archive.

This module treats the public account ledger as a research-side behavior source,
not as primary trading truth. It can:

1. ingest a flat public CSV root into a local sidecar directory,
2. build a dataset contract artifact,
3. derive behavior and episode summaries,
4. map the derived behavior into a replayable baseline variant, and
5. study AHR999 alignment versus the public trading behavior.
"""

from __future__ import annotations

import itertools
import json
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from zenquant.strategy.engine import ARCStrategyConfig

REQUIRED_PUBLIC_FILES = [
    "api-v1-execution-tradeHistory.csv",
    "api-v1-order.csv",
    "api-v1-user-walletHistory.csv",
    "api-v1-position.snapshot.csv",
    "api-v1-user-wallet.snapshot-all.csv",
    "api-v1-user-margin.snapshot-all.csv",
    "manifest.json",
]


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (Path,)):
        return str(value)
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _btc_symbol(symbol: str) -> bool:
    value = (symbol or "").upper()
    return "XBT" in value or "BTC" in value


def _price_col(frame: pd.DataFrame) -> str:
    for candidate in ("lastPx", "avgPx", "price"):
        if candidate in frame.columns:
            return candidate
    raise KeyError("missing_price_column")


def ingest_paulwei_public_dataset(*, source_root: Path, output_root: Path) -> dict[str, Any]:
    """Copy the public flat repo root into a local research-only sidecar directory."""
    source_root = source_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    missing = [name for name in REQUIRED_PUBLIC_FILES if not (source_root / name).exists()]
    if missing:
        raise FileNotFoundError(f"missing_required_public_files:{','.join(missing)}")

    copied: list[str] = []
    for name in REQUIRED_PUBLIC_FILES + ["derived-equity-curve.csv", "api-v1-instrument.all.csv"]:
        path = source_root / name
        if not path.exists():
            continue
        shutil.copy2(path, output_root / name)
        copied.append(name)

    metadata = {
        "copied_at": _ts(),
        "source_root": str(source_root),
        "output_root": str(output_root.resolve()),
        "required_files": list(REQUIRED_PUBLIC_FILES),
        "copied_files": copied,
    }
    _write_json(output_root / "metadata.json", metadata)
    return metadata


def build_paulwei_dataset_contract(*, data_root: Path) -> dict[str, Any]:
    data_root = data_root.resolve()
    manifest_path = data_root / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    missing = [name for name in REQUIRED_PUBLIC_FILES if not (data_root / name).exists()]

    execution_rows = _safe_row_count(data_root / "api-v1-execution-tradeHistory.csv")
    order_rows = _safe_row_count(data_root / "api-v1-order.csv")
    wallet_rows = _safe_row_count(data_root / "api-v1-user-walletHistory.csv")
    position_rows = _safe_row_count(data_root / "api-v1-position.snapshot.csv")

    window_start = manifest.get("window", {}).get("start") or manifest.get("dataset", {}).get("start")
    window_end = manifest.get("window", {}).get("end") or manifest.get("dataset", {}).get("end")
    if not window_start:
        window_start = _first_timestamp(data_root / "api-v1-user-walletHistory.csv", "timestamp")
    if not window_end:
        window_end = _last_timestamp(data_root / "api-v1-position.snapshot.csv", "timestamp")

    asset_bias = "btc_dominant"
    if execution_rows:
        executions = pd.read_csv(data_root / "api-v1-execution-tradeHistory.csv", usecols=["symbol", "foreignNotional"])
        executions["foreignNotional"] = pd.to_numeric(executions["foreignNotional"], errors="coerce").abs().fillna(0.0)
        total_notional = float(executions["foreignNotional"].sum())
        btc_notional = float(executions.loc[executions["symbol"].astype(str).map(_btc_symbol), "foreignNotional"].sum())
        if total_notional > 0:
            asset_bias = "btc_dominant" if btc_notional / total_notional >= 0.75 else "mixed"

    return {
        "generated_at": _ts(),
        "source_type": "external_public_account_archive",
        "venue": "bitmex",
        "dataset_name": "paulwei_public_archive",
        "data_root": str(data_root),
        "asset_bias": asset_bias,
        "window_start": window_start,
        "window_end": window_end,
        "pit_safe_for_behavior_research": "conditional",
        "admit_as_primary_truth": False,
        "allowed_uses": [
            "behavior_research",
            "baseline_rule_extraction",
            "comparison_variant_design",
        ],
        "required_files": list(REQUIRED_PUBLIC_FILES),
        "missing_files": missing,
        "ready": len(missing) == 0,
        "row_counts": {
            "execution_trade_history": execution_rows,
            "order": order_rows,
            "wallet_history": wallet_rows,
            "position_snapshot": position_rows,
        },
        "manifest_summary": {
            "generated_at": manifest.get("generated_at"),
            "root": manifest.get("root"),
            "tag": manifest.get("tag"),
        },
        "blocked_by": [] if not missing else [f"missing:{name}" for name in missing],
    }


def write_paulwei_dataset_contract(*, data_root: Path, output_dir: Path) -> dict[str, Any]:
    payload = build_paulwei_dataset_contract(data_root=data_root)
    json_path = output_dir / "paulwei_dataset_contract.json"
    md_path = output_dir / "paulwei_dataset_contract.md"
    _write_json(json_path, payload)
    md_lines = [
        "# PaulWei Public Dataset Contract",
        "",
        f"- source_type: `{payload['source_type']}`",
        f"- venue: `{payload['venue']}`",
        f"- dataset_name: `{payload['dataset_name']}`",
        f"- asset_bias: `{payload['asset_bias']}`",
        f"- window_start: `{payload['window_start']}`",
        f"- window_end: `{payload['window_end']}`",
        f"- pit_safe_for_behavior_research: `{payload['pit_safe_for_behavior_research']}`",
        f"- admit_as_primary_truth: `{payload['admit_as_primary_truth']}`",
        f"- ready: `{payload['ready']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
        "",
        "## Row Counts",
        "",
    ]
    for key, value in payload["row_counts"].items():
        md_lines.append(f"- `{key}`: `{value}`")
    md_lines.extend(["", "## Allowed Uses", ""])
    md_lines.extend(f"- `{item}`" for item in payload["allowed_uses"])
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "ready": payload["ready"]}


def load_paulwei_frames(*, data_root: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for name in REQUIRED_PUBLIC_FILES + ["derived-equity-curve.csv", "api-v1-instrument.all.csv"]:
        path = data_root / name
        if path.exists() and path.suffix == ".csv":
            frames[name] = pd.read_csv(path, low_memory=False)
    return frames


def build_trade_episode_catalog(*, data_root: Path) -> pd.DataFrame:
    frames = load_paulwei_frames(data_root=data_root)
    executions = frames.get("api-v1-execution-tradeHistory.csv")
    if executions is None or executions.empty:
        return pd.DataFrame()

    trades = executions.copy()
    if "execType" in trades.columns:
        trades = trades.loc[trades["execType"].astype(str) == "Trade"].copy()
    if trades.empty:
        return pd.DataFrame()

    trades["timestamp"] = _parse_ts(trades["timestamp"])
    trades["side_sign"] = trades["side"].map({"Buy": 1.0, "Sell": -1.0}).fillna(0.0)
    trades["lastQty"] = pd.to_numeric(trades["lastQty"], errors="coerce").fillna(0.0)
    trades["signed_qty"] = trades["lastQty"] * trades["side_sign"]
    price_col = _price_col(trades)
    trades["trade_price"] = pd.to_numeric(trades[price_col], errors="coerce").fillna(0.0)
    trades = trades.loc[trades["timestamp"].notna() & (trades["signed_qty"] != 0)].copy()
    trades = trades.sort_values(["symbol", "timestamp", "orderID", "execID"], kind="stable")

    episodes: list[dict[str, Any]] = []
    for symbol, group in trades.groupby("symbol", sort=False):
        current_qty = 0.0
        episode: dict[str, Any] | None = None
        add_times: list[pd.Timestamp] = []

        for row in group.itertuples(index=False):
            signed_qty = float(row.signed_qty)
            price = float(row.trade_price)
            ts = row.timestamp
            new_qty = current_qty + signed_qty
            old_sign = 0 if current_qty == 0 else (1 if current_qty > 0 else -1)
            new_sign = 0 if new_qty == 0 else (1 if new_qty > 0 else -1)

            if old_sign == 0 and new_sign != 0:
                episode = _new_episode(symbol=symbol, side=new_sign, timestamp=ts, price=price, qty=abs(new_qty))
                current_qty = new_qty
                continue

            if old_sign != 0 and new_sign == old_sign and abs(new_qty) > abs(current_qty):
                if episode is not None:
                    episode["add_events"] += 1
                    episode["peak_abs_position"] = max(episode["peak_abs_position"], abs(new_qty))
                    episode["entry_notional"] += abs(signed_qty) * price
                    episode["entry_qty"] += abs(signed_qty)
                    add_times.append(ts)
                current_qty = new_qty
                continue

            if old_sign != 0 and new_sign == old_sign and abs(new_qty) < abs(current_qty):
                if episode is not None:
                    episode["reduce_events"] += 1
                    episode["last_reduce_at"] = ts
                current_qty = new_qty
                continue

            if old_sign != 0 and new_sign == 0:
                if episode is not None:
                    episodes.append(_finalize_episode(episode, close_at=ts, close_price=price, add_times=add_times))
                episode = None
                add_times = []
                current_qty = 0.0
                continue

            if old_sign != 0 and new_sign != old_sign:
                if episode is not None:
                    episodes.append(
                        _finalize_episode(episode, close_at=ts, close_price=price, add_times=add_times, flipped=True)
                    )
                add_times = []
                episode = _new_episode(symbol=symbol, side=new_sign, timestamp=ts, price=price, qty=abs(new_qty))
                episode["flip_entry"] = True
                current_qty = new_qty
                continue

        if episode is not None:
            episodes.append(_finalize_episode(episode, close_at=None, close_price=None, add_times=add_times))

    return pd.DataFrame(episodes)


def build_behavior_profile(*, data_root: Path, spot_root: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    frames = load_paulwei_frames(data_root=data_root)
    episodes = build_trade_episode_catalog(data_root=data_root)
    executions = frames.get("api-v1-execution-tradeHistory.csv", pd.DataFrame()).copy()
    orders = frames.get("api-v1-order.csv", pd.DataFrame()).copy()
    wallet = frames.get("api-v1-user-walletHistory.csv", pd.DataFrame()).copy()
    equity_curve = frames.get("derived-equity-curve.csv", pd.DataFrame()).copy()

    profile: dict[str, Any] = {
        "generated_at": _ts(),
        "dataset_window": {
            "start": _first_timestamp(data_root / "api-v1-user-walletHistory.csv", "timestamp"),
            "end": _last_timestamp(data_root / "api-v1-position.snapshot.csv", "timestamp"),
        },
        "row_counts": {
            "execution_trade_history": len(executions),
            "order": len(orders),
            "wallet_history": len(wallet),
            "episodes": len(episodes),
            "closed_episodes": int(episodes["closed"].sum()) if not episodes.empty and "closed" in episodes.columns else 0,
        },
    }

    if not executions.empty:
        trade_rows = executions.loc[executions["execType"].astype(str) == "Trade"].copy() if "execType" in executions.columns else executions.copy()
        trade_rows["timestamp"] = _parse_ts(trade_rows["timestamp"])
        trade_rows["hour_utc"] = trade_rows["timestamp"].dt.hour
        trade_rows["foreignNotional"] = pd.to_numeric(trade_rows.get("foreignNotional"), errors="coerce").abs().fillna(0.0)
        product_mix = (
            trade_rows.groupby("symbol", dropna=False)["foreignNotional"].sum().sort_values(ascending=False).head(8)
        )
        total_notional = float(trade_rows["foreignNotional"].sum())
        btc_notional = float(trade_rows.loc[trade_rows["symbol"].astype(str).map(_btc_symbol), "foreignNotional"].sum())
        profile["dominant_products"] = [
            {"symbol": str(symbol), "notional_share": round((float(value) / total_notional) if total_notional > 0 else 0.0, 4)}
            for symbol, value in product_mix.items()
        ]
        profile["btc_notional_share"] = round((btc_notional / total_notional) if total_notional > 0 else 0.0, 4)
        profile["dominant_trading_hours_utc"] = [
            {"hour": int(hour), "count": int(count)}
            for hour, count in trade_rows["hour_utc"].value_counts().sort_values(ascending=False).head(6).items()
        ]

    if not episodes.empty:
        closed = episodes.loc[episodes["closed"]].copy()
        positive = closed.loc[closed["estimated_return_bps"] > 0]
        negative = closed.loc[closed["estimated_return_bps"] <= 0]
        span_days = max(1.0, (pd.Timestamp(profile["dataset_window"]["end"]) - pd.Timestamp(profile["dataset_window"]["start"])).total_seconds() / 86_400.0)
        yearly_btc_share = _yearly_btc_share(frames.get("api-v1-execution-tradeHistory.csv", pd.DataFrame()))
        profile.update(
            {
                "position_switches_per_30d": round((float(episodes["flip_exit"].sum()) / span_days) * 30.0, 4),
                "median_adds_per_episode": float(episodes["add_events"].median()),
                "median_reduces_per_episode": float(episodes["reduce_events"].median()),
                "median_add_interval_hours": _median_nonnull(episodes["median_add_interval_hours"]),
                "long_episode_share": round(float((episodes["side"] == "long").mean()), 4),
                "win_median_hold_hours": _median_nonnull(positive["duration_hours"]) if not positive.empty else None,
                "loss_median_hold_hours": _median_nonnull(negative["duration_hours"]) if not negative.empty else None,
                "yearly_btc_notional_share": yearly_btc_share,
            }
        )

    if not equity_curve.empty:
        dd_stats = _drawdown_profile(equity_curve)
        profile["drawdown_profile"] = dd_stats

    vol_alignment = _extreme_vol_alignment(episodes=episodes, spot_root=spot_root)
    if vol_alignment:
        profile["extreme_vol_alignment"] = vol_alignment

    return profile, episodes


def build_baseline_mapping(*, behavior_profile: dict[str, Any]) -> dict[str, Any]:
    from zenquant.research.comparison import paulwei_execution_baseline, paulwei_risk_baseline

    base_risk = paulwei_risk_baseline()
    base_exec = paulwei_execution_baseline()
    execution_rules: dict[str, Any] = {}
    risk_rules: dict[str, Any] = {}
    strategy_rules: dict[str, Any] = {}
    unsupported = [
        "discretionary_macro_inputs",
        "news_and_narrative_interpretation",
        "cross-exchange_context",
        "manual conviction scaling beyond observable ledger behavior",
    ]

    long_share = float(behavior_profile.get("long_episode_share", 0.5) or 0.5)
    median_adds = float(behavior_profile.get("median_adds_per_episode", 0.0) or 0.0)
    median_reduces = float(behavior_profile.get("median_reduces_per_episode", 0.0) or 0.0)
    switch_rate = float(behavior_profile.get("position_switches_per_30d", 0.0) or 0.0)
    dd_profile = behavior_profile.get("drawdown_profile", {})
    max_dd = float(dd_profile.get("max_drawdown_pct", 0.0) or 0.0)
    recovery_days = float(dd_profile.get("median_recovery_days", 6.0) or 6.0)
    protective_share = float((behavior_profile.get("extreme_vol_alignment") or {}).get("protective_action_share", 0.0) or 0.0)

    execution_rules["pyramid_initial_pct"] = 0.30 if median_adds >= 1.0 else 0.25
    execution_rules["pyramid_add_pct"] = min(0.30, max(0.18, 0.18 + 0.04 * median_adds))
    execution_rules["pyramid_short_initial_pct"] = 0.20 if long_share >= 0.55 else 0.25
    execution_rules["pyramid_short_add_pct"] = 0.08 if long_share >= 0.60 else 0.12
    execution_rules["gradual_exit_enabled"] = True
    execution_rules["exit_unwind_pct"] = 0.35 if median_reduces >= 1.0 else 0.30
    execution_rules["flip_immediate"] = True
    execution_rules["flip_min_confidence"] = 0.55 if switch_rate >= 0.5 else 0.60

    risk_rules["dd_step_1_long"] = base_risk.dd_step_1_long
    risk_rules["dd_step_1_short"] = 0.08 if long_share >= 0.55 else 0.10
    risk_rules["kill_switch_drawdown"] = 0.30 if max_dd <= 0.35 else 0.28
    risk_rules["kill_cooldown_bars"] = int(min(48, max(24, round(recovery_days * 6))))
    risk_rules["kill_ramp_bars"] = int(min(36, max(18, round(recovery_days * 4))))
    risk_rules["vol_shock_mult"] = 0.35 if protective_share >= 0.5 else 0.40

    mapping = {
        "generated_at": _ts(),
        "mapping_source": "paulwei_public_archive_behavior_profile",
        "evidence_window": behavior_profile.get("dataset_window", {}),
        "derived_execution_rules": execution_rules,
        "derived_risk_rules": risk_rules,
        "derived_strategy_rules": strategy_rules,
        "unsupported_dimensions": unsupported,
        "mapping_confidence": "medium",
        "execution_config_overrides": execution_rules,
        "risk_config_overrides": risk_rules,
        "strategy_config_overrides": strategy_rules,
    }
    return mapping


def build_ahr999_alignment(*, data_root: Path, spot_root: Path, macro_root: Path) -> dict[str, Any]:
    episodes = build_trade_episode_catalog(data_root=data_root)
    spot_daily = _load_spot_daily(spot_root / "klines" / "BTCUSDT" / "1d" / "data.parquet")
    if spot_daily.empty:
        return {
            "generated_at": _ts(),
            "ready": False,
            "blocked_by": ["spot_daily_missing"],
        }

    ahr = _compute_ahr999(spot_daily)
    event_alignment = _align_episodes_to_ahr(episodes, ahr)
    mvrv_corr = _ahr999_mvrv_correlation(ahr, macro_root / "btc_mvrv" / "data.parquet")

    return {
        "generated_at": _ts(),
        "ready": True,
        "role_candidate": ["regime_prior", "risk_filter", "explainability_context"],
        "blocked_by": [],
        "ahr999_formula": {
            "dca_component": "200d geometric mean of BTC close",
            "valuation_component": "power-law valuation based on days since 2009-01-03",
            "power_law_params": {"slope": 5.84, "intercept": -17.01},
        },
        "zone_thresholds": {"accumulation": 0.45, "dca_upper": 1.2},
        "zone_behavior_alignment": event_alignment,
        "correlation_to_mvrv": mvrv_corr,
        "correlation_to_cycle_position": _corr_pair(
            ahr[["date", "ahr999"]],
            pd.DataFrame({"date": ahr["date"], "cycle_position": ahr["close"] / ahr["sma200"]}),
            left="ahr999",
            right="cycle_position",
        ),
        "recommendation": {
            "worth_admission_review": bool(
                mvrv_corr.get("pearson_corr") is not None and abs(float(mvrv_corr["pearson_corr"])) < 0.85
            ),
            "use_for_research_only": True,
            "do_not_use_as_intraday_trigger": True,
        },
    }


def write_behavior_profile(*, data_root: Path, spot_root: Path, output_dir: Path) -> dict[str, Any]:
    profile, episodes = build_behavior_profile(data_root=data_root, spot_root=spot_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "behavior_profile.json"
    md_path = output_dir / "behavior_profile.md"
    catalog_path = output_dir / "trade_episode_catalog.csv"
    _write_json(json_path, profile)
    episodes.to_csv(catalog_path, index=False)
    lines = [
        "# PaulWei Behavior Profile",
        "",
        f"- dataset_window: `{profile.get('dataset_window')}`",
        f"- row_counts: `{profile.get('row_counts')}`",
        f"- btc_notional_share: `{profile.get('btc_notional_share')}`",
        f"- position_switches_per_30d: `{profile.get('position_switches_per_30d')}`",
        f"- median_adds_per_episode: `{profile.get('median_adds_per_episode')}`",
        f"- median_reduces_per_episode: `{profile.get('median_reduces_per_episode')}`",
        f"- win_median_hold_hours: `{profile.get('win_median_hold_hours')}`",
        f"- loss_median_hold_hours: `{profile.get('loss_median_hold_hours')}`",
        "",
        f"- trade_episode_catalog: `{catalog_path}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "catalog_path": str(catalog_path),
        "episode_count": len(episodes),
    }


def write_baseline_mapping(*, behavior_profile: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    mapping = build_baseline_mapping(behavior_profile=behavior_profile)
    json_path = output_dir / "baseline_mapping.json"
    md_path = output_dir / "baseline_mapping.md"
    _write_json(json_path, mapping)
    lines = [
        "# PaulWei Public Archive Baseline Mapping",
        "",
        f"- evidence_window: `{mapping['evidence_window']}`",
        f"- mapping_confidence: `{mapping['mapping_confidence']}`",
        f"- derived_execution_rules: `{mapping['derived_execution_rules']}`",
        f"- derived_risk_rules: `{mapping['derived_risk_rules']}`",
        f"- unsupported_dimensions: `{mapping['unsupported_dimensions']}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path)}


def write_ahr999_alignment(*, data_root: Path, spot_root: Path, macro_root: Path, output_dir: Path) -> dict[str, Any]:
    payload = build_ahr999_alignment(data_root=data_root, spot_root=spot_root, macro_root=macro_root)
    json_path = output_dir / "ahr999_alignment.json"
    md_path = output_dir / "ahr999_alignment.md"
    _write_json(json_path, payload)
    lines = [
        "# AHR999 Alignment",
        "",
        f"- ready: `{payload.get('ready')}`",
        f"- correlation_to_mvrv: `{payload.get('correlation_to_mvrv')}`",
        f"- correlation_to_cycle_position: `{payload.get('correlation_to_cycle_position')}`",
        f"- recommendation: `{payload.get('recommendation')}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "ready": payload.get("ready", False)}


def load_paulwei_public_archive_variant(*, mapping_path: Path) -> dict[str, Any] | None:
    if not mapping_path.exists():
        return None
    try:
        return _read_json(mapping_path)
    except Exception:
        return None


def paulwei_public_archive_variant_spec(*, mapping_path: Path) -> dict[str, Any] | None:
    from zenquant.research.comparison import paulwei_execution_baseline, paulwei_risk_baseline

    payload = load_paulwei_public_archive_variant(mapping_path=mapping_path)
    if not payload:
        return None
    risk = replace_config(paulwei_risk_baseline(), payload.get("risk_config_overrides", {}))
    execution = replace_config(paulwei_execution_baseline(), payload.get("execution_config_overrides", {}))
    strategy_overrides = payload.get("strategy_config_overrides", {})
    strategy = replace_config(ARCStrategyConfig(), strategy_overrides) if strategy_overrides else None
    return {
        "name": "paulwei_public_archive_baseline",
        "label": "PaulWei Public Archive Baseline",
        "risk_config": risk,
        "execution_config": execution,
        "strategy_config": strategy,
    }


def replace_config(config: Any, overrides: dict[str, Any]) -> Any:
    values = {field: getattr(config, field) for field in config.__dataclass_fields__}  # type: ignore[attr-defined]
    values.update(overrides)
    return type(config)(**values)


def _safe_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.open("r", encoding="utf-8")) - 1


def _first_timestamp(path: Path, column: str) -> str | None:
    if not path.exists():
        return None
    frame = pd.read_csv(path, usecols=[column], nrows=1)
    if frame.empty:
        return None
    ts = pd.to_datetime(frame.iloc[0][column], utc=True, errors="coerce")
    return None if pd.isna(ts) else ts.isoformat()


def _last_timestamp(path: Path, column: str) -> str | None:
    if not path.exists():
        return None
    frame = pd.read_csv(path, usecols=[column])
    if frame.empty:
        return None
    ts = pd.to_datetime(frame.iloc[-1][column], utc=True, errors="coerce")
    return None if pd.isna(ts) else ts.isoformat()


def _new_episode(*, symbol: str, side: int, timestamp: pd.Timestamp, price: float, qty: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "side": "long" if side > 0 else "short",
        "open_at": timestamp,
        "close_at": None,
        "open_price": price,
        "close_price": None,
        "entry_notional": qty * price,
        "entry_qty": qty,
        "peak_abs_position": qty,
        "add_events": 0,
        "reduce_events": 0,
        "flip_exit": False,
        "flip_entry": False,
        "last_reduce_at": None,
        "closed": False,
    }


def _finalize_episode(
    episode: dict[str, Any],
    *,
    close_at: pd.Timestamp | None,
    close_price: float | None,
    add_times: list[pd.Timestamp],
    flipped: bool = False,
) -> dict[str, Any]:
    payload = dict(episode)
    payload["close_at"] = close_at
    payload["close_price"] = close_price
    payload["closed"] = close_at is not None
    payload["flip_exit"] = bool(flipped)
    payload["avg_entry_price"] = (
        float(payload["entry_notional"]) / float(payload["entry_qty"]) if float(payload["entry_qty"]) > 0 else None
    )
    if close_at is not None:
        payload["duration_hours"] = round((close_at - payload["open_at"]).total_seconds() / 3600.0, 4)
    else:
        payload["duration_hours"] = None
    if close_price and payload["avg_entry_price"]:
        side_sign = 1.0 if payload["side"] == "long" else -1.0
        payload["estimated_return_bps"] = round(
            side_sign * ((float(close_price) / float(payload["avg_entry_price"])) - 1.0) * 10_000.0,
            4,
        )
    else:
        payload["estimated_return_bps"] = None
    if len(add_times) >= 2:
        diffs = [
            (later - earlier).total_seconds() / 3600.0
            for earlier, later in itertools.pairwise(add_times)
            if later >= earlier
        ]
        payload["median_add_interval_hours"] = round(float(pd.Series(diffs).median()), 4) if diffs else None
    else:
        payload["median_add_interval_hours"] = None
    return payload


def _median_nonnull(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return None if clean.empty else round(float(clean.median()), 4)


def _yearly_btc_share(executions: pd.DataFrame) -> list[dict[str, Any]]:
    if executions.empty:
        return []
    trades = executions.copy()
    if "execType" in trades.columns:
        trades = trades.loc[trades["execType"].astype(str) == "Trade"].copy()
    trades["timestamp"] = _parse_ts(trades["timestamp"])
    trades["year"] = trades["timestamp"].dt.year
    trades["foreignNotional"] = pd.to_numeric(trades.get("foreignNotional"), errors="coerce").abs().fillna(0.0)
    rows: list[dict[str, Any]] = []
    for year, group in trades.groupby("year"):
        total = float(group["foreignNotional"].sum())
        btc = float(group.loc[group["symbol"].astype(str).map(_btc_symbol), "foreignNotional"].sum())
        rows.append({"year": int(year), "btc_notional_share": round((btc / total) if total > 0 else 0.0, 4)})
    return rows


def _drawdown_profile(equity_curve: pd.DataFrame) -> dict[str, Any]:
    frame = equity_curve.copy()
    if "timestamp" not in frame.columns or frame.empty:
        return {}
    value_col = next(
        (
            c
            for c in (
                "adjustedWealthXBT",
                "adjustedMarkedWealthXBT",
                "walletBalanceXBTEquivalent",
                "adjusted_wallet_equivalent_xbt",
                "wallet_equivalent_xbt",
                "wealth_xbt",
            )
            if c in frame.columns
        ),
        None,
    )
    if value_col is None:
        numeric_cols = [c for c in frame.columns if c != "timestamp"]
        value_col = numeric_cols[0] if numeric_cols else None
    if value_col is None:
        return {}
    frame["timestamp"] = _parse_ts(frame["timestamp"])
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.dropna(subset=["timestamp", value_col]).sort_values("timestamp")
    if frame.empty:
        return {}
    values = frame[value_col].astype(float)
    hwm = values.cummax()
    drawdown = (hwm - values) / hwm.replace(0, pd.NA)
    max_dd = float(drawdown.max()) if not drawdown.empty else 0.0
    threshold = 0.10
    in_dd = drawdown >= threshold
    recoveries: list[float] = []
    start_idx: int | None = None
    peak_value = None
    for idx, flag in enumerate(in_dd):
        if flag and start_idx is None:
            start_idx = idx
            peak_value = float(hwm.iloc[idx])
        elif not flag and start_idx is not None:
            start_ts = frame["timestamp"].iloc[start_idx]
            end_ts = frame["timestamp"].iloc[idx]
            recoveries.append((end_ts - start_ts).total_seconds() / 86_400.0)
            start_idx = None
            peak_value = None
    return {
        "max_drawdown_pct": round(max_dd, 4),
        "drawdown_episode_count_over_10pct": int(((in_dd.astype(int).diff() == 1).sum()) + int(in_dd.iloc[0])),
        "median_recovery_days": round(float(pd.Series(recoveries).median()), 4) if recoveries else None,
    }


def _load_spot_daily(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    if frame.empty or "close" not in frame.columns:
        return pd.DataFrame()
    ts_col = "close_time_ns" if "close_time_ns" in frame.columns else "open_time_ns"
    daily = frame[["close", ts_col]].copy()
    daily["date"] = pd.to_datetime(daily[ts_col], unit="ns", utc=True).dt.floor("D")
    daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
    return daily.dropna(subset=["date", "close"]).sort_values("date")


def _extreme_vol_alignment(*, episodes: pd.DataFrame, spot_root: Path) -> dict[str, Any] | None:
    if episodes.empty:
        return None
    daily = _load_spot_daily(spot_root / "klines" / "BTCUSDT" / "1d" / "data.parquet")
    if daily.empty:
        return None
    daily["ret_abs"] = daily["close"].pct_change().abs()
    threshold = float(daily["ret_abs"].quantile(0.90))
    daily["high_vol"] = daily["ret_abs"] >= threshold
    events = episodes.copy()
    events["event_date"] = pd.to_datetime(events["open_at"], utc=True).dt.floor("D")
    merged = events.merge(daily[["date", "high_vol"]], left_on="event_date", right_on="date", how="left")
    high_vol = merged.loc[merged["high_vol"] == True]  # noqa: E712
    if high_vol.empty:
        return None
    protective = (high_vol["reduce_events"] > 0) | (high_vol["flip_exit"] == True)  # noqa: E712
    return {
        "high_vol_threshold_abs_return": round(threshold, 6),
        "high_vol_episode_count": len(high_vol),
        "protective_action_share": round(float(protective.mean()), 4),
    }


def _compute_ahr999(spot_daily: pd.DataFrame) -> pd.DataFrame:
    frame = spot_daily.copy().sort_values("date")
    frame["log_close"] = frame["close"].map(lambda x: math.log(max(x, 1e-12)))
    frame["gm200"] = frame["log_close"].rolling(200).mean().map(lambda x: math.exp(x) if pd.notna(x) else math.nan)
    genesis = pd.Timestamp("2009-01-03T00:00:00Z")
    days_since = (frame["date"] - genesis).dt.total_seconds() / 86_400.0
    frame["power_law"] = days_since.map(
        lambda x: 10 ** (5.84 * math.log10(max(x, 1.0)) - 17.01) if pd.notna(x) and x > 0 else math.nan
    )
    frame["ahr999"] = (frame["close"] / frame["gm200"]) * (frame["close"] / frame["power_law"])
    frame["sma200"] = frame["close"].rolling(200).mean()
    frame["zone"] = pd.cut(
        frame["ahr999"],
        bins=[-math.inf, 0.45, 1.2, math.inf],
        labels=["accumulation", "dca", "overheated"],
    ).astype("string")
    return frame.dropna(subset=["ahr999"]).copy()


def _align_episodes_to_ahr(episodes: pd.DataFrame, ahr: pd.DataFrame) -> dict[str, Any]:
    if episodes.empty or ahr.empty:
        return {}
    frame = episodes.copy()
    frame["event_date"] = pd.to_datetime(frame["open_at"], utc=True).dt.floor("D")
    merged = frame.merge(ahr[["date", "zone"]], left_on="event_date", right_on="date", how="left")
    rows: dict[str, Any] = {}
    for zone, group in merged.groupby("zone", dropna=False):
        key = "unknown" if pd.isna(zone) else str(zone)
        rows[key] = {
            "episode_count": len(group),
            "long_share": round(float((group["side"] == "long").mean()), 4) if len(group) else None,
            "median_hold_hours": _median_nonnull(group["duration_hours"]) if "duration_hours" in group else None,
            "risk_expansion_frequency": round(float((group["add_events"] > 0).mean()), 4) if len(group) else None,
            "protective_action_frequency": round(float((group["reduce_events"] > 0).mean()), 4) if len(group) else None,
        }
    return rows


def _ahr999_mvrv_correlation(ahr: pd.DataFrame, mvrv_path: Path) -> dict[str, Any]:
    if not mvrv_path.exists():
        return {"pearson_corr": None, "observations": 0, "blocked_by": "mvrv_missing"}
    mvrv = pd.read_parquet(mvrv_path)
    if mvrv.empty or "ts_ns" not in mvrv.columns or "value" not in mvrv.columns:
        return {"pearson_corr": None, "observations": 0, "blocked_by": "mvrv_invalid"}
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(mvrv["ts_ns"], unit="ns", utc=True).dt.floor("D"),
            "mvrv": pd.to_numeric(mvrv["value"], errors="coerce"),
        }
    ).dropna()
    return _corr_pair(ahr[["date", "ahr999"]], daily, left="ahr999", right="mvrv")


def _corr_pair(left_df: pd.DataFrame, right_df: pd.DataFrame, *, left: str, right: str) -> dict[str, Any]:
    merged = left_df.merge(right_df, on="date", how="inner").dropna(subset=[left, right])
    if merged.empty:
        return {"pearson_corr": None, "observations": 0}
    corr = float(merged[left].corr(merged[right]))
    return {"pearson_corr": round(corr, 4) if not math.isnan(corr) else None, "observations": len(merged)}
