"""Pure point-in-time BTC OptionsState calculations.

The module performs no I/O and has no strategy or execution dependencies.
Deribit ``mark_iv`` values are normalized from percent to annualized decimal
volatility before any calculation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from zenquant.data._timestamp import lossless_timestamp
from zenquant.domain import OptionsState

FOUR_HOURS_NS = 4 * 60 * 60 * 1_000_000_000
DAY_NS = 24 * 60 * 60 * 1_000_000_000
CALCULATION_VERSION = "options-state-v2"
CALCULATION_METHOD = (
    "mark_iv_unit=percent_or_decimal;normalized_to_annualized_decimal;"
    "nearest_expiry_atm_oi_weighted;"
    "black76_forward=index_price;delta_selection=per_leg_selection_recorded;"
    "25d_skew=put_minus_call;25d_risk_reversal=call_minus_put;"
    "gex_call_positive_put_negative"
)
GEX_NORMALIZATION = "net_gex/total_absolute_gex"


@dataclass(frozen=True, slots=True)
class DeltaIvSelection:
    """Auditable result of interpolation at absolute forward delta 0.25."""

    iv: float
    method: str
    lower_delta: float
    upper_delta: float


def _finite_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def black76_forward_delta(
    forward: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    option_type: str,
) -> float | None:
    """Return undiscounted Black-76 forward delta.

    Calls are in ``[0, 1]`` and puts in ``[-1, 0]``. The chain's index or
    underlying price is deliberately used as the point-in-time forward proxy.
    """
    if forward <= 0 or strike <= 0 or time_to_expiry_years <= 0 or volatility <= 0:
        return None
    sigma_sqrt_t = volatility * math.sqrt(time_to_expiry_years)
    if sigma_sqrt_t <= 0:
        return None
    d1 = (math.log(forward / strike) + 0.5 * volatility**2 * time_to_expiry_years) / sigma_sqrt_t
    call_delta = _normal_cdf(d1)
    if option_type == "call":
        return call_delta
    if option_type == "put":
        return call_delta - 1.0
    return None


def interpolate_25_delta_iv(points: Sequence[Mapping[str, float]]) -> DeltaIvSelection | None:
    """Interpolate IV at absolute delta 0.25 from valid chain coordinates."""
    target = 0.25
    cleaned: list[tuple[float, float, float]] = []
    for point in points:
        delta = _finite_float(point.get("delta_abs"))
        iv = _finite_float(point.get("iv"))
        strike = _finite_float(point.get("strike"))
        if delta is None or iv is None or strike is None or not 0 < delta < 1 or iv <= 0:
            continue
        cleaned.append((delta, iv, strike))
    cleaned.sort(key=lambda item: (item[0], item[2], item[1]))
    if not cleaned:
        return None
    for delta, iv, _strike in cleaned:
        if math.isclose(delta, target, abs_tol=1e-12):
            return DeltaIvSelection(iv=iv, method="exact_delta", lower_delta=delta, upper_delta=delta)
    lower = [point for point in cleaned if point[0] < target]
    upper = [point for point in cleaned if point[0] > target]
    if lower and upper:
        low = lower[-1]
        high = upper[0]
        weight = (target - low[0]) / (high[0] - low[0])
        iv = low[1] + weight * (high[1] - low[1])
        return DeltaIvSelection(
            iv=iv,
            method="linear_delta_interpolation",
            lower_delta=low[0],
            upper_delta=high[0],
        )
    nearest = min(cleaned, key=lambda item: (abs(item[0] - target), item[2]))
    if abs(nearest[0] - target) > 0.15:
        return None
    return DeltaIvSelection(
        iv=nearest[1],
        method="nearest_delta",
        lower_delta=nearest[0],
        upper_delta=nearest[0],
    )


def _canonical_scalar(value: object) -> object:
    if hasattr(value, "item"):
        value = value.item()  # type: ignore[union-attr]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def _canonical_row(row: Mapping[str, Any]) -> dict[str, object]:
    return {str(key): _canonical_scalar(row[key]) for key in sorted(row)}


def _canonical_row_text(row: Mapping[str, Any]) -> str:
    return json.dumps(_canonical_row(row), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _prepare_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    history: bool = False,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...], int, list[str]]:
    """Canonicalize rows, resolve duplicate instruments, and flag bad data."""
    prefix = "OPTIONS_HISTORY" if history else "OPTIONS"
    raw: list[dict[str, Any]] = []
    flags: list[str] = []
    for row in rows:
        try:
            raw.append(dict(row))
        except (TypeError, ValueError):
            flags.append(f"{prefix}_MALFORMED_ROW")
    raw_ordered = tuple(sorted(raw, key=_canonical_row_text))

    currencies = {str(row.get("currency", "")).upper() for row in raw_ordered if row.get("currency") is not None}
    if currencies - {"BTC"}:
        raise ValueError("OptionsState is a BTC-only contract")
    if raw_ordered and (not currencies or any(not str(row.get("currency", "")).strip() for row in raw_ordered)):
        flags.append(f"{prefix}_MALFORMED_CURRENCY")

    timestamps: list[int] = []
    bad_timestamp = False
    for row in raw_ordered:
        value = lossless_timestamp(row.get("snapshot_ts_ns"))
        if value is None:
            bad_timestamp = True
        else:
            timestamps.append(value)
    unique_timestamps = set(timestamps)
    if bad_timestamp or len(unique_timestamps) != 1 or len(timestamps) != len(raw_ordered):
        source_ns = 0
        if raw_ordered:
            flags.append(f"{prefix}_MALFORMED_SOURCE_TIMESTAMP")
    else:
        source_ns = unique_timestamps.pop()

    groups: dict[tuple[object, ...], list[dict[str, Any]]] = {}
    for row in raw_ordered:
        instrument_name = str(row.get("instrument_name", "")).strip()
        if instrument_name:
            key: tuple[object, ...] = ("instrument", instrument_name)
        else:
            key = (
                "coordinates",
                _canonical_scalar(row.get("expiry_ts_ms")),
                _canonical_scalar(row.get("strike")),
                str(row.get("option_type", "")).lower(),
            )
        groups.setdefault(key, []).append(row)
    selected: list[dict[str, Any]] = []
    for key in sorted(groups, key=lambda value: repr(value)):
        candidates = groups[key]
        if len(candidates) > 1:
            flags.append(f"{prefix}_DUPLICATE_INSTRUMENTS")
        selected.append(min(candidates, key=_canonical_row_text))
    selected.sort(key=_canonical_row_text)

    for row in selected:
        unit = str(row.get("mark_iv_unit", "percent")).lower()
        if unit not in {"percent", "pct", "decimal", "annualized_decimal"}:
            flags.append(f"{prefix}_MALFORMED_IV_UNIT")
        if _normalized_iv(row) is None:
            flags.append(f"{prefix}_MALFORMED_IV")
        oi = _finite_float(row.get("open_interest"))
        if oi is None or oi < 0:
            flags.append(f"{prefix}_MALFORMED_OPEN_INTEREST")
        strike = _finite_float(row.get("strike"))
        if strike is None or strike <= 0:
            flags.append(f"{prefix}_MALFORMED_STRIKE")
        expiry = lossless_timestamp(row.get("expiry_ts_ms"))
        if expiry is None:
            flags.append(f"{prefix}_MALFORMED_EXPIRY")
        if str(row.get("option_type", "")).lower() not in {"call", "put"}:
            flags.append(f"{prefix}_MALFORMED_OPTION_TYPE")
        underlying = _finite_float(row.get("underlying_price"))
        if underlying is None or underlying <= 0:
            flags.append(f"{prefix}_MALFORMED_UNDERLYING")
        if "gamma" in row and _finite_float(row.get("gamma")) is None:
            flags.append(f"{prefix}_MALFORMED_GREEKS")
    return raw_ordered, tuple(selected), source_ns, sorted(set(flags))


def _spot(rows: Sequence[Mapping[str, Any]]) -> float:
    spots = sorted(
        value for row in rows if (value := _finite_float(row.get("underlying_price"))) is not None and value > 0
    )
    return spots[len(spots) // 2] if spots else 0.0


def _normalized_iv(row: Mapping[str, Any]) -> float | None:
    iv = _finite_float(row.get("mark_iv"))
    if iv is None or iv <= 0:
        return None
    unit = str(row.get("mark_iv_unit", "percent")).lower()
    if unit in {"decimal", "annualized_decimal"}:
        return iv if iv <= 5 else None
    if unit in {"percent", "pct"}:
        return iv / 100.0 if iv <= 500 else None
    return None


def _dte(row: Mapping[str, Any], source_timestamp_ns: int) -> float | None:
    expiry_ms = lossless_timestamp(row.get("expiry_ts_ms"))
    if expiry_ms is None:
        return None
    value = (expiry_ms * 1_000_000 - source_timestamp_ns) / DAY_NS
    return value if value > 0 else None


def _selected_expiry_ts_ms(
    rows: Sequence[Mapping[str, Any]],
    source_timestamp_ns: int,
    target_days: int,
) -> int | None:
    if source_timestamp_ns <= 0:
        return None
    max_distance = {7: 5.0, 30: 15.0, 90: 35.0}[target_days]
    expiries: dict[int, float] = {}
    for row in rows:
        expiry = lossless_timestamp(row.get("expiry_ts_ms"))
        dte = _dte(row, source_timestamp_ns)
        if expiry is not None and dte is not None:
            expiries[expiry] = dte
    eligible = [item for item in expiries.items() if abs(item[1] - target_days) <= max_distance]
    if not eligible:
        return None
    expiry_ms, _ = min(eligible, key=lambda item: (abs(item[1] - target_days), item[0]))
    return expiry_ms


def _expiry_rows(
    rows: Sequence[Mapping[str, Any]],
    source_timestamp_ns: int,
    target_days: int,
) -> list[Mapping[str, Any]]:
    expiry_ms = _selected_expiry_ts_ms(rows, source_timestamp_ns, target_days)
    if expiry_ms is None:
        return []
    return [row for row in rows if lossless_timestamp(row.get("expiry_ts_ms")) == expiry_ms]


def _atm_iv(rows: Sequence[Mapping[str, Any]], spot: float) -> float | None:
    candidates: list[tuple[float, str, float, float]] = []
    for row in rows:
        strike = _finite_float(row.get("strike"))
        iv = _normalized_iv(row)
        oi = _finite_float(row.get("open_interest"))
        option_type = str(row.get("option_type", "")).lower()
        if strike is None or iv is None or oi is None or oi <= 0 or option_type not in {"call", "put"}:
            continue
        candidates.append((abs(strike - spot), option_type, iv, oi))
    if not candidates:
        return None
    min_distance = min(item[0] for item in candidates)
    nearest = [item for item in candidates if math.isclose(item[0], min_distance, abs_tol=1e-9)]
    weight = sum(item[3] for item in nearest)
    return sum(item[2] * item[3] for item in nearest) / weight if weight > 0 else None


def _delta_iv(
    rows: Sequence[Mapping[str, Any]],
    source_timestamp_ns: int,
    spot: float,
    option_type: str,
) -> DeltaIvSelection | None:
    points: list[dict[str, float]] = []
    for row in rows:
        if str(row.get("option_type", "")).lower() != option_type:
            continue
        strike = _finite_float(row.get("strike"))
        iv = _normalized_iv(row)
        dte = _dte(row, source_timestamp_ns)
        if strike is None or iv is None or dte is None:
            continue
        delta = black76_forward_delta(spot, strike, dte / 365.0, iv, option_type)
        if delta is not None:
            points.append({"delta_abs": abs(delta), "iv": iv, "strike": strike})
    return interpolate_25_delta_iv(points)


def _moneyness_proxy(rows: Sequence[Mapping[str, Any]], spot: float) -> float | None:
    def nearest_iv(option_type: str, target_strike: float) -> float | None:
        candidates: list[tuple[float, float, float]] = []
        for row in rows:
            if str(row.get("option_type", "")).lower() != option_type:
                continue
            strike = _finite_float(row.get("strike"))
            iv = _normalized_iv(row)
            if strike is not None and iv is not None:
                candidates.append((abs(strike - target_strike), strike, iv))
        return min(candidates, default=(0.0, 0.0, math.nan))[2] if candidates else None

    put_iv = nearest_iv("put", 0.9 * spot)
    call_iv = nearest_iv("call", 1.1 * spot)
    if put_iv is None or call_iv is None:
        return None
    return put_iv - call_iv


def _oi_concentrations(rows: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    by_strike: dict[float, float] = {}
    by_expiry: dict[int, float] = {}
    total = 0.0
    for row in rows:
        strike = _finite_float(row.get("strike"))
        expiry = lossless_timestamp(row.get("expiry_ts_ms"))
        oi = _finite_float(row.get("open_interest"))
        if strike is None or expiry is None or oi is None or oi <= 0:
            continue
        by_strike[strike] = by_strike.get(strike, 0.0) + oi
        by_expiry[expiry] = by_expiry.get(expiry, 0.0) + oi
        total += oi
    if total <= 0:
        return 0.0, 0.0
    return max(by_strike.values()) / total, max(by_expiry.values()) / total


def _gex_fields(
    rows: Sequence[Mapping[str, Any]], source_timestamp_ns: int, spot: float
) -> tuple[float, float | None, float | None, float | None, float | None, bool]:
    if not rows or spot <= 0:
        return 0.0, None, None, None, None, False
    # Lazy import prevents the research package's historical runner from
    # creating a data-package import cycle during application bootstrap.
    from zenquant.research.gex import compute_gex_profile

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        oi = _finite_float(row.get("open_interest"))
        strike = _finite_float(row.get("strike"))
        expiry = lossless_timestamp(row.get("expiry_ts_ms"))
        iv = _normalized_iv(row)
        option_type = str(row.get("option_type", "")).lower()
        if (
            oi is None
            or oi < 0
            or strike is None
            or strike <= 0
            or expiry is None
            or iv is None
            or option_type not in {"call", "put"}
        ):
            continue
        normalized_rows.append(
            {
                "snapshot_ts_ns": source_timestamp_ns,
                "strike": strike,
                "option_type": option_type,
                "open_interest": oi,
                # The legacy helper consumes Deribit percent units.
                "mark_iv": iv * 100.0,
                "expiry_ts_ms": expiry,
                "underlying_price": spot,
            }
        )
    if not normalized_rows or source_timestamp_ns <= 0:
        return 0.0, None, None, None, None, False
    profile = compute_gex_profile(
        normalized_rows,
        spot=spot,
        snapshot_ts_ns=source_timestamp_ns,
        max_dte=365,
    )
    denominator = profile.total_call_gex + abs(profile.total_put_gex)
    normalized = profile.net_gex / denominator if denominator > 0 else 0.0
    wall_distance = (profile.gamma_wall_strike - spot) / spot if profile.gamma_wall_strike is not None else None
    flip_distance = (profile.flip_strike - spot) / spot if profile.flip_strike is not None else None
    return (
        max(-1.0, min(1.0, normalized)),
        profile.gamma_wall_strike,
        wall_distance,
        profile.flip_strike,
        flip_distance,
        denominator > 0,
    )


def _joint_label(spot_return: float | None, skew_change: float | None) -> str:
    if spot_return is None or skew_change is None:
        return "UNKNOWN"
    spot_label = "SPOT_UP" if spot_return > 0 else "SPOT_DOWN" if spot_return < 0 else "SPOT_FLAT"
    skew_label = "SKEW_STEEPENING" if skew_change > 0 else "SKEW_FLATTENING" if skew_change < 0 else "SKEW_FLAT"
    return f"{spot_label}_{skew_label}"


def _quality_and_multiplier(
    *,
    complete: bool,
    stale: bool,
    put_call_skew: float | None,
    normalized_gex: float,
    joint_label: str,
    atm_7d: float | None,
    atm_30d: float | None,
) -> tuple[str, float]:
    if not complete or stale:
        return "UNKNOWN", 1.0
    if (
        (put_call_skew is not None and put_call_skew >= 0.08)
        or normalized_gex <= -0.35
        or (atm_7d is not None and atm_30d is not None and atm_7d - atm_30d >= 0.08)
    ):
        return "STRESS", 0.50
    if (
        (put_call_skew is not None and put_call_skew >= 0.04)
        or normalized_gex < -0.10
        or joint_label == "SPOT_DOWN_SKEW_STEEPENING"
    ):
        return "FRAGILE", 0.75
    if joint_label == "SPOT_UP_SKEW_FLATTENING" and normalized_gex >= 0:
        return "CONFIRMING", 1.0
    return "NEUTRAL", 1.0


def _canonical_id(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_options_state_identity(state: OptionsState) -> bool:
    """Return whether the claimed ID hashes the complete immutable payload."""
    try:
        payload = state.to_dict()
        claimed = str(payload.pop("options_state_id"))
        return bool(claimed) and claimed == _canonical_id(payload)
    except (TypeError, ValueError):
        return False


def _row_data_identity(rows: Sequence[Mapping[str, Any]]) -> str:
    """Hash the full selected raw-chain payload after scalar normalization."""
    normalized = sorted((_canonical_row(row) for row in rows), key=lambda row: _canonical_row_text(row))
    return _canonical_id({"rows": normalized})


def build_options_state(
    rows: Iterable[Mapping[str, Any]],
    *,
    cutoff_time_ns: int,
    realized_vol_30d: float | None,
    realized_vol_source_timestamp_ns: int,
    history_24h_rows: Iterable[Mapping[str, Any]] | None = None,
    stale_after_ns: int = DAY_NS,
) -> OptionsState:
    """Build one immutable observation-only state using information visible at cutoff."""
    cutoff_ns = lossless_timestamp(cutoff_time_ns)
    if cutoff_ns is None or cutoff_ns % FOUR_HOURS_NS != 0:
        raise ValueError("OptionsState requires a confirmed 4h cutoff")
    raw_ordered, ordered, source_ns, preflight_flags = _prepare_rows(rows)
    if source_ns > cutoff_ns:
        raise ValueError("OptionsState source timestamp is after cutoff")
    realized_vol_source_ns = lossless_timestamp(realized_vol_source_timestamp_ns)
    if realized_vol_source_ns is not None and realized_vol_source_ns > cutoff_ns:
        raise ValueError("OptionsState realized volatility source is after cutoff")
    spot = _spot(ordered)
    raw_history, history, history_source_ns, history_flags = _prepare_rows(history_24h_rows or (), history=True)
    if history_source_ns > cutoff_ns:
        raise ValueError("OptionsState history source timestamp is after cutoff")

    selected_expiries = {days: _selected_expiry_ts_ms(ordered, source_ns, days) for days in (7, 30, 90)}
    tenor_rows = {days: _expiry_rows(ordered, source_ns, days) for days in (7, 30, 90)}
    atm_7d = _atm_iv(tenor_rows[7], spot)
    atm_30d = _atm_iv(tenor_rows[30], spot)
    atm_90d = _atm_iv(tenor_rows[90], spot)
    put_25d = _delta_iv(tenor_rows[30], source_ns, spot, "put")
    call_25d = _delta_iv(tenor_rows[30], source_ns, spot, "call")
    put_iv = put_25d.iv if put_25d else None
    call_iv = call_25d.iv if call_25d else None
    put_call_skew = put_iv - call_iv if put_iv is not None and call_iv is not None else None
    risk_reversal = -put_call_skew if put_call_skew is not None else None
    proxy = _moneyness_proxy(tenor_rows[30], spot) if tenor_rows[30] else None

    rv = _finite_float(realized_vol_30d)
    if rv is not None and (rv < 0 or rv > 5):
        rv = None
    if realized_vol_source_ns is None:
        rv = None
    iv_minus_rv = atm_30d - rv if atm_30d is not None and rv is not None else None
    slope_7_30 = atm_30d - atm_7d if atm_30d is not None and atm_7d is not None else None
    slope_30_90 = atm_90d - atm_30d if atm_90d is not None and atm_30d is not None else None

    history_lag_ns = source_ns - history_source_ns if source_ns and history_source_ns else 0
    history_is_24h_asof = DAY_NS <= history_lag_ns <= DAY_NS + stale_after_ns
    if not history_is_24h_asof:
        history = ()
        history_source_ns = 0
    history_spot = _spot(history) if history else 0.0
    history_30d = _expiry_rows(history, history_source_ns, 30) if history else []
    history_put = _delta_iv(history_30d, history_source_ns, history_spot, "put") if history_30d else None
    history_call = _delta_iv(history_30d, history_source_ns, history_spot, "call") if history_30d else None
    history_skew = history_put.iv - history_call.iv if history_put is not None and history_call is not None else None
    spot_return = spot / history_spot - 1.0 if spot > 0 and history_spot > 0 else None
    skew_change = put_call_skew - history_skew if put_call_skew is not None and history_skew is not None else None
    joint_label = _joint_label(spot_return, skew_change)

    strike_concentration, expiry_concentration = _oi_concentrations(ordered)
    normalized_gex, wall, wall_distance, flip, flip_distance, gex_available = _gex_fields(ordered, source_ns, spot)
    valid_rows = sum(
        1
        for row in ordered
        if _normalized_iv(row) is not None
        and (_finite_float(row.get("strike")) or 0) > 0
        and str(row.get("option_type", "")).lower() in {"call", "put"}
        and (_finite_float(row.get("open_interest")) or 0) >= 0
    )
    coverage = valid_rows / len(ordered) if ordered else 0.0
    age_ns = cutoff_ns - source_ns if source_ns else cutoff_ns
    stale = not source_ns or age_ns > stale_after_ns

    flags: list[str] = [*preflight_flags, *history_flags]
    if realized_vol_source_ns is None:
        flags.append("OPTIONS_REALIZED_VOL_SOURCE_TIMESTAMP_MALFORMED")
    if not raw_ordered:
        flags.append("OPTIONS_MISSING")
    if stale:
        flags.append("OPTIONS_STALE")
    if valid_rows < len(ordered) and not any("MALFORMED" in flag for flag in preflight_flags):
        flags.append("OPTIONS_MALFORMED_ROW")
    if any(value is None for value in (atm_7d, atm_30d, atm_90d)):
        flags.append("OPTIONS_TENOR_COVERAGE_INCOMPLETE")
    if put_iv is None or call_iv is None:
        flags.append("OPTIONS_25DELTA_COVERAGE_INCOMPLETE")
    if rv is None:
        flags.append("OPTIONS_REALIZED_VOL_MISSING")
    if spot_return is None or skew_change is None:
        flags.append("OPTIONS_24H_HISTORY_MISSING")
    if strike_concentration <= 0 or expiry_concentration <= 0:
        flags.append("OPTIONS_OI_COVERAGE_INCOMPLETE")
    if not gex_available:
        flags.append("OPTIONS_GEX_UNAVAILABLE")
    complete = not flags
    trend_quality, multiplier = _quality_and_multiplier(
        complete=complete,
        stale=stale,
        put_call_skew=put_call_skew,
        normalized_gex=normalized_gex,
        joint_label=joint_label,
        atm_7d=atm_7d,
        atm_30d=atm_30d,
    )

    payload: dict[str, Any] = {
        "symbol": "BTC",
        "cutoff_time_ns": cutoff_ns,
        "source_timestamp_ns": source_ns,
        "history_24h_source_timestamp_ns": history_source_ns,
        "data_identity": _row_data_identity(raw_ordered),
        "history_24h_data_identity": _row_data_identity(raw_history) if history else "",
        "calculation_method": CALCULATION_METHOD,
        "calculation_version": CALCULATION_VERSION,
        "underlying_price": spot,
        "atm_iv_7d": atm_7d,
        "atm_iv_30d": atm_30d,
        "atm_iv_90d": atm_90d,
        "selected_expiry_ts_ms_7d": selected_expiries[7],
        "selected_expiry_ts_ms_30d": selected_expiries[30],
        "selected_expiry_ts_ms_90d": selected_expiries[90],
        "put_iv_25d_30d": put_iv,
        "call_iv_25d_30d": call_iv,
        "put_25d_selection_method": put_25d.method if put_25d is not None else "unavailable",
        "call_25d_selection_method": call_25d.method if call_25d is not None else "unavailable",
        "put_25d_lower_delta": put_25d.lower_delta if put_25d is not None else None,
        "put_25d_upper_delta": put_25d.upper_delta if put_25d is not None else None,
        "call_25d_lower_delta": call_25d.lower_delta if call_25d is not None else None,
        "call_25d_upper_delta": call_25d.upper_delta if call_25d is not None else None,
        "put_call_skew_25d_30d": put_call_skew,
        "risk_reversal_25d_30d": risk_reversal,
        "moneyness_skew_proxy_30d": proxy,
        "realized_vol_30d": rv,
        "realized_vol_source_timestamp_ns": realized_vol_source_ns if rv is not None else 0,
        "iv_minus_rv_30d": iv_minus_rv,
        "term_slope_7d_30d": slope_7_30,
        "term_slope_30d_90d": slope_30_90,
        "spot_return_24h": spot_return,
        "skew_change_24h": skew_change,
        "spot_skew_joint_label": joint_label,
        "strike_oi_concentration": strike_concentration,
        "expiry_oi_concentration": expiry_concentration,
        "normalized_net_gex": normalized_gex,
        "gex_normalization": GEX_NORMALIZATION,
        "gamma_wall_strike": wall,
        "gamma_wall_distance": wall_distance,
        "gamma_flip_strike": flip,
        "gamma_flip_distance": flip_distance,
        "source_age_seconds": age_ns / 1_000_000_000,
        "is_stale": stale,
        "chain_row_count": len(raw_ordered),
        "valid_row_count": valid_rows,
        "coverage": coverage,
        "trend_quality": trend_quality,
        "shadow_risk_multiplier": multiplier,
        "quality_flags": tuple(sorted(set(flags))),
    }
    return OptionsState(options_state_id=_canonical_id(payload), **payload)


__all__ = [
    "CALCULATION_METHOD",
    "CALCULATION_VERSION",
    "DeltaIvSelection",
    "OptionsState",
    "black76_forward_delta",
    "build_options_state",
    "interpolate_25_delta_iv",
    "validate_options_state_identity",
]
