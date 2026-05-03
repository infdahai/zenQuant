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

import hashlib
import itertools
import json
import math
import shutil
import subprocess
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


def _artifact_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=_json_default).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _path_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return proc.stdout.strip() or None


def _lineage(
    *,
    source_contract_path: Path,
    source_contract_id: str | None,
    behavior_profile_path: Path | None = None,
    behavior_profile_id: str | None = None,
    mapping_path: Path | None = None,
    mapping_id: str | None = None,
    ahr_review_path: Path | None = None,
    ahr_review_id: str | None = None,
    comparison_run_path: Path | None = None,
    comparison_run_id: str | None = None,
    split_contract_id: str = "btc_temporal_v2_train_selection_only",
) -> dict[str, Any]:
    return {
        "source_contract_id": source_contract_id,
        "source_contract_sha256": _path_sha256(source_contract_path),
        "behavior_profile_id": behavior_profile_id,
        "behavior_profile_sha256": _path_sha256(behavior_profile_path) if behavior_profile_path else None,
        "mapping_id": mapping_id,
        "mapping_sha256": _path_sha256(mapping_path) if mapping_path else None,
        "ahr_review_id": ahr_review_id,
        "ahr_review_sha256": _path_sha256(ahr_review_path) if ahr_review_path else None,
        "comparison_run_id": comparison_run_id,
        "comparison_run_sha256": _path_sha256(comparison_run_path) if comparison_run_path else None,
        "split_contract_id": split_contract_id,
        "formula_hash": "unavailable_outside_replay_context",
        "code_commit": _git_commit(),
        "generated_at": _ts(),
    }


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
    payload["artifact_id"] = _artifact_id(payload)
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
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "ready": payload["ready"],
        "artifact_id": payload["artifact_id"],
    }


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
            "closed_episodes": int(episodes["closed"].sum())
            if not episodes.empty and "closed" in episodes.columns
            else 0,
        },
    }

    if not executions.empty:
        trade_rows = (
            executions.loc[executions["execType"].astype(str) == "Trade"].copy()
            if "execType" in executions.columns
            else executions.copy()
        )
        trade_rows["timestamp"] = _parse_ts(trade_rows["timestamp"])
        trade_rows["hour_utc"] = trade_rows["timestamp"].dt.hour
        trade_rows["foreignNotional"] = (
            pd.to_numeric(trade_rows.get("foreignNotional"), errors="coerce").abs().fillna(0.0)
        )
        product_mix = (
            trade_rows.groupby("symbol", dropna=False)["foreignNotional"].sum().sort_values(ascending=False).head(8)
        )
        total_notional = float(trade_rows["foreignNotional"].sum())
        btc_notional = float(trade_rows.loc[trade_rows["symbol"].astype(str).map(_btc_symbol), "foreignNotional"].sum())
        profile["dominant_products"] = [
            {
                "symbol": str(symbol),
                "notional_share": round((float(value) / total_notional) if total_notional > 0 else 0.0, 4),
            }
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
        span_days = max(
            1.0,
            (
                pd.Timestamp(profile["dataset_window"]["end"]) - pd.Timestamp(profile["dataset_window"]["start"])
            ).total_seconds()
            / 86_400.0,
        )
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
        inventory_build_profile = {
            "median_entry_tranches": round(float(episodes["add_events"].add(1).median()), 4),
            "median_peak_abs_position": _median_nonnull(episodes["peak_abs_position"]),
            "median_add_interval_hours": _median_nonnull(episodes["median_add_interval_hours"]),
            "long_short_add_asymmetry": round(
                float(
                    episodes.loc[episodes["side"] == "long", "add_events"].mean()
                    - episodes.loc[episodes["side"] == "short", "add_events"].mean()
                ),
                4,
            )
            if {"long", "short"}.issubset(set(episodes["side"].unique()))
            else None,
        }
        protective_exit_profile = {
            "protective_reduce_share": round(float((episodes["reduce_events"] > 0).mean()), 4),
            "flip_exit_share": round(float(episodes["flip_exit"].mean()), 4),
            "extreme_vol_protective_action_share": None,
        }
        recovery_profile = {
            "max_drawdown_pct": None,
            "drawdown_episode_count_over_10pct": None,
            "median_recovery_days": None,
            "kill_recovery_style": "gradual_ramp_after_cooldown",
        }
        flip_profile = {
            "position_switches_per_30d": profile["position_switches_per_30d"],
            "flip_exit_share": round(float(episodes["flip_exit"].mean()), 4),
            "suggested_min_confidence_band": [0.55, 0.6],
        }
        trend_hold_profile = {
            "long_episode_share": profile["long_episode_share"],
            "win_median_hold_hours": profile["win_median_hold_hours"],
            "loss_median_hold_hours": profile["loss_median_hold_hours"],
            "trend_hold_bias": "longer_hold_for_positive_trend_continuations"
            if (profile["win_median_hold_hours"] or 0.0) >= (profile["loss_median_hold_hours"] or 0.0)
            else "faster_profit_realization_than_loss_recovery",
        }
        profile["inventory_build_profile"] = inventory_build_profile
        profile["protective_exit_profile"] = protective_exit_profile
        profile["recovery_profile"] = recovery_profile
        profile["flip_profile"] = flip_profile
        profile["trend_hold_profile"] = trend_hold_profile

    if not equity_curve.empty:
        dd_stats = _drawdown_profile(equity_curve)
        profile["drawdown_profile"] = dd_stats
        if "recovery_profile" in profile:
            profile["recovery_profile"].update(dd_stats)

    vol_alignment = _extreme_vol_alignment(episodes=episodes, spot_root=spot_root)
    if vol_alignment:
        profile["extreme_vol_alignment"] = vol_alignment
        if "protective_exit_profile" in profile:
            profile["protective_exit_profile"]["extreme_vol_protective_action_share"] = vol_alignment.get(
                "protective_action_share"
            )

    return profile, episodes


def build_baseline_mapping(*, behavior_profile: dict[str, Any]) -> dict[str, Any]:
    from zenquant.research.comparison import paulwei_execution_baseline, paulwei_risk_baseline

    base_risk = paulwei_risk_baseline()
    paulwei_execution_baseline()
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
    protective_share = float(
        (behavior_profile.get("extreme_vol_alignment") or {}).get("protective_action_share", 0.0) or 0.0
    )

    execution_rules["pyramid_initial_pct"] = 0.30
    execution_rules["pyramid_add_pct"] = 0.30
    execution_rules["pyramid_short_initial_pct"] = 0.20 if long_share >= 0.55 else 0.25
    execution_rules["pyramid_short_add_pct"] = 0.10 if long_share >= 0.60 else 0.12
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

    execution_mapping = {
        "pyramid_initial_pct": {
            "value": execution_rules["pyramid_initial_pct"],
            "source_evidence": {"median_adds_per_episode": median_adds},
            "mapping_rationale": "Higher add cadence supports chunked entry rather than one-shot sizing.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "retain multi-tranche build style on long entries",
        },
        "pyramid_add_pct": {
            "value": execution_rules["pyramid_add_pct"],
            "source_evidence": {"median_adds_per_episode": median_adds},
            "mapping_rationale": "Observed repeat add behavior warrants keeping aggressive add capacity on winning builds.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "allow continuation adds before inventory cap",
        },
        "pyramid_short_initial_pct": {
            "value": execution_rules["pyramid_short_initial_pct"],
            "source_evidence": {"long_episode_share": long_share},
            "mapping_rationale": "Long bias in the ledger argues for slightly smaller initial short inventory.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "reduce initial short aggressiveness vs long side",
        },
        "pyramid_short_add_pct": {
            "value": execution_rules["pyramid_short_add_pct"],
            "source_evidence": {"long_episode_share": long_share},
            "mapping_rationale": "Public archive shows materially lower short-side expansion than long-side expansion.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "cap short-side add speed",
        },
        "exit_unwind_pct": {
            "value": execution_rules["exit_unwind_pct"],
            "source_evidence": {"median_reduces_per_episode": median_reduces},
            "mapping_rationale": "Frequent staged reductions support a gradual unwind configuration.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "favor partial exits over all-at-once liquidation",
        },
        "flip_min_confidence": {
            "value": execution_rules["flip_min_confidence"],
            "source_evidence": {"position_switches_per_30d": switch_rate},
            "mapping_rationale": "Observed flip frequency is active but not hyper-reactive; confirmation should stay above a neutral threshold.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "permit flips while avoiding overly noisy reversals",
        },
    }
    risk_mapping = {
        "dd_step_1_long": {
            "value": risk_rules["dd_step_1_long"],
            "source_evidence": {"baseline": "retain known-good PaulWei long DD step"},
            "mapping_rationale": "No ledger evidence supports loosening the first long drawdown guard.",
            "mapping_confidence": "high",
            "expected_behavioral_effect": "preserve early long-side risk compression",
        },
        "dd_step_1_short": {
            "value": risk_rules["dd_step_1_short"],
            "source_evidence": {"long_episode_share": long_share},
            "mapping_rationale": "Long-dominant ledger history supports tighter first-step short drawdown control.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "contain short-side stress earlier",
        },
        "kill_switch_drawdown": {
            "value": risk_rules["kill_switch_drawdown"],
            "source_evidence": {"max_drawdown_pct": max_dd},
            "mapping_rationale": "Observed drawdown depth argues for preserving a hard stop near legacy PaulWei levels.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "force capital preservation before severe equity impairment",
        },
        "kill_cooldown_bars": {
            "value": risk_rules["kill_cooldown_bars"],
            "source_evidence": {"median_recovery_days": recovery_days},
            "mapping_rationale": "Recovery cadence implies a longer cooldown before re-risking after major damage.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "avoid immediate re-risk after drawdown shock",
        },
        "kill_ramp_bars": {
            "value": risk_rules["kill_ramp_bars"],
            "source_evidence": {"median_recovery_days": recovery_days},
            "mapping_rationale": "If recovery is gradual, risk ramp should also be gradual.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "slowly restore risk after kill-switch recovery",
        },
        "vol_shock_mult": {
            "value": risk_rules["vol_shock_mult"],
            "source_evidence": {"extreme_vol_protective_action_share": protective_share},
            "mapping_rationale": "High protective-action share during high-vol episodes supports a stronger volatility shock haircut.",
            "mapping_confidence": "medium",
            "expected_behavioral_effect": "compress leverage faster during volatility shocks",
        },
    }
    unsupported_mapping = [
        {
            "dimension": item,
            "reason": "Not inferable from public ledger behavior without inventing hidden discretionary logic.",
        }
        for item in unsupported
    ]

    mapping = {
        "generated_at": _ts(),
        "mapping_source": "paulwei_public_archive_behavior_profile",
        "evidence_window": behavior_profile.get("dataset_window", {}),
        "execution_mapping": execution_mapping,
        "risk_mapping": risk_mapping,
        "strategy_mapping": strategy_rules,
        "unsupported_mapping": unsupported_mapping,
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
        "role_candidate": ["regime_prior", "risk_budget_modifier", "dca_guidance_signal", "explainability_context"],
        "prohibited_roles": ["intraday_trigger", "execution_signal", "direction_vote"],
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
    profile["artifact_id"] = _artifact_id(profile)
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
        "artifact_id": profile["artifact_id"],
    }


def write_baseline_mapping(*, behavior_profile: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    mapping = build_baseline_mapping(behavior_profile=behavior_profile)
    mapping["artifact_id"] = _artifact_id(mapping)
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
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": mapping["artifact_id"]}


def write_ahr999_alignment(*, data_root: Path, spot_root: Path, macro_root: Path, output_dir: Path) -> dict[str, Any]:
    payload = build_ahr999_alignment(data_root=data_root, spot_root=spot_root, macro_root=macro_root)
    payload["artifact_id"] = _artifact_id(payload)
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
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "ready": payload.get("ready", False),
        "artifact_id": payload["artifact_id"],
    }


def build_execution_research_review(
    *,
    dataset_contract: dict[str, Any],
    behavior_profile: dict[str, Any],
    mapping: dict[str, Any],
    dataset_contract_path: Path,
    behavior_profile_path: Path,
    mapping_path: Path,
) -> dict[str, Any]:
    payload = {
        "generated_at": _ts(),
        "baseline_family": [
            "current",
            "paulwei_baseline",
            "paulwei_public_archive_baseline",
        ],
        "research_target": "baseline_pk_plus_execution_philosophy_transfer",
        "inventory_build_profile": behavior_profile.get("inventory_build_profile", {}),
        "protective_exit_profile": behavior_profile.get("protective_exit_profile", {}),
        "recovery_profile": behavior_profile.get("recovery_profile", {}),
        "flip_profile": behavior_profile.get("flip_profile", {}),
        "trend_hold_profile": behavior_profile.get("trend_hold_profile", {}),
        "mapping_summary": {
            "mapping_confidence": mapping.get("mapping_confidence"),
            "execution_mapping_keys": sorted((mapping.get("execution_mapping") or {}).keys()),
            "risk_mapping_keys": sorted((mapping.get("risk_mapping") or {}).keys()),
            "unsupported_dimensions": mapping.get("unsupported_dimensions", []),
        },
        "cache_topology": {
            "ledger_behavior_cache": {
                "keyed_by": ["source_contract_id", "source_contract_sha256", "code_commit"],
                "purpose": "reuse deterministic ledger-to-episode reconstruction",
            },
            "slow_signal_review_cache": {
                "keyed_by": ["mapping_id", "ahr_review_id", "split_contract_id"],
                "purpose": "reuse slow-risk guidance reviews without mutating execution truth",
            },
            "baseline_comparison_cache": {
                "keyed_by": ["mapping_id", "split_contract_id", "code_commit"],
                "purpose": "reuse comparison outputs for the same mapping and split contract",
            },
        },
        "scheduler_contract": {
            "tasks": [
                "paulwei_behavior_refresh",
                "paulwei_mapping_refresh",
                "ahr999_guidance_refresh",
                "paulwei_variant_compare",
                "paulwei_promotion_review",
            ],
            "mode": "single_machine_research_track",
            "promote_truth_side_effects": False,
        },
        "recommendation": {
            "track_status": "research_only",
            "intended_use": [
                "execution_philosophy_learning",
                "baseline_pk",
                "slow_risk_alignment_study",
            ],
            "not_allowed": [
                "primary_truth_source",
                "signal_label_supervision",
                "intraday_execution_trigger",
            ],
        },
    }
    payload["lineage"] = _lineage(
        source_contract_path=dataset_contract_path,
        source_contract_id=dataset_contract.get("artifact_id"),
        behavior_profile_path=behavior_profile_path,
        behavior_profile_id=behavior_profile.get("artifact_id"),
        mapping_path=mapping_path,
        mapping_id=mapping.get("artifact_id"),
    )
    payload["artifact_id"] = _artifact_id(payload)
    return payload


def build_ahr999_execution_guidance_review(
    *,
    alignment: dict[str, Any],
    dataset_contract_path: Path,
    dataset_contract_id: str | None,
    behavior_profile_path: Path,
    behavior_profile_id: str | None,
    mapping_path: Path,
    mapping_id: str | None,
    ahr_alignment_path: Path,
) -> dict[str, Any]:
    zone_guidance = {
        "accumulation": {
            "dca_intensity": "high",
            "dca_budget_multiplier": 1.25,
            "long_risk_budget_modifier": 1.10,
            "guidance": "Prefer aggressive accumulation cadence when broader quality filters allow risk.",
        },
        "dca": {
            "dca_intensity": "normal",
            "dca_budget_multiplier": 1.0,
            "long_risk_budget_modifier": 1.0,
            "guidance": "Maintain standard DCA cadence and neutral long-risk budget.",
        },
        "overheated": {
            "dca_intensity": "low",
            "dca_budget_multiplier": 0.25,
            "long_risk_budget_modifier": 0.75,
            "guidance": "Slow DCA materially and avoid expanding slow-risk budgets into overheated zones.",
        },
    }
    mvrv_corr = alignment.get("correlation_to_mvrv", {}).get("pearson_corr")
    cycle_corr = alignment.get("correlation_to_cycle_position", {}).get("pearson_corr")
    overlap = {
        "to_mvrv": mvrv_corr,
        "to_cycle_position": cycle_corr,
        "overlap_conclusion": "high_but_not_alias"
        if mvrv_corr is not None and abs(float(mvrv_corr)) < 0.85
        else "too_close_to_existing_slow_overlays",
    }
    worth_review = bool(
        alignment.get("recommendation", {}).get("worth_admission_review")
        and overlap["overlap_conclusion"] == "high_but_not_alias"
    )
    payload = {
        "generated_at": _ts(),
        "role_candidate": ["regime_prior", "risk_budget_modifier", "dca_guidance_signal", "explainability_context"],
        "prohibited_roles": ["intraday_trigger", "execution_signal", "direction_vote"],
        "zone_thresholds": alignment.get("zone_thresholds", {}),
        "zone_guidance": zone_guidance,
        "zone_behavior_alignment": alignment.get("zone_behavior_alignment", {}),
        "overlap_assessment": overlap,
        "admission_review": {
            "worth_admission_review": worth_review,
            "allowed_if_admitted": ["risk_budget_modifier", "dca_guidance_signal", "regime_prior"],
            "blocked_if_only_alias": ["independent_feature_source", "intraday_trigger"],
        },
        "recommendation": {
            "use_for_research_only": True,
            "eligible_for_slow_risk_path": worth_review,
            "do_not_use_as_intraday_trigger": True,
        },
    }
    payload["lineage"] = _lineage(
        source_contract_path=dataset_contract_path,
        source_contract_id=dataset_contract_id,
        behavior_profile_path=behavior_profile_path,
        behavior_profile_id=behavior_profile_id,
        mapping_path=mapping_path,
        mapping_id=mapping_id,
        ahr_review_path=ahr_alignment_path,
        ahr_review_id=alignment.get("artifact_id"),
    )
    payload["artifact_id"] = _artifact_id(payload)
    return payload


def build_variant_comparison_review(
    *,
    comparison_payload: dict[str, Any],
    mapping: dict[str, Any],
    dataset_contract_path: Path,
    dataset_contract_id: str | None,
    behavior_profile_path: Path,
    behavior_profile_id: str | None,
    mapping_path: Path,
    comparison_run_path: Path | None = None,
) -> dict[str, Any]:
    reports = comparison_payload.get("window_reports", [])
    variants = sorted({str(item.get("variant")) for item in reports})
    summary: dict[str, Any] = {}
    for variant in variants:
        variant_reports = {str(item.get("window_label")): item for item in reports if item.get("variant") == variant}
        dev = variant_reports.get("dev", {})
        hostile_labels = [label for label in variant_reports if label != "dev"]
        hostile_runs = [variant_reports[label] for label in hostile_labels]
        worst_hostile_sharpe = min(
            [float(item.get("metrics", {}).get("sharpe", 0.0) or 0.0) for item in hostile_runs],
            default=None,
        )
        hostile_driver = None
        if hostile_runs:
            hostile_driver = min(
                hostile_runs,
                key=lambda item: float(item.get("metrics", {}).get("sharpe", 0.0) or 0.0),
            ).get("window_label")
        summary[variant] = {
            "dev_metrics": dev.get("metrics", {}),
            "hostile_windows": {item.get("window_label"): item.get("metrics", {}) for item in hostile_runs},
            "worst_window_sharpe": worst_hostile_sharpe,
            "hostile_window_regressed": None,
            "hostile_regression_driver": hostile_driver,
        }

    current = summary.get("current", {})
    legacy = summary.get("paulwei_baseline", {})
    public = summary.get("paulwei_public_archive_baseline", {})
    public_dev = public.get("dev_metrics", {})
    current_dev = current.get("dev_metrics", {})
    legacy_dev = legacy.get("dev_metrics", {})
    hostile_regressed = False
    hostile_driver = public.get("hostile_regression_driver")
    for label, metrics in (public.get("hostile_windows") or {}).items():
        current_metrics = (current.get("hostile_windows") or {}).get(label, {})
        if (
            float(metrics.get("sharpe", 0.0) or 0.0) < float(current_metrics.get("sharpe", 0.0) or 0.0) - 0.25
            or float(metrics.get("max_drawdown", 0.0) or 0.0)
            > float(current_metrics.get("max_drawdown", 0.0) or 0.0) + 0.03
        ):
            hostile_regressed = True
            hostile_driver = label
            break
    summary.setdefault("paulwei_public_archive_baseline", {})["hostile_window_regressed"] = hostile_regressed

    payload = {
        "generated_at": _ts(),
        "baseline_family": ["current", "paulwei_baseline", "paulwei_public_archive_baseline"],
        "window_set": [item.get("label") for item in comparison_payload.get("windows", [])],
        "variant_window_summary": summary,
        "baseline_tiers": {
            "gate_baseline": "current",
            "compat_baseline": "paulwei_baseline",
            "stretch_baseline": "paulwei_public_archive_baseline",
            "current_and_legacy_same_tier": current_dev == legacy_dev,
        },
        "pk_summary": {
            "public_vs_current": {
                "dev_sharpe_delta": round(
                    float(public_dev.get("sharpe", 0.0) or 0.0) - float(current_dev.get("sharpe", 0.0) or 0.0),
                    4,
                ),
                "dev_drawdown_delta": round(
                    float(public_dev.get("max_drawdown", 0.0) or 0.0)
                    - float(current_dev.get("max_drawdown", 0.0) or 0.0),
                    6,
                ),
            },
            "public_vs_paulwei_baseline": {
                "dev_sharpe_delta": round(
                    float(public_dev.get("sharpe", 0.0) or 0.0) - float(legacy_dev.get("sharpe", 0.0) or 0.0),
                    4,
                ),
                "dev_drawdown_delta": round(
                    float(public_dev.get("max_drawdown", 0.0) or 0.0)
                    - float(legacy_dev.get("max_drawdown", 0.0) or 0.0),
                    6,
                ),
            },
        },
        "mapping_constraints": {
            "unsupported_dimensions": mapping.get("unsupported_dimensions", []),
            "mapping_confidence": mapping.get("mapping_confidence"),
        },
        "recommendation": {
            "gate_baseline": "current",
            "stretch_baseline": "paulwei_public_archive_baseline",
            "best_research_baseline": "paulwei_public_archive_baseline"
            if float(public_dev.get("sharpe", 0.0) or 0.0) > float(legacy_dev.get("sharpe", 0.0) or 0.0)
            else "paulwei_baseline",
            "hostile_window_regressed": hostile_regressed,
            "hostile_regression_driver": hostile_driver,
        },
    }
    payload["lineage"] = _lineage(
        source_contract_path=dataset_contract_path,
        source_contract_id=dataset_contract_id,
        behavior_profile_path=behavior_profile_path,
        behavior_profile_id=behavior_profile_id,
        mapping_path=mapping_path,
        mapping_id=mapping.get("artifact_id"),
        comparison_run_path=comparison_run_path,
        comparison_run_id=_artifact_id(comparison_payload),
    )
    payload["artifact_id"] = _artifact_id(payload)
    return payload


def build_promotion_eligibility_review(
    *,
    variant_review: dict[str, Any],
    mapping: dict[str, Any],
    dataset_contract_path: Path,
    dataset_contract_id: str | None,
    behavior_profile_path: Path,
    behavior_profile_id: str | None,
    mapping_path: Path,
    ahr_guidance_path: Path,
    ahr_guidance_id: str | None,
    comparison_run_path: Path | None = None,
) -> dict[str, Any]:
    public_summary = variant_review.get("variant_window_summary", {}).get("paulwei_public_archive_baseline", {})
    current_summary = variant_review.get("variant_window_summary", {}).get("current", {})
    legacy_summary = variant_review.get("variant_window_summary", {}).get("paulwei_baseline", {})
    public_dev = public_summary.get("dev_metrics", {})
    current_dev = current_summary.get("dev_metrics", {})
    legacy_dev = legacy_summary.get("dev_metrics", {})
    blocked_by: list[str] = []
    if float(public_dev.get("sharpe", 0.0) or 0.0) <= float(current_dev.get("sharpe", 0.0) or 0.0):
        blocked_by.append("no_dev_sharpe_advantage_vs_current")
    if float(public_dev.get("sharpe", 0.0) or 0.0) <= float(legacy_dev.get("sharpe", 0.0) or 0.0):
        blocked_by.append("no_dev_sharpe_advantage_vs_paulwei_baseline")
    if float(public_dev.get("max_drawdown", 0.0) or 0.0) > float(current_dev.get("max_drawdown", 0.0) or 0.0) + 0.03:
        blocked_by.append("drawdown_outside_current_band")
    if bool(variant_review.get("recommendation", {}).get("hostile_window_regressed")):
        blocked_by.append("hostile_window_regressed")
    if len(mapping.get("unsupported_dimensions", [])) > 4:
        blocked_by.append("unsupported_mapping_too_large")

    payload = {
        "generated_at": _ts(),
        "baseline_family": ["current", "paulwei_baseline", "paulwei_public_archive_baseline"],
        "promotion_readiness": len(blocked_by) == 0,
        "blocked_by": blocked_by,
        "current_best_research_baseline": variant_review.get("recommendation", {}).get("best_research_baseline"),
        "variant_roles": {
            "current": "authoritative_default_baseline",
            "paulwei_baseline": "frozen_manual_execution_risk_baseline",
            "paulwei_public_archive_baseline": "research_only_mapped_public_ledger_baseline",
        },
        "decision": {
            "status": "authoritative_candidate_review" if len(blocked_by) == 0 else "research_only",
            "reason": "eligible_for_authoritative_candidate_review"
            if len(blocked_by) == 0
            else "insufficient_stability_or_explanatory_confidence",
        },
    }
    payload["lineage"] = _lineage(
        source_contract_path=dataset_contract_path,
        source_contract_id=dataset_contract_id,
        behavior_profile_path=behavior_profile_path,
        behavior_profile_id=behavior_profile_id,
        mapping_path=mapping_path,
        mapping_id=mapping.get("artifact_id"),
        ahr_review_path=ahr_guidance_path,
        ahr_review_id=ahr_guidance_id,
        comparison_run_path=comparison_run_path,
        comparison_run_id=variant_review.get("lineage", {}).get("comparison_run_id"),
    )
    payload["artifact_id"] = _artifact_id(payload)
    return payload


def build_strict_execution_learning_review(
    *,
    execution_review: dict[str, Any],
    variant_review: dict[str, Any],
    promotion_review: dict[str, Any],
    ahr_guidance_review: dict[str, Any],
    dataset_contract_path: Path,
    dataset_contract_id: str | None,
    behavior_profile_path: Path,
    behavior_profile_id: str | None,
    mapping_path: Path,
    mapping_id: str | None,
    ahr_guidance_path: Path,
    comparison_run_path: Path | None = None,
) -> dict[str, Any]:
    inventory = execution_review.get("inventory_build_profile", {})
    protective = execution_review.get("protective_exit_profile", {})
    recovery = execution_review.get("recovery_profile", {})
    flip = execution_review.get("flip_profile", {})
    trend = execution_review.get("trend_hold_profile", {})
    mapping_summary = execution_review.get("mapping_summary", {})

    variant_summary = variant_review.get("variant_window_summary", {})
    current = variant_summary.get("current", {})
    public = variant_summary.get("paulwei_public_archive_baseline", {})
    current_dev = current.get("dev_metrics", {})
    public_dev = public.get("dev_metrics", {})
    current_hostile = current.get("hostile_windows", {})
    public_hostile = public.get("hostile_windows", {})

    learnable_dimensions = [
        {
            "module": "inventory_build_profile",
            "observation_evidence": inventory,
            "maps_to": ["ExecutionKernelConfig.pyramid_initial_pct", "ExecutionKernelConfig.pyramid_add_pct"],
            "difference_vs_current": "public baseline keeps multi-tranche long inventory building with smaller short-side expansion",
            "worth_absorbing": True,
        },
        {
            "module": "protective_exit_profile",
            "observation_evidence": protective,
            "maps_to": ["ExecutionKernelConfig.exit_unwind_pct", "RiskOverlayConfig.vol_shock_mult"],
            "difference_vs_current": "public baseline favors staged de-risking and volatility-shock compression over all-at-once exits",
            "worth_absorbing": True,
        },
        {
            "module": "recovery_profile",
            "observation_evidence": recovery,
            "maps_to": ["RiskOverlayConfig.kill_cooldown_bars", "RiskOverlayConfig.kill_ramp_bars"],
            "difference_vs_current": "public mapping restores risk more gradually after deep drawdown episodes",
            "worth_absorbing": True,
        },
        {
            "module": "flip_profile",
            "observation_evidence": flip,
            "maps_to": ["ExecutionKernelConfig.flip_min_confidence"],
            "difference_vs_current": "public mapping allows flips, but only above a moderate confidence floor",
            "worth_absorbing": True,
        },
        {
            "module": "trend_hold_profile",
            "observation_evidence": trend,
            "maps_to": ["ExecutionKernelConfig.exit_unwind_pct"],
            "difference_vs_current": "public mapping realizes winning trend inventory faster than loss recovery, without abandoning staged exits",
            "worth_absorbing": True,
        },
    ]

    non_transferable_dimensions = [
        {
            "dimension": "discretionary_macro_inputs",
            "reason": "not observable from public fills, orders, or wallet snapshots",
        },
        {
            "dimension": "news_and_narrative_interpretation",
            "reason": "requires hidden discretionary context outside the ledger",
        },
        {
            "dimension": "cross-exchange_context",
            "reason": "BitMEX account history does not reveal the full venue context used by the trader",
        },
        {
            "dimension": "manual conviction scaling beyond observable ledger behavior",
            "reason": "position sizing intent cannot be reconstructed beyond what executed inventory shows",
        },
    ]

    public_advantages = {
        "dev_sharpe_delta_vs_current": round(
            float(public_dev.get("sharpe", 0.0) or 0.0) - float(current_dev.get("sharpe", 0.0) or 0.0),
            4,
        ),
        "dev_drawdown_delta_vs_current": round(
            float(public_dev.get("max_drawdown", 0.0) or 0.0) - float(current_dev.get("max_drawdown", 0.0) or 0.0),
            6,
        ),
        "hostile_2023_08_sharpe_delta_vs_current": round(
            float((public_hostile.get("2023-08") or {}).get("sharpe", 0.0) or 0.0)
            - float((current_hostile.get("2023-08") or {}).get("sharpe", 0.0) or 0.0),
            4,
        ),
        "hostile_2024_11_dd_sharpe_delta_vs_current": round(
            float((public_hostile.get("2024-11 DD") or {}).get("sharpe", 0.0) or 0.0)
            - float((current_hostile.get("2024-11 DD") or {}).get("sharpe", 0.0) or 0.0),
            4,
        ),
    }

    blocker = (promotion_review.get("blocked_by") or [None])[0]
    absorb_next = [
        "long-side multi-tranche inventory building with capped short-side expansion",
        "gradual protective exits and stronger volatility-shock compression",
        "slower drawdown recovery ramp after deep equity damage",
        "moderate flip confirmation floor rather than noisy immediate reversals",
    ]
    retain_research_only = blocker == "drawdown_outside_current_band"

    payload = {
        "generated_at": _ts(),
        "baseline_family": ["current", "paulwei_baseline", "paulwei_public_archive_baseline"],
        "strict_learning_modules": learnable_dimensions,
        "public_baseline_pk_conclusion": {
            "stronger_than_legacy_paulwei": True,
            "stronger_than_current_on_research_metrics": public_advantages["dev_sharpe_delta_vs_current"] > 0.0,
            "primary_strength_drivers": [
                "inventory_build_structure",
                "short_side_expansion_restraint",
                "staged_protective_exit_behavior",
                "volatility_shock_risk_compression",
            ],
            "primary_blocker": blocker,
            "research_only_reason": "dev drawdown remains outside the current baseline band"
            if retain_research_only
            else "promotion blockers remain",
            "public_advantages": public_advantages,
        },
        "learning_boundaries": {
            "non_transferable_dimensions": non_transferable_dimensions,
            "mapping_confidence": mapping_summary.get("mapping_confidence"),
            "unsupported_dimensions": mapping_summary.get("unsupported_dimensions", []),
        },
        "ahr999_role_in_learning_track": {
            "allowed_roles": ahr_guidance_review.get("role_candidate", []),
            "prohibited_roles": ahr_guidance_review.get("prohibited_roles", []),
            "overlap_assessment": ahr_guidance_review.get("overlap_assessment", {}),
            "slow_risk_path_eligible": ahr_guidance_review.get("recommendation", {}).get("eligible_for_slow_risk_path"),
            "conclusion": "use as slow-risk / DCA guidance only; do not promote into intraday execution",
        },
        "next_action_review": {
            "absorb_into_next_pk_round": absorb_next,
            "keep_research_only": retain_research_only,
            "ready_for_authoritative_candidate_review": bool(promotion_review.get("promotion_readiness")),
        },
    }
    payload["lineage"] = _lineage(
        source_contract_path=dataset_contract_path,
        source_contract_id=dataset_contract_id,
        behavior_profile_path=behavior_profile_path,
        behavior_profile_id=behavior_profile_id,
        mapping_path=mapping_path,
        mapping_id=mapping_id,
        ahr_review_path=ahr_guidance_path,
        ahr_review_id=ahr_guidance_review.get("artifact_id"),
        comparison_run_path=comparison_run_path,
        comparison_run_id=variant_review.get("lineage", {}).get("comparison_run_id"),
    )
    payload["artifact_id"] = _artifact_id(payload)
    return payload


def write_execution_research_review(
    *,
    dataset_contract: dict[str, Any],
    behavior_profile: dict[str, Any],
    mapping: dict[str, Any],
    dataset_contract_path: Path,
    behavior_profile_path: Path,
    mapping_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    payload = build_execution_research_review(
        dataset_contract=dataset_contract,
        behavior_profile=behavior_profile,
        mapping=mapping,
        dataset_contract_path=dataset_contract_path,
        behavior_profile_path=behavior_profile_path,
        mapping_path=mapping_path,
    )
    json_path = output_dir / "execution_research_review.json"
    md_path = output_dir / "execution_research_review.md"
    _write_json(json_path, payload)
    lines = [
        "# PaulWei Execution Research Review",
        "",
        f"- artifact_id: `{payload['artifact_id']}`",
        f"- research_target: `{payload['research_target']}`",
        f"- mapping_confidence: `{payload['mapping_summary']['mapping_confidence']}`",
        f"- inventory_build_profile: `{payload['inventory_build_profile']}`",
        f"- protective_exit_profile: `{payload['protective_exit_profile']}`",
        f"- recovery_profile: `{payload['recovery_profile']}`",
        f"- flip_profile: `{payload['flip_profile']}`",
        f"- trend_hold_profile: `{payload['trend_hold_profile']}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": payload["artifact_id"]}


def write_ahr999_execution_guidance_review(
    *,
    alignment: dict[str, Any],
    dataset_contract_path: Path,
    dataset_contract_id: str | None,
    behavior_profile_path: Path,
    behavior_profile_id: str | None,
    mapping_path: Path,
    mapping_id: str | None,
    ahr_alignment_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    payload = build_ahr999_execution_guidance_review(
        alignment=alignment,
        dataset_contract_path=dataset_contract_path,
        dataset_contract_id=dataset_contract_id,
        behavior_profile_path=behavior_profile_path,
        behavior_profile_id=behavior_profile_id,
        mapping_path=mapping_path,
        mapping_id=mapping_id,
        ahr_alignment_path=ahr_alignment_path,
    )
    json_path = output_dir / "ahr999_execution_guidance_review.json"
    md_path = output_dir / "ahr999_execution_guidance_review.md"
    _write_json(json_path, payload)
    lines = [
        "# AHR999 Execution Guidance Review",
        "",
        f"- artifact_id: `{payload['artifact_id']}`",
        f"- role_candidate: `{payload['role_candidate']}`",
        f"- overlap_assessment: `{payload['overlap_assessment']}`",
        f"- admission_review: `{payload['admission_review']}`",
        f"- zone_guidance: `{payload['zone_guidance']}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": payload["artifact_id"]}


def write_variant_comparison_review(
    *,
    comparison_payload: dict[str, Any],
    mapping: dict[str, Any],
    dataset_contract_path: Path,
    dataset_contract_id: str | None,
    behavior_profile_path: Path,
    behavior_profile_id: str | None,
    mapping_path: Path,
    comparison_run_path: Path | None,
    output_dir: Path,
) -> dict[str, Any]:
    payload = build_variant_comparison_review(
        comparison_payload=comparison_payload,
        mapping=mapping,
        dataset_contract_path=dataset_contract_path,
        dataset_contract_id=dataset_contract_id,
        behavior_profile_path=behavior_profile_path,
        behavior_profile_id=behavior_profile_id,
        mapping_path=mapping_path,
        comparison_run_path=comparison_run_path,
    )
    json_path = output_dir / "variant_comparison_review.json"
    md_path = output_dir / "variant_comparison_review.md"
    _write_json(json_path, payload)
    lines = [
        "# Variant Comparison Review",
        "",
        f"- artifact_id: `{payload['artifact_id']}`",
        f"- baseline_family: `{payload['baseline_family']}`",
        f"- pk_summary: `{payload['pk_summary']}`",
        f"- recommendation: `{payload['recommendation']}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": payload["artifact_id"]}


def write_promotion_eligibility_review(
    *,
    variant_review: dict[str, Any],
    mapping: dict[str, Any],
    dataset_contract_path: Path,
    dataset_contract_id: str | None,
    behavior_profile_path: Path,
    behavior_profile_id: str | None,
    mapping_path: Path,
    ahr_guidance_path: Path,
    ahr_guidance_id: str | None,
    comparison_run_path: Path | None,
    output_dir: Path,
) -> dict[str, Any]:
    payload = build_promotion_eligibility_review(
        variant_review=variant_review,
        mapping=mapping,
        dataset_contract_path=dataset_contract_path,
        dataset_contract_id=dataset_contract_id,
        behavior_profile_path=behavior_profile_path,
        behavior_profile_id=behavior_profile_id,
        mapping_path=mapping_path,
        ahr_guidance_path=ahr_guidance_path,
        ahr_guidance_id=ahr_guidance_id,
        comparison_run_path=comparison_run_path,
    )
    json_path = output_dir / "promotion_eligibility_review.json"
    md_path = output_dir / "promotion_eligibility_review.md"
    _write_json(json_path, payload)
    lines = [
        "# Promotion Eligibility Review",
        "",
        f"- artifact_id: `{payload['artifact_id']}`",
        f"- promotion_readiness: `{payload['promotion_readiness']}`",
        f"- blocked_by: `{payload['blocked_by']}`",
        f"- current_best_research_baseline: `{payload['current_best_research_baseline']}`",
        f"- decision: `{payload['decision']}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": payload["artifact_id"]}


def write_strict_execution_learning_review(
    *,
    execution_review: dict[str, Any],
    variant_review: dict[str, Any],
    promotion_review: dict[str, Any],
    ahr_guidance_review: dict[str, Any],
    dataset_contract_path: Path,
    dataset_contract_id: str | None,
    behavior_profile_path: Path,
    behavior_profile_id: str | None,
    mapping_path: Path,
    mapping_id: str | None,
    ahr_guidance_path: Path,
    comparison_run_path: Path | None,
    output_dir: Path,
) -> dict[str, Any]:
    payload = build_strict_execution_learning_review(
        execution_review=execution_review,
        variant_review=variant_review,
        promotion_review=promotion_review,
        ahr_guidance_review=ahr_guidance_review,
        dataset_contract_path=dataset_contract_path,
        dataset_contract_id=dataset_contract_id,
        behavior_profile_path=behavior_profile_path,
        behavior_profile_id=behavior_profile_id,
        mapping_path=mapping_path,
        mapping_id=mapping_id,
        ahr_guidance_path=ahr_guidance_path,
        comparison_run_path=comparison_run_path,
    )
    json_path = output_dir / "strict_execution_learning_review.json"
    md_path = output_dir / "strict_execution_learning_review.md"
    _write_json(json_path, payload)
    lines = [
        "# Strict Execution Learning Review",
        "",
        f"- artifact_id: `{payload['artifact_id']}`",
        f"- primary_blocker: `{payload['public_baseline_pk_conclusion']['primary_blocker']}`",
        f"- public_advantages: `{payload['public_baseline_pk_conclusion']['public_advantages']}`",
        f"- absorb_into_next_pk_round: `{payload['next_action_review']['absorb_into_next_pk_round']}`",
        f"- keep_research_only: `{payload['next_action_review']['keep_research_only']}`",
        f"- ahr999_role_in_learning_track: `{payload['ahr999_role_in_learning_track']}`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "artifact_id": payload["artifact_id"]}


def load_paulwei_public_archive_variant(*, mapping_path: Path) -> dict[str, Any] | None:
    if not mapping_path.exists():
        return None
    try:
        return _read_json(mapping_path)
    except Exception:
        return None


def paulwei_public_archive_variant_spec(
    *,
    mapping_path: Path,
    variant_name: str = "paulwei_public_archive_baseline",
    variant_label: str = "PaulWei Public Archive Baseline",
) -> dict[str, Any] | None:
    from zenquant.research.comparison import paulwei_execution_baseline, paulwei_risk_baseline

    payload = load_paulwei_public_archive_variant(mapping_path=mapping_path)
    if not payload:
        return None
    risk = replace_config(paulwei_risk_baseline(), payload.get("risk_config_overrides", {}))
    execution = replace_config(paulwei_execution_baseline(), payload.get("execution_config_overrides", {}))
    strategy_overrides = payload.get("strategy_config_overrides", {})
    strategy = replace_config(ARCStrategyConfig(), strategy_overrides) if strategy_overrides else None
    return {
        "name": variant_name,
        "label": variant_label,
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
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle) - 1


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
