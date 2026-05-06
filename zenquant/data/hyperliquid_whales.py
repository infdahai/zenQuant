"""Hyperliquid public whale-position collection.

The collector is deliberately watchlist based. Public account state can be
queried by address, but discovering "whales" is a separate source-admission
problem and should not be hidden inside the ingestion path.
"""

from __future__ import annotations

import json
import math
import re
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from zenquant.historical import write_parquet_rows

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass(slots=True, frozen=True)
class HyperliquidWhaleAccount:
    name: str
    address: str
    notes: str | None = None


@dataclass(slots=True, frozen=True)
class HyperliquidWhaleAccountSnapshot:
    observed_at_ns: int
    account_name: str
    address: str
    account_value_usd: float | None
    total_margin_used_usd: float | None
    total_notional_position_usd: float | None
    total_raw_usd: float | None
    withdrawable_usd: float | None
    position_count: int
    gross_position_value_usd: float
    net_position_value_usd: float
    source: str = "hyperliquid_clearinghouse_state"


@dataclass(slots=True, frozen=True)
class HyperliquidWhalePositionSnapshot:
    observed_at_ns: int
    account_name: str
    address: str
    coin: str
    side: str
    signed_size: float
    entry_px: float | None
    position_value_usd: float | None
    signed_position_value_usd: float | None
    unrealized_pnl_usd: float | None
    return_on_equity: float | None
    margin_used_usd: float | None
    liquidation_px: float | None
    leverage_type: str | None
    leverage_value: float | None
    max_long_size: float | None
    max_short_size: float | None
    raw_position_json: str
    source: str = "hyperliquid_clearinghouse_state"


def _now_ns() -> int:
    return int(datetime.now(UTC).timestamp() * 1_000_000_000)


