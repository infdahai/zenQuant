"""Research-only large Deribit option-flow review helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from zenquant.data.deribit_block_flow import DeribitBlockFlowRow


def filter_large_option_flow_rows(
    rows: list[DeribitBlockFlowRow],
    *,
    min_amount: float = 25.0,
    min_underlying_notional_usd: float | None = None,
    require_block_trade: bool = False,
) -> list[DeribitBlockFlowRow]:
    """Return leg-level option trades that pass deterministic large-flow gates."""
    large_rows: list[DeribitBlockFlowRow] = []
    for row in rows:
        if require_block_trade and not row.is_block_trade:
            continue
        if abs(row.amount) < min_amount:
            continue
        if min_underlying_notional_usd is not None:
            notional_usd = _underlying_notional_usd(row)
            if notional_usd is None or notional_usd < min_underlying_notional_usd:
                continue
        large_rows.append(row)
    return sorted(large_rows, key=lambda row: (row.ts_ns, row.instrument_name, row.source, row.trade_id or ""))


def build_large_option_flow_records(
    rows: list[DeribitBlockFlowRow],
    *,
    min_amount: float = 25.0,
    min_underlying_notional_usd: float | None = None,
    require_block_trade: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Build JSON-safe records for large Deribit option-flow review."""
    large_rows = filter_large_option_flow_rows(
        rows,
        min_amount=min_amount,
        min_underlying_notional_usd=min_underlying_notional_usd,
        require_block_trade=require_block_trade,
    )
    records = [_large_option_flow_record(row) for row in reversed(large_rows)]
    if limit is not None:
        return records[: max(0, limit)]
    return records


def _underlying_notional_usd(row: DeribitBlockFlowRow) -> float | None:
    if row.index_price is None:
        return None
    return abs(row.amount) * row.index_price


def _premium_usd(row: DeribitBlockFlowRow) -> float | None:
    if row.price is None or row.index_price is None:
        return None
    return abs(row.amount) * row.price * row.index_price


def _large_option_flow_record(row: DeribitBlockFlowRow) -> dict[str, Any]:
    expiry = None
    if row.expiry_ts_ms is not None:
        expiry = datetime.fromtimestamp(row.expiry_ts_ms / 1000, tz=UTC).date().isoformat()
    return {
        "ts": datetime.fromtimestamp(row.ts_ns / 1_000_000_000, tz=UTC).isoformat(),
        "trade_id": row.trade_id,
        "block_trade_id": row.block_trade_id,
        "combo_id": row.combo_id,
        "combo_trade_id": row.combo_trade_id,
        "instrument_name": row.instrument_name,
        "option_type": row.option_type,
        "expiry": expiry,
        "strike": row.strike,
        "direction": row.direction,
        "amount": row.amount,
        "underlying_notional_usd": _underlying_notional_usd(row),
        "premium_usd": _premium_usd(row),
        "price": row.price,
        "index_price": row.index_price,
        "mark_price": row.mark_price,
        "iv": row.iv,
        "liquidity": row.liquidity,
        "is_block_trade": row.is_block_trade,
        "is_combo_leg": row.is_combo_leg,
        "leg_count": row.leg_count,
        "source": row.source,
    }
