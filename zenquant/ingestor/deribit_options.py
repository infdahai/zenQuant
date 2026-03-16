"""Observation-only Deribit options adapter for the live runtime.

The goal is intentionally narrow: publish a lightweight options risk pack that
operators can inspect without changing the core strategy path yet.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from zenquant.domain import DeribitRiskPack
from zenquant.infra import EventBus


DERIBIT_API_BASE = "https://www.deribit.com/api/v2/public"
_DAY_MS = 86_400_000


@dataclass(slots=True)
class DeribitOptionsIngestorConfig:
    """Polling and aggregation settings for the Deribit observation layer.

    Attributes:
        currency: Underlying currency on Deribit, kept to ``BTC`` for V1.
        poll_interval_s: Poll cadence. Fifteen minutes is enough for 4h/1d use.
        request_timeout_s: HTTP timeout for each public API request.
        atm_target_days: Expiry target used for the headline ATM IV metric.
        near_target_days: Short-dated expiry target used for term structure.
        far_target_days: Long-dated expiry target used for term structure.
        skew_put_moneyness: Put strike target as a spot fraction for skew proxy.
        skew_call_moneyness: Call strike target as a spot fraction for skew proxy.
        top_oi_count: Number of strikes used for the OI concentration metric.
    """

    currency: str = "BTC"
    poll_interval_s: float = 900.0
    request_timeout_s: float = 8.0
    atm_target_days: float = 30.0
    near_target_days: float = 7.0
    far_target_days: float = 90.0
    skew_put_moneyness: float = 0.90
    skew_call_moneyness: float = 1.10
    top_oi_count: int = 3


@dataclass(slots=True)
class _OptionSurfacePoint:
    instrument_name: str
    option_type: str
    strike: float
    expiry_ms: int
    open_interest: float
    mark_iv: float
    volume_usd: float


class DeribitOptionsIngestor:
    """Poll Deribit public options data and publish an aggregated risk pack.

    The adapter is fail-open. If Deribit is unavailable, the core live runtime
    keeps running and simply reports the observation layer as stale.
    """

    def __init__(
        self,
        bus: EventBus,
        config: DeribitOptionsIngestorConfig | None = None,
    ) -> None:
        self._bus = bus
        self._config = config or DeribitOptionsIngestorConfig()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_error = ""
        self._last_success_ns = 0
        self._polls = 0
        self._published = 0

    async def start(self) -> None:
        """Start the background polling loop if it is not running already."""
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="zenquant-deribit-options")

    async def stop(self) -> None:
        """Stop the polling loop and wait for it to exit cleanly."""
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def snapshot(self) -> dict[str, Any]:
        """Return operator-facing health for dashboard/runtime endpoints."""
        return {
            "enabled": True,
            "last_success_ns": self._last_success_ns,
            "polls": self._polls,
            "published": self._published,
            "last_error": self._last_error,
        }

    async def poll_once(self) -> DeribitRiskPack | None:
        """Fetch one pack and publish it immediately if successful."""
        pack = await asyncio.to_thread(self._fetch_pack)
        self._polls += 1
        if pack is None:
            return None
        self._last_success_ns = pack.ts_ns
        self._last_error = ""
        self._published += 1
        self._bus.publish(f"risk.deribit.{pack.symbol}", pack)
        return pack

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._polls += 1
                self._last_error = str(exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._config.poll_interval_s)
            except asyncio.TimeoutError:
                continue

    def _fetch_pack(self) -> DeribitRiskPack | None:
        currency = self._config.currency.upper()
        summaries = _fetch_json(
            f"{DERIBIT_API_BASE}/get_book_summary_by_currency",
            timeout_s=self._config.request_timeout_s,
            query={"currency": currency, "kind": "option"},
        )
        instruments = _fetch_json(
            f"{DERIBIT_API_BASE}/get_instruments",
            timeout_s=self._config.request_timeout_s,
            query={"currency": currency, "kind": "option", "expired": "false"},
        )
        index_name = f"{currency.lower()}_usd"
        index_payload = _fetch_json(
            f"{DERIBIT_API_BASE}/get_index_price",
            timeout_s=self._config.request_timeout_s,
            query={"index_name": index_name},
        )

        if not isinstance(summaries, list) or not isinstance(instruments, list):
            raise RuntimeError("Deribit API returned an invalid options surface payload")
        spot = float((index_payload or {}).get("index_price", 0.0) or 0.0)
        surface = _merge_surface_points(summaries=summaries, instruments=instruments)
        if not surface:
            self._last_error = "Deribit returned no usable option summaries"
            return None
        if spot <= 0:
            # Fall back to the median underlying price visible in the option
            # summaries when the dedicated index endpoint is unavailable.
            underlying_prices = [
                float(item.get("underlying_price", 0.0) or 0.0)
                for item in summaries
                if isinstance(item, dict)
            ]
            spot = next((price for price in underlying_prices if price > 0), 0.0)
        if spot <= 0:
            raise RuntimeError("Deribit spot reference is unavailable")

        quality_flags: list[str] = []
        now_ms = int(time.time() * 1_000)
        atm_expiry = _pick_expiry(surface, now_ms=now_ms, target_days=self._config.atm_target_days)
        near_expiry = _pick_expiry(surface, now_ms=now_ms, target_days=self._config.near_target_days)
        far_expiry = _pick_expiry(surface, now_ms=now_ms, target_days=self._config.far_target_days)

        atm_iv_30d = _atm_iv(surface, expiry_ms=atm_expiry, spot=spot)
        if atm_iv_30d is None:
            raise RuntimeError("Deribit ATM IV could not be constructed")

        near_iv = _atm_iv(surface, expiry_ms=near_expiry, spot=spot)
        far_iv = _atm_iv(surface, expiry_ms=far_expiry, spot=spot)
        if near_iv is None or far_iv is None or far_iv <= 0:
            term_structure_slope = 0.0
            quality_flags.append("TERM_STRUCTURE_PARTIAL")
        else:
            term_structure_slope = (far_iv - near_iv) / far_iv

        put_call_skew = _skew_proxy(
            surface=surface,
            expiry_ms=atm_expiry,
            spot=spot,
            put_moneyness=self._config.skew_put_moneyness,
            call_moneyness=self._config.skew_call_moneyness,
        )
        if put_call_skew is None:
            put_call_skew = 0.0
            quality_flags.append("SKEW_PARTIAL")

        oi_concentration = _oi_concentration(surface, top_n=self._config.top_oi_count)
        block_flow_imbalance = _volume_imbalance_proxy(surface)
        quality_flags.append("FLOW_PROXY_VOLUME")
        if not quality_flags:
            quality_flags = ["OK"]

        return DeribitRiskPack(
            symbol=currency,
            ts_ns=time.time_ns(),
            atm_iv_30d=round(atm_iv_30d, 4),
            term_structure_slope=round(term_structure_slope, 6),
            put_call_skew_25d=round(put_call_skew, 4),
            oi_concentration=round(oi_concentration, 6),
            block_flow_imbalance=round(block_flow_imbalance, 6),
            quality_flags=quality_flags,
        )


def _merge_surface_points(
    *,
    summaries: list[object],
    instruments: list[object],
) -> list[_OptionSurfacePoint]:
    """Join summary and instrument metadata into one normalized option surface."""
    by_name: dict[str, dict[str, Any]] = {}
    for item in instruments:
        if not isinstance(item, dict):
            continue
        name = str(item.get("instrument_name", "") or "")
        if not name:
            continue
        by_name[name] = item

    points: list[_OptionSurfacePoint] = []
    for item in summaries:
        if not isinstance(item, dict):
            continue
        name = str(item.get("instrument_name", "") or "")
        instrument = by_name.get(name)
        if instrument is None:
            continue
        expiry_ms = int(instrument.get("expiration_timestamp", 0) or 0)
        strike = float(instrument.get("strike", 0.0) or 0.0)
        mark_iv = float(item.get("mark_iv", 0.0) or 0.0)
        if expiry_ms <= 0 or strike <= 0 or mark_iv <= 0:
            continue
        points.append(
            _OptionSurfacePoint(
                instrument_name=name,
                option_type=str(instrument.get("option_type", "") or "").lower(),
                strike=strike,
                expiry_ms=expiry_ms,
                open_interest=float(item.get("open_interest", 0.0) or 0.0),
                mark_iv=mark_iv,
                volume_usd=float(item.get("volume_usd", 0.0) or 0.0),
            )
        )
    return points


def _pick_expiry(
    points: list[_OptionSurfacePoint],
    *,
    now_ms: int,
    target_days: float,
) -> int:
    """Return the listed expiry closest to the requested horizon."""
    expiries = sorted(
        {
            point.expiry_ms
            for point in points
            if point.expiry_ms > now_ms + _DAY_MS
        }
    )
    if not expiries:
        raise RuntimeError("Deribit returned no future option expiries")
    return min(expiries, key=lambda expiry_ms: abs(((expiry_ms - now_ms) / _DAY_MS) - target_days))


def _atm_iv(points: list[_OptionSurfacePoint], *, expiry_ms: int, spot: float) -> float | None:
    """Return a weighted ATM IV estimate for one expiry."""
    expiry_points = [point for point in points if point.expiry_ms == expiry_ms]
    if not expiry_points:
        return None

    selected: list[_OptionSurfacePoint] = []
    for option_type in ("call", "put"):
        candidates = [point for point in expiry_points if point.option_type == option_type]
        if not candidates:
            continue
        selected.append(min(candidates, key=lambda point: abs(point.strike - spot)))

    if not selected:
        return None
    total_weight = sum(max(point.open_interest, 1.0) for point in selected)
    return sum(point.mark_iv * max(point.open_interest, 1.0) for point in selected) / total_weight


def _skew_proxy(
    *,
    surface: list[_OptionSurfacePoint],
    expiry_ms: int,
    spot: float,
    put_moneyness: float,
    call_moneyness: float,
) -> float | None:
    """Approximate skew with OTM put/call strikes around the target expiry."""
    expiry_points = [point for point in surface if point.expiry_ms == expiry_ms]
    if not expiry_points:
        return None
    put_candidates = [point for point in expiry_points if point.option_type == "put"]
    call_candidates = [point for point in expiry_points if point.option_type == "call"]
    if not put_candidates or not call_candidates:
        return None

    put_target = spot * put_moneyness
    call_target = spot * call_moneyness
    put_point = min(put_candidates, key=lambda point: abs(point.strike - put_target))
    call_point = min(call_candidates, key=lambda point: abs(point.strike - call_target))
    return put_point.mark_iv - call_point.mark_iv


def _oi_concentration(surface: list[_OptionSurfacePoint], *, top_n: int) -> float:
    """Measure how concentrated open interest is in the top listed strikes."""
    open_interest = sorted((max(point.open_interest, 0.0) for point in surface), reverse=True)
    total_oi = sum(open_interest)
    if total_oi <= 0:
        return 0.0
    return sum(open_interest[: max(1, top_n)]) / total_oi


def _volume_imbalance_proxy(surface: list[_OptionSurfacePoint]) -> float:
    """Use call-vs-put volume as a simple public proxy for options flow."""
    call_volume = sum(point.volume_usd for point in surface if point.option_type == "call")
    put_volume = sum(point.volume_usd for point in surface if point.option_type == "put")
    total = call_volume + put_volume
    if total <= 0:
        return 0.0
    return (call_volume - put_volume) / total


def _fetch_json(url: str, *, timeout_s: float, query: dict[str, str]) -> Any:
    """Fetch a public JSON payload with consistent headers and soft failures."""
    encoded = urllib.parse.urlencode(query)
    request = urllib.request.Request(
        f"{url}?{encoded}",
        method="GET",
        headers={"User-Agent": "zenquant/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Deribit request failed: {url}") from exc
    return payload.get("result")


__all__ = [
    "DeribitOptionsIngestor",
    "DeribitOptionsIngestorConfig",
]