def _post_info(payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        HYPERLIQUID_INFO_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "zenquant/1.0"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_address(address: str) -> str:
    normalized = address.strip().lower()
    if not ADDRESS_PATTERN.match(normalized):
        raise ValueError(f"invalid Hyperliquid address: {address}")
    return normalized


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _signed_position_value(signed_size: float, position_value: float | None) -> float | None:
    if position_value is None:
        return None
    if signed_size > 0:
        return abs(position_value)
    if signed_size < 0:
        return -abs(position_value)
    return 0.0


def _side(signed_size: float) -> str:
    if signed_size > 0:
        return "LONG"
    if signed_size < 0:
        return "SHORT"
    return "FLAT"


def load_whale_watchlist(path: Path) -> list[HyperliquidWhaleAccount]:
    """Load enabled accounts from a TOML watchlist."""

    if not path.exists():
        return []
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("accounts", [])
    if not isinstance(rows, list):
        raise ValueError("hyperliquid whale watchlist must use [[accounts]] entries")

    accounts: list[HyperliquidWhaleAccount] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not bool(row.get("enabled", True)):
            continue
        address = _normalize_address(str(row.get("address", "")))
        name = str(row.get("name") or f"account_{index + 1}")
        notes = row.get("notes")
        accounts.append(HyperliquidWhaleAccount(name=name, address=address, notes=str(notes) if notes else None))
    return accounts


def parse_account_spec(value: str) -> HyperliquidWhaleAccount:
    """Parse CLI account specs in either NAME=0x... or 0x... form."""

    if "=" in value:
        name, address = value.split("=", 1)
        return HyperliquidWhaleAccount(name=name.strip() or "account", address=_normalize_address(address))
    address = _normalize_address(value)
    return HyperliquidWhaleAccount(name=address[:10], address=address)


def fetch_clearinghouse_state(address: str, *, dex: str | None = None) -> dict[str, Any]:
    """Fetch a public Hyperliquid account state by master or sub-account address."""

    payload: dict[str, Any] = {"type": "clearinghouseState", "user": _normalize_address(address)}
    if dex:
        payload["dex"] = dex
    state = _post_info(payload)
    if not isinstance(state, dict):
        raise RuntimeError("hyperliquid_clearinghouse_state_unexpected_shape")
    return state


def parse_clearinghouse_state(
    account: HyperliquidWhaleAccount,
    state: dict[str, Any],
    *,
    observed_at_ns: int,
    min_abs_position_value_usd: float = 0.0,
    coin_filter: set[str] | None = None,
) -> tuple[HyperliquidWhaleAccountSnapshot, list[HyperliquidWhalePositionSnapshot]]:
    """Normalize a clearinghouseState payload into account and position snapshots."""

    positions: list[HyperliquidWhalePositionSnapshot] = []
    total_gross = 0.0
    total_net = 0.0
    nonzero_count = 0

    for row in state.get("assetPositions") or []:
        if not isinstance(row, dict):
            continue
        position = row.get("position") if isinstance(row.get("position"), dict) else row
        if not isinstance(position, dict):
            continue
        coin = str(position.get("coin") or "").upper()
        if coin_filter is not None and coin not in coin_filter:
            continue

        signed_size = _safe_float(position.get("szi")) or 0.0
        if abs(signed_size) <= 0.0:
            continue
        position_value = _safe_float(position.get("positionValue"))
        signed_value = _signed_position_value(signed_size, position_value)
        if position_value is not None:
            total_gross += abs(position_value)
        if signed_value is not None:
            total_net += signed_value
        nonzero_count += 1

        if position_value is not None and abs(position_value) < min_abs_position_value_usd:
            continue

        leverage = position.get("leverage") if isinstance(position.get("leverage"), dict) else {}
        max_trade_sizes = position.get("maxTradeSzs") if isinstance(position.get("maxTradeSzs"), list) else []
        positions.append(
            HyperliquidWhalePositionSnapshot(
                observed_at_ns=observed_at_ns,
                account_name=account.name,
                address=account.address,
                coin=coin,
                side=_side(signed_size),
                signed_size=signed_size,
                entry_px=_safe_float(position.get("entryPx")),
                position_value_usd=position_value,
                signed_position_value_usd=signed_value,
                unrealized_pnl_usd=_safe_float(position.get("unrealizedPnl")),
                return_on_equity=_safe_float(position.get("returnOnEquity")),
                margin_used_usd=_safe_float(position.get("marginUsed")),
                liquidation_px=_safe_float(position.get("liquidationPx")),
                leverage_type=str(leverage.get("type")) if leverage.get("type") is not None else None,
                leverage_value=_safe_float(leverage.get("value")),
                max_long_size=_safe_float(max_trade_sizes[0]) if len(max_trade_sizes) >= 1 else None,
                max_short_size=_safe_float(max_trade_sizes[1]) if len(max_trade_sizes) >= 2 else None,
                raw_position_json=json.dumps(position, sort_keys=True),
            )
        )

    summary = state.get("marginSummary") if isinstance(state.get("marginSummary"), dict) else {}
    cross_summary = state.get("crossMarginSummary") if isinstance(state.get("crossMarginSummary"), dict) else {}
    account_snapshot = HyperliquidWhaleAccountSnapshot(
        observed_at_ns=observed_at_ns,
        account_name=account.name,
        address=account.address,
        account_value_usd=_safe_float(summary.get("accountValue") or cross_summary.get("accountValue")),
        total_margin_used_usd=_safe_float(summary.get("totalMarginUsed") or cross_summary.get("totalMarginUsed")),
        total_notional_position_usd=_safe_float(summary.get("totalNtlPos") or cross_summary.get("totalNtlPos")),
        total_raw_usd=_safe_float(summary.get("totalRawUsd") or cross_summary.get("totalRawUsd")),
        withdrawable_usd=_safe_float(state.get("withdrawable")),
        position_count=nonzero_count,
        gross_position_value_usd=round(total_gross, 8),
        net_position_value_usd=round(total_net, 8),
    )
    return account_snapshot, positions


def collect_hyperliquid_whale_positions(
    accounts: list[HyperliquidWhaleAccount],
    *,
    min_abs_position_value_usd: float = 0.0,
    coins: set[str] | None = None,
    dex: str | None = None,
    observed_at_ns: int | None = None,
) -> tuple[list[HyperliquidWhaleAccountSnapshot], list[HyperliquidWhalePositionSnapshot], list[dict[str, str]]]:
    observed_ns = _now_ns() if observed_at_ns is None else observed_at_ns
    account_snapshots: list[HyperliquidWhaleAccountSnapshot] = []
    position_snapshots: list[HyperliquidWhalePositionSnapshot] = []
    errors: list[dict[str, str]] = []

    for account in accounts:
        try:
            state = fetch_clearinghouse_state(account.address, dex=dex)
            account_snapshot, positions = parse_clearinghouse_state(
                account,
                state,
                observed_at_ns=observed_ns,
                min_abs_position_value_usd=min_abs_position_value_usd,
                coin_filter={coin.upper() for coin in coins} if coins else None,
            )
        except Exception as exc:
            errors.append({"account_name": account.name, "address": account.address, "error": str(exc)})
            continue
        account_snapshots.append(account_snapshot)
        position_snapshots.extend(positions)
    return account_snapshots, position_snapshots, errors


def write_whale_snapshots(
    root: Path,
    *,
    account_snapshots: list[HyperliquidWhaleAccountSnapshot],
    position_snapshots: list[HyperliquidWhalePositionSnapshot],
) -> list[Path]:
    paths: list[Path] = []
    observed_at_ns = None
    if account_snapshots:
        observed_at_ns = account_snapshots[0].observed_at_ns
    elif position_snapshots:
        observed_at_ns = position_snapshots[0].observed_at_ns
    if observed_at_ns is None:
        return paths

    observed_dt = datetime.fromtimestamp(observed_at_ns / 1_000_000_000, tz=UTC)
    partition = observed_dt.strftime("%Y-%m-%d")
    stamp = observed_dt.strftime("%Y%m%dT%H%M%SZ")
    if account_snapshots:
        account_path = root / "accounts" / partition / f"accounts_{stamp}.parquet"
        write_parquet_rows(account_path, [asdict(row) for row in account_snapshots])
        paths.append(account_path)
    if position_snapshots:
        position_path = root / "positions" / partition / f"positions_{stamp}.parquet"
        write_parquet_rows(position_path, [asdict(row) for row in position_snapshots])
        paths.append(position_path)
    return paths


def collect_and_write_hyperliquid_whales(
    *,
    accounts: list[HyperliquidWhaleAccount],
    root: Path,
    min_abs_position_value_usd: float = 0.0,
    coins: set[str] | None = None,
    dex: str | None = None,
) -> dict[str, Any]:
    observed_at_ns = _now_ns()
    if not accounts:
        status = {
            "last_success_ts": datetime.fromtimestamp(observed_at_ns / 1_000_000_000, tz=UTC).isoformat(),
            "account_count": 0,
            "position_count": 0,
            "blocked_by": "hyperliquid_whale_watchlist_empty",
            "detail": "no Hyperliquid whale accounts configured",
            "source": "hyperliquid_clearinghouse_state",
        }
        _write_status(root, status)
        return status

    account_snapshots, position_snapshots, errors = collect_hyperliquid_whale_positions(
        accounts,
        min_abs_position_value_usd=min_abs_position_value_usd,
        coins=coins,
        dex=dex,
        observed_at_ns=observed_at_ns,
    )
    paths = write_whale_snapshots(
        root,
        account_snapshots=account_snapshots,
        position_snapshots=position_snapshots,
    )
    blocked_by = None
    if errors and not account_snapshots:
        blocked_by = "hyperliquid_whale_all_accounts_failed"
    elif errors:
        blocked_by = "hyperliquid_whale_partial_account_failure"
    status = {
        "last_success_ts": datetime.fromtimestamp(observed_at_ns / 1_000_000_000, tz=UTC).isoformat(),
        "account_count": len(account_snapshots),
        "configured_account_count": len(accounts),
        "position_count": len(position_snapshots),
        "min_abs_position_value_usd": min_abs_position_value_usd,
        "coins": sorted(coins) if coins else [],
        "blocked_by": blocked_by,
        "detail": "Hyperliquid public whale-position snapshots collected",
        "paths": [str(path) for path in paths],
        "errors": errors,
        "source": "hyperliquid_clearinghouse_state",
    }
    _write_status(root, status)
    return status


def _write_status(root: Path, status: dict[str, Any]) -> Path:
    path = root / "_collector_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return path
