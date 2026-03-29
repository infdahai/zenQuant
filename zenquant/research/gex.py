"""Gamma Exposure (GEX) analysis for Deribit BTC options.

Gamma Exposure measures the aggregate delta-hedging pressure that market makers
exert on the underlying price. Large positive GEX creates a "gamma wall" that
acts as a price magnet — market makers buy dips and sell rips to stay delta-neutral.
Large negative GEX (gamma flip) amplifies volatility.

Formula:
    GEX_per_contract = gamma * open_interest * contract_size * spot_price^2
    Total GEX = sum(call_GEX) - sum(put_GEX)

Convention:
    - Call GEX: positive (market makers are long gamma)
    - Put GEX: negative (market makers are short gamma to buyers)
    - Net GEX > 0: price pinning regime
    - Net GEX < 0: volatility amplification regime

Usage:
    from zenquant.research.gex import compute_gex_profile, GexProfile

    rows = load_option_chain_rows(path)  # list[OptionChainRow dicts]
    profile = compute_gex_profile(rows, spot=95000.0)
    print(profile.gamma_wall_strike)   # price level with max positive GEX
    print(profile.flip_strike)          # price where net GEX crosses zero
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Deribit BTC option contract size in BTC
BTC_CONTRACT_SIZE = 1.0


@dataclass(frozen=True, slots=True)
class StrikeGex:
    """GEX contribution at a single strike."""

    strike: float
    call_gex: float
    put_gex: float

    @property
    def net_gex(self) -> float:
        return self.call_gex + self.put_gex


@dataclass(frozen=True, slots=True)
class GexProfile:
    """Full GEX surface analysis from one options chain snapshot."""

    spot: float
    ts_ns: int
    strikes: list[StrikeGex]

    # Key levels
    gamma_wall_strike: float | None  # strike with highest net positive GEX
    flip_strike: float | None        # strike where cumulative GEX crosses zero
    max_pain_strike: float | None    # strike where total OI loss is minimized

    # Aggregate metrics
    total_call_gex: float
    total_put_gex: float
    net_gex: float

    # Regime signal
    regime: str  # "PINNING" | "AMPLIFYING" | "NEUTRAL"
    regime_reason: str


def _black_scholes_gamma(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    iv: float,
    risk_free_rate: float = 0.05,
) -> float:
    """Black-Scholes gamma for a European option.

    Returns 0.0 for invalid inputs (expired options, zero IV, etc.).
    """
    if time_to_expiry_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0

    try:
        sigma_sqrt_t = iv * math.sqrt(time_to_expiry_years)
        if sigma_sqrt_t <= 0:
            return 0.0
        d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * iv ** 2) * time_to_expiry_years) / sigma_sqrt_t
        gamma = math.exp(-0.5 * d1 ** 2) / (spot * sigma_sqrt_t * math.sqrt(2 * math.pi))
        return max(0.0, gamma)
    except (ValueError, ZeroDivisionError):
        return 0.0


def compute_gex_profile(
    rows: list[dict[str, Any]],
    spot: float,
    snapshot_ts_ns: int = 0,
    min_oi: float = 0.0,
    max_dte: int = 60,
) -> GexProfile:
    """Compute the full GEX profile from a list of option chain rows.

    Args:
        rows: List of dicts matching OptionChainRow schema
            (snapshot_ts_ns, strike, option_type, open_interest,
             mark_iv, expiry_ts_ms, underlying_price)
        spot: Current underlying spot price
        snapshot_ts_ns: Snapshot timestamp (nanoseconds since epoch)
        min_oi: Minimum open_interest to include (filter noise)
        max_dte: Maximum days to expiry to include
    """
    if not rows or spot <= 0:
        return GexProfile(
            spot=spot, ts_ns=snapshot_ts_ns, strikes=[],
            gamma_wall_strike=None, flip_strike=None, max_pain_strike=None,
            total_call_gex=0.0, total_put_gex=0.0, net_gex=0.0,
            regime="NEUTRAL", regime_reason="No data",
        )

    now_ns = snapshot_ts_ns or (rows[0].get("snapshot_ts_ns", 0))
    now_s = now_ns / 1_000_000_000

    # Aggregate GEX per strike
    strike_data: dict[float, dict[str, float]] = {}

    for row in rows:
        oi = float(row.get("open_interest", 0.0) or 0.0)
        if oi < min_oi:
            continue

        strike = float(row.get("strike", 0.0))
        option_type = str(row.get("option_type", "")).lower()  # "call" or "put"
        mark_iv = float(row.get("mark_iv", 0.0) or 0.0) / 100.0  # Deribit gives IV as pct
        expiry_ts_ms = int(row.get("expiry_ts_ms", 0))

        if strike <= 0 or option_type not in ("call", "put"):
            continue

        expiry_s = expiry_ts_ms / 1000.0
        dte = (expiry_s - now_s) / 86400
        if dte > max_dte or dte < 0:
            continue

        time_to_expiry_years = max(0.0, dte / 365.0)
        gamma = _black_scholes_gamma(spot, strike, time_to_expiry_years, mark_iv)

        # GEX = gamma * OI * contract_size * spot^2
        gex = gamma * oi * BTC_CONTRACT_SIZE * (spot ** 2)

        if strike not in strike_data:
            strike_data[strike] = {"call_gex": 0.0, "put_gex": 0.0, "call_oi": 0.0, "put_oi": 0.0}

        if option_type == "call":
            strike_data[strike]["call_gex"] += gex
            strike_data[strike]["call_oi"] += oi
        else:
            # Put GEX is negative (market makers are short gamma to put buyers)
            strike_data[strike]["put_gex"] -= gex
            strike_data[strike]["put_oi"] += oi

    if not strike_data:
        return GexProfile(
            spot=spot, ts_ns=snapshot_ts_ns, strikes=[],
            gamma_wall_strike=None, flip_strike=None, max_pain_strike=None,
            total_call_gex=0.0, total_put_gex=0.0, net_gex=0.0,
            regime="NEUTRAL", regime_reason="No strikes after filtering",
        )

    strikes = [
        StrikeGex(strike=s, call_gex=d["call_gex"], put_gex=d["put_gex"])
        for s, d in sorted(strike_data.items())
    ]

    total_call_gex = sum(s.call_gex for s in strikes)
    total_put_gex = sum(s.put_gex for s in strikes)
    net_gex = total_call_gex + total_put_gex

    # Gamma wall: strike with highest net GEX
    gamma_wall = max(strikes, key=lambda s: s.net_gex)
    gamma_wall_strike = gamma_wall.strike if gamma_wall.net_gex > 0 else None

    # Gamma flip: strike nearest to zero cumulative GEX
    flip_strike = _find_gamma_flip(strikes, spot)

    # Max pain: strike minimizing total option value at expiry
    max_pain_strike = _compute_max_pain(strike_data)

    # Regime classification
    regime, regime_reason = _classify_regime(net_gex, spot, gamma_wall_strike, flip_strike)

    return GexProfile(
        spot=spot,
        ts_ns=snapshot_ts_ns,
        strikes=strikes,
        gamma_wall_strike=gamma_wall_strike,
        flip_strike=flip_strike,
        max_pain_strike=max_pain_strike,
        total_call_gex=total_call_gex,
        total_put_gex=total_put_gex,
        net_gex=net_gex,
        regime=regime,
        regime_reason=regime_reason,
    )


def _find_gamma_flip(strikes: list[StrikeGex], spot: float) -> float | None:
    """Find the strike closest to where cumulative GEX crosses zero."""
    if not strikes:
        return None
    # Look at strikes near spot (within 20%)
    near_strikes = [s for s in strikes if abs(s.strike - spot) / spot < 0.20]
    if not near_strikes:
        near_strikes = strikes

    # Find where cumulative (call + put) GEX transitions from positive to negative
    sorted_strikes = sorted(near_strikes, key=lambda s: s.strike)
    cumulative = 0.0
    prev_strike = None
    for s in sorted_strikes:
        cumulative += s.net_gex
        if prev_strike is not None and cumulative <= 0:
            return s.strike  # flip happened here
        prev_strike = s.strike
    return None


def _compute_max_pain(strike_data: dict[float, dict[str, float]]) -> float | None:
    """Compute max pain: strike where total option holders lose the most."""
    if not strike_data:
        return None

    all_strikes = sorted(strike_data.keys())
    min_pain = float("inf")
    max_pain_strike = None

    for test_strike in all_strikes:
        pain = 0.0
        for strike, data in strike_data.items():
            # Call holders lose if test_strike < their strike
            if test_strike < strike:
                pain += (strike - test_strike) * data["call_oi"]
            # Put holders lose if test_strike > their strike
            if test_strike > strike:
                pain += (test_strike - strike) * data["put_oi"]
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = test_strike

    return max_pain_strike


def _classify_regime(
    net_gex: float,
    spot: float,
    gamma_wall: float | None,
    flip: float | None,
) -> tuple[str, str]:
    """Classify the options market regime based on net GEX."""
    if abs(net_gex) < 1e6:  # near-zero GEX
        return "NEUTRAL", "Net GEX near zero — balanced hedging pressure"

    if net_gex > 0:
        detail = f"Net GEX positive"
        if gamma_wall:
            detail += f", gamma wall at {gamma_wall:,.0f}"
        return "PINNING", detail
    else:
        detail = "Net GEX negative — market makers amplify moves"
        if flip:
            detail += f", gamma flip at {flip:,.0f}"
        return "AMPLIFYING", detail


def gex_to_signal(profile: GexProfile, spot: float) -> dict[str, float]:
    """Extract trading signals from GEX profile.

    Returns a dict suitable for use as a signal plugin input.

    Keys:
        gex_regime_score: +1 (pinning), 0 (neutral), -1 (amplifying)
        gamma_wall_distance_pct: % distance from spot to gamma wall
        max_pain_distance_pct: % distance from spot to max pain
    """
    regime_score = {
        "PINNING": 1.0,
        "NEUTRAL": 0.0,
        "AMPLIFYING": -1.0,
    }.get(profile.regime, 0.0)

    gw_dist = 0.0
    if profile.gamma_wall_strike and spot > 0:
        gw_dist = (profile.gamma_wall_strike - spot) / spot

    mp_dist = 0.0
    if profile.max_pain_strike and spot > 0:
        mp_dist = (profile.max_pain_strike - spot) / spot

    return {
        "gex_regime_score": regime_score,
        "gamma_wall_distance_pct": gw_dist,
        "max_pain_distance_pct": mp_dist,
        "net_gex": profile.net_gex,
    }
